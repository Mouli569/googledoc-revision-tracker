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
    download_revisions,
    fetch_document_title,
    get_required_env,
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
                client_secrets_file=client_secret_file,
                scopes=SCOPES,
                autogenerate_code_verifier=True,
            )
            credentials = run_flow_with_timeout(flow, timeout=timeout)

        # Save credentials for next time
        with open(token_file, "w") as token:
            token.write(credentials.to_json())

    return credentials


@app.command()
def download(
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

    downloaded_files = download_revisions(service_v2, document_id, "revisions", credentials)

    print(f"Downloaded {len(downloaded_files)} revisions to revisions/{document_id}/")
    for file_path in downloaded_files:
        print(f"  - {file_path.name}")


if __name__ == "__main__":
    app()
