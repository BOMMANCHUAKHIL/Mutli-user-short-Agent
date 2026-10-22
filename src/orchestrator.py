"""The brain: topic -> content -> verified images -> poster(s) -> video -> upload."""
import json
import random
import time
from pathlib import Path

from .facebook_uploader import upload_reel
from .food_fetcher import ImageFetcher, to_data_uri
from .groq_pool import GroqPool
from .html_renderer import render_many
from .poster_composer import render_html
from .reviewer_agent import find_mismatches
from .telugu_generator import ContentGenerator, TABLE_ROWS_NEEDED
from .video_composer import compose_video
from .youtube_uploader import upload_short

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


class ShortsOrchestrator:
    def __init__(self, groq_key: str, pixabay_keys: str, topic: str, subtopic: str = "",
                 language: str = "English", mode: str = "table", system_prompt: str | None = None,
                 html_template: str | None = None, pexels_key: str | None = None,
                 youtube_token: str | None = None, facebook_page_id: str | None = None,
                 facebook_token: str | None = None, watermark: str = "", config: dict | None = None):
        self.cfg = config or load_config()
        self.topic, self.sub = topic, subtopic
        self.language = language or "English"
        self.mode = mode if mode in ("table", "story", "puzzle", "mixed") else "table"
        self.html_template = html_template
        self.watermark = watermark or ""
        self.yt_token = youtube_token
        self.fb_page, self.fb_token = facebook_page_id, facebook_token

        self.pool = GroqPool(groq_key)
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
    def run_cycle(self) -> dict:
        mode = random.choice(["table", "story", "puzzle"]) if self.mode == "mixed" else self.mode
        content = None
        for attempt in range(3):
            content = self.gen.generate(mode, self.topic, self.sub)
            items = self._label_items(content)
            if items and find_mismatches(self.pool, self.language, items):
                content = None            # label/picture mismatch -> write new content
                continue
            break
        if content is None:
            raise RuntimeError("content kept failing the label check")

        builder = {"table": self._build_table, "story": self._build_story,
                   "puzzle": self._build_puzzle}[mode]
        htmls = builder(content)

        stamp = time.strftime("%Y%m%d-%H%M%S")
        posters = render_many([(h, OUT / "posters" / f"{stamp}_{i}.png") for i, h in enumerate(htmls)])
        video = compose_video(posters, OUT / "videos" / f"{stamp}.mp4",
                              duration=int(self.cfg["duration"]), fps=int(self.cfg["fps"]),
                              countdown_first=(mode == "puzzle"),
                              question_seconds=int(self.cfg["question_seconds"]))

        tags = content["hashtags"] + ["#Shorts"]
        description = f"{content['caption']}\n\n{' '.join(tags)}".strip()
        result = {"mode": mode, "title": content["yt_title"], "video": str(video),
                  "youtube_url": None, "facebook_video_id": None, "errors": []}

        if self.yt_token:
            try:
                result["youtube_url"] = upload_short(self.yt_token, str(video), content["yt_title"],
                                                     description, tags, self.cfg["privacy"])
            except Exception as e:
                result["errors"].append(f"youtube: {str(e)[:200]}")
        if self.fb_page and self.fb_token:
            try:
                result["facebook_video_id"] = upload_reel(
                    self.fb_page, self.fb_token, str(video), description,
                    ai_generated="ai" in self.sources)
            except Exception as e:
                result["errors"].append(f"facebook: {str(e)[:200]}")

        uploads_wanted = bool(self.yt_token) + bool(self.fb_page and self.fb_token)
        if uploads_wanted and not (result["youtube_url"] or result["facebook_video_id"]):
            raise RuntimeError("all uploads failed: " + "; ".join(result["errors"]))
        return result
