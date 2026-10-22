
import json

from .groq_pool import GroqPool


def find_mismatches(pool: GroqPool, language: str, items: list[dict]) -> set[int]:
    """Return indexes whose labels do not match their English image queries."""
    if not items:
        return set()

    listing = "\n".join(
        f'{i}. label="{item.get("label", "")}" | '
        f'image="{item.get("query", "")}"'
        for i, item in enumerate(items)
    )

    system = (
        "You are a careful image-label matching reviewer. "
        f"The labels may be written in {language or 'English'}. "
        "Compare each label with its English image query. "
        "Return an item as bad ONLY when they clearly refer to different "
        "objects, animals, foods, people, or important varieties. "
        "Accept translations, synonyms, common names, and equivalent wording. "
        "Do not reject an item merely because the wording differs or the "
        "label is shorter than the query. "
        'Return ONLY valid JSON in this format: {"bad": [0, 2]}. '
        "If all items match, return {\"bad\": []}."
    )

    try:
        result = pool.chat_json(
            system,
            listing,
            temperature=0,
            max_tokens=300,
        )

        bad = result.get("bad", [])
        if not isinstance(bad, list):
            return set()

        return {
            int(index)
            for index in bad
            if str(index).isdigit() and 0 <= int(index) < len(items)
        }
    except Exception:
        # If the reviewer fails, do not reject content based on an unavailable review.
        return set()
