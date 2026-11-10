"""YouTube upload using OAuth credentials stored as encrypted JSON."""

import json

from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class YouTubeRestrictedError(Exception):
    pass


def upload_short(
    token_json: str,
    video_path: str,
    title: str,
    description: str,
    tags: list[str] | None = None,
    privacy: str = "public",
    return_token: bool = False,
):
    try:
        info = json.loads(token_json)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid YouTube token JSON.") from exc

    if not isinstance(info, dict):
        raise ValueError("YouTube token must be a JSON object.")

    required_fields = ["token", "refresh_token", "client_id", "client_secret"]
    missing = [field for field in required_fields if not info.get(field)]

    if missing:
        raise ValueError(
            f"YouTube token is missing required fields: {', '.join(missing)}"
        )

    creds = Credentials.from_authorized_user_info(info, SCOPES)

    # Explicitly refresh an expired or invalid access token.
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            print("YouTube OAuth refresh error:", str(exc))

            raise RuntimeError(
                f"YouTube OAuth refresh failed: {str(exc)[:500]}"
            ) from exc

    # Capture the latest credentials after refresh.
    updated_token_json = creds.to_json()

    yt = build(
        "youtube",
        "v3",
        credentials=creds,
        cache_discovery=False,
    )

    title = title.strip()[:90]

    if "#shorts" not in (title + description).lower():
        description += "\n#Shorts"

    body = {
        "snippet": {
            "title": title or "Shorts",
            "description": description[:4900],
            "tags": [tag.lstrip("#") for tag in (tags or [])][:15],
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=-1,
    )

    req = yt.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    try:
        response = None

        while response is None:
            _, response = req.next_chunk()

        youtube_url = f"https://youtube.com/shorts/{response['id']}"

        if return_token:
            return youtube_url, updated_token_json

        return youtube_url

    except HttpError as exc:
        content = exc.content.decode("utf-8", errors="ignore")

        if (
            exc.resp.status in (400, 401, 403)
            and (
                "quotaExceeded" in content
                or "uploadLimitExceeded" in content
                or exc.resp.status == 403
            )
        ):
            raise YouTubeRestrictedError(
                "YouTube API limit reached or account restricted."
            ) from exc

        raise