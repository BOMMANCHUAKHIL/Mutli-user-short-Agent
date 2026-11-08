"""The brain: topic -> content -> verified images -> poster(s) -> video -> upload."""
import json
import random
import time
from pathlib import Path
import hashlib
import requests
from .facebook_uploader import upload_reel
from .food_fetcher import ImageFetcher, to_data_uri
from .groq_pool import GroqPool
from .html_renderer import render_many
from .poster_composer import render_html
from .reviewer_agent import find_mismatches
from .telugu_generator import ContentGenerator, TABLE_ROWS_NEEDED
from .video_composer import compose_video, _next_music
from .youtube_uploader import upload_short
from .youtube_uploader import upload_short, YouTubeRestrictedError
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"


def load_config() -> dict:
    defaults = {"duration": 15, "fps": 24, "privacy": "public",
                "allow_ai_fallback": True, "question_seconds": 10}
    try:
        defaults.update(json.loads((ROOT / "config.json").read_text()))
    except Exception:
        pass
    return defaults

def download_branding_image(url: str | None, name: str) -> str | None:
    if not url:
        return None

    # Already a local image path
    local_path = Path(url)

    if local_path.is_file():
        return str(local_path)

    if not url.startswith(("https://", "http://")):
        return None

    branding_dir = OUT / "branding"
    branding_dir.mkdir(parents=True, exist_ok=True)

    file_hash = hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()[:12]

    extension = ".png"

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        content_type = response.headers.get(
            "Content-Type", ""
        ).lower()

        if "jpeg" in content_type or "jpg" in content_type:
            extension = ".jpg"
        elif "webp" in content_type:
            extension = ".webp"

        output_path = (
            branding_dir / f"{name}_{file_hash}{extension}"
        )

        output_path.write_bytes(response.content)

        return str(output_path)

    except Exception as exc:
        raise RuntimeError(
            f"Could not download {name} branding image: {exc}"
        ) from exc
class ShortsOrchestrator:
    def __init__(self, groq_key: str, pixabay_keys: str, topic: str, subtopic: str = "",
                 language: str = "English", mode: str = "table", system_prompt: str | None = None,
                 html_template: str | None = None, pexels_key: str | None = None,
                 youtube_token: str | None = None,
                 facebook_page_id: str | None = None,
                 facebook_token: str | None = None,
                 watermark: str = "",
                 youtube_profile_pic: str | None = None,
                 facebook_profile_pic: str | None = None,
                 youtube_watermark: str = "",
                 facebook_watermark: str = "",
                 subscribe_gif: str | None = None,
                 config: dict | None = None,
                 enable_youtube: bool = True,
                 enable_facebook: bool = True):
        self.cfg = config or load_config()
        self.topic, self.sub = topic, subtopic
        self.language = language or "English"
        self.mode = mode if mode in ("table", "story", "puzzle", "mixed") else "table"
        self.html_template = html_template
        self.watermark = watermark or ""
        self.watermark = watermark or ""
        self.yt_token = youtube_token if enable_youtube else None  # <-- NEW
        self.fb_page = facebook_page_id if enable_facebook else None  # <-- NEW
        self.fb_token = facebook_token if enable_facebook else None  # <-- NEW
        self._actual_fb_page = facebook_page_id
        self._actual_fb_token = facebook_token

        self.fb_page, self.fb_token = facebook_page_id, facebook_token
        self.youtube_profile_pic = youtube_profile_pic
        self.facebook_profile_pic = facebook_profile_pic

        self.youtube_watermark = youtube_watermark
        self.facebook_watermark = facebook_watermark

        self.subscribe_gif =  subscribe_gif or str(ROOT / "assets" / "branding" / "subscribe.gif")

        self.pool = GroqPool(groq_key)
        self.gen = ContentGenerator(self.pool, self.language, system_prompt)
        self.gen = ContentGenerator(self.pool, self.language, system_prompt)
        self.fetcher = ImageFetcher(self.pool, pixabay_keys, pexels_key, OUT / "food_cache",
                                    self.cfg.get("allow_ai_fallback", True))
        self.sources: list[str] = []

    # ── per-mode builders: return (list of html strings, content) ──────
    def _fetch(self, pairs):
        res = self.fetcher.fetch_many(pairs)
        self.sources += [r["source"] for r in res if r]
        return res

    def _build_table(self, c: dict):
        rows = c["rows"]
        items = [x for r in rows for x in (r["a"], r["b"])]
        bad = find_mismatches(self.pool, self.language, items)
        ok = [r for i, r in enumerate(rows) if 2 * i not in bad and 2 * i + 1 not in bad]
        pending = ok if len(ok) >= TABLE_ROWS_NEEDED else rows
        chosen = []
        while len(chosen) < TABLE_ROWS_NEEDED and pending:
            batch = pending[:TABLE_ROWS_NEEDED - len(chosen)]
            pending = pending[len(batch):]
            pairs = [(x["query"], x["alt"]) for r in batch for x in (r["a"], r["b"])]
            res = self._fetch(pairs)
            for k, r in enumerate(batch):
                if res[2 * k] and res[2 * k + 1]:
                    chosen.append({"tag": r["tag"],
                                   "a_label": r["a"]["label"], "a_img": to_data_uri(res[2 * k]["bytes"]),
                                   "b_label": r["b"]["label"], "b_img": to_data_uri(res[2 * k + 1]["bytes"])})
        if len(chosen) < TABLE_ROWS_NEEDED:
            raise RuntimeError(f"only {len(chosen)} rows have verified images")
        ctx = {"title": c["title"], "col_a_title": c["col_a_title"], "col_b_title": c["col_b_title"],
               "rows": chosen, "tips": c["tips"], "watermark": self.watermark}
        return [render_html("table", ctx, self.html_template)]

    def _build_story(self, c: dict):
        res = self._fetch([(c["image"]["query"], c["image"]["alt"])])[0]
        if not res:
            raise RuntimeError("no verified image for the story")
        ctx = {"title": c["title"], "story": c["story"], "image": to_data_uri(res["bytes"]),
               "image_label": c["image"]["label"], "hashtags": c["hashtags"], "watermark": self.watermark}
        return [render_html("story", ctx)]

    def _build_puzzle(self, c: dict):
        res = self._fetch([(o["query"], o["alt"]) for o in c["options"]])
        if not all(res):
            raise RuntimeError("a puzzle option has no verified image")
        opts = [{"label": o["label"], "img": to_data_uri(r["bytes"])} for o, r in zip(c["options"], res)]
        base = {"title": c["title"], "question": c["question"], "options": opts,
                "answer_index": c["answer_index"], "explanation": c["explanation"],
                "hint": "⏳ A · B · C · D ?", "watermark": self.watermark}
        return [render_html("puzzle", {**base, "reveal": False}),
                render_html("puzzle", {**base, "reveal": True})]

    @staticmethod
    def _label_items(c: dict) -> list[dict]:
        if c["mode"] == "story":
            return [c["image"]]
        if c["mode"] == "puzzle":
            return c["options"]
        return []

    # ── main entry ─────────────────────────────────────────────────────
    # ── main entry ─────────────────────────────────────────────────────
    def run_cycle(self) -> dict:
        mode = random.choice(["table", "story", "puzzle"]) if self.mode == "mixed" else self.mode
        content = None
        for attempt in range(3):
            content = self.gen.generate(mode, self.topic, self.sub)
            items = self._label_items(content)
            if items and find_mismatches(self.pool, self.language, items):
                content = None  # label/picture mismatch -> write new content
                continue
            break
        if content is None:
            raise RuntimeError("content kept failing the label check")

        builder = {"table": self._build_table, "story": self._build_story,
                   "puzzle": self._build_puzzle}[mode]
        htmls = builder(content)

        stamp = time.strftime("%Y%m%d-%H%M%S")

        posters = render_many([
            (h, OUT / "posters" / f"{stamp}_{i}.png")
            for i, h in enumerate(htmls)
        ])

        tags = content["hashtags"] + ["#Shorts"]

        description = (
            f"{content['caption']}\n\n{' '.join(tags)}"
        ).strip()

        result = {
            "mode": mode,
            "title": content["yt_title"],
            "video": None,
            "youtube_video": None,
            "youtube_token_json": None,
            "facebook_video": None,
            "youtube_url": None,
            "facebook_video_id": None,
            "errors": [],
        }

        # Download platform-specific profile pictures
        youtube_profile_path = download_branding_image(
            self.youtube_profile_pic,
            "youtube_profile"
        )

        facebook_profile_path = download_branding_image(
            self.facebook_profile_pic,
            "facebook_profile"
        )

        print("YouTube profile image:", youtube_profile_path)
        print("Facebook profile image:", facebook_profile_path)

        if self.youtube_profile_pic and not youtube_profile_path:
            result["errors"].append("YouTube profile picture could not be loaded.")

        if self.facebook_profile_pic and not facebook_profile_path:
            result["errors"].append("Facebook profile picture could not be loaded.")

        # Use the same music track for both platform videos
        shared_music = _next_music()

        # Common video settings
        common_video_settings = {
            "duration": int(self.cfg["duration"]),
            "fps": int(self.cfg["fps"]),
            "countdown_first": (mode == "puzzle"),
            "question_seconds": int(self.cfg["question_seconds"]),
            "music_path": shared_music,
        }

        # ------------------------------------
        # YOUTUBE VIDEO
        # ------------------------------------
        if self.yt_token:
            try:
                youtube_video = compose_video(
                    posters,
                    OUT / "videos" / f"{stamp}_youtube.mp4",
                    profile_pic_path=youtube_profile_path,
                    # THIS IS THE FIX: Use watermark instead of "YouTube"
                    branding_title=self.youtube_watermark or "",
                    subscribe_gif_path=self.subscribe_gif,
                    watermark_text="",  # Empty to avoid duplicate at top right
                    **common_video_settings,
                )

                result["youtube_video"] = str(youtube_video)
                result["video"] = str(youtube_video)

                youtube_url, updated_youtube_token = upload_short(
                    self.yt_token,
                    str(youtube_video),
                    content["yt_title"],
                    description,
                    tags,
                    self.cfg["privacy"],
                    return_token=True,
                )

                result["youtube_url"] = youtube_url
                result["youtube_token_json"] = updated_youtube_token


            except YouTubeRestrictedError as exc:

                result["errors"].append(f"youtube_restricted: {str(exc)}")

                result["youtube_restricted"] = True  # <--- FLAG IT

            except Exception as exc:

                result["errors"].append(

                    f"youtube: {str(exc)[:300]}"

                )

        # ------------------------------------
        # FACEBOOK VIDEO
        # ------------------------------------
        if self.fb_page and self.fb_token:
            try:
                facebook_video = compose_video(
                    posters,
                    OUT / "videos" / f"{stamp}_facebook.mp4",
                    profile_pic_path=facebook_profile_path,
                    # THIS IS THE FIX: Use watermark instead of "Facebook"
                    branding_title=self.facebook_watermark or "",
                    subscribe_gif_path=None,
                    watermark_text="",  # Empty to avoid duplicate at top right
                    **common_video_settings,
                )

                result["facebook_video"] = str(facebook_video)

                result["facebook_video_id"] = upload_reel(
                    self.fb_page,
                    self.fb_token,
                    str(facebook_video),
                    description,
                    ai_generated="ai" in self.sources,
                )

            except Exception as exc:
                result["errors"].append(
                    f"facebook: {str(exc)[:300]}"
                )

        # If neither platform is configured, generate a plain preview
        if not self.yt_token and not (self.fb_page and self.fb_token):
            preview_video = compose_video(
                posters,
                OUT / "videos" / f"{stamp}_preview.mp4",
                **common_video_settings,
            )

            result["video"] = str(preview_video)

        uploads_wanted = (
                bool(self.yt_token)
                or bool(self.fb_page and self.fb_token)
        )

        if uploads_wanted and not (
                result["youtube_url"]
                or result["facebook_video_id"]
        ):
            raise RuntimeError(
                "All uploads failed: " + "; ".join(result["errors"])
            )

        return result
