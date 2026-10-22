
"""
Run once on your computer to authorize YouTube uploads.

1. Enable YouTube Data API v3 in Google Cloud.
2. Create an OAuth client ID of type Desktop app.
3. Save its downloaded JSON as credentials/client_secret.json.
4. Run: python scripts/get_youtube_token.py
5. Sign in with the Google account for your YouTube channel.
6. Copy the generated JSON into your dashboard's YouTube OAuth token field.
"""

from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

CLIENT_FILE = Path("credentials/client_secret.json")

if not CLIENT_FILE.exists():
    raise FileNotFoundError(
        f"OAuth client file not found: {CLIENT_FILE.resolve()}"
    )

flow = InstalledAppFlow.from_client_secrets_file(
    str(CLIENT_FILE),
    SCOPES,
)

creds = flow.run_local_server(
    host="localhost",
    port=8080,
    open_browser=True,
    authorization_prompt_message="Open this URL to authorize YouTube: {url}",
    success_message="Authorization successful! You can close this browser tab.",
)

print("\n=== COPY THIS JSON INTO YOUR DASHBOARD ===\n")
print(creds.to_json())
print("\nKeep this JSON private. Do not share it publicly.")
