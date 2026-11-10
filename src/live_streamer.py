"""
YouTube Live streamer.

1. Normalize each video to the same resolution, FPS, codec and audio format.
2. Join the normalized clips into one loop video.
3. Create or reuse a YouTube RTMP stream.
4. Create and bind a broadcast.
5. Stream the loop for the requested duration.
6. Stop the stream and complete the broadcast.
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

LAYOUTS = {
    "landscape": (1280, 720, "3000k"),
    "vertical": (1080, 1920, "4500k"),
}

FPS = 30


class LiveScopeError(Exception):
    """The stored OAuth token does not have the required Live API scope."""


# ---------------------------------------------------------
# FFmpeg helpers
# ---------------------------------------------------------

def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")

    if exe:
        return exe

    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError(
            "FFmpeg is not installed. Install FFmpeg or imageio-ffmpeg."
        )


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    """Run FFmpeg and show its actual error if it fails."""

    print("\nRunning FFmpeg:")
    print(" ".join(str(x) for x in cmd))

    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )

    if result.stdout:
        print("FFmpeg stdout:")
        print(result.stdout[-3000:])

    if result.returncode != 0:
        print("\n========== FFMPEG ERROR ==========")
        print(result.stderr[-12000:])
        print("==================================")

        raise RuntimeError(
            f"FFmpeg failed with exit code {result.returncode}. "
            "Check the FFmpeg error above."
        )

    if result.stderr:
        print("FFmpeg messages:")
        print(result.stderr[-2000:])


def _has_audio(ff: str, path: Path) -> bool:
    result = subprocess.run(
        [ff, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
    )

    return "Audio:" in result.stderr


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )


# ---------------------------------------------------------
# YouTube API errors
# ---------------------------------------------------------

def explain_http_error(exc: HttpError) -> str:
    body = (
        exc.content.decode("utf-8", errors="ignore")
        if exc.content
        else ""
    )

    if "liveStreamingNotEnabled" in body:
        return (
            "Live streaming is not enabled on this YouTube channel. "
            "Enable it in YouTube Studio."
        )

    if (
        "insufficientPermissions" in body
        or "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in body
    ):
        return (
            "The YouTube token does not have the required Live API scope. "
            "Generate a token with https://www.googleapis.com/auth/youtube."
        )

    if "quotaExceeded" in body:
        return "YouTube API quota exceeded."

    return f"YouTube API error {exc.resp.status}: {body[:500]}"


# ---------------------------------------------------------
# OAuth credentials
# ---------------------------------------------------------

def load_credentials(token_json: str) -> Credentials:
    info = json.loads(token_json)

    granted = info.get("scopes") or []

    if isinstance(granted, str):
        granted = granted.split()

    if not granted or not (LIVE_SCOPES & set(granted)):
        raise LiveScopeError(
            "Missing YouTube Live authorization. "
            "Please reconnect the Google account with Live permissions."
        )

    creds = Credentials.from_authorized_user_info(info)

    if not creds.valid:
        if not creds.refresh_token:
            raise RuntimeError(
                "YouTube token expired and has no refresh token. "
                "Generate a new Live OAuth token."
            )

        creds.refresh(Request())

    return creds


# ---------------------------------------------------------
# Normalize videos and build the loop
# ---------------------------------------------------------

def build_loop(
    videos: list[Path],
    workdir: Path,
    layout: str = "landscape",
) -> Path:

    if not videos:
        raise RuntimeError("No videos were supplied to FFmpeg.")

    ff = _ffmpeg()

    width, height, bitrate = LAYOUTS.get(
        layout,
        LAYOUTS["landscape"],
    )

    workdir.mkdir(parents=True, exist_ok=True)

    segments = []

    for index, source in enumerate(videos):
        source = Path(source)

        if not source.exists():
            raise RuntimeError(f"Video file does not exist: {source}")

        if source.stat().st_size == 0:
            raise RuntimeError(f"Video file is empty: {source}")

        destination = workdir / f"normalized_{index}.mp4"

        print(
            f"\nNormalizing clip {index + 1}/{len(videos)}: "
            f"{source.name}"
        )

        # Portrait/landscape fit with a blurred background.
        filter_graph = (
            f"[0:v]split=2[background][foreground];"
            f"[background]"
            f"scale={width}:{height}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"boxblur=20:5[bg];"
            f"[foreground]"
            f"scale={width}:{height}:"
            f"force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]"
            f"overlay=(W-w)/2:(H-h)/2,"
            f"fps={FPS},"
            f"setsar=1,"
            f"format=yuv420p[v]"
        )

        has_audio = _has_audio(ff, source)

        if has_audio:
            inputs = [
                "-i", str(source),
            ]

            audio_map = [
                "-map", "0:a:0",
            ]

        else:
            print("No audio found. Adding silent audio.")

            inputs = [
                "-i", str(source),
                "-f", "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
            ]

            audio_map = [
                "-map", "1:a:0",
                "-shortest",
            ]

        command = [
            ff,
            "-y",
            "-hide_banner",
            *inputs,

            "-filter_complex", filter_graph,

            "-map", "[v]",
            *audio_map,

            "-c:v", "libx264",
            "-preset", "veryfast",
            "-b:v", bitrate,
            "-maxrate", bitrate,
            "-bufsize", f"{int(bitrate[:-1]) * 2}k",
            "-r", str(FPS),
            "-g", str(FPS * 2),
            "-pix_fmt", "yuv420p",

            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-ac", "2",

            "-movflags", "+faststart",
            str(destination),
        ]

        _run(command)

        if not destination.exists() or destination.stat().st_size == 0:
            raise RuntimeError(
                f"FFmpeg did not create normalized clip: {destination.name}"
            )

        segments.append(destination)

    # The normalized filenames are simple, so no special-character
    # escaping is needed in the concat list.
    listing = workdir / "list.txt"

    listing.write_text(
        "".join(
            f"file '{segment.name}'\n"
            for segment in segments
        ),
        encoding="utf-8",
    )

    loop_file = workdir / "loop.mp4"

    print("\nJoining normalized clips...")

    _run(
        [
            ff,
            "-y",
            "-hide_banner",
            "-f", "concat",
            "-safe", "0",
            "-i", "list.txt",
            "-c", "copy",
            "-movflags", "+faststart",
            loop_file.name,
        ],
        cwd=workdir,
    )

    if not loop_file.exists() or loop_file.stat().st_size == 0:
        raise RuntimeError("The final loop video was not created.")

    print(
        f"\nLoop created successfully: {loop_file} "
        f"({loop_file.stat().st_size} bytes)"
    )

    return loop_file


# ---------------------------------------------------------
# YouTube Live API
# ---------------------------------------------------------

def get_or_create_stream(yt) -> dict:

    response = yt.liveStreams().list(
        part="id,snippet,cdn",
        mine=True,
        maxResults=50,
    ).execute()

    for stream in response.get("items", []):
        if (
            stream["snippet"]["title"] == STREAM_TITLE
            and stream["cdn"].get("ingestionType") == "rtmp"
        ):
            print("Reusing existing YouTube RTMP stream.")
            return stream

    print("Creating a reusable YouTube RTMP stream.")

    return yt.liveStreams().insert(
        part="snippet,cdn",
        body={
            "snippet": {
                "title": STREAM_TITLE,
            },
            "cdn": {
                "frameRate": "variable",
                "ingestionType": "rtmp",
                "resolution": "variable",
            },
        },
    ).execute()


def create_broadcast(
    yt,
    title: str,
    description: str,
    privacy: str,
) -> dict:

    start = datetime.now(timezone.utc) + timedelta(minutes=1)

    return yt.liveBroadcasts().insert(
        part="snippet,status,contentDetails",
        body={
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "scheduledStartTime": _iso(start),
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
            "contentDetails": {
                "enableAutoStart": False,
                "enableAutoStop": True,
                "enableDvr": True,
                "recordFromStart": True,
                "monitorStream": {
                    "enableMonitorStream": False,
                },
            },
        },
    ).execute()


def _wait_stream_active(
    yt,
    stream_id: str,
    timeout: int = 180,
) -> bool:

    deadline = time.time() + timeout

    while time.time() < deadline:
        response = yt.liveStreams().list(
            part="status",
            id=stream_id,
        ).execute()

        items = response.get("items", [])

        if items:
            status = items[0]["status"]["streamStatus"]

            print("YouTube stream status:", status)

            if status == "active":
                return True

        time.sleep(5)

    return False


def _transition(
    yt,
    broadcast_id: str,
    status: str,
    tries: int = 6,
) -> None:

    for attempt in range(tries):
        try:
            yt.liveBroadcasts().transition(
                broadcastStatus=status,
                id=broadcast_id,
                part="status",
            ).execute()

            print(f"Broadcast transitioned to: {status}")
            return

        except HttpError as exc:
            body = (
                exc.content.decode("utf-8", errors="ignore")
                if exc.content
                else ""
            )

            if "redundantTransition" in body:
                return

            if attempt == tries - 1:
                raise

            print(
                f"Broadcast transition retry "
                f"{attempt + 1}/{tries}: {body[:200]}"
            )

            time.sleep(8)


# ---------------------------------------------------------
# FFmpeg RTMP streaming
# ---------------------------------------------------------

def _spawn_ffmpeg(
    ff: str,
    loop: Path,
    rtmp_url: str,
    log_path: Path,
) -> subprocess.Popen:

    log_path.parent.mkdir(parents=True, exist_ok=True)

    log_file = open(log_path, "ab")

    try:
        return subprocess.Popen(
            [
                ff,
                "-hide_banner",
                "-loglevel", "warning",
                "-re",
                "-stream_loop", "-1",
                "-i", str(loop),
                "-c", "copy",
                "-flvflags", "no_duration_filesize",
                "-f", "flv",
                rtmp_url,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
        )
    finally:
        log_file.close()


def _stop(proc: subprocess.Popen | None) -> None:

    if proc and proc.poll() is None:
        proc.terminate()

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# ---------------------------------------------------------
# Main Live function
# ---------------------------------------------------------

def run_live(
    token_json: str,
    videos: list[Path],
    title: str,
    description: str,
    duration_min: int = 120,
    privacy: str = "public",
    layout: str = "landscape",
    workdir: Path = Path("output/live"),
    on_live=None,
    log=print,
) -> dict:

    if duration_min <= 0:
        raise ValueError("Live duration must be greater than zero.")

    workdir.mkdir(parents=True, exist_ok=True)

    ff = _ffmpeg()

    creds = load_credentials(token_json)

    yt = build(
        "youtube",
        "v3",
        credentials=creds,
        cache_discovery=False,
    )

    log(f"Building loop from {len(videos)} clip(s)...")

    loop = build_loop(
        videos,
        workdir,
        layout,
    )

    stream = get_or_create_stream(yt)

    ingestion = stream["cdn"]["ingestionInfo"]

    rtmp_url = (
        f'{ingestion["ingestionAddress"]}/'
        f'{ingestion["streamName"]}'
    )

    broadcast = create_broadcast(
        yt,
        title,
        description,
        privacy,
    )

    broadcast_id = broadcast["id"]

    yt.liveBroadcasts().bind(
        id=broadcast_id,
        part="id,contentDetails",
        streamId=stream["id"],
    ).execute()

    url = f"https://www.youtube.com/watch?v={broadcast_id}"

    log("Broadcast created:", url)

    ffmpeg_log = workdir / "ffmpeg_stream.log"

    proc = None
    went_live = False

    try:
        proc = _spawn_ffmpeg(
            ff,
            loop,
            rtmp_url,
            ffmpeg_log,
        )

        if not _wait_stream_active(yt, stream["id"]):
            raise RuntimeError(
                "YouTube did not receive an active video stream. "
                f"Check {ffmpeg_log} for FFmpeg errors."
            )

        _transition(
            yt,
            broadcast_id,
            "live",
        )

        went_live = True

        log("LIVE:", url)

        if on_live:
            on_live(url)

        deadline = time.time() + duration_min * 60
        restarts = 0

        while time.time() < deadline:
            time.sleep(5)

            if proc.poll() is not None:
                restarts += 1

                log(
                    f"FFmpeg stopped with exit code "
                    f"{proc.returncode}. Restart #{restarts}"
                )

                if restarts > 5:
                    raise RuntimeError(
                        "FFmpeg stopped more than 5 times. "
                        f"Check {ffmpeg_log}."
                    )

                time.sleep(3)

                proc = _spawn_ffmpeg(
                    ff,
                    loop,
                    rtmp_url,
                    ffmpeg_log,
                )

        log(f"Live duration completed: {duration_min} minutes.")

    finally:
        _stop(proc)

        try:
            if went_live:
                _transition(
                    yt,
                    broadcast_id,
                    "complete",
                )
            else:
                yt.liveBroadcasts().delete(
                    id=broadcast_id,
                ).execute()

        except Exception as exc:
            log("Cleanup warning:", str(exc)[:300])

    return {
        "url": url,
        "token_json": creds.to_json(),
    }