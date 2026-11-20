import sys
import os

# Add the current directory to Python's path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

import base64
import html as htmllib
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components

from src.auth import require_login, get_client, sign_out, handle_auth_callback
from src.crypto_utils import encrypt_key
from src.poster_composer import TABLE_TEMPLATE, render_html, CUSTOM_STARTER

st.set_page_config(page_title="Shorts Agent", page_icon="🎬", layout="wide")

# --- HANDLE OAUTH REDIRECT ---
handle_auth_callback()
# ── 1. LOGIN GATE ── nothing below runs until a user is logged in
user = require_login()
sb = get_client()

# --- PRE-INITIALIZE VARIABLES TO PREVENT NameError ---
cfg = {}
res = sb.table("user_configs").select("*").eq("user_id", user["id"]).execute()
if res.data:
    cfg = res.data[0]

main_topic = cfg.get("main_topic", "")
sub_topics = cfg.get("sub_topics", "")
content_mode = cfg.get("content_mode", "table")
language = cfg.get("language", "English")
watermark = cfg.get("watermark") or ""
system_prompt = cfg.get("system_prompt") or ""
html_template = cfg.get("html_template") or ""
custom_python = cfg.get("custom_python") or ""
facebook_page_id = cfg.get("facebook_page_id") or ""
is_active = bool(cfg.get("is_active", False))
groq_key = pixabay_keys = pexels_key = youtube_token = facebook_token = ""
custom_num_images = int(cfg.get("custom_num_images") or 0)


def upload_branding_image(uploaded_file, platform: str) -> str | None:
    if uploaded_file is None:
        return None
    file_ext = uploaded_file.name.rsplit(".", 1)[-1].lower()
    allowed_extensions = {"png", "jpg", "jpeg", "webp"}
    if file_ext not in allowed_extensions:
        raise ValueError("Unsupported profile picture format.")
    file_path = f"{user['id']}/{platform}.{file_ext}"
    sb.storage.from_("branding").upload(
        path=file_path,
        file=uploaded_file.getvalue(),
        file_options={"content-type": uploaded_file.type, "upsert": "true"},
    )
    return sb.storage.from_("branding").get_public_url(file_path)


DEFAULT_SYSTEM_PROMPT = (
    "You create accurate, engaging short-form video content. "
    "Every image query must describe exactly what the label says."
)
DEFAULT_CUSTOM_PYTHON = '''def process(context):
    """Modify `context` before the poster is rendered. Return the dict."""
    return context
'''
MODES = {
    "table": "Table (14 images + labels in one poster)",
    "story": "Story (1 picture + short story about it)",
    "puzzle": "Puzzle / quiz (4 pictures, countdown, answer reveal)",
    "custom": "Custom (your own HTML template and image count)",
    "mixed": "Mixed (random mode each run)",
}


def saved(field: str) -> str:
    return "✅ saved" if cfg.get(field) else "❌ not set"


def placeholder(color: str) -> str:
    svg = f"<svg xmlns='http://www.w3.org/2000/svg' width='400' height='400'><rect width='400' height='400' fill='{color}'/></svg>"
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def mock_context(mode: str, num_images: int = 0) -> dict:
    cols = ["#e76f51", "#2a9d8f", "#e9c46a", "#457b9d", "#8d5a97", "#6a994e", "#bc4749", "#277da1"]
    if mode == "table":
        rows = [{"tag": f"Row {i + 1}", "a_label": f"Item A{i + 1}", "a_img": placeholder(cols[i % 8]),
                 "b_label": f"Item B{i + 1}", "b_img": placeholder(cols[(i + 3) % 8])} for i in range(7)]
        return {"title": "Sample title", "col_a_title": "Column A", "col_b_title": "Column B",
                "rows": rows, "tips": ["Tip one", "Tip two", "Tip three"], "watermark": "@yourchannel"}
    if mode == "story":
        return {"title": "Sample story", "story": "A short sample story about the picture. " * 4,
                "image": placeholder("#457b9d"), "image_label": "Main subject",
                "hashtags": ["#sample", "#story"], "watermark": "@yourchannel"}
    if mode == "puzzle":
        return {"title": "Sample puzzle", "question": "Which one does not belong?",
                "options": [{"label": f"Option {c}", "img": placeholder(cols[i])} for i, c in enumerate("ABCD")],
                "answer_index": 2, "explanation": "Because it is different.", "reveal": False,
                "hint": "⏳ A · B · C · D ?", "watermark": "@yourchannel"}

    # custom
    images = [placeholder(cols[i % len(cols)]) for i in range(num_images)]
    labels = [f"Label {i + 1}" for i in range(num_images)]
    return {
        "title": "Sample title", "text": "This is sample body text for your custom poster. " * 3,
        "caption": "Sample caption", "hashtags": ["#sample", "#custom"],
        "watermark": "@yourchannel", "images": images, "labels": labels,
    }


def show_preview(page_html: str):
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
    st.caption("Saved keys are encrypted and never shown again.")

st.title("🎬 Your Shorts Dashboard")
tab_topic, tab_keys, tab_template, tab_publish, tab_live = st.tabs(
    ["📌 Topic & Mode", "🔑 API Keys", "🎨 Template", "🚀 Publish & Status", "📡 Live Stream"])

# ── TAB 1: topic ──
with tab_topic:
    main_topic = st.text_input("Main topic", value=main_topic, placeholder="e.g. Healthy vs unhealthy breakfast")
    sub_topics = st.text_area("Sub-topics (comma or new-line separated)", value=sub_topics, height=140)

    mode_keys = list(MODES)
    cur_mode_idx = mode_keys.index(content_mode) if content_mode in mode_keys else 0
    content_mode = st.selectbox("Content mode", mode_keys, index=cur_mode_idx, format_func=lambda k: MODES[k])

    language = st.text_input("Label language", value=language, help="e.g. English, Telugu, Hindi")
    watermark = st.text_input("Watermark text", value=watermark)
    system_prompt = st.text_area("Extra instructions for the AI", value=system_prompt or DEFAULT_SYSTEM_PROMPT,
                                 height=110)

    st.subheader("📤 Upload Targets")
    col_yt, col_fb = st.columns(2)
    with col_yt:
        enable_youtube = st.toggle("Upload to YouTube", value=cfg.get("enable_youtube", True))
    with col_fb:
        enable_facebook = st.toggle("Upload to Facebook", value=cfg.get("enable_facebook", True))

# ── TAB 2: keys ──
with tab_keys:
    st.subheader("Content & images")
    groq_key = st.text_input(f"Groq API key(s) ({saved('groq_key')})", type="password")
    pixabay_keys = st.text_input(f"Pixabay key(s) ({saved('pixabay_keys')})", type="password")
    pexels_key = st.text_input(f"Pexels key, optional ({saved('pexels_key')})", type="password")
    st.subheader("YouTube")
    youtube_token = st.text_area(f"YouTube OAuth token JSON ({saved('youtube_token')})", height=100)
    st.subheader("Facebook Reels")
    facebook_page_id = st.text_input("Facebook Page ID", value=facebook_page_id)
    facebook_token = st.text_input(f"Facebook Page access token ({saved('facebook_token')})", type="password")

# ── TAB 3: template ──
with tab_template:
    st.subheader("🎨 Template Settings")

    # 1. SHOW HTML ONLY FOR RELEVANT MODES
    if content_mode == "table":
        st.info("💡 Table mode allows custom HTML.")
        use_custom = st.checkbox("Use my own HTML template for Table mode", value=bool(html_template))
        if use_custom:
            html_template = st.text_area(
                "HTML template (Jinja). Variables: title, col_a_title, rows, tips, watermark.",
                value=html_template or TABLE_TEMPLATE, height=380)
        else:
            html_template = None

    elif content_mode == "custom":
        st.info("💡 Custom mode requires your own HTML template.")
        custom_num_images = st.number_input("Number of images your template needs (0 = text-only)", min_value=0,
                                            max_value=20, value=custom_num_images)
        html_template = st.text_area(
            "HTML template (Jinja). Variables: title, text, caption, hashtags, watermark, images, labels.",
            value=html_template or CUSTOM_STARTER, height=380)

    else:
        st.info(f"ℹ️ {content_mode.capitalize()} mode uses the built-in design. No HTML needed.")
        html_template = None
        custom_num_images = 0

    st.divider()

    # 2. SHOW PYTHON FOR ALL MODES
    st.subheader("🐍 Custom Python — all modes")
    use_python = st.checkbox("Use my own Python code", value=bool(custom_python))
    if use_python:
        custom_python = st.text_area(
            "Python — must define `process(context) -> dict`",
            value=custom_python or DEFAULT_CUSTOM_PYTHON, height=300)
    else:
        custom_python = None

    st.divider()

    # 3. SHOW PREVIEW
    st.subheader("👁️ Preview")
    # Only show preview buttons for modes that actually use this tab
    if content_mode in ["table", "custom"]:
        if st.button("Show preview"):
            try:
                custom_html = html_template if html_template else None
                show_preview(render_html(
                    content_mode, mock_context(content_mode, custom_num_images),
                    custom_template=custom_html,
                    custom_python=custom_python,
                ))
            except Exception as e:
                st.error(f"Template error: {e}")
    else:
        st.caption("Switch to Table or Custom mode to preview templates here.")

# ── TAB 4: publish ──
with tab_publish:
    is_active = st.toggle("Run my automation", value=is_active)
    st.caption("Each run picks one random sub-topic and makes one video.")
    if cfg.get("last_run_at"):
        st.info(f"Last run: {cfg['last_run_at']}\n\n{cfg.get('last_status') or ''}")

# ── TAB 5: live stream ──
with tab_live:
    st.caption("Live stream settings here...")

# ── SAVE ──
st.divider()
if st.button("💾 Save settings", type="primary", use_container_width=True):
    row = {
        "user_id": user["id"], "email": user["email"],
        "main_topic": main_topic.strip(), "sub_topics": sub_topics.strip(),
        "content_mode": content_mode, "language": language.strip() or "English",
        "watermark": watermark.strip() or None,
        "system_prompt": system_prompt,
        "html_template": html_template,
        "custom_python": custom_python,
        "facebook_page_id": facebook_page_id.strip() or None,
        "is_active": is_active,
        "enable_youtube": enable_youtube,
        "enable_facebook": enable_facebook,
        "custom_num_images": int(custom_num_images or 0),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    for field, value in [("groq_key", groq_key), ("pixabay_keys", pixabay_keys), ("pexels_key", pexels_key),
                         ("youtube_token", youtube_token), ("facebook_token", facebook_token)]:
        if value.strip():
            row[field] = encrypt_key(value.strip())

    try:
        sb.table("user_configs").upsert(row, on_conflict="user_id").execute()
        st.success("Saved successfully!")
        st.rerun()
    except Exception as e:
        st.error(f"Could not save: {e}")