# Google Docs Sync

A Python CLI tool to download and track Google Docs content and revision history.

## Features

- **Revision History Download**: Download all historical revisions of a Google Doc as individual timestamped files
- **OAuth Authentication**: Secure authentication with automatic token refresh
- **Simple CLI**: Single command interface - just run and download

## Prerequisites

- Python 3.12+
- Google Cloud Project with Drive API enabled
- OAuth 2.0 Client ID credentials

## Setup

### 1. Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Navigate to "APIs & Services" > "Library"
4. Enable the **Google Drive API**

### 2. Create OAuth Credentials

1. Go to "APIs & Services" > "Credentials"
2. Click "+ Create Credentials" > "OAuth client ID"
3. Choose "Desktop app" as the application type
4. Download the JSON file and save it (e.g., `client_secrets.json`)

### 3. Configure Environment Variables

Create a `.env` file in the project root:

```bash
# Path to your OAuth client secrets JSON file
GOOGLE_OAUTH_CLIENT_SECRETS=path/to/client_secrets.json

# Your Google Doc ID (from the document URL)
# https://docs.google.com/document/d/YOUR_DOC_ID_HERE/edit
GOOGLE_DOCUMENT_ID=your_document_id_here
```

### 4. Install Dependencies

```bash
pip install -r requirements.txt
```

Or if using `uv`:

```bash
uv sync
```

## Usage

Download all revisions of your Google Doc:

```bash
python main.py
```

Or with a custom OAuth timeout:

```bash
python main.py --timeout 300  # 5 minutes
```

### Output

Revisions are saved to: `revisions/DOCUMENT_ID/{timestamp}.txt`

Example filenames:
```
revisions/1Q-qMIRexwdCRd38hhCRHEBpXeru2oi54LwfQU7NvWi8/
├── 2025-07-30T07-18-16-081Z.txt
├── 2025-07-30T07-35-51-386Z.txt
├── 2025-10-09T16-34-01-365Z.txt
└── 2025-12-15T19-31-43-713Z.txt
```

Each filename is the exact modification timestamp from Google Drive.

## Authentication

On first run, the tool will:
1. Open your browser for Google OAuth authorization
2. Ask you to grant access to Google Drive
3. Save credentials to `token.json` for future use

The OAuth flow has a 2-minute timeout by default. If you need more time:

```bash
python main.py --timeout 300  # 5 minutes
```

## Project Structure

```
google-sync-simple/
├── main.py          # CLI interface and OAuth flow
├── doc_sync.py      # Core Google Drive API functionality
├── .env             # Environment variables (not committed)
├── .env.example     # Example environment configuration
├── token.json       # OAuth credentials (generated, not committed)
└── revisions/       # Downloaded revision history
    └── DOCUMENT_ID/ # One folder per document
```

## How It Works

1. **Authenticate**: Uses Google OAuth 2.0 (opens browser on first run)
2. **Fetch Revisions**: Uses Drive API v2 to list all document revisions (v3 doesn't support this)
3. **Download**: For each revision:
   - Gets the plain text export link from the API
   - Downloads with OAuth bearer token authentication
   - Saves with ISO 8601 timestamp as filename
4. **Save**: All revisions stored in `revisions/DOCUMENT_ID/`

## Troubleshooting

### "Missing required environment variable" Error

Ensure your `.env` file exists and contains both required variables:
- `GOOGLE_OAUTH_CLIENT_SECRETS`
- `GOOGLE_DOCUMENT_ID`

### "Authorization timed out" Error

Increase the timeout:

```bash
python main.py --timeout 300
```

### "Insufficient permissions" or "Access denied"

1. Delete `token.json`
2. Re-run the command to re-authenticate with updated scopes
3. Ensure your Google account has access to the document

### No revisions found

The Drive API v2 only returns "grouped" revisions. Fine-grained revision history visible in the Google Docs UI may not all be accessible via the API.

### "HTTP Error 429: Too Many Requests"

Google API rate limits may be hit when downloading many revisions. The tool will skip failed revisions and continue with the rest. Re-run the command to retry failed downloads.

## Development

### Running Tests

```bash
# TODO: Add test suite
```

### Code Structure

- `doc_sync.py`: Pure functions for Google Drive operations
- `main.py`: Typer CLI interface and OAuth flow management

## License

[Add your license here]

## Contributing

[Add contributing guidelines here]
