"""LLM content generation for table / story / puzzle posters.

The filename is kept from the original project; any language works (default Telugu is
just what the first user wanted). Image queries are always plain English so the image
search stays accurate, while visible labels are in the requested language.
"""
import random
import re

from .groq_pool import GroqPool
from .poster_composer import clean_label

TABLE_ROWS_ASKED = 8      # we need 7 good rows (= 14 images); 1 spare for failures
TABLE_ROWS_NEEDED = 7


def _s(v, n=120) -> str:
    return clean_label(v)[:n]


def _ascii_query(v) -> str:
    q = re.sub(r"\s+", " ", str(v or "")).strip()
    return q if q and q.isascii() and len(q) <= 60 else ""


def _item(d) -> dict | None:
    if not isinstance(d, dict):
        return None
    label, query = _s(d.get("label"), 60), _ascii_query(d.get("query"))
    if not label or not query:
        return None
    return {"label": label, "query": query, "alt": _ascii_query(d.get("alt"))}


def _hashtags(raw) -> list[str]:
    tags = []
    for t in raw if isinstance(raw, list) else []:
        t = "#" + re.sub(r"[^\w]", "", str(t), flags=re.UNICODE)
        if len(t) > 1 and t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    return tags[:6]


def _meta(d: dict) -> dict:
    return {
        "hashtags": _hashtags(d.get("hashtags")),
        "yt_title": _s(d.get("yt_title") or d.get("title"), 90) or "Shorts",
        "caption": _s(d.get("caption"), 300),
    }


class ContentGenerator:
    def __init__(self, pool: GroqPool, language: str = "English", system_prompt: str | None = None):
        self.pool = pool
        self.language = (language or "English").strip()
        self.extra = (system_prompt or "").strip()

    # ── prompts ────────────────────────────────────────────────────────
    def _system(self) -> str:
        lang = self.language
        is_english = lang.strip().lower() in ("english", "en")

        if is_english:
            lang_rule = (
                "LANGUAGE: write every visible field in English.\n"
            )
        else:
            lang_rule = (
                f"LANGUAGE — STRICT RULE, HIGHEST PRIORITY:\n"
                f"- Every user-visible string MUST be written in {lang}.\n"
                f"- This includes: title, col_a_title, col_b_title, row tags, "
                f"every label (a.label, b.label, option labels, image.label), "
                f"all tips, story, question, explanation, caption, yt_title, hashtags.\n"
                f"- Do NOT write any visible field in English unless it is a proper noun "
                f"with no standard {lang} form.\n"
                f"- If you write visible text in English, the output is WRONG.\n"
            )

        return (
                lang_rule
                + "\nThe ONLY fields that are always English are 'query' and 'alt' — "
                  "they are used for image search, never shown to viewers, so keep them "
                  "SHORT ENGLISH (2-4 words) naming exactly what the label says "
                  "(e.g. 'boiled eggs', 'red sports car').\n\n"
                  "You write accurate content for 15-second vertical videos and return "
                  "ONLY valid JSON. Facts must be true.\n"
                + self.extra
        )

    def _prompt(self, mode: str, topic: str, sub: str, kind: str = "") -> str:
        lang = self.language
        note = f"[REMEMBER: all visible text below must be in {lang}. Only 'query'/'alt' are English.]\n"
        focus = note + f"Main topic: {topic}\nFocus for this video: {sub or 'any aspect'}\n"

        item = (f'{{"label": str in {lang} (<=3 words), '
                f'"query": short English str, "alt": short English str}}')
        meta = (f'"hashtags": [5 short tags in {lang}], '
                f'"yt_title": str in {lang} (<=80 chars), '
                f'"caption": str in {lang} (1-2 sentences)')
        if mode == "table":
            return focus + (
                f"Make a two-column comparison poster with exactly {TABLE_ROWS_ASKED} rows. The columns are two "
                "contrasting or complementary groups relevant to the topic (e.g. good vs bad, before vs after, "
                "A vs B).\nReturn JSON: {\"title\": str (<=60 chars), \"col_a_title\": str, \"col_b_title\": str, "
                f"\"rows\": [{{\"tag\": str (<=14 chars, e.g. a time or category), \"a\": {item}, \"b\": {item}}}], "
                f"\"tips\": [3 short tips], {meta}}}"
            )
        if mode == "story":
            return focus + (
                "Write a short engaging story or fact-story (45-70 words) about ONE main subject that can be shown "
                "in a single photo.\nReturn JSON: {\"title\": str (<=60 chars), \"story\": str, "
                f"\"image\": {item}, {meta}}}"
            )
        rule = ("Exactly 3 options belong to the same group and 1 does NOT; the answer is the one that does not belong."
                if kind == "odd_one_out" else
                "Ask a question where exactly 1 of the 4 options is correct and 3 are clearly wrong.")
        return focus + (
            f"Make a picture puzzle ({kind}) with exactly 4 options. {rule}\n"
            "Return JSON: {\"title\": str (<=50 chars), \"question\": str (<=90 chars), "
            f"\"options\": [4 x {item}], \"answer_index\": int 0-3, \"explanation\": str (1 sentence), {meta}}}"
        )

    # ── validation ─────────────────────────────────────────────────────
    def _validate_table(self, d: dict) -> dict:
        rows = []
        for r in d.get("rows") or []:
            a, b = _item((r or {}).get("a")), _item((r or {}).get("b"))
            if a and b:
                rows.append({"tag": _s(r.get("tag"), 14), "a": a, "b": b})
        if len(rows) < TABLE_ROWS_NEEDED:
            raise ValueError(f"table: only {len(rows)} valid rows")
        tips = [_s(t, 90) for t in (d.get("tips") or []) if _s(t, 90)][:3]
        if len(tips) < 2:
            raise ValueError("table: missing tips")
        title = _s(d.get("title"), 60)
        if not title:
            raise ValueError("table: no title")
        return {"mode": "table", "title": title,
                "col_a_title": _s(d.get("col_a_title"), 30) or "A",
                "col_b_title": _s(d.get("col_b_title"), 30) or "B",
                "rows": rows[:TABLE_ROWS_ASKED], "tips": tips, **_meta(d)}

    def _validate_story(self, d: dict) -> dict:
        if not isinstance(d, dict):
            raise ValueError("story: response was not an object")
        img = _item(d.get("image"))
        story = re.sub(r"\s+", " ", str(d.get("story") or "")).strip()
        if not img or len(story.split()) < 20 or not _s(d.get("title"), 60):
            raise ValueError("story: incomplete")
        return {"mode": "story", "title": _s(d.get("title"), 60), "story": story[:600],
                "image": img, **_meta(d)}

    def _validate_puzzle(self, kind: str, d: dict) -> dict:
        opts = [_item(o) for o in (d.get("options") or [])]
        if len(opts) != 4 or any(o is None for o in opts):
            raise ValueError("puzzle: need 4 valid options")
        try:
            ans = int(d.get("answer_index"))
        except (TypeError, ValueError):
            raise ValueError("puzzle: bad answer_index")
        if not 0 <= ans <= 3:
            raise ValueError("puzzle: answer_index out of range")
        question = _s(d.get("question"), 90)
        if not question:
            raise ValueError("puzzle: no question")
        # shuffle so the answer is not biased toward one letter
        order = list(range(4))
        random.shuffle(order)
        opts = [opts[i] for i in order]
        ans = order.index(ans)
        return {"mode": "puzzle", "kind": kind, "title": _s(d.get("title"), 50) or "Puzzle",
                "question": question, "options": opts, "answer_index": ans,
                "explanation": _s(d.get("explanation"), 160), **_meta(d)}

    def _has_target_script(self, text: str) -> bool:
        if not text or not text.strip():
            return True
        if self.language.strip().lower() in ("english", "en"):
            return True
        # non-English target: at least one non-ASCII letter (Telugu, Hindi, Arabic, ...)
        return bool(re.search(r"[^\x00-\x7F]", text))

    def _visible_fields(self, c: dict) -> list[str]:
        out = [c.get("title", ""), c.get("caption", ""), c.get("yt_title", "")]
        if c["mode"] == "table":
            out += [c.get("col_a_title", ""), c.get("col_b_title", "")]
            for r in c.get("rows", []):
                out += [r.get("tag", ""), r["a"].get("label", ""), r["b"].get("label", "")]
            out += c.get("tips", [])
        elif c["mode"] == "story":
            out += [c.get("story", ""), c["image"].get("label", "")]
        else:
            out += [c.get("question", ""), c.get("explanation", "")]
            out += [o.get("label", "") for o in c.get("options", [])]
        out += c.get("hashtags", [])
        return [x for x in out if x and str(x).strip()]

    def _language_ok(self, c: dict) -> bool:
        if self.language.strip().lower() in ("english", "en"):
            return True
        fields = self._visible_fields(c)
        if not fields:
            return True
        good = sum(1 for f in fields if self._has_target_script(f))
        return good / len(fields) >= 0.6

    # ── public ─────────────────────────────────────────────────────────
    def generate(self, mode: str, topic: str, sub: str = "", attempts: int = 4) -> dict:
        kind = random.choice(["odd_one_out", "quiz"]) if mode == "puzzle" else ""
        last = None
        for _ in range(attempts):
            try:
                d = self.pool.chat_json(self._system(), self._prompt(mode, topic, sub, kind))
                if mode == "table":
                    result = self._validate_table(d)
                elif mode == "story":
                    result = self._validate_story(d)
                else:
                    result = self._validate_puzzle(kind, d)

                if not self._language_ok(result):
                    last = ValueError(f"visible text not in {self.language}")
                    continue
                return result
            except (ValueError, KeyError, AttributeError, TypeError) as e:
                last = e
        raise RuntimeError(f"Could not generate valid {mode} content in {self.language}: {last}")