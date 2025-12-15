from __future__ import annotations

import os.path

import typer
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from doc_sync import (
    SCOPES,
    build_drive_service,
    create_diff,
    export_file_content,
    fetch_document_title,
    get_recent_exports,
    get_required_env,
    get_time,
    run_flow_with_timeout,
)

app = typer.Typer()


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
    client_secret_file = get_required_env("GOOGLE_OAUTH_CLIENT_SECRETS")

    credentials = None
    token_file = "token.json"

    if os.path.exists(token_file):
        credentials = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                client_secrets_file=client_secret_file,
                scopes=SCOPES,
                autogenerate_code_verifier=True,
            )
            credentials = run_flow_with_timeout(flow, timeout=timeout)

        with open(token_file, "w") as token:
            token.write(credentials.to_json())

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


if __name__ == "__main__":
    app()
