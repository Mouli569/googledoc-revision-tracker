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

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
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
    return datetime.now(timezone.utc).strftime(format)

def get_required_env(var_name: str) -> str:
    """Retrieve required environment variable or exit with helpful message."""
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
    """Convert a document title into a filesystem-safe filename segment."""
    safe_title = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", title)
    safe_title = re.sub(r"[^\w.\-]+", "_", safe_title)
    safe_title = re.sub(r"[_\s]+", "_", safe_title).strip("_")
    if not safe_title:
        safe_title = "untitled"
    allowed_length = max_length - 21
    if len(safe_title) > allowed_length:
        safe_title = safe_title[:allowed_length]
    return safe_title

def export_file_content(export_dir: str, content: str, filename_base: str) -> Path:
    """Write content to a file in the exports directory."""
    filename = f"{get_time()}_{sanitize_filename(filename_base)}.txt"
    EXPORT_DIR = Path(export_dir)
    EXPORT_DIR.mkdir(exist_ok=True, parents=True)
    export_path = EXPORT_DIR / filename
    export_path.write_text(content, encoding="utf-8")
    return export_path

def run_flow_with_timeout(flow: InstalledAppFlowProtocol, timeout: int = 120) -> object:
    """Run the OAuth flow with a timeout to avoid hanging browser sessions."""

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

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"Authorization timed out after {timeout} seconds")
    if result.error:
        raise result.error
    if result.credentials is None:
        raise RuntimeError("OAuth flow did not return credentials")
    return result.credentials


def build_drive_service(credentials: object) -> DriveService:
    """Build a Drive API client."""

    service = build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )
    return cast(DriveService, service)


def fetch_document_title(service: DriveService, file_id: str) -> str:
    """Fetch the Drive document title."""

    response = service.files().get(fileId=file_id, fields="name").execute()
    return str(response.get("name", "Untitled Document"))


def get_recent_exports(limit: int = 2, folder: str="exports") -> List[Path]:
    """Retrieve the most recent export files."""

    EXPORT_DIR = Path(folder)
    if not EXPORT_DIR.exists():
        return []

    files = list(EXPORT_DIR.glob("*.txt"))
    # Sort by modification time, newest first
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def create_diff(old_path: Path, new_path: Path) -> str:
    """Generate a unified diff between two files."""
    old_lines = old_path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = new_path.read_text(encoding="utf-8").splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=old_path.name,
        tofile=new_path.name,
        lineterm="",
        n=0
    )
    return "".join(diff)
