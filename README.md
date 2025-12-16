# Google Docs Revision Tracker

A Python CLI tool to download and track Google Docs revision history.

## Features

- **Multiple Document Support**: Track revision history for multiple Google Docs simultaneously
- **Revision History Download**: Download all historical revisions as individual timestamped files
- **ID-Based Organization**: Revisions organized by document ID (stable and unique identifiers)
- **Automatic Retry with Backoff**: Handles rate limiting with exponential backoff (up to 5 retries)
- **OAuth Authentication**: Secure authentication with automatic token refresh
- **Flexible Input**: Specify documents via CLI arguments, config file, or environment variable
- **Simple CLI**: Clean command interface with progress tracking

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

### 3. Configure Documents to Track

You have three options for specifying which documents to track:

**Option 1: YAML Configuration File (Recommended for multiple documents)**

Create a `documents.yaml` file:

```bash
cp documents.yaml.example documents.yaml
# Edit documents.yaml and add your document IDs
```

Example `documents.yaml` with custom folder names:
```yaml
documents:
  - id: 1Q-qMIRexwdCRd38hhCRHEBpXeru2oi54LwfQU7NvWi8
    name: cv-matt
  - id: 2A-bNkPstuvwxCEf45ijKLMNOPabcd6efgh9hijklmno
    name: project-proposal
```

Or simple format (uses document ID as folder name):
```yaml
documents:
  - 1Q-qMIRexwdCRd38hhCRHEBpXeru2oi54LwfQU7NvWi8
  - 2A-bNkPstuvwxCEf45ijKLMNOPabcd6efgh9hijklmno
```

**Option 2: Environment Variable (Single document)**

Create a `.env` file in the project root:

```bash
# Path to your OAuth client secrets JSON file
GOOGLE_OAUTH_CLIENT_SECRETS=path/to/client_secrets.json

# Single document ID
GOOGLE_DOCUMENT_ID=your_document_id_here
```

**Option 3: CLI Arguments (Ad-hoc usage)**

Pass document IDs directly when running the command (see Usage below).

### 4. Install Dependencies

```bash
pip install -r requirements.txt
```

Or if using `uv`:

```bash
uv sync
```

## Usage

### Basic Usage

**Using config file (documents.yaml):**
```bash
python main.py
```

**Using environment variable (single document):**
```bash
export GOOGLE_DOCUMENT_ID=your_document_id
python main.py
```

**Using CLI arguments (one or more documents):**
```bash
# Single document
python main.py 1Q-qMIRexwdCRd38hhCRHEBpXeru2oi54LwfQU7NvWi8

# Multiple documents
python main.py DOC_ID_1 DOC_ID_2 DOC_ID_3
```

**With custom OAuth timeout:**
```bash
python main.py --timeout 300  # 5 minutes
```

### Output

Revisions are saved to: `revisions/{folder_name}/{timestamp}.txt`

Example folder structure with custom names:
```
revisions/
├── cv-matt/                # Custom name from documents.yaml
│   ├── 2025-07-30T07-18-16-081Z.txt
│   ├── 2025-07-30T07-35-51-386Z.txt
│   └── 2025-12-15T19-31-43-713Z.txt
├── project-proposal/       # Custom name from documents.yaml
│   ├── 2025-08-15T10-22-33-123Z.txt
│   └── 2025-09-20T14-56-12-456Z.txt
└── 1Q-qMIRexwd.../        # Falls back to document ID if no name specified
    └── 2025-11-01T09-15-30-500Z.txt
```

- Folders use custom names from `documents.yaml` (if specified)
- If no custom name provided, uses document ID (stable and unique)
- Document titles are displayed in the CLI output for reference
- Each filename is the exact modification timestamp from Google Drive

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
├── main.py                 # CLI interface and OAuth flow
├── drive_revisions.py      # Core Google Drive API functionality
├── documents.yaml          # Document IDs and custom names (not committed)
├── documents.yaml.example  # Example document configuration
├── .env                    # Environment variables (not committed)
├── token.json              # OAuth credentials (generated, not committed)
└── revisions/              # Downloaded revision history
    ├── cv-matt/            # Custom folder name from documents.yaml
    └── project-proposal/   # Or document ID if no name specified
```

## How It Works

1. **Resolve Document IDs**: Checks CLI arguments, config file, or environment variable
2. **Authenticate**: Uses Google OAuth 2.0 (opens browser on first run)
3. **For Each Document**:
   - Fetches document title via Drive API v3 (for display)
   - Creates folder using document ID
   - Uses Drive API v2 to list all document revisions (v3 doesn't support this)
4. **Download Revisions**: For each revision:
   - Gets the plain text export link from the API
   - Downloads with OAuth bearer token authentication
   - Automatically retries with exponential backoff on rate limiting (429 errors)
   - Saves with ISO 8601 timestamp as filename
5. **Save**: All revisions stored in `revisions/{DOCUMENT_ID}/`

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

- `drive_revisions.py`: Core functions for Google Drive operations and revision downloads
- `main.py`: Typer CLI interface and OAuth flow management
- `documents.yaml`: Configuration file for tracking multiple documents

## License

[Add your license here]

## Contributing

[Add contributing guidelines here]
