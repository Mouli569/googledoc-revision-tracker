from __future__ import annotations

import os
import os.path
import shutil
import sys
from pathlib import Path

import typer
import yaml
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.errors import HttpError

from drive_revisions import (
    GOOGLE_DRIVE_SCOPES,
    DocumentConfig,
    Granularity,
    build_drive_service,
    build_drive_service_v2,
    download_revisions,
    extract_doc_id_from_url,
    fetch_document_title,
    get_required_env,
    load_document_ids_from_config,
    run_flow_with_timeout,
)

app = typer.Typer(
    help="Download and track Google Docs revision history with granular time filtering."
)

# Create sub-app for config commands
config_app = typer.Typer(help="Manage document configuration")
app.add_typer(config_app, name="config")


def credentials_exist() -> bool:
    """Check if valid credentials exist."""
    token_file = "token.json"
    if not os.path.exists(token_file):
        return False

    try:
        credentials = Credentials.from_authorized_user_file(token_file, GOOGLE_DRIVE_SCOPES)
        # Check if credentials are valid or can be refreshed
        return credentials and (credentials.valid or (credentials.expired and credentials.refresh_token))
    except Exception:
        return False


def get_credentials(timeout: int = 120, force_reauth: bool = False) -> Credentials:
    """
    Get or refresh Google OAuth credentials.

    Handles the complete OAuth flow:
    1. Tries to load existing credentials from token.json
    2. Refreshes expired credentials if refresh token is available
    3. Runs full OAuth flow if needed
    4. Saves credentials to token.json for future use

    Args:
        timeout: Seconds to wait for OAuth browser authorization (default: 120).
        force_reauth: Force re-authentication even if valid credentials exist.

    Returns:
        Valid OAuth2 credentials.

    Raises:
        SystemExit: If required environment variables are missing.
        TimeoutError: If OAuth authorization times out.
    """
    client_secret_file = get_required_env("GOOGLE_OAUTH_CLIENT_SECRETS")

    credentials = None
    token_file = "token.json"

    # Try to load existing credentials (unless forcing reauth)
    if not force_reauth and os.path.exists(token_file):
        credentials = Credentials.from_authorized_user_file(token_file, GOOGLE_DRIVE_SCOPES)

    # Refresh or re-authorize if needed
    if force_reauth or not credentials or not credentials.valid:
        if not force_reauth and credentials and credentials.expired and credentials.refresh_token:
            # Refresh expired credentials
            print("Refreshing expired credentials...")
            credentials.refresh(Request())
        else:
            # Run full OAuth flow
            print("\n🔐 Starting Google OAuth authentication...")
            print("A browser window will open for you to authorize access.")
            flow = InstalledAppFlow.from_client_secrets_file(
                client_secrets_file=client_secret_file,
                scopes=GOOGLE_DRIVE_SCOPES,
                autogenerate_code_verifier=True,
            )
            credentials = run_flow_with_timeout(flow, timeout=timeout)
            print("✓ Authentication successful!\n")

        # Save credentials for next time with restrictive permissions
        with open(token_file, "w") as token:
            token.write(credentials.to_json())

        # Set file permissions to owner read/write only (0600)
        os.chmod(token_file, 0o600)

    return credentials


@app.command()
def auth(
    force: bool = typer.Option(
        False, "--force", "-f", help="Force re-authentication even if already authenticated"
    ),
    timeout: int = typer.Option(
        120, help="Seconds to wait for OAuth browser authorization"
    ),
) -> None:
    """
    Authenticate with Google OAuth.

    This will open a browser window for you to authorize the application
    to access your Google Drive. Credentials are saved to token.json.

    Examples:
        uv run google-sync auth              # Authenticate (or check existing auth)
        uv run google-sync auth --force      # Force re-authentication
    """
    if not force and credentials_exist():
        print("✓ Already authenticated!")
        print("Use 'auth --force' to re-authenticate.")
        return

    try:
        get_credentials(timeout=timeout, force_reauth=force)
        print("✓ Successfully authenticated!")
        print("\nYou can now use 'google-sync download' to fetch document revisions.")
    except Exception as e:
        print(f"✗ Authentication failed: {e}", file=sys.stderr)
        raise typer.Exit(1)


@app.command()
def download(
    document_ids: list[str] = typer.Argument(
        None,
        help="Google Doc IDs or URLs to download. If not provided, reads from config file.",
    ),
    timeout: int = typer.Option(
        120, help="Seconds to wait for OAuth browser authorization"
    ),
) -> None:
    """
    Download revision history for one or more Google Docs.

    Accepts either document IDs or full Google Docs URLs.
    Requires authentication first (run 'google-sync auth' if needed).

    Examples:
        uv run google-sync download                    # Use config file (documents.yaml)
        uv run google-sync download DOC_ID_1           # Single document
        uv run google-sync download DOC_ID_1 DOC_ID_2  # Multiple documents
        uv run google-sync download https://docs.google.com/document/d/DOC_ID/edit  # From URL
    """
    # Check for authentication
    if not credentials_exist():
        print("✗ Not authenticated!", file=sys.stderr)
        print("\nPlease run 'google-sync auth' first to authenticate with Google.", file=sys.stderr)
        raise typer.Exit(1)
    # Resolve document configurations from multiple sources (priority order)
    doc_configs: list[DocumentConfig] = []

    # 1. CLI arguments (highest priority) - no custom names/granularity for CLI args
    # Extract document IDs from URLs if needed
    if document_ids:
        extracted_ids = [extract_doc_id_from_url(doc_id) for doc_id in document_ids]
        doc_configs = [DocumentConfig(doc_id=doc_id) for doc_id in extracted_ids]

    # 2. Default config file: documents.yaml
    if not doc_configs:
        doc_configs = load_document_ids_from_config("documents.yaml")


    # Error if no document IDs found
    if not doc_configs:
        print(
            "Error: No document IDs provided.\n"
            "Please provide document IDs via:\n"
            "  1. CLI arguments: python main.py DOC_ID_1 DOC_ID_2\n"
            "  2. Config file: Create documents.yaml with document IDs\n",
            file=sys.stderr,
        )
        raise typer.Exit(1)

    # Get credentials
    credentials = get_credentials(timeout)

    # Build Drive services
    service_v2 = build_drive_service_v2(credentials)
    service_v3 = build_drive_service(credentials)

    # Process each document
    total_downloaded = 0
    total_documents = len(doc_configs)
    successful_downloads = 0

    print(f"Processing {total_documents} document(s)...\n")

    for idx, doc_config in enumerate(doc_configs, 1):
        try:
            # Fetch document title for display
            doc_title = fetch_document_title(service_v3, doc_config.doc_id)
            granularity_info = f" ({doc_config.granularity} granularity)" if doc_config.granularity != "all" else ""
            print(f"[{idx}/{total_documents}] Downloading '{doc_title}' ({doc_config.doc_id}){granularity_info}...")

            # Download revisions with config settings
            downloaded_files = download_revisions(
                service_v2,
                doc_config.doc_id,
                "revisions",
                credentials,
                doc_title=doc_title,
                folder_name=doc_config.folder_name,
                granularity=doc_config.granularity,
            )

            file_count = len(downloaded_files)
            total_downloaded += file_count
            successful_downloads += 1

            # Show which folder was used
            target_folder = doc_config.folder_name if doc_config.folder_name else doc_config.doc_id
            print(f"  ✓ Downloaded {file_count} revision(s) to revisions/{target_folder}/\n")

        except HttpError as e:
            # Handle common HTTP errors with friendly messages
            doc_id = doc_config.doc_id
            if e.resp.status == 404:
                print(f"  ✗ Document not found: {doc_id}", file=sys.stderr)
                print(f"    → Check that the document ID is correct", file=sys.stderr)
                print(f"    → Ensure you have access to this document\n", file=sys.stderr)
            elif e.resp.status == 403:
                print(f"  ✗ Permission denied: {doc_id}", file=sys.stderr)
                print(f"    → You don't have permission to access this document", file=sys.stderr)
                print(f"    → Ask the owner to share it with you\n", file=sys.stderr)
            elif e.resp.status == 401:
                print(f"  ✗ Authentication error: {doc_id}", file=sys.stderr)
                print(f"    → Try re-authenticating with: google-sync auth --force\n", file=sys.stderr)
            else:
                print(f"  ✗ HTTP {e.resp.status} error for {doc_id}: {e.error_details}\n", file=sys.stderr)
            continue
        except Exception as e:
            print(f"  ✗ Unexpected error downloading {doc_config.doc_id}: {e}\n", file=sys.stderr)
            continue

    # Print summary
    print("=" * 50)
    print(f"Summary: Successfully downloaded {successful_downloads}/{total_documents} document(s)")
    print(f"Total revisions downloaded: {total_downloaded}")
    print("=" * 50)


@config_app.command("init")
def config_init(force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing documents.yaml")) -> None:
    """
    Initialize a new documents.yaml configuration file.

    Creates documents.yaml from the example template.

    Examples:
        uv run google-sync config init         # Create config file
        uv run google-sync config init --force # Overwrite existing
    """
    config_file = Path("documents.yaml")
    example_file = Path("documents.yaml.example")

    if config_file.exists() and not force:
        print("✗ documents.yaml already exists!", file=sys.stderr)
        print("Use --force to overwrite", file=sys.stderr)
        raise typer.Exit(1)

    if not example_file.exists():
        print("✗ documents.yaml.example not found!", file=sys.stderr)
        raise typer.Exit(1)

    shutil.copy(example_file, config_file)
    print(f"✓ Created documents.yaml from template")
    print(f"\nEdit documents.yaml to add your document IDs")


@config_app.command("add")
def config_add(
    document_id: str = typer.Argument(None, help="Google Doc ID or URL to add"),
    name: str = typer.Option(None, "--name", "-n", help="Custom folder name"),
    granularity: Granularity = typer.Option(None, "--granularity", "-g", help="Time granularity for revisions"),
) -> None:
    """
    Add a new document to documents.yaml configuration.

    Accepts either a document ID or full Google Docs URL.
    Interactive mode if arguments missing, otherwise runs directly.

    Examples:
        uv run google-sync config add                                    # Fully interactive
        uv run google-sync config add DOC_ID                             # Prompt for optional fields
        uv run google-sync config add https://docs.google.com/document/d/DOC_ID/edit  # From URL
        uv run google-sync config add DOC_ID -n cv -g daily              # Direct add (no prompts)
    """
    config_file = Path("documents.yaml")

    # Extract document ID from URL if needed
    if document_id:
        document_id = extract_doc_id_from_url(document_id)

    # Determine if we should run in interactive mode
    # Interactive if document_id is not provided OR if optional fields are not set
    interactive = document_id is None or name is None or granularity is None

    if interactive:
        print("📝 Add a new document to configuration\n")

        # Prompt for document ID if not provided
        if not document_id:
            doc_input = typer.prompt("Google Doc ID or URL")
            document_id = extract_doc_id_from_url(doc_input)
        else:
            print(f"Google Doc ID: {document_id}")

        # Prompt for custom name if not provided
        if name is None:
            name_input = typer.prompt(
                "Custom folder name (optional, press Enter to skip)",
                default="",
                show_default=False,
            )
            name = name_input if name_input else None

        if name:
            print(f"Folder name: {name}")

        # Prompt for granularity if not provided
        if granularity is None:
            print("\nGranularity options:")
            print("  all     - Download all revisions (default)")
            print("  hourly  - Final revision per hour")
            print("  daily   - Final revision per day")
            print("  weekly  - Final revision per week")
            print("  monthly - Final revision per month")

            valid_granularities = ["all", "hourly", "daily", "weekly", "monthly"]
            while True:
                gran_input = typer.prompt("\nGranularity", default="all")
                if gran_input in valid_granularities:
                    granularity = gran_input
                    break
                else:
                    print(f"✗ Invalid granularity. Choose from: {', '.join(valid_granularities)}")

        print(f"Granularity: {granularity}")

        # Show summary and confirm (only in interactive mode)
        print("\n" + "─" * 50)
        print("Summary:")
        print(f"  Document ID: {document_id}")
        print(f"  Folder name: {name or '(use document ID)'}")
        print(f"  Granularity: {granularity}")
        print("─" * 50)

        confirm = typer.confirm("\nAdd this document to config?")
        if not confirm:
            print("Cancelled")
            raise typer.Exit(0)
        print()

    # Validate required document_id
    if not document_id:
        print("✗ Document ID is required!", file=sys.stderr)
        raise typer.Exit(1)

    # Load existing config or create new
    if config_file.exists():
        with open(config_file) as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}

    if "documents" not in config:
        config["documents"] = []

    # Check if document already exists
    for doc in config["documents"]:
        if isinstance(doc, dict) and doc.get("id") == document_id:
            print(f"✗ Document {document_id} already in config!", file=sys.stderr)
            raise typer.Exit(1)
        elif isinstance(doc, str) and doc == document_id:
            print(f"✗ Document {document_id} already in config!", file=sys.stderr)
            raise typer.Exit(1)

    # Add new document
    if name or (granularity and granularity != "all"):
        new_doc = {"id": document_id}
        if name:
            new_doc["name"] = name
        if granularity and granularity != "all":
            new_doc["granularity"] = granularity
        config["documents"].append(new_doc)
    else:
        # Simple format
        config["documents"].append(document_id)

    # Save config
    with open(config_file, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print(f"✓ Added document {document_id} to config")
    if name:
        print(f"  Folder name: {name}")
    if granularity and granularity != "all":
        print(f"  Granularity: {granularity}")


@config_app.command("list")
def config_list() -> None:
    """
    List all documents in configuration.

    Examples:
        uv run google-sync config list
    """
    config_file = Path("documents.yaml")

    if not config_file.exists():
        print("✗ No documents.yaml found!", file=sys.stderr)
        print("Run 'google-sync config init' to create one", file=sys.stderr)
        raise typer.Exit(1)

    doc_configs = load_document_ids_from_config(str(config_file))

    if not doc_configs:
        print("No documents configured")
        return

    print(f"Configured documents ({len(doc_configs)}):\n")
    for idx, doc_config in enumerate(doc_configs, 1):
        folder = doc_config.folder_name or doc_config.doc_id
        gran = f" ({doc_config.granularity})" if doc_config.granularity != "all" else ""
        print(f"{idx}. {doc_config.doc_id}")
        print(f"   Folder: {folder}{gran}")


if __name__ == "__main__":
    app()
