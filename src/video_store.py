"""Keeps each user's most recent generated videos in a private Supabase Storage bucket.

Why: the Shorts job runs on a fresh GitHub Actions runner every time, so output/videos is
wiped after each run. The live worker (a different job) needs those files later.
Bucket 'user_videos' must exist (see migration_live.sql). Use the SERVICE ROLE client.
"""
from pathlib import Path

BUCKET = "user_videos"


def _names(sb, user_id: str) -> list[str]:
    items = sb.storage.from_(BUCKET).list(user_id, {"limit": 100}) or []
    # file names start with a timestamp (YYYYmmdd-HHMMSS...), so reverse sort = newest first
    return sorted((i["name"] for i in items if i.get("name", "").endswith(".mp4")), reverse=True)


def save_video(sb, user_id: str, local_path: str, keep: int = 6) -> str:
    p = Path(local_path)
    remote = f"{user_id}/{p.stem}.mp4"
    sb.storage.from_(BUCKET).upload(
        path=remote,
        file=p.read_bytes(),
        file_options={"content-type": "video/mp4", "upsert": "true"},
    )
    old = _names(sb, user_id)[keep:]
    if old:
        sb.storage.from_(BUCKET).remove([f"{user_id}/{n}" for n in old])
    return remote


def latest_videos(sb, user_id: str, dest_dir: Path, n: int = 3) -> list[Path]:
    """Download the n newest videos (newest first) into dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for name in _names(sb, user_id)[:n]:
        data = sb.storage.from_(BUCKET).download(f"{user_id}/{name}")
        path = dest_dir / name
        path.write_bytes(data)
        out.append(path)
    return out