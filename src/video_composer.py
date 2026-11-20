"""
Create branded 1080x1920 videos with optional profile pictures,
watermarks and animated Subscribe GIFs.
"""

import json
from pathlib import Path

import numpy as np
from PIL import (
    Image,
    ImageDraw,
    ImageFont,
    ImageOps,
    ImageSequence,
)

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
    STATE.write_text(
        json.dumps({"music_index": (idx + 1) % len(tracks)})
    )

    return tracks[idx % len(tracks)]


def _font(size: int):
    for path in (
        ROOT / "assets/fonts/NotoSansTelugu-Bold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ):
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                pass

    return ImageFont.load_default()


def _load_profile_picture(path: str | None, max_h: int = 100, max_w: int = 450):
    if not path:
        return None

    image_path = Path(path)

    if not image_path.is_file():
        return None

    try:
        img = Image.open(image_path).convert("RGBA")

        # Calculate aspect ratio to fit within max_h and max_w
        ratio = img.width / img.height
        new_h = max_h
        new_w = int(new_h * ratio)

        if new_w > max_w:
            new_w = max_w
            new_h = int(new_w / ratio)

        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # Apply a rounded rectangle mask instead of a circle
        mask = Image.new("L", (new_w, new_h), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle(
            (0, 0, new_w, new_h),
            radius=15,
            fill=255
        )
        img.putalpha(mask)

        return img
    except Exception:
        return None


def _load_gif(path: str | None, size=(220, 90)):
    if not path:
        return []

    gif_path = Path(path)

    if not gif_path.is_file():
        return []

    try:
        gif = Image.open(gif_path)
        frames = []

        for frame in ImageSequence.Iterator(gif):
            img = frame.convert("RGBA")
            img.thumbnail(size)
            frames.append(img.copy())

        return frames
    except Exception:
        return []


def _draw_branding(
    im,
    t,
    profile,
    title,
    gif_frames,
    watermark_text="",
    show_end_branding=False
):
    im = im.convert("RGBA")
    draw = ImageDraw.Draw(im)

    # Top Bar Branding
    x, y = 40, 25

    # Paste the wide banner/profile picture directly
    if profile:
        im.paste(profile, (x, y), profile)

    # If text is provided, draw it next to the image
    if title:
        draw.text(
            (x + profile.width + 20 if profile else x + 20, y + 25),
            title[:25],
            font=_font(42),
            fill="white",
            stroke_width=1,
            stroke_fill="black"
        )

    # Animated Subscribe GIF (Only at the last 2 seconds)
    if gif_frames and show_end_branding:
        gif_index = int(t * 10) % len(gif_frames)
        gif = gif_frames[gif_index]

        # Place at the bottom center of the screen
        gx = (W - gif.width) // 2
        gy = H - gif.height - 40
        im.paste(gif, (gx, gy), gif)

    # Platform-specific watermark (Only at the last 2 seconds)
    if watermark_text and show_end_branding:
        text = watermark_text[:35]
        watermark_font = _font(32)

        bbox = draw.textbbox((0, 0), text, font=watermark_font)
        text_width = bbox[2] - bbox[0]
        padding = 20

        box_width = text_width + padding * 2
        box_height = 65

        wx = W - box_width - 40
        wy = 40  # Aligned with the profile picture

        draw.rounded_rectangle(
            (wx, wy, wx + box_width, wy + box_height),
            radius=20,
            fill=(0, 0, 0, 160)
        )

        draw.text(
            (wx + padding, wy + 12),
            text,
            font=watermark_font,
            fill="white"
        )

    return im


def compose_video(
    posters: list[Path],
    out_path: Path,
    duration: int = 15,
    fps: int = 24,
    countdown_first: bool = False,
    question_seconds: int = 10,
    profile_pic_path: str | None = None,
    branding_title: str = "",
    subscribe_gif_path: str | None = None,
    watermark_text: str = "",
    music_path: str | Path | None = None,
) -> Path:

    from moviepy.editor import AudioFileClip, VideoClip, afx

    if not posters:
        raise ValueError("No posters supplied to compose_video().")

    imgs = [
        Image.open(p).convert("RGB").resize((W, H))
        for p in posters
    ]

    font = _font(110)

    profile = _load_profile_picture(profile_pic_path)
    gif_frames = _load_gif(subscribe_gif_path)

    def frame(t: float):

        if len(imgs) == 2:
            seg = 0 if t < question_seconds else 1
            seg_t = t if seg == 0 else t - question_seconds

            seg_len = (
                question_seconds
                if seg == 0
                else duration - question_seconds
            )
        else:
            seg, seg_t, seg_len = 0, t, duration

        # --- Zoom Effect on Original Poster ---
        zoom = 1.0 + 0.06 * (seg_t / max(seg_len, 0.1))
        cw, ch = W / zoom, H / zoom
        x0, y0 = (W - cw) / 2, (H - ch) / 2

        base_im = imgs[seg].resize(
            (W, H),
            Image.Resampling.BILINEAR,
            box=(x0, y0, x0 + cw, y0 + ch)
        )

        # --- Fit Poster into Safe Area ---
        top_bar_h = 150
        target_h = H - top_bar_h
        scale = target_h / H
        new_w = int(W * scale)
        new_h = target_h

        poster = base_im.resize((new_w, new_h), Image.Resampling.BILINEAR)

        # Create Canvas with Background Color (matches HTML #10122b)
        canvas = Image.new("RGB", (W, H), (16, 18, 43))
        x_offset = (W - new_w) // 2
        canvas.paste(poster, (x_offset, top_bar_h))

        im = canvas.convert("RGBA")

        # --- Countdown Timer (Puzzle Mode) ---
        if countdown_first and seg == 0:
            left = max(1, int(question_seconds - t) + 1)
            d = ImageDraw.Draw(im)
            # Moved down so it sits nicely inside the poster area
            cx, cy, r = W - 120, top_bar_h + 120, 80

            d.ellipse(
                (cx-r, cy-r, cx+r, cy+r),
                fill=(124, 58, 237, 255),
                outline="white",
                width=6
            )

            d.text(
                (cx, cy),
                str(left),
                fill="white",
                font=font,
                anchor="mm"
            )

        # --- Apply Branding (Top Bar + End Screen) ---
        if profile or branding_title or gif_frames or watermark_text:
            show_end_branding = (t >= duration - 2.0)

            im = _draw_branding(
                im,
                t,
                profile,
                branding_title,
                gif_frames,
                watermark_text,
                show_end_branding=show_end_branding
            )

        return np.asarray(im.convert("RGB"))

    clip = VideoClip(frame, duration=duration).set_fps(fps)

    audio = None

    selected_music = (
        Path(music_path)
        if music_path
        else _next_music()
    )

    if selected_music and selected_music.is_file():
        audio = AudioFileClip(str(selected_music))

        audio = (
            afx.audio_loop(audio, duration=duration)
            .volumex(0.35)
            .audio_fadeout(1.5)
        )

        clip = clip.set_audio(audio)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        clip.write_videofile(
            str(out_path),
            fps=fps,
            codec="libx264",
            audio_codec="aac",
            audio_fps=48000,
            audio_bitrate="128k",
            preset="medium",
            threads=2,
            logger=None,
            ffmpeg_params=[
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-ac", "2"
            ],
        )
    finally:
        clip.close()

        if audio:
            audio.close()

        for img in imgs:
            img.close()

    return out_path