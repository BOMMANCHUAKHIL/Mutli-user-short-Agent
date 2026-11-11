"""YouTube upload using OAuth credentials stored as encrypted JSON."""

import json

from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload"
]


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


    # Read saved scopes for debugging.
    saved_scopes = info.get("scopes")
    print("[YouTube OAuth] Saved token scopes:", repr(saved_scopes))

    # Do not send saved scopes during refresh.
    credential_info = dict(info)
    credential_info.pop("scopes", None)

    creds = Credentials.from_authorized_user_info(
        credential_info
    )

    print("[YouTube OAuth] Saved token scopes:", repr(saved_scopes))
    print("[YouTube OAuth] Credential scopes:", creds.scopes)
    print("[YouTube OAuth] Has refresh token:", bool(creds.refresh_token))

    if not creds.valid:
        try:
            creds.refresh(Request())
            print("[YouTube OAuth] Token refreshed successfully.")

        except RefreshError as exc:
            print(
                f"[YouTube OAuth] Refresh failed: "
                f"{type(exc).__name__}: {str(exc)[:500]}"
            )
            raise RuntimeError(
                f"YouTube OAuth refresh failed: {str(exc)[:500]}"
            ) from exc

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

        try:

            error_data = json.loads(content)

        except (json.JSONDecodeError, TypeError):

            error_data = {}

        error_info = error_data.get("error", {})

        errors = error_info.get("errors", [])

        reasons = [

            item.get("reason", "unknown")

            for item in errors

        ]

        message = error_info.get("message", str(exc))

        print("[YouTube API] HTTP status:", exc.resp.status)

        print("[YouTube API] Error reasons:", reasons)

        print("[YouTube API] Error message:", message)

        print("[YouTube API] Full response:", content[:2000])

        # Only classify known quota/upload-limit errors as limits.

        limit_reasons = {

            "quotaExceeded",

            "dailyLimitExceeded",

            "uploadLimitExceeded",

        }

        if any(reason in limit_reasons for reason in reasons):
            raise YouTubeRestrictedError(

                f"YouTube quota/upload limit: {reasons} - {message}"

            ) from exc

        # Preserve other errors instead of misclassifying every 403.

        raise
