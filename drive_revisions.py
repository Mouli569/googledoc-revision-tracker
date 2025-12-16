from __future__ import annotations

import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Protocol, cast, runtime_checkable

import yaml
from googleapiclient.discovery import build

GOOGLE_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

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
        >>> doc_id = get_required_env("GOOGLE_DOCUMENT_ID")
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

def load_document_ids_from_config(config_path: str = "documents.yaml") -> Dict[str, str | None]:
    """
    Load document IDs and optional names from YAML configuration file.

    This function reads a YAML file containing document IDs and optional custom names.
    Supports two formats:
    1. Simple: list of document ID strings
    2. Named: list of dicts with 'id' and optional 'name' fields

    Args:
        config_path: Path to YAML configuration file (default: "documents.yaml").

    Returns:
        Dict mapping document IDs to optional folder names. If no name is specified,
        the value is None and the document ID will be used as the folder name.
        Returns empty dict if file doesn't exist or is malformed.

    Example:
        >>> # documents.yaml content (named format):
        >>> # documents:
        >>> #   - id: 1Q-qMIRexwd...
        >>> #     name: cv-matt
        >>> #   - id: 2A-bNkPstu...
        >>> #     name: project-proposal
        >>> docs = load_document_ids_from_config()
        >>> print(docs)
        {'1Q-qMIRexwd...': 'cv-matt', '2A-bNkPstu...': 'project-proposal'}

        >>> # documents.yaml content (simple format):
        >>> # documents:
        >>> #   - 1Q-qMIRexwd...
        >>> #   - 2A-bNkPstu...
        >>> docs = load_document_ids_from_config()
        >>> print(docs)
        {'1Q-qMIRexwd...': None, '2A-bNkPstu...': None}
    """
    path = Path(config_path)
    if not path.exists():
        return {}

    try:
        with path.open() as f:
            config = yaml.safe_load(f)

        if not config or 'documents' not in config:
            return {}

        result = {}
        for item in config['documents']:
            if isinstance(item, str):
                # Simple format: just document ID
                result[item] = None
            elif isinstance(item, dict) and 'id' in item:
                # Named format: dict with id and optional name
                doc_id = item['id']
                doc_name = item.get('name')
                result[doc_id] = doc_name
            # Ignore malformed entries

        return result
    except Exception:
        # If YAML is malformed or any other error, return empty dict
        return {}

def sanitize_filename(title: str, max_length: int = 200) -> str:
    """
    Convert a document title into a safe filename by removing/replacing problematic characters.

    This function handles several edge cases:
    - Removes filesystem-unsafe characters (< > : " / \\ | ? *)
    - Replaces non-alphanumeric characters with underscores
    - Collapses multiple underscores/spaces into single underscores
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
    """
    # Replace filesystem-unsafe characters with underscores
    safe_title = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", title)
    # Replace non-alphanumeric characters (except dots and hyphens) with underscores
    safe_title = re.sub(r"[^\w.\-]+", "_", safe_title)
    # Collapse multiple underscores/whitespace into single underscores
    safe_title = re.sub(r"[_\s]+", "_", safe_title).strip("_")

    # Handle empty result
    if not safe_title:
        safe_title = "untitled"

    # Truncate if needed (reserve 21 chars for timestamp prefix and extension)
    allowed_length = max_length - 21
    if len(safe_title) > allowed_length:
        safe_title = safe_title[:allowed_length]

    return safe_title

def get_unique_folder_name(base_dir: str, doc_title: str, doc_id: str) -> str:
    """
    Generate unique folder name for document revisions.

    If a folder with the sanitized title already exists, appends the last 6 characters
    of the document ID to ensure uniqueness. This handles the case where multiple
    documents have identical titles.

    Args:
        base_dir: Base directory where revision folders are stored (e.g., "revisions").
        doc_title: Document title from Google Drive.
        doc_id: Google Drive document ID.

    Returns:
        Unique folder name (not a full path, just the folder name).

    Example:
        >>> # First document named "Meeting Notes"
        >>> get_unique_folder_name("revisions", "Meeting Notes", "abc123xyz")
        'Meeting_Notes'
        >>> # Second document with same title (folder already exists)
        >>> get_unique_folder_name("revisions", "Meeting Notes", "def456uvw")
        'Meeting_Notes_456uvw'
    """
    base_path = Path(base_dir)
    safe_title = sanitize_filename(doc_title)
    folder_path = base_path / safe_title

    if not folder_path.exists():
        return safe_title

    # Folder exists - append last 6 chars of document ID
    id_suffix = doc_id[-6:]
    return f"{safe_title}_{id_suffix}"

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
        except BaseException as exc:  # noqa: BLE001
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
) -> List[Path]:
    """
    Download all revisions of a Google Doc as individual text files.

    Uses Drive API v2 to fetch the complete revision history of a document.
    Each revision is saved as a separate file with metadata in the filename.

    The function:
    1. Creates a subdirectory using folder_name (if provided) or document ID
    2. Fetches all available revisions via API
    3. Downloads each revision's plain text export
    4. Saves with filename: {timestamp}.txt

    Args:
        service_v2: Drive API v2 service object (required for revisions).
        file_id: Google Drive document ID.
        export_dir: Base directory for saving revisions (e.g., "revisions").
        credentials: OAuth2 credentials for authenticated downloads (optional).
        doc_title: Document title (used for display in calling code).
        folder_name: Custom folder name for this document's revisions. If None,
                     uses file_id as folder name (optional).

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
        ...     folder_name="cv-matt", doc_title="My CV"
        ... )
        >>> print(f"Downloaded {len(files)} revisions")
        >>> for f in files:
        ...     print(f"  - {f.name}")
    """
    import urllib.request

    # Create output directory using custom folder name or document ID
    target_folder = folder_name if folder_name else file_id
    output_dir = Path(export_dir) / target_folder
    output_dir.mkdir(exist_ok=True, parents=True)

    # Fetch all revisions from Drive API v2
    revisions = service_v2.revisions().list(fileId=file_id).execute()

    if 'items' not in revisions:
        return []

    downloaded_files = []
    items = revisions['items']

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
