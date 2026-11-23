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

# ── 1. LOGIN GATE ──
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

# Initialize branding & schedule variables
youtube_profile_pic_url = cfg.get("youtube_profile_pic_url")
facebook_profile_pic_url = cfg.get("facebook_profile_pic_url")
if youtube_profile_pic_url:
    st.markdown("**YouTube Profile Picture Preview**")
    st.image(
        youtube_profile_pic_url,
        width=150,
        caption="YouTube Profile Picture",
    )
else:
    st.info("No YouTube profile picture uploaded.")

if facebook_profile_pic_url:
    st.markdown("**Facebook Profile Picture Preview**")
    st.image(
        facebook_profile_pic_url,
        width=150,
        caption="Facebook Profile Picture",
    )
else:
    st.info("No Facebook profile picture uploaded.")
youtube_watermark = cfg.get("youtube_watermark") or ""
facebook_watermark = cfg.get("facebook_watermark") or ""
user_timezone = cfg.get("timezone", "Asia/Kolkata")
posting_times = cfg.get("posting_times", "06:00,12:00,19:00,21:00")
live_enabled = bool(cfg.get("live_enabled", False))
live_times = cfg.get("live_times") or "20:00"
live_duration_min = int(cfg.get("live_duration_min") or 120)
live_title = cfg.get("live_title") or ""
live_privacy = cfg.get("live_privacy") or "public"
live_layout = cfg.get("live_layout") or "landscape"


# -----------------------------------------------------

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


def valid_times(text: str) -> bool:
    parts = [t.strip() for t in (text or "").split(",") if t.strip()]
    if not parts: return False
    for t in parts:
        try:
            h, m = map(int, t.split(":"))
        except ValueError:
            return False
        if not (0 <= h <= 23 and 0 <= m <= 59): return False
    return True


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
    st.caption("Saved keys are encrypted and never shown again. Leave a key box empty to keep the saved one.")

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

    st.subheader("YouTube Branding")
    youtube_profile_pic = st.file_uploader("YouTube profile picture", type=["png", "jpg", "jpeg", "webp"], key="yt_pic")
    if youtube_profile_pic_url:
        st.image(youtube_profile_pic_url, width=120)
    youtube_watermark = st.text_input("YouTube watermark / Channel handle", value=youtube_watermark,
                                      placeholder="@KathaZap")

    st.subheader("Facebook Branding")
    facebook_profile_pic = st.file_uploader("Facebook Page profile picture", type=["png", "jpg", "jpeg", "webp"],
                                            key="fb_pic")
    if facebook_profile_pic_url:
        st.image(facebook_profile_pic_url, width=120)
    facebook_watermark = st.text_input("Facebook watermark / Page name", value=facebook_watermark,
                                       placeholder="KathaZap")

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
    use_custom = False

    # Conditional UI based on current content mode
    if content_mode == "table":
        st.subheader("🎨 Custom HTML — Table mode only")

        use_custom = st.checkbox(
            "Use my own HTML template for Table mode",
            value=bool(html_template),
            key="use_custom_table_html"
        )

        if use_custom:
            html_template = st.text_area(
                "HTML template (Jinja). Variables: title, col_a_title, rows, tips, watermark.",
                value=html_template or TABLE_TEMPLATE,
                height=380,
                key="table_html_template"
            )
        else:
            html_template = None

    elif content_mode == "custom":
        st.subheader("🧩 Custom template")

        st.markdown(
            "Variables received: `title`, `text`, `caption`, "
            "`hashtags`, `watermark`, `images`, `labels`."
        )

        custom_num_images = st.number_input(
            "Number of images your template needs (0 = text-only)",
            min_value=0,
            max_value=20,
            value=custom_num_images,
            key="custom_template_image_count"
        )

        html_template = st.text_area(
            "HTML template (Jinja)",
            value=html_template or CUSTOM_STARTER,
            height=380,
            key="custom_html_template"
        )

    else:
        html_template = None
        st.info(
            f"ℹ️ {content_mode.capitalize()} mode uses "
            "the built-in design. No custom HTML needed here."
        )

    st.divider()

    # Custom Python — all modes
    st.subheader("🐍 Custom Python — all modes")

    use_python = st.checkbox(
        "Use my own Python code",
        value=bool(custom_python),
        key="use_custom_python"
    )

    if use_python:
        custom_python = st.text_area(
            "Python — must define process(context) -> dict",
            value=custom_python or DEFAULT_CUSTOM_PYTHON,
            height=300,
            key="custom_python_code"
        )
    else:
        custom_python = None

    st.divider()

    # Preview
    st.subheader("👁️ Preview Poster")

    pv_modes = ["table", "story", "puzzle", "custom"]

    pv_mode = st.radio(
        "Preview Mode",
        pv_modes,
        horizontal=True,
        index=pv_modes.index(content_mode)
        if content_mode in pv_modes else 0
    )

    if st.button("Show preview"):
        try:
            is_active_preview = (pv_mode == content_mode)

            custom_html = (
                html_template
                if is_active_preview
                and pv_mode in ["table", "custom"]
                else None
            )

            python_code = custom_python if is_active_preview else None

            show_preview(
                render_html(
                    pv_mode,
                    mock_context(pv_mode, int(custom_num_images)),
                    custom_template=custom_html,
                    custom_python=python_code
                )
            )

        except Exception as e:
            st.error(f"Template error: {e}")


# ── TAB 4: publish ──
with tab_publish:
    is_active = st.toggle("Run my automation", value=is_active)
    st.subheader("⏰ Upload Schedule")

    tz_options = ["Asia/Kolkata", "UTC", "America/New_York", "Europe/London", "Asia/Dubai"]
    user_timezone = st.selectbox("Your Timezone", tz_options,
                                 index=tz_options.index(user_timezone) if user_timezone in tz_options else 0)

    posting_times = st.text_input("Upload Times (comma separated, 24-hour format)", value=posting_times,
                                  placeholder="06:00, 12:00, 19:00, 21:00")
    st.caption("The system runs hourly and will upload if the current hour matches one of these times.")

    if cfg.get("last_run_at"):
        st.info(f"Last run: {cfg['last_run_at']}\n\n{cfg.get('last_status') or ''}")
    else:
        st.caption("No run yet.")

# ── TAB 5: live stream ──
with tab_live:
    st.subheader("📡 Scheduled YouTube Live")
    st.caption("Loops your latest 3 generated videos as a live stream on your channel.")

    live_enabled = st.toggle("Enable scheduled live stream", value=live_enabled)
    live_times = st.text_input("Go-live time(s) — 24-hour format, comma separated", value=live_times,
                               placeholder="20:00, 09:30")
    st.caption("A checker runs every 15 minutes, so the stream starts 0–30 minutes after the time you set.")

    if live_enabled and not valid_times(live_times):
        st.error("Use HH:MM, for example 20:00 or 09:30, 21:00")

    live_duration_min = st.slider("Stream length (minutes)", 30, 180, live_duration_min, step=15)
    live_title = st.text_input("Live title (optional)", value=live_title,
                               placeholder="{topic} • Live Shorts Loop • {date}")

    lc1, lc2 = st.columns(2)
    privacy_opts = ["public", "unlisted", "private"]
    layout_opts = ["landscape", "vertical"]
    with lc1:
        live_privacy = st.selectbox("Privacy (use unlisted to test)", privacy_opts,
                                    index=privacy_opts.index(live_privacy) if live_privacy in privacy_opts else 0)
    with lc2:
        live_layout = st.selectbox("Layout", layout_opts,
                                   index=layout_opts.index(live_layout) if live_layout in layout_opts else 0,
                                   format_func=lambda k: "16:9 landscape" if k == "landscape" else "9:16 vertical")

    if cfg.get("live_status"):
        st.info(f"Live status: {cfg['live_status']}")
    if cfg.get("live_url"):
        st.markdown(f"Last live: {cfg['live_url']}")

# ── SAVE ──
st.divider()
if st.button("💾 Save settings", type="primary", use_container_width=True):
    try:
        if live_enabled and not valid_times(live_times):
            raise ValueError("Live go-live time must look like 20:00 (or 09:30, 21:00).")

        # Upload new images if provided
        if youtube_profile_pic is not None:
            youtube_profile_pic_url = upload_branding_image(youtube_profile_pic, "youtube")
        if facebook_profile_pic is not None:
            facebook_profile_pic_url = upload_branding_image(facebook_profile_pic, "facebook")

        row = {
            "user_id": user["id"], "email": user["email"],
            "main_topic": main_topic.strip(), "sub_topics": sub_topics.strip(),
            "content_mode": content_mode, "language": language.strip() or "English",
            "system_prompt": system_prompt,
            "html_template": html_template if content_mode in ["table", "custom"] else None,
            "custom_python": custom_python,
            "facebook_page_id": facebook_page_id.strip() or None,
            "is_active": is_active,
            "enable_youtube": enable_youtube,
            "enable_facebook": enable_facebook,
            "custom_num_images": int(custom_num_images or 0),

            # Schedule
            "timezone": user_timezone,
            "posting_times": posting_times,

            # Branding
            "youtube_profile_pic_url": youtube_profile_pic_url,
            "youtube_watermark": youtube_watermark.strip() or None,
            "facebook_profile_pic_url": facebook_profile_pic_url,
            "facebook_watermark": facebook_watermark.strip() or None,

            # Live
            "live_enabled": live_enabled,
            "live_times": live_times.strip() or "20:00",
            "live_duration_min": int(live_duration_min),
            "live_title": live_title.strip() or None,
            "live_privacy": live_privacy,
            "live_layout": live_layout,

            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        for field, value in [("groq_key", groq_key), ("pixabay_keys", pixabay_keys), ("pexels_key", pexels_key),
                             ("youtube_token", youtube_token), ("facebook_token", facebook_token)]:
            if value.strip():
                row[field] = encrypt_key(value.strip())

        sb.table("user_configs").upsert(row, on_conflict="user_id").execute()
        st.success("Settings and branding images saved successfully!")
        st.rerun()
    except Exception as e:
        st.error(f"Could not save settings: {e}")