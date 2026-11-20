"""Small Groq wrapper: several API keys, rate-limit rotation, JSON parsing, vision picks."""
import json
import os
import re
import time

import groq as groq_mod
from groq import Groq

# Model names change over time, so they can be overridden with env vars.
TEXT_MODEL = os.environ.get("GROQ_TEXT_MODEL", "openai/gpt-oss-120b")
VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(0))
            if isinstance(result, dict):
                return result
        except Exception:
            pass
    raise ValueError("model did not return a valid JSON object")

class GroqPool:
    def __init__(self, keys: str):
        self.keys = [k.strip() for k in (keys or "").split(",") if k.strip()]
        if not self.keys:
            raise ValueError("no Groq API key")
        self.clients = [Groq(api_key=k) for k in self.keys]
        self.i = 0

    def _call(self, fn):
        last = None
        for attempt in range(len(self.clients) * 3):
            client = self.clients[self.i % len(self.clients)]
            try:
                return fn(client)
            except groq_mod.RateLimitError as e:
                last = e
                self.i += 1                       # try the next key
                time.sleep(min(2 * (attempt + 1), 15))
            except (groq_mod.APIConnectionError, groq_mod.InternalServerError) as e:
                last = e
                time.sleep(2)
        raise RuntimeError(f"Groq request failed after retries: {type(last).__name__}")

    def chat_json(self, system: str, user: str, temperature: float = 0.7,
                  max_tokens: int = 3500) -> dict:
        def go(c):
            r = c.chat.completions.create(
                model=TEXT_MODEL,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return r.choices[0].message.content
        return parse_json(self._call(go))

    def vision_pick(self, prompt: str, images_b64: list[str]) -> int:
        """Returns the 0-based index of the best matching image, or -1 for none."""
        content = [{"type": "text", "text": prompt}]
        for b in images_b64:
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b}"}})

        def go(c):
            r = c.chat.completions.create(
                model=VISION_MODEL,
                messages=[{"role": "user", "content": content}],
                temperature=0,
                max_tokens=40,
            )
            return r.choices[0].message.content
        out = self._call(go) or ""
        m = re.search(r'"best"\s*:\s*(-?\d+)', out) or re.search(r"-?\d+", out)
        if not m:
            return -1
        n = int(m.group(1) if m.lastindex else m.group(0))
        return n - 1 if 1 <= n <= len(images_b64) else -1
