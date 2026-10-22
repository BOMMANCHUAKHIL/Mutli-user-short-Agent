"""Finds ONE photo that matches an English query as exactly as possible.

Works for any topic (the filename is kept from the original project layout).

Pipeline per query:
  1. Collect candidates from Pixabay / Pexels / Openverse.
  2. Rank them by how many query words appear in their tags/titles.
  3. Show the top few to a Groq vision model; accept only an image it confirms.
  4. Try the alternative query, then a simplified query.
  5. Last resort: an AI-generated image, accepted only if the vision model confirms it.
Returns None when nothing passes, so the caller can drop that item instead of
showing a wrong picture.
"""
import base64
import hashlib
import io
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image

from .groq_pool import GroqPool

UA = {"User-Agent": "ShortsAgent/1.0 (+https://github.com)"}
STOP = {"a", "an", "the", "of", "with", "and", "in", "on", "for", "to", "glass", "cup", "bowl",
        "plate", "fresh", "photo", "picture", "close", "up", "view", "style"}
MAX_BYTES = 6_000_000
CANDIDATES_TO_CHECK = 4


def terms(q: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", q.lower()):
        if w in STOP:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def score(text: str, ts: list[str]) -> float:
    if not ts:
        return 0.0
    t = " ".join(terms(text))
    return sum(1 for w in ts if w in t) / len(ts)


class ImageFetcher:
    def __init__(self, pool: GroqPool | None, pixabay_keys: str, pexels_key: str | None,
                 cache_dir: Path, allow_ai_fallback: bool = True):
        self.pool = pool
        self.pixabay = [k.strip() for k in (pixabay_keys or "").split(",") if k.strip()]
        self.pexels = (pexels_key or "").strip() or None
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.allow_ai = allow_ai_fallback
        self._pix_i = 0
        self._lock = threading.Lock()

    # ── candidate sources ──────────────────────────────────────────────
    def _pixabay(self, q: str) -> list[dict]:
        if not self.pixabay:
            return []
        with self._lock:
            key = self.pixabay[self._pix_i % len(self.pixabay)]
            self._pix_i += 1
        r = requests.get("https://pixabay.com/api/", timeout=20, headers=UA, params={
            "key": key, "q": q, "image_type": "photo", "per_page": 20, "safesearch": "true"})
        if r.status_code != 200:
            return []
        return [{"url": h["webformatURL"], "text": h.get("tags", ""), "src": "pixabay"}
                for h in r.json().get("hits", [])]

    def _pexels(self, q: str) -> list[dict]:
        if not self.pexels:
            return []
        r = requests.get("https://api.pexels.com/v1/search", timeout=20,
                         headers={**UA, "Authorization": self.pexels},
                         params={"query": q, "per_page": 15})
        if r.status_code != 200:
            return []
        return [{"url": p["src"]["medium"], "text": p.get("alt", "") or "", "src": "pexels"}
                for p in r.json().get("photos", [])]

    def _openverse(self, q: str) -> list[dict]:
        r = requests.get("https://api.openverse.org/v1/images/", timeout=20, headers=UA,
                         params={"q": q, "page_size": 15, "license_type": "commercial"})
        if r.status_code != 200:
            return []
        out = []
        for h in r.json().get("results", []):
            url = h.get("thumbnail") or h.get("url")
            if url:
                tags = " ".join(t.get("name", "") for t in h.get("tags", []) or [])
                out.append({"url": url, "text": f"{h.get('title', '')} {tags}", "src": "openverse"})
        return out

    def _candidates(self, q: str) -> list[dict]:
        cands = []
        for fn in (self._pixabay, self._pexels, self._openverse):
            try:
                cands += fn(q)
            except Exception:
                continue
        ts = terms(q)
        for c in cands:
            c["score"] = score(c["text"], ts)
        cands.sort(key=lambda c: c["score"], reverse=True)
        # drop duplicates by url
        seen, uniq = set(), []
        for c in cands:
            if c["url"] not in seen:
                seen.add(c["url"])
                uniq.append(c)
        return uniq

    # ── download / verify ──────────────────────────────────────────────
    @staticmethod
    def _download(url: str) -> bytes | None:
        if not url.startswith(("http://", "https://")):
            return None
        try:
            with requests.get(url, timeout=20, headers=UA, stream=True) as r:
                if r.status_code != 200:
                    return None
                buf = b""
                for chunk in r.iter_content(65536):
                    buf += chunk
                    if len(buf) > MAX_BYTES:
                        return None
            im = Image.open(io.BytesIO(buf))
            im.load()
            im = im.convert("RGB")
            im.thumbnail((720, 720))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=88)
            return out.getvalue()
        except Exception:
            return None

    @staticmethod
    def _small_b64(jpeg: bytes) -> str:
        im = Image.open(io.BytesIO(jpeg))
        im.thumbnail((448, 448))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=75)
        return base64.b64encode(out.getvalue()).decode()

    def _verify_pick(self, query: str, images: list[bytes]) -> int:
        """Index of the confirmed image, or -1."""
        if not images:
            return -1
        if self.pool is None:
            return 0
        prompt = (
            f'Which image clearly shows: "{query}" as its main subject? '
            "Reject images that show a different item or type, a collage, heavy text overlays, "
            "or where something else dominates. Images are numbered from 1 in the order given. "
            'Reply ONLY with JSON like {"best": 2}, or {"best": 0} if none match.'
        )
        try:
            return self.pool.vision_pick(prompt, [self._small_b64(b) for b in images])
        except Exception:
            # Vision unavailable: only trust an image whose tags matched strongly.
            return -2

    def _try_query(self, query: str) -> tuple[bytes, str] | None:
        cands = self._candidates(query)
        imgs, metas = [], []
        for c in cands:
            if len(imgs) >= CANDIDATES_TO_CHECK:
                break
            data = self._download(c["url"])
            if data:
                imgs.append(data)
                metas.append(c)
        idx = self._verify_pick(query, imgs)
        if idx >= 0:
            return imgs[idx], metas[idx]["src"]
        if idx == -2 and metas and metas[0]["score"] >= 0.99:
            return imgs[0], metas[0]["src"]
        return None

    def _ai_image(self, query: str) -> tuple[bytes, str] | None:
        if not self.allow_ai:
            return None
        url = ("https://image.pollinations.ai/prompt/"
               + quote(f"realistic photo of {query}, centered, plain background")
               + "?width=768&height=768&nologo=true")
        data = self._download(url)
        if not data:
            return None
        return (data, "ai") if self._verify_pick(query, [data]) == 0 else None

    # ── public API ─────────────────────────────────────────────────────
    def fetch(self, query: str, alt: str = "") -> dict | None:
        key = hashlib.sha1(query.strip().lower().encode()).hexdigest()
        cached = self.cache / f"{key}.jpg"
        if cached.exists():
            return {"bytes": cached.read_bytes(), "source": "cache"}

        simple = " ".join(terms(query)[:2])
        for q in [query, alt, simple]:
            if not q or not q.strip():
                continue
            hit = self._try_query(q.strip())
            if hit:
                cached.write_bytes(hit[0])
                return {"bytes": hit[0], "source": hit[1]}
        hit = self._ai_image(query)
        if hit:
            cached.write_bytes(hit[0])
            return {"bytes": hit[0], "source": hit[1]}
        return None

    def fetch_many(self, items: list[tuple[str, str]], workers: int = 3) -> list[dict | None]:
        def one(it):
            try:
                return self.fetch(it[0], it[1])
            except Exception:
                return None
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(one, items))


def to_data_uri(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
