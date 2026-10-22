"""YouTube upload using an OAuth token stored as JSON (no pickle files).

Create the token JSON once per channel with scripts/get_youtube_token.py and paste it
into the dashboard. It holds a refresh token, so the worker can upload unattended.
"""
import json

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def upload_short(token_json: str, video_path: str, title: str, description: str,
                 tags: list[str] | None = None, privacy: str = "public") -> str:
    info = json.loads(token_json)
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
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return f"https://youtube.com/shorts/{resp['id']}"
