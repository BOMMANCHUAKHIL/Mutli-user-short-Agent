"""Multi-user worker run by GitHub Actions: one random sub-topic and one video per active user."""
import os
import random
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

from src.crypto_utils import decrypt_key
from src.orchestrator import ShortsOrchestrator


def pick_prompt(sub_topics: str) -> str:
    """Pick ONE random sub-topic from a comma-separated list of any length."""
    subs = [s.strip() for s in (sub_topics or "").replace("\n", ",").split(",") if s.strip()]
    return random.choice(subs) if subs else ""


def should_run_now(user: dict) -> bool:
    """Check whether a user's scheduled posting time is due."""

    email = user.get("email", "Unknown")
    tz_str = (user.get("timezone") or "Asia/Kolkata").strip()

    try:
        user_tz = ZoneInfo(tz_str)
    except Exception as e:
        print(f"[{email}] Invalid timezone {tz_str}: {e}")
        return False

    now_local = datetime.now(user_tz)

    times_str = user.get("posting_times") or "06:00,12:00,20:00,21:00"

    last_run = user.get("last_run_at")

    last_run_local = None

    if last_run:
        try:
            last_run_dt = datetime.fromisoformat(
                str(last_run).replace("Z", "+00:00")
            )

            if last_run_dt.tzinfo is None:
                last_run_dt = last_run_dt.replace(tzinfo=timezone.utc)

            last_run_local = last_run_dt.astimezone(user_tz)

        except Exception as e:
            print(f"[{email}] Could not parse last_run_at: {e}")

    for t in times_str.split(","):
        t = t.strip()

        try:
            hour, minute = map(int, t.split(":"))

            scheduled = now_local.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0
            )

            # Allow execution up to 65 minutes after scheduled time.
            if not (scheduled <= now_local < scheduled + timedelta(minutes=65)):
                continue

            # Prevent multiple uploads for the same scheduled slot.
            if last_run_local and scheduled <= last_run_local < scheduled + timedelta(minutes=65):
                print(f"[{email}] Already processed {t}. Skipping.")
                continue

            print(
                f"[{email}] Local time: {now_local.strftime('%Y-%m-%d %H:%M')} "
                f"| Scheduled: {t} | RUN"
            )

            return True

        except (ValueError, TypeError):
            print(f"[{email}] Invalid posting time: {t}")

    print(
        f"[{email}] Local time: {now_local.strftime('%Y-%m-%d %H:%M')} "
        f"| Schedule: {times_str} | SKIP"
    )

    return False


def run_multi_user_cycle():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is missing.")
        return

    sb = create_client(url, key)
    users = sb.table("user_configs").select("*").eq("is_active", True).execute().data
    print(f"Found {len(users)} active user(s).")

    def status(user, text):
        try:
            sb.table("user_configs").update({
                "last_status": text[:300],
                "last_run_at": datetime.now(timezone.utc).isoformat(),
            }).eq("user_id", user["user_id"]).execute()
        except Exception as e:
            print("  (could not save status:", str(e)[:100], ")")

    for user in users:
        print(f"\n--- {user['email']} ---")

        if not should_run_now(user):
            continue

        groq = decrypt_key(user.get("groq_key"))
        pixabay = decrypt_key(user.get("pixabay_keys")) or ""
        pexels = decrypt_key(user.get("pexels_key"))
        yt = decrypt_key(user.get("youtube_token"))
        fb_token = decrypt_key(user.get("facebook_token"))
        fb_page = user.get("facebook_page_id")
        topic = (user.get("main_topic") or "").strip()

        # --- CHECK YOUTUBE RESTRICTION ---
        restricted_until = user.get("youtube_restricted_until")
        if restricted_until:
            try:
                r_time = datetime.fromisoformat(str(restricted_until).replace("Z", "+00:00"))
                if datetime.now(timezone.utc) < r_time:
                    print(f"YouTube restricted until {r_time}. Skipping YouTube upload.")
                    yt = None # Disable YouTube for this run
            except Exception as e:
                print("Error parsing restriction time:", e)

        if not groq or not topic:
            status(user, "Skipped: Groq key and main topic are required.")
            continue
        if not yt and not (fb_page and fb_token):
            status(user, "Skipped: add a YouTube token or Facebook page + token.")
            continue

        sub = pick_prompt(user.get("sub_topics"))
        print(f"Topic: {topic} | sub-topic: {sub or '(none)'} | mode: {user.get('content_mode')}")

        try:
            orch = ShortsOrchestrator(
                groq_key=groq, pixabay_keys=pixabay, pexels_key=pexels,
                topic=topic, subtopic=sub,
                language=user.get("language") or "English",
                mode=user.get("content_mode") or "table",
                system_prompt=user.get("system_prompt"),
                html_template=user.get("html_template"),
                youtube_token=yt, facebook_page_id=fb_page, facebook_token=fb_token,
                watermark=user.get("watermark") or "",
                youtube_profile_pic=user.get("youtube_profile_pic_url"),
                facebook_profile_pic=user.get("facebook_profile_pic_url"),
                youtube_watermark=user.get("youtube_watermark") or "",
                facebook_watermark=user.get("facebook_watermark") or "",
                enable_youtube=user.get("enable_youtube", True),
                enable_facebook=user.get("enable_facebook", True),
            )

            r = orch.run_cycle()

            # --- IF RESTRICTED, SAVE 1 DAY COOLDOWN ---
            if r.get("youtube_restricted"):
                restricted_time = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
                sb.table("user_configs").update({"youtube_restricted_until": restricted_time}).eq("user_id", user["user_id"]).execute()
                print("YouTube restricted. Pausing YouTube uploads for 1 day.")

            msg = f"OK {r['mode']}: {r['youtube_url'] or ''} {r['facebook_video_id'] or ''}".strip()
            if r["errors"]:
                msg += " | partial: " + "; ".join(r["errors"])
            print("✓", msg)
            status(user, msg)

        except Exception as e:
            print("✗ failed:", str(e)[:300])
            status(user, f"Failed: {str(e)[:250]}")


if __name__ == "__main__":
    run_multi_user_cycle()