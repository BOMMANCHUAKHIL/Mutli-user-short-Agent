"""YouTube Live: loop a user's latest Shorts as a live stream.

Flow
  1. normalise each clip (blurred-background fit, 30 fps, AAC) and concat into ONE loop file
  2. find or create a reusable RTMP stream key on the channel
  3. create a broadcast and bind it to that stream
  4. FFmpeg streams the loop with `-stream_loop -1 -c copy` (no re-encode -> almost no CPU)
  5. wait until YouTube reports the stream "active", transition the broadcast to "live"
  6. keep FFmpeg alive for N minutes (auto-restart if it drops), then transition to "complete"

The stored OAuth token needs the https://www.googleapis.com/auth/youtube scope
(the old youtube.upload-only token is NOT enough for the Live API).
"""
import json
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

LIVE_SCOPES = {
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
}
STREAM_TITLE = "ShortsAgent reusable stream"
# layout -> (width, height, video bitrate)
LAYOUTS = {"landscape": (1280, 720, "3000k"), "vertical": (1080, 1920, "4500k")}
FPS = 30


class LiveScopeError(Exception):
    """The stored token was not authorised for the YouTube Live API."""


# ── helpers ────────────────────────────────────────────────────────────
def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg  # ships with moviepy
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-500:])


def _has_audio(ff: str, path: Path) -> bool:
    r = subprocess.run([ff, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    return "Audio:" in r.stderr


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def explain_http_error(exc: HttpError) -> str:
    body = exc.content.decode("utf-8", errors="ignore") if exc.content else ""
    if "liveStreamingNotEnabled" in body:
        return ("Live streaming is not enabled on this YouTube channel. Enable it in YouTube Studio "
                "(phone verification; first activation can take up to 24 hours).")
    if "insufficientPermissions" in body or "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in body:
        return "YouTube token lacks the Live scope. Re-create it with scope .../auth/youtube and paste it again."
    if "quotaExceeded" in body:
        return "YouTube API quota exceeded for today."
    return f"YouTube API error {exc.resp.status}: {body[:200]}"


# ── credentials ────────────────────────────────────────────────────────
def load_credentials(token_json: str) -> Credentials:
    info = json.loads(token_json)
    granted = info.get("scopes") or []
    if isinstance(granted, str):
        granted = granted.split()
    if granted and not (LIVE_SCOPES & set(granted)):
        raise LiveScopeError(
            "YouTube token only has the upload scope. Re-create it with the "
            "https://www.googleapis.com/auth/youtube scope and paste it in the dashboard."
        )
    # No scopes override: refresh must use the scopes that were actually granted.
    creds = Credentials.from_authorized_user_info(info)
    if not creds.valid:
        creds.refresh(Request())
    return creds


# ── loop file ──────────────────────────────────────────────────────────
def build_loop(videos: list[Path], workdir: Path, layout: str = "landscape") -> Path:
    """Normalise every clip to identical codec settings, then concat them (stream copy)."""
    ff = _ffmpeg()
    w, h, rate = LAYOUTS.get(layout, LAYOUTS["landscape"])
    workdir.mkdir(parents=True, exist_ok=True)

    fc = (
        f"[0:v]split=2[a][b];"
        f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=25:5[bg];"
        f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={FPS},format=yuv420p[v]"
    )
    bufsize = f"{int(rate[:-1]) * 2}k"
    segs = []
    for i, src in enumerate(videos):
        dst = workdir / f"seg{i}.mp4"
        if _has_audio(ff, src):
            inputs, amap = ["-i", str(src)], ["-map", "0:a:0"]
        else:  # silent clip -> add silent audio so every segment has the same streams
            inputs = ["-i", str(src), "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
            amap = ["-map", "1:a", "-shortest"]
        _run([
            ff, "-y", *inputs, "-filter_complex", fc, "-map", "[v]", *amap,
            "-c:v", "libx264", "-preset", "veryfast", "-b:v", rate, "-maxrate", rate,
            "-bufsize", bufsize, "-g", str(FPS * 2), "-keyint_min", str(FPS * 2),
            "-sc_threshold", "0", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart", str(dst),
        ])
        segs.append(dst)

    listing = workdir / "list.txt"
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in segs))
    loop = workdir / "loop.mp4"
    _run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(loop)])
    return loop


# ── YouTube Live API ───────────────────────────────────────────────────
def get_or_create_stream(yt) -> dict:
    res = yt.liveStreams().list(part="id,snippet,cdn", mine=True, maxResults=50).execute()
    for s in res.get("items", []):
        if s["snippet"]["title"] == STREAM_TITLE and s["cdn"].get("ingestionType") == "rtmp":
            return s
    return yt.liveStreams().insert(part="snippet,cdn", body={
        "snippet": {"title": STREAM_TITLE},
        "cdn": {"frameRate": "variable", "ingestionType": "rtmp", "resolution": "variable"},
    }).execute()


def create_broadcast(yt, title: str, description: str, privacy: str) -> dict:
    start = datetime.now(timezone.utc) + timedelta(minutes=1)
    return yt.liveBroadcasts().insert(part="snippet,status,contentDetails", body={
        "snippet": {"title": title[:100], "description": description[:5000],
                    "scheduledStartTime": _iso(start)},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        "contentDetails": {
            "enableAutoStart": False,   # we go live explicitly once data is flowing
            "enableAutoStop": True,
            "enableDvr": True,
            "recordFromStart": True,
            "monitorStream": {"enableMonitorStream": False},
        },
    }).execute()


def _wait_stream_active(yt, stream_id: str, timeout: int = 120) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        items = yt.liveStreams().list(part="status", id=stream_id).execute().get("items", [])
        if items and items[0]["status"]["streamStatus"] == "active":
            return True
        time.sleep(5)
    return False


def _transition(yt, broadcast_id: str, status: str, tries: int = 6) -> None:
    for i in range(tries):
        try:
            yt.liveBroadcasts().transition(broadcastStatus=status, id=broadcast_id, part="status").execute()
            return
        except HttpError as exc:
            body = exc.content.decode("utf-8", errors="ignore") if exc.content else ""
            if "redundantTransition" in body:
                return
            if i == tries - 1:
                raise
            time.sleep(8)  # stream not healthy yet; retry


def _spawn_ffmpeg(ff: str, loop: Path, rtmp_url: str, log_path: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [ff, "-hide_banner", "-loglevel", "warning", "-re", "-stream_loop", "-1", "-i", str(loop),
         "-c", "copy", "-flvflags", "no_duration_filesize", "-f", "flv", rtmp_url],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=open(log_path, "ab"),
    )


def _stop(proc: subprocess.Popen | None) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


# ── public entry ───────────────────────────────────────────────────────
def run_live(token_json: str, videos: list[Path], title: str, description: str,
             duration_min: int = 120, privacy: str = "public", layout: str = "landscape",
             workdir: Path = Path("output/live"), on_live=None, log=print) -> dict:
    """Blocks for `duration_min` minutes while live. Returns {"url", "token_json"}."""
    ff = _ffmpeg()
    creds = load_credentials(token_json)
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)

    log("Building loop from", len(videos), "clip(s)...")
    loop = build_loop(videos, workdir, layout)

    stream = get_or_create_stream(yt)
    ing = stream["cdn"]["ingestionInfo"]
    rtmp_url = f'{ing["ingestionAddress"]}/{ing["streamName"]}'

    broadcast = create_broadcast(yt, title, description, privacy)
    bid = broadcast["id"]
    yt.liveBroadcasts().bind(id=bid, part="id,contentDetails", streamId=stream["id"]).execute()
    url = f"https://www.youtube.com/watch?v={bid}"
    log("Broadcast created:", url)

    ffmpeg_log = workdir / "ffmpeg_stream.log"
    proc, went_live = None, False
    try:
        proc = _spawn_ffmpeg(ff, loop, rtmp_url, ffmpeg_log)
        if not _wait_stream_active(yt, stream["id"]):
            raise RuntimeError("YouTube never received the video stream from FFmpeg.")
        _transition(yt, bid, "live")
        went_live = True
        log("LIVE:", url)
        if on_live:
            on_live(url)

        deadline = time.time() + duration_min * 60
        restarts = 0
        while time.time() < deadline:
            time.sleep(5)
            if proc.poll() is not None:        # network blip / ffmpeg crash -> reconnect
                restarts += 1
                if restarts > 5:
                    raise RuntimeError("FFmpeg kept stopping (more than 5 restarts).")
                log(f"FFmpeg stopped (exit {proc.returncode}); restart #{restarts}")
                time.sleep(3)
                proc = _spawn_ffmpeg(ff, loop, rtmp_url, ffmpeg_log)
    finally:
        _stop(proc)
        try:
            if went_live:
                _transition(yt, bid, "complete")
            else:
                yt.liveBroadcasts().delete(id=bid).execute()  # never went live -> clean up
        except Exception as exc:
            log("cleanup warning:", str(exc)[:150])

    return {"url": url, "token_json": creds.to_json()}