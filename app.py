import sys
import os

# Add the current directory to Python's path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

import base64
import html as htmllib
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components


from src.auth import require_login, get_client, sign_out
from src.crypto_utils import encrypt_key
from src.poster_composer import TABLE_TEMPLATE, render_html
st.set_page_config(page_title="Shorts Agent", page_icon="🎬", layout="wide")

# ── 1. LOGIN GATE ── nothing below runs until a user is logged in
user = require_login()
sb = get_client()

DEFAULT_SYSTEM_PROMPT = (
    "You create accurate, engaging short-form video content. "
    "Every image query must describe exactly what the label says."
)
MODES = {
    "table": "Table (14 images + labels in one poster)",
    "story": "Story (1 picture + short story about it)",
    "puzzle": "Puzzle / quiz (4 pictures, countdown, answer reveal)",
    "mixed": "Mixed (random mode each run)",
}


def load_config() -> dict:
    res = sb.table("user_configs").select("*").eq("user_id", user["id"]).execute()
    return res.data[0] if res.data else {}


cfg = load_config()


def saved(field: str) -> str:
    return "✅ saved" if cfg.get(field) else "❌ not set"


def placeholder(color: str) -> str:
    svg = f"<svg xmlns='http://www.w3.org/2000/svg' width='400' height='400'><rect width='400' height='400' fill='{color}'/></svg>"
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def mock_context(mode: str) -> dict:
    cols = ["#e76f51", "#2a9d8f", "#e9c46a", "#457b9d", "#8d5a97", "#6a994e", "#bc4749", "#277da1"]
    if mode == "table":
        rows = [{"tag": f"Row {i+1}", "a_label": f"Item A{i+1}", "a_img": placeholder(cols[i % 8]),
                 "b_label": f"Item B{i+1}", "b_img": placeholder(cols[(i + 3) % 8])} for i in range(7)]
        return {"title": "Sample title", "col_a_title": "Column A", "col_b_title": "Column B",
                "rows": rows, "tips": ["Tip one", "Tip two", "Tip three"], "watermark": "@yourchannel"}
    if mode == "story":
        return {"title": "Sample story", "story": "A short sample story about the picture. " * 4,
                "image": placeholder("#457b9d"), "image_label": "Main subject",
                "hashtags": ["#sample", "#story"], "watermark": "@yourchannel"}
    return {"title": "Sample puzzle", "question": "Which one does not belong?",
            "options": [{"label": f"Option {c}", "img": placeholder(cols[i])} for i, c in enumerate("ABCD")],
            "answer_index": 2, "explanation": "Because it is different.", "reveal": False,
            "hint": "⏳ A · B · C · D ?", "watermark": "@yourchannel"}


def show_preview(page_html: str):
    """Scaled, script-free preview of a 1080x1920 poster."""
    src = htmllib.escape(page_html, quote=True)
    components.html(
        f"<div style='width:360px;height:640px;overflow:hidden;border:1px solid #888;border-radius:8px'>"
        f"<iframe sandbox='' srcdoc=\"{src}\" style='width:1080px;height:1920px;border:0;"
        f"transform:scale(.3333);transform-origin:top left'></iframe></div>",
        height=660, width=380)


# ── 2. SIDEBAR ──
with st.sidebar:
    st.markdown(f"**Logged in as**  \n{user['email']}")
    if st.button("Log out", use_container_width=True):
        sign_out()
        st.rerun()
    st.divider()
    st.caption("Saved keys are encrypted and never shown again. Leave a key box empty to keep the saved one.")

st.title("🎬 Your Shorts Dashboard")
tab_topic, tab_keys, tab_template, tab_publish = st.tabs(
    ["📌 Topic & Mode", "🔑 API Keys", "🎨 Template", "🚀 Publish & Status"])

# ── TAB 1: topic ──
with tab_topic:
    main_topic = st.text_input("Main topic", value=cfg.get("main_topic", ""),
                               placeholder="e.g. Healthy vs unhealthy breakfast")
    sub_topics = st.text_area(
        "Sub-topics (comma or new-line separated, any number — one is picked at random each run)",
        value=cfg.get("sub_topics", ""), height=140)
    count = len([s for s in sub_topics.replace("\n", ",").split(",") if s.strip()])
    st.caption(f"{count} sub-topic(s) entered.")

    mode_keys = list(MODES)
    cur = cfg.get("content_mode", "table")
    content_mode = st.selectbox("Content mode", mode_keys,
                                index=mode_keys.index(cur) if cur in mode_keys else 0,
                                format_func=lambda k: MODES[k])
    language = st.text_input("Label language", value=cfg.get("language", "English"),
                             help="e.g. English, Telugu, Hindi")
    st.subheader("YouTube Branding")

    youtube_profile_pic = st.file_uploader(
        "YouTube profile picture",
        type=["png", "jpg", "jpeg", "webp"],
        key="youtube_profile_pic_upload"
    )

    if cfg.get("youtube_profile_pic_url"):
        st.image(cfg["youtube_profile_pic_url"], width=120)

    st.subheader("Facebook Branding")

    facebook_profile_pic = st.file_uploader(
        "Facebook Page profile picture",
        type=["png", "jpg", "jpeg", "webp"],
        key="facebook_profile_pic_upload"
    )

    if cfg.get("facebook_profile_pic_url"):
        st.image(cfg["facebook_profile_pic_url"], width=120)
    system_prompt = st.text_area("Extra instructions for the AI", value=cfg.get("system_prompt") or DEFAULT_SYSTEM_PROMPT,
                                 height=110)

# ── TAB 2: keys ──
with tab_keys:
    st.subheader("Content & images")
    groq_key = st.text_input(f"Groq API key(s), comma separated  ({saved('groq_key')})", type="password")
    pixabay_keys = st.text_input(f"Pixabay key(s), comma separated  ({saved('pixabay_keys')})", type="password")
    pexels_key = st.text_input(f"Pexels key, optional  ({saved('pexels_key')})", type="password")
    st.subheader("YouTube")
    youtube_token = st.text_area(
        f"YouTube OAuth token JSON  ({saved('youtube_token')})", height=100,
        help="Create it once on your computer with scripts/get_youtube_token.py, then paste the JSON here.")
    st.subheader("Facebook Reels (optional)")
    facebook_page_id = st.text_input("Facebook Page ID", value=cfg.get("facebook_page_id") or "")
    facebook_token = st.text_input(f"Facebook Page access token  ({saved('facebook_token')})", type="password")

# ── TAB 3: template ──
with tab_template:
    st.caption("Story and Puzzle always use the built-in designs. Table mode can use your own HTML.")
    use_custom = st.checkbox("Use my own HTML template for Table mode", value=bool(cfg.get("html_template")))
    html_template = None
    if use_custom:
        html_template = st.text_area(
            "HTML template (Jinja). Variables: title, col_a_title, col_b_title, rows "
            "(tag, a_label, a_img, b_label, b_img), tips, watermark. No JavaScript runs.",
            value=cfg.get("html_template") or TABLE_TEMPLATE, height=380)
    pv_mode = st.radio("Preview", ["table", "story", "puzzle"], horizontal=True)
    if st.button("Show preview"):
        try:
            custom = html_template if (pv_mode == "table" and use_custom) else None
            show_preview(render_html(pv_mode, mock_context(pv_mode), custom))
        except Exception as e:
            st.error(f"Template error: {e}")

# ── TAB 4: publish ──
with tab_publish:
    is_active = st.toggle("Run my automation (included in every scheduled run)",
                          value=bool(cfg.get("is_active", False)))
    st.caption("Each run picks one random sub-topic and makes one 15-second video.")
    if cfg.get("last_run_at"):
        st.info(f"Last run: {cfg['last_run_at']}\n\n{cfg.get('last_status') or ''}")
    else:
        st.caption("No run yet.")

# ── SAVE ──
st.divider()
if st.button("💾 Save settings", type="primary", use_container_width=True):
    row = {
        "user_id": user["id"], "email": user["email"],
        "main_topic": main_topic.strip(), "sub_topics": sub_topics.strip(),
        "content_mode": content_mode, "language": language.strip() or "English",
        "watermark": watermark.strip() or None,
        "system_prompt": system_prompt, "html_template": html_template,
        "facebook_page_id": facebook_page_id.strip() or None,
        "is_active": is_active,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    for field, value in [("groq_key", groq_key), ("pixabay_keys", pixabay_keys), ("pexels_key", pexels_key),
                         ("youtube_token", youtube_token), ("facebook_token", facebook_token)]:
        if value.strip():
            row[field] = encrypt_key(value.strip())
    try:
        sb.table("user_configs").upsert(row, on_conflict="user_id").execute()
        st.success("Saved.")
        st.rerun()
    except Exception as e:
        st.error(f"Could not save: {e}")
