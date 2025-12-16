from __future__ import annotations

import os.path

import typer
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from doc_sync import (
    SCOPES,
    build_drive_service,
    build_drive_service_v2,
    create_diff,
    download_revisions,
    export_file_content,
    fetch_document_title,
    get_recent_exports,
    get_required_env,
    get_time,
    run_flow_with_timeout,
)

app = typer.Typer()


def get_credentials(timeout: int = 120) -> Credentials:
    """
    Get or refresh Google OAuth credentials.

    Handles the complete OAuth flow:
    1. Tries to load existing credentials from token.json
    2. Refreshes expired credentials if refresh token is available
    3. Runs full OAuth flow if needed
    4. Saves credentials to token.json for future use

    Args:
        timeout: Seconds to wait for OAuth browser authorization (default: 120).

    Returns:
        Valid OAuth2 credentials.

    Raises:
        SystemExit: If required environment variables are missing.
        TimeoutError: If OAuth authorization times out.
    """
    client_secret_file = get_required_env("GOOGLE_OAUTH_CLIENT_SECRETS")

    credentials = None
    token_file = "token.json"

    # Try to load existing credentials
    if os.path.exists(token_file):
        credentials = Credentials.from_authorized_user_file(token_file, SCOPES)

    # Refresh or re-authorize if needed
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            # Refresh expired credentials
            credentials.refresh(Request())
        else:
            # Run full OAuth flow
            flow = InstalledAppFlow.from_client_secrets_file(
                client_secret_file=client_secret_file,
                scopes=SCOPES,
                autogenerate_code_verifier=True,
            )
            credentials = run_flow_with_timeout(flow, timeout=timeout)

        # Save credentials for next time
        with open(token_file, "w") as token:
            token.write(credentials.to_json())

    return credentials


@app.command()
def export(
    timeout: int = typer.Option(
        120, help="Seconds to wait for OAuth browser authorization"
    ),
) -> None:
    """
    Export the current content of a Google Doc to a text file.
    """
    document_id = get_required_env("GOOGLE_DOCUMENT_ID")
    credentials = get_credentials(timeout)
    service = build_drive_service(credentials)
    doc_title = fetch_document_title(service, document_id)
    response = (
        service.files()
        .export(
            fileId=document_id,
            mimeType="text/plain",
        )
        .execute()
    )

    if isinstance(response, bytes):
        content = response.decode("utf-8")
    else:
        content = str(response)

    export_path = export_file_content("exports", content, doc_title)

    print(f"Exported current content of '{doc_title}' to {export_path}")


@app.command()
def diff() -> None:
    """
    Compare the two most recent exports and save the diff.
    """
    print("Finding the two most recent exports...")
    recent_files = get_recent_exports(2)

    if len(recent_files) < 2:
        print("Error: Not enough export files to compare. Run the export command at least twice.")
        raise typer.Exit(code=1)

    new_file, old_file = recent_files
    print(f"Comparing '{old_file.name}' (old) and '{new_file.name}' (new)...")

    diff_content = create_diff(old_file, new_file)

    if not diff_content:
        print("No differences found between the two files.")
        return

    diff_path = export_file_content("diffs", diff_content, "diff")

    print(f"Differences saved to {diff_path}")


@app.command()
def revisions(
    timeout: int = typer.Option(
        120, help="Seconds to wait for OAuth browser authorization"
    ),
) -> None:
    """
    Download all revision history of a Google Doc.
    """
    document_id = get_required_env("GOOGLE_DOCUMENT_ID")
    credentials = get_credentials(timeout)

    # Build v2 service for revisions
    service_v2 = build_drive_service_v2(credentials)

    # Build v3 service for getting document title
    service_v3 = build_drive_service(credentials)
    doc_title = fetch_document_title(service_v3, document_id)

    print(f"Downloading revision history for '{doc_title}'...")

    downloaded_files = download_revisions(service_v2, document_id, "revisions")

    print(f"Downloaded {len(downloaded_files)} revisions to revisions/{document_id}/")
    for file_path in downloaded_files:
        print(f"  - {file_path.name}")


if __name__ == "__main__":
    app()
