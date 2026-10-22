"""Facebook Page Reels upload (Reels Publishing API, 3 steps).

1. POST /{page-id}/video_reels  upload_phase=start   -> video_id, upload_url
2. POST rupload.facebook.com upload_url              (raw bytes, offset/file_size headers)
3. POST /{page-id}/video_reels  upload_phase=finish  video_state=PUBLISHED

Needs a PAGE access token with pages_manage_posts, pages_read_engagement, pages_show_list
and a user with the CREATE_CONTENT task on the Page.
"""
import os

import requests

GRAPH = os.environ.get("FB_GRAPH_VERSION", "v26.0")


def _check(r: requests.Response, step: str) -> dict:
    try:
        data = r.json()
    except Exception:
        data = {}
    if r.status_code >= 400 or "error" in data:
        msg = (data.get("error") or {}).get("message") or r.text[:200]
        raise RuntimeError(f"Facebook {step} failed ({r.status_code}): {msg}")
    return data


def upload_reel(page_id: str, token: str, video_path: str, description: str,
                ai_generated: bool = False) -> str:
    base = f"https://graph.facebook.com/{GRAPH}/{page_id}/video_reels"

    start = _check(requests.post(base, data={"upload_phase": "start", "access_token": token},
                                 timeout=30), "start")
    video_id, upload_url = start["video_id"], start["upload_url"]

    size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        _check(requests.post(
            upload_url,
            headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size),
                     "Content-Type": "application/octet-stream"},
            data=f, timeout=600), "upload")

    _check(requests.post(base, data={
        "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
        "description": description[:2000], "is_ai_generated": str(ai_generated).lower(),
        "access_token": token}, timeout=60), "publish")
    return video_id
