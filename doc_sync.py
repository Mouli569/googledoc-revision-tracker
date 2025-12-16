from __future__ import annotations

import difflib
import os
import re
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Protocol, cast, runtime_checkable

from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive"]
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

def export_file_content(export_dir: str, content: str, filename_base: str) -> Path:
    """
    Write content to a timestamped text file in the specified export directory.

    This function:
    1. Creates a filename with current timestamp and sanitized base name
    2. Creates the export directory if it doesn't exist
    3. Writes the content to the file
    4. Returns the path to the created file

    Args:
        export_dir: Directory to save the file in (e.g., 'exports', 'diffs').
        content: Text content to write to the file.
        filename_base: Base name for the file (will be sanitized and timestamped).

    Returns:
        Path object pointing to the created file.

    Example:
        >>> path = export_file_content("exports", "Document text", "My Doc")
        >>> print(path)
        exports/2025-12-15-143000_My_Doc.txt
    """
    filename = f"{get_time()}_{sanitize_filename(filename_base)}.txt"
    EXPORT_DIR = Path(export_dir)
    EXPORT_DIR.mkdir(exist_ok=True, parents=True)
    export_path = EXPORT_DIR / filename
    export_path.write_text(content, encoding="utf-8")
    return export_path

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


def get_recent_exports(limit: int = 2, folder: str="exports") -> List[Path]:
    """
    Retrieve the most recent export files from a directory.

    Finds all .txt files in the specified folder and returns the most recently
    modified files. Useful for finding recent exports to compare with diff.

    Args:
        limit: Maximum number of files to return (default: 2).
        folder: Directory to search for exports (default: "exports").

    Returns:
        List of Path objects, sorted by modification time (newest first).
        Returns empty list if folder doesn't exist or contains no .txt files.

    Example:
        >>> recent = get_recent_exports(2, "exports")
        >>> if len(recent) >= 2:
        ...     new_file, old_file = recent
        ...     print(f"Comparing {old_file} to {new_file}")
    """
    EXPORT_DIR = Path(folder)
    if not EXPORT_DIR.exists():
        return []

    files = list(EXPORT_DIR.glob("*.txt"))
    # Sort by modification time, newest first
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def create_diff(old_path: Path, new_path: Path) -> str:
    """
    Generate a unified diff between two text files.

    Creates a diff showing changes between two files, similar to git diff.
    Uses unified diff format with minimal context (0 surrounding lines).

    Args:
        old_path: Path to the older file (baseline).
        new_path: Path to the newer file (comparison).

    Returns:
        Unified diff string showing additions (+) and deletions (-).
        Returns empty string if files are identical.

    Example:
        >>> diff_content = create_diff(
        ...     Path("exports/old.txt"),
        ...     Path("exports/new.txt")
        ... )
        >>> if diff_content:
        ...     print("Files differ:")
        ...     print(diff_content)
        ... else:
        ...     print("Files are identical")
    """
    old_lines = old_path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = new_path.read_text(encoding="utf-8").splitlines(keepends=True)

    # Generate unified diff with minimal context (n=0)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=old_path.name,
        tofile=new_path.name,
        lineterm="",
        n=0
    )
    return "".join(diff)


def download_revisions(service_v2: object, file_id: str, export_dir: str, credentials: object = None) -> List[Path]:
    """
    Download all revisions of a Google Doc as individual text files.

    Uses Drive API v2 to fetch the complete revision history of a document.
    Each revision is saved as a separate file with metadata in the filename.

    The function:
    1. Creates a subdirectory named after the file_id
    2. Fetches all available revisions via API
    3. Downloads each revision's plain text export
    4. Saves with filename: {revision_id}_{author}_{timestamp}.txt

    Args:
        service_v2: Drive API v2 service object (required for revisions).
        file_id: Google Drive document ID.
        export_dir: Base directory for saving revisions (e.g., "revisions").
        credentials: OAuth2 credentials for authenticated downloads (optional).

    Returns:
        List of Path objects for all downloaded revision files.
        Returns empty list if no revisions are available.

    Note:
        The API only returns "grouped" revisions, not every individual edit.
        Some fine-grained changes visible in Google Docs UI may be grouped
        together in the API response.

    Example:
        >>> service_v2 = build_drive_service_v2(credentials)
        >>> files = download_revisions(service_v2, "doc_id", "revisions")
        >>> print(f"Downloaded {len(files)} revisions")
        >>> for f in files:
        ...     print(f"  - {f.name}")
    """
    import urllib.request

    # Create output directory for this document's revisions
    output_dir = Path(export_dir) / file_id
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

        # Extract author email (or use 'unknown' if not available)
        modifier = revision.get('lastModifyingUser', {}).get('emailAddress', 'unknown')

        # Get the plain text export link
        export_links = revision.get('exportLinks', {})
        if 'text/plain' not in export_links:
            continue  # Skip revisions without text export

        export_link = export_links['text/plain']

        # Create descriptive filename with revision metadata
        safe_modifier = sanitize_filename(modifier.split('@')[0])  # Username only
        safe_date = modified_date.replace(':', '-').replace('.', '-')
        filename = f"{revision_id}_{safe_modifier}_{safe_date}.txt"
        file_path = output_dir / filename

        # Download the revision content with OAuth authentication
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
        except Exception as e:
            # Skip revisions that can't be downloaded
            print(f"Warning: Could not download revision {revision_id}: {e}")
            continue

    return downloaded_files
