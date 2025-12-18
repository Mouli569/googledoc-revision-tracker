from __future__ import annotations

import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Protocol, cast, runtime_checkable

import yaml
from googleapiclient.discovery import build

GOOGLE_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


def extract_doc_id_from_url(text: str) -> str:
    """
    Extract Google Doc ID from URL or return the text as-is if it's already an ID.

    Supports URLs like:
    - https://docs.google.com/document/d/DOCUMENT_ID/edit
    - https://docs.google.com/document/d/DOCUMENT_ID/edit?tab=t.0

    Args:
        text: Either a Google Docs URL or a document ID

    Returns:
        The document ID

    Example:
        >>> extract_doc_id_from_url("https://docs.google.com/document/d/1evpXdZF.../edit")
        '1evpXdZF...'
        >>> extract_doc_id_from_url("1evpXdZF...")
        '1evpXdZF...'
    """
    # Pattern to match Google Docs URL and extract the ID
    pattern = r'https://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)'
    match = re.match(pattern, text)

    if match:
        return match.group(1)

    # If not a URL, assume it's already a document ID
    return text

Granularity = Literal["all", "hourly", "daily", "weekly", "monthly"]

@dataclass
class DocumentConfig:
    """Configuration for a single document to track."""
    doc_id: str
    folder_name: str | None = None
    granularity: Granularity = "all"

@runtime_checkable
class DriveListRequest(Protocol):
    def execute(self) -> Dict[str, object]: ...


@runtime_checkable
class DriveMediaRequest(Protocol):
    def execute(self) -> bytes: ...


@runtime_checkable
class DriveFilesResource(Protocol):
    def get(self, *, fileId: str, fields: str) -> DriveListRequest: ...

    def export(
        self,
        *,
        fileId: str,
        revisionId: str,
        mimeType: str,
    ) -> DriveMediaRequest: ...


@runtime_checkable
class DriveService(Protocol):
    def files(self) -> DriveFilesResource: ...


@runtime_checkable
class InstalledAppFlowProtocol(Protocol):
    def run_local_server(
        self,
        *,
        port: int,
        open_browser: bool,
        authorization_prompt_message: str,
        success_message: str,
    ) -> object: ...


@dataclass
class FlowResult:
    credentials: object | None = None
    error: BaseException | None = None

def get_time(format: str = '%Y-%m-%d-%H%M%S') -> str:
    """
    Get the current UTC timestamp as a formatted string.

    Args:
        format: Python strftime format string. Default is 'YYYY-MM-DD-HHMMSS'.

    Returns:
        Formatted timestamp string (e.g., '2025-12-15-143000').

    Example:
        >>> get_time()
        '2025-12-15-143000'
        >>> get_time(format='%Y-%m-%d')
        '2025-12-15'
    """
    return datetime.now(timezone.utc).strftime(format)

def get_required_env(var_name: str) -> str:
    """
    Retrieve a required environment variable or exit with a helpful error message.

    This function looks for an environment variable and exits the program if it's not found,
    providing clear instructions to the user on how to set it.

    Args:
        var_name: Name of the environment variable to retrieve.

    Returns:
        The value of the environment variable.

    Raises:
        SystemExit: If the environment variable is not set (exits with code 1).

    Example:
        # If not set, prints error and exits
        # If set, returns the value
    """
    value = os.environ.get(var_name)
    if value:
        return value
    print(
        f"Error: Missing required environment variable: {var_name}\n"
        f"Please set it before running the CLI, e.g.\n"
        f"  export {var_name}='example-value'",
        file=sys.stderr,
    )
    raise SystemExit(1)

def load_document_ids_from_config(config_path: str = "documents.yaml") -> List[DocumentConfig]:
    """
    Load document configurations from YAML configuration file.

    This function reads a YAML file containing document IDs, optional custom names,
    and optional granularity settings.

    Supported formats:
    1. Simple: list of document ID strings
    2. Full: list of dicts with 'id' and optional 'name' and 'granularity' fields

    Args:
        config_path: Path to YAML configuration file (default: "documents.yaml").

    Returns:
        List of DocumentConfig objects. Returns empty list if file doesn't exist
        or is malformed.

    Example:
        >>> # documents.yaml content (full format):
        >>> # documents:
        >>> #   - id: 1Q-qMIRexwd...
        >>> #     name: cv-matt
        >>> #     granularity: daily
        >>> #   - id: 2A-bNkPstu...
        >>> #     name: project-proposal
        >>> #     granularity: weekly
        >>> docs = load_document_ids_from_config()
        >>> print(docs[0])
        DocumentConfig(doc_id='1Q-qMIRexwd...', folder_name='cv-matt', granularity='daily')

        >>> # documents.yaml content (simple format):
        >>> # documents:
        >>> #   - 1Q-qMIRexwd...
        >>> #   - 2A-bNkPstu...
        >>> docs = load_document_ids_from_config()
        >>> print(docs[0])
        DocumentConfig(doc_id='1Q-qMIRexwd...', folder_name=None, granularity='all')
    """
    path = Path(config_path)
    if not path.exists():
        return []

    try:
        with path.open() as f:
            config = yaml.safe_load(f)

        if not config or 'documents' not in config:
            return []

        result = []
        for item in config['documents']:
            if isinstance(item, str):
                # Simple format: just document ID
                result.append(DocumentConfig(doc_id=item))
            elif isinstance(item, dict) and 'id' in item:
                # Full format: dict with id and optional name/granularity
                doc_id = item['id']
                folder_name = item.get('name')
                granularity = item.get('granularity', 'all')

                # Validate granularity
                valid_granularities = {"all", "hourly", "daily", "weekly", "monthly"}
                if granularity not in valid_granularities:
                    print(f"Warning: Invalid granularity '{granularity}' for {doc_id}, using 'all'")
                    granularity = 'all'

                result.append(DocumentConfig(
                    doc_id=doc_id,
                    folder_name=folder_name,
                    granularity=granularity
                ))
            # Ignore malformed entries

        return result
    except (yaml.YAMLError, IOError, OSError, KeyError, TypeError, ValueError) as e:
        # If YAML is malformed or any other error, warn and return empty list
        print(f"Warning: Failed to parse config file '{config_path}': {e}", file=sys.stderr)
        return []

def sanitize_filename(title: str, max_length: int = 200) -> str:
    """
    Convert a document title into a safe filename by removing/replacing problematic characters.

    This function handles several edge cases:
    - Removes filesystem-unsafe characters (< > : " / \\ | ? *)
    - Replaces non-alphanumeric characters with underscores
    - Collapses multiple underscores/spaces into single underscores
    - Blocks path traversal attempts (.. sequences)
    - Handles empty strings by using 'untitled'
    - Truncates to max_length (accounting for 21 chars for timestamp and extension)

    Args:
        title: The original document title or filename base.
        max_length: Maximum allowed length for the sanitized filename (default: 200).

    Returns:
        Sanitized filename safe for all operating systems.

    Example:
        >>> sanitize_filename("My Document: Draft #1")
        'My_Document_Draft_1'
        >>> sanitize_filename("")
        'untitled'
        >>> sanitize_filename("A" * 300, max_length=200)
        'AAAA...'  # Truncated to 179 chars (200 - 21 for timestamp)
        >>> sanitize_filename("../../etc/passwd")
        'untitled'  # Path traversal blocked
    """
    # Replace filesystem-unsafe characters with underscores
    safe_title = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", title)
    # Replace non-alphanumeric characters (except dots and hyphens) with underscores
    safe_title = re.sub(r"[^\w.\-]+", "_", safe_title)
    # Collapse multiple underscores/whitespace into single underscores
    safe_title = re.sub(r"[_\s]+", "_", safe_title).strip("_")

    # Block path traversal: if result contains .. or path separators, reject it
    if '..' in safe_title or '/' in safe_title or '\\' in safe_title:
        safe_title = "untitled"

    # Handle empty result
    if not safe_title:
        safe_title = "untitled"

    # Truncate if needed (reserve 21 chars for timestamp prefix and extension)
    allowed_length = max_length - 21
    if len(safe_title) > allowed_length:
        safe_title = safe_title[:allowed_length]

    return safe_title

def filter_revisions_by_granularity(revisions: List[Dict], granularity: Granularity) -> List[Dict]:
    """
    Filter revisions to keep only the final revision per time period.

    Groups revisions by time period (hour, day, week, or month) and keeps only
    the last revision from each period. This reduces the number of revisions
    downloaded while maintaining representative snapshots over time.

    Args:
        revisions: List of revision dicts from Google Drive API
        granularity: Time period granularity ('all', 'hourly', 'daily', 'weekly', 'monthly')

    Returns:
        Filtered list of revisions (last revision per period)

    Example:
        >>> revisions = [
        ...     {'id': '1', 'modifiedDate': '2025-01-15T10:00:00.000Z'},
        ...     {'id': '2', 'modifiedDate': '2025-01-15T14:00:00.000Z'},
        ...     {'id': '3', 'modifiedDate': '2025-01-16T09:00:00.000Z'},
        ... ]
        >>> filtered = filter_revisions_by_granularity(revisions, 'daily')
        >>> len(filtered)
        2  # One for Jan 15, one for Jan 16
    """
    if granularity == 'all':
        return revisions

    if not revisions:
        return []

    # Group revisions by time period
    from collections import defaultdict
    periods = defaultdict(list)

    for revision in revisions:
        # Parse ISO 8601 timestamp
        timestamp_str = revision['modifiedDate']
        # Parse format: 2025-01-15T10:30:45.123Z
        dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))

        # Determine period key based on granularity
        if granularity == 'hourly':
            period_key = dt.strftime('%Y-%m-%d-%H')
        elif granularity == 'daily':
            period_key = dt.strftime('%Y-%m-%d')
        elif granularity == 'weekly':
            # Use ISO week number
            period_key = dt.strftime('%Y-W%W')
        elif granularity == 'monthly':
            period_key = dt.strftime('%Y-%m')
        else:
            period_key = 'all'

        periods[period_key].append((dt, revision))

    # Get the last revision from each period
    filtered = []
    for period_revisions in periods.values():
        # Sort by timestamp and take the last one
        period_revisions.sort(key=lambda x: x[0])
        _, last_revision = period_revisions[-1]
        filtered.append(last_revision)

    # Sort filtered revisions by timestamp
    filtered.sort(key=lambda r: r['modifiedDate'])

    return filtered

def run_flow_with_timeout(flow: InstalledAppFlowProtocol, timeout: int = 120) -> object:
    """
    Run the Google OAuth flow with a timeout to avoid hanging browser sessions.

    This function runs the OAuth authorization in a separate thread with a timeout.
    If the user doesn't complete authorization within the timeout period, the
    operation fails with a TimeoutError.

    The OAuth flow:
    1. Opens a browser window for user authorization
    2. Starts a local server to receive the OAuth callback
    3. Waits for user to grant permissions
    4. Returns credentials if successful

    Args:
        flow: InstalledAppFlow object configured with client secrets and scopes.
        timeout: Maximum seconds to wait for user authorization (default: 120).

    Returns:
        OAuth credentials object that can be used with Google APIs.

    Raises:
        TimeoutError: If user doesn't complete authorization within timeout.
        RuntimeError: If OAuth flow completes but doesn't return credentials.
        Any exception raised during the OAuth flow itself.

    Example:
        >>> flow = InstalledAppFlow.from_client_secrets_file(
        ...     "client_secrets.json",
        ...     scopes=["https://www.googleapis.com/auth/drive"]
        ... )
        >>> credentials = run_flow_with_timeout(flow, timeout=300)
    """
    result = FlowResult()

    def target() -> None:
        try:
            result.credentials = flow.run_local_server(
                port=0,
                open_browser=True,
                authorization_prompt_message="Please authorize the CLI to read the document...",
                success_message="Authorization complete. You may close this tab.",
            )
        except Exception as exc:
            result.error = exc

    # Run OAuth flow in separate thread to enable timeout
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)

    # Check if authorization completed successfully
    if thread.is_alive():
        raise TimeoutError(f"Authorization timed out after {timeout} seconds")
    if result.error:
        raise result.error
    if result.credentials is None:
        raise RuntimeError("OAuth flow did not return credentials")
    return result.credentials


def build_drive_service(credentials: object) -> DriveService:
    """
    Build a Google Drive API v3 client.

    This creates a service object for interacting with Drive API v3, which is used
    for getting document metadata and exporting current content.

    Args:
        credentials: OAuth2 credentials from authorization flow.

    Returns:
        DriveService object for making API v3 calls.

    Example:
        >>> service = build_drive_service(credentials)
        >>> title = service.files().get(fileId="doc_id", fields="name").execute()
    """
    service = build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )
    return cast(DriveService, service)


def build_drive_service_v2(credentials: object) -> object:
    """
    Build a Google Drive API v2 client for accessing revisions.

    Drive API v2 is required for accessing revision history. The v3 API does not
    support the revisions endpoint, so we must use v2 for that specific functionality.

    Args:
        credentials: OAuth2 credentials from authorization flow.

    Returns:
        Drive API v2 service object.

    Note:
        Only use this for revision-related operations. Use build_drive_service()
        (v3) for all other Drive operations.

    Example:
        >>> service_v2 = build_drive_service_v2(credentials)
        >>> revisions = service_v2.revisions().list(fileId="doc_id").execute()
    """
    service = build(
        "drive",
        "v2",
        credentials=credentials,
        cache_discovery=False,
    )
    return service


def fetch_document_title(service: DriveService, file_id: str) -> str:
    """
    Fetch the title of a Google Drive document.

    Uses the Drive API to retrieve document metadata and extract the title.
    Returns "Untitled Document" if the title is not available.

    Args:
        service: Drive API v3 service object.
        file_id: Google Drive file ID (from document URL).

    Returns:
        Document title string, or "Untitled Document" if not found.

    Example:
        >>> service = build_drive_service(credentials)
        >>> title = fetch_document_title(service, "1abc...xyz")
        >>> print(title)
        'My Important Document'
    """
    response = service.files().get(fileId=file_id, fields="name").execute()
    return str(response.get("name", "Untitled Document"))


def download_revisions(
    service_v2: object,
    file_id: str,
    export_dir: str,
    credentials: object = None,
    doc_title: str | None = None,
    folder_name: str | None = None,
    granularity: Granularity = "all",
) -> List[Path]:
    """
    Download revisions of a Google Doc as individual text files.

    Uses Drive API v2 to fetch the complete revision history of a document.
    Each revision is saved as a separate file with metadata in the filename.
    Can filter revisions by time granularity to download only the final
    revision per period (hour, day, week, or month).

    The function:
    1. Creates a subdirectory using folder_name (if provided) or document ID
    2. Fetches all available revisions via API
    3. Filters by granularity (if not 'all')
    4. Downloads each revision's plain text export
    5. Saves with filename: {timestamp}.txt

    Args:
        service_v2: Drive API v2 service object (required for revisions).
        file_id: Google Drive document ID.
        export_dir: Base directory for saving revisions (e.g., "revisions").
        credentials: OAuth2 credentials for authenticated downloads (optional).
        doc_title: Document title (used for display in calling code).
        folder_name: Custom folder name for this document's revisions. If None,
                     uses file_id as folder name (optional).
        granularity: Time period for filtering revisions. Options:
                     'all' (default), 'hourly', 'daily', 'weekly', 'monthly'.

    Returns:
        List of Path objects for all downloaded revision files.
        Returns empty list if no revisions are available.

    Note:
        The API only returns "grouped" revisions, not every individual edit.
        Some fine-grained changes visible in Google Docs UI may be grouped
        together in the API response.

    Example:
        >>> service_v2 = build_drive_service_v2(credentials)
        >>> files = download_revisions(
        ...     service_v2, "doc_id", "revisions",
        ...     folder_name="cv-matt", doc_title="My CV",
        ...     granularity="daily"
        ... )
        >>> print(f"Downloaded {len(files)} revisions")
        >>> for f in files:
        ...     print(f"  - {f.name}")
    """
    import urllib.request

    # Create output directory using custom folder name or document ID
    # Sanitize folder_name to prevent path traversal attacks
    if folder_name:
        target_folder = sanitize_filename(folder_name)
    else:
        target_folder = file_id
    output_dir = Path(export_dir) / target_folder
    output_dir.mkdir(exist_ok=True, parents=True)

    # Fetch all revisions from Drive API v2 with retry logic for rate limiting
    max_retries = 5
    initial_delay = 1  # seconds
    revisions = None

    for attempt in range(max_retries):
        try:
            revisions = service_v2.revisions().list(fileId=file_id).execute()
            break  # Success - exit retry loop
        except Exception as e:
            # Check if it's a rate limit error (HTTP 429)
            if hasattr(e, 'resp') and hasattr(e.resp, 'status') and e.resp.status == 429:
                if attempt < max_retries - 1:
                    delay = initial_delay * (2 ** attempt)
                    print(f"  Rate limited when fetching revisions, retrying in {delay}s (attempt {attempt + 1}/{max_retries})...")
                    time.sleep(delay)
                    continue
                else:
                    print(f"  Error: Could not fetch revisions after {max_retries} attempts: {e}", file=sys.stderr)
                    raise
            else:
                # Non-retriable error
                raise

    if not revisions:
        print(f"  Error: Failed to fetch revisions for {file_id}", file=sys.stderr)
        return []

    if 'items' not in revisions:
        return []

    items = revisions['items']

    # Filter by granularity
    if granularity != 'all':
        original_count = len(items)
        items = filter_revisions_by_granularity(items, granularity)
        print(f"  Filtered {original_count} revisions to {len(items)} ({granularity} granularity)")

    downloaded_files = []

    for revision in items:
        revision_id = revision['id']
        modified_date = revision['modifiedDate']

        # Get the plain text export link
        export_links = revision.get('exportLinks', {})
        if 'text/plain' not in export_links:
            continue  # Skip revisions without text export

        export_link = export_links['text/plain']

        # Create filename from timestamp only
        safe_date = modified_date.replace(':', '-').replace('.', '-')
        filename = f"{safe_date}.txt"
        file_path = output_dir / filename

        # Download the revision content with OAuth authentication and retry logic
        max_retries = 5
        initial_delay = 1  # seconds

        for attempt in range(max_retries):
            try:
                # Create request with authorization header
                req = urllib.request.Request(export_link)
                if credentials:
                    # Ensure token is fresh
                    if hasattr(credentials, 'expired') and credentials.expired:
                        from google.auth.transport.requests import Request
                        credentials.refresh(Request())
                    # Add authorization header
                    req.add_header('Authorization', f'Bearer {credentials.token}')

                # Download the content
                with urllib.request.urlopen(req) as response:
                    content = response.read()
                    file_path.write_bytes(content)
                    downloaded_files.append(file_path)
                    break  # Success - exit retry loop

            except urllib.error.HTTPError as e:
                if e.code == 429:  # Rate limit error
                    if attempt < max_retries - 1:
                        # Calculate exponential backoff delay
                        delay = initial_delay * (2 ** attempt)
                        print(f"  Rate limited on revision {revision_id}, retrying in {delay}s (attempt {attempt + 1}/{max_retries})...")
                        time.sleep(delay)
                        continue
                    else:
                        # Max retries reached
                        print(f"  Warning: Could not download revision {revision_id} after {max_retries} attempts: {e}")
                        break
                else:
                    # Non-retriable HTTP error
                    print(f"  Warning: Could not download revision {revision_id}: HTTP {e.code} {e.reason}")
                    break

            except Exception as e:
                # Other non-retriable errors
                print(f"  Warning: Could not download revision {revision_id}: {e}")
                break

    return downloaded_files
