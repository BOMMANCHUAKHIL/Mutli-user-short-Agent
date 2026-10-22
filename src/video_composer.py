"""Posters -> 15-second 1080x1920 MP4 with slow zoom, optional countdown and looping music.

Requires moviepy==1.0.3 and ffmpeg. Frames are produced with Pillow so no moviepy
resize (which breaks on new Pillow versions) is used.
"""
import json
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
MUSIC_DIR = ROOT / "assets" / "music"
STATE = ROOT / "output" / "state.json"
W, H = 1080, 1920


def _next_music() -> Path | None:
    tracks = sorted(MUSIC_DIR.glob("*.mp3"))
    if not tracks:
        return None
    try:
        idx = int(json.loads(STATE.read_text()).get("music_index", 0))
    except Exception:
        idx = 0
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"music_index": (idx + 1) % len(tracks)}))
    return tracks[idx % len(tracks)]


def _font(size: int):
    for p in (ROOT / "assets/fonts/NotoSansTelugu-Bold.ttf",
              Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")):
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                pass
    return ImageFont.load_default()


def compose_video(posters: list[Path], out_path: Path, duration: int = 15, fps: int = 24,
                  countdown_first: bool = False, question_seconds: int = 10) -> Path:
    """One poster = zoom for the whole video. Two posters = question (with countdown) then reveal."""
    from moviepy.editor import AudioFileClip, VideoClip, afx

    imgs = [Image.open(p).convert("RGB").resize((W, H)) for p in posters]
    font = _font(110)

    def frame(t: float):
        if len(imgs) == 2:
            seg = 0 if t < question_seconds else 1
            seg_t = t if seg == 0 else t - question_seconds
            seg_len = question_seconds if seg == 0 else duration - question_seconds
        else:
            seg, seg_t, seg_len = 0, t, duration
        zoom = 1.0 + 0.06 * (seg_t / max(seg_len, 0.1))
        cw, ch = W / zoom, H / zoom
        x0, y0 = (W - cw) / 2, (H - ch) / 2
        im = imgs[seg].resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch))
        if countdown_first and seg == 0:
            left = max(1, int(question_seconds - t) + 1)
            d = ImageDraw.Draw(im)
            cx, cy, r = W - 120, 120, 80
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(124, 58, 237), outline="white", width=6)
            d.text((cx, cy), str(left), fill="white", font=font, anchor="mm")
        return np.asarray(im)

    clip = VideoClip(frame, duration=duration).set_fps(fps)
    music = _next_music()
    audio = None
    if music:
        audio = AudioFileClip(str(music))
        audio = afx.audio_loop(audio, duration=duration).volumex(0.35).audio_fadeout(1.5)
        clip = clip.set_audio(audio)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        clip.write_videofile(
            str(out_path), fps=fps, codec="libx264", audio_codec="aac", audio_fps=48000,
            audio_bitrate="128k", preset="medium", threads=2, logger=None,
            ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart", "-ac", "2"],
        )
    finally:
        clip.close()
        if audio:
            audio.close()
    return out_path
