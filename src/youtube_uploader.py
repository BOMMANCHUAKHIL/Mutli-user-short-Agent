"""YouTube upload using an OAuth token stored as JSON (no pickle files)."""
import json

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# Create a custom error to identify restrictions
class YouTubeRestrictedError(Exception):
    pass

def upload_short(token_json: str, video_path: str, title: str, description: str,
                 tags: list[str] | None = None, privacy: str = "public") -> str:
    try:
        info = json.loads(token_json)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Invalid YouTube token format. It is not valid JSON. Please re-upload your token.json file. ({e})")

    if not isinstance(info, dict):
        raise ValueError("Invalid YouTube token. The token must be a JSON object (dictionary).")

    required_fields = ["token", "refresh_token", "client_id", "client_secret"]
    missing = [f for f in required_fields if f not in info]
    if missing:
        raise ValueError(
            f"Invalid YouTube token. Missing required fields: {', '.join(missing)}. Please re-download and upload token.json.")
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)

    title = title.strip()[:90]
    if "#shorts" not in (title + description).lower():
        description += "\n#Shorts"
    body = {
        "snippet": {
            "title": title or "Shorts",
            "description": description[:4900],
            "tags": [t.lstrip("#") for t in (tags or [])][:15],
            "categoryId": "22",
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=-1)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    try:
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
        return f"https://youtube.com/shorts/{resp['id']}"
    except HttpError as e:
        # 403 is usually quota exceeded or account restricted
        if e.resp.status in [400, 401, 403]:
            content = e.content.decode("utf-8")
            if "quotaExceeded" in content or "uploadLimitExceeded" in content or e.resp.status == 403:
                raise YouTubeRestrictedError("YouTube API limit reached or account restricted.") from e
        raise