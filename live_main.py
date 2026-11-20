"""Live-stream worker (separate from main.py, which only makes Shorts).

  python live_main.py plan           prints a JSON list of user_ids whose go-live time is due
                                     (also writes it to $GITHUB_OUTPUT as `users=`)
  python live_main.py run <user_id>  streams for that user (blocks for the whole broadcast)

Set FORCE_USER=<user_id> before `plan` to skip the schedule check (manual test run).
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import traceback
from dotenv import load_dotenv
from googleapiclient.errors import HttpError
from supabase import create_client

load_dotenv()

from src.crypto_utils import decrypt_key, encrypt_key
from src.live_streamer import LiveScopeError, explain_http_error, run_live
from src.video_store import latest_videos

WINDOW_MIN = int(os.environ.get("LIVE_WINDOW_MIN", "30"))   # how late after the slot we may still start
CLIPS_PER_LOOP = 3
DEFAULT_TITLE = "{topic} • Live Shorts Loop • {date}"


def _sb():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is missing.")
    return create_client(url, key)


def _dt(value):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def is_due(user: dict) -> bool:
    email = user.get("email", "?")
    try:
        tz = ZoneInfo((user.get("timezone") or "Asia/Kolkata").strip())
    except Exception:
        print(f"[{email}] invalid timezone")
        return False

    lock = _dt(user.get("live_lock_until"))
    if lock and lock > datetime.now(timezone.utc):
        print(f"[{email}] already queued/live. SKIP")
        return False

    now = datetime.now(tz)
    last = _dt(user.get("last_live_at"))
    last_local = last.astimezone(tz) if last else None

    for t in (user.get("live_times") or "").split(","):
        try:
            hh, mm = map(int, t.strip().split(":"))
            slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        except ValueError:
            continue
        if not (slot <= now < slot + timedelta(minutes=WINDOW_MIN)):
            continue
        if last_local and last_local >= slot:        # this slot was already streamed
            print(f"[{email}] slot {t} already done. SKIP")
            continue
        print(f"[{email}] slot {t} due. GO")
        return True
    print(f"[{email}] not due ({now:%H:%M}, times: {user.get('live_times')})")
    return False


def plan() -> None:
    sb = _sb()
    forced = os.environ.get("FORCE_USER", "").strip()
    ids: list[str] = []
    if forced:
        ids = [forced]
    else:
        rows = sb.table("user_configs").select("*").eq("live_enabled", True).execute().data
        for u in rows:
            if not is_due(u):
                continue
            minutes = int(u.get("live_duration_min") or 120) + 20
            lock = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
            # Lock now so the next 15-minute checker cannot start the same stream twice.
            sb.table("user_configs").update(
                {"live_lock_until": lock, "live_status": "Queued"}
            ).eq("user_id", u["user_id"]).execute()
            ids.append(u["user_id"])
    out = json.dumps(ids)
    print("Due users:", out)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"users={out}\n")


def run(user_id: str) -> None:
    sb = _sb()
    rows = sb.table("user_configs").select("*").eq("user_id", user_id).execute().data
    if not rows:
        sys.exit(f"No config for user {user_id}")
    user = rows[0]

    def upd(**fields):
        try:
            sb.table("user_configs").update(fields).eq("user_id", user_id).execute()
        except Exception as e:
            print("  (could not save status:", str(e)[:100], ")")

    ok = False
    try:
        token = decrypt_key(user.get("youtube_token"))
        if not token:
            raise RuntimeError("No YouTube token saved.")

        workdir = Path("output/live") / user_id
        clips = latest_videos(sb, user_id, workdir / "clips", CLIPS_PER_LOOP)
        if not clips:
            raise RuntimeError("No stored videos yet. Wait for the next Shorts run (with live enabled).")
        print(f"Using {len(clips)} clip(s): {[c.name for c in clips]}")

        try:
            tz = ZoneInfo((user.get("timezone") or "Asia/Kolkata").strip())
        except Exception:
            tz = timezone.utc
        topic = (user.get("main_topic") or "Shorts").strip()
        title = (user.get("live_title") or DEFAULT_TITLE).replace("{topic}", topic) \
            .replace("{date}", datetime.now(tz).strftime("%d %b %Y"))
        description = f"Non-stop {topic} shorts, streaming live. Subscribe for more!"

        def went_live(url):
            upd(last_live_at=datetime.now(timezone.utc).isoformat(), live_url=url,
                live_status=f"LIVE since {datetime.now(timezone.utc):%H:%M} UTC")

        res = run_live(
            token, clips, title, description,
            duration_min=int(user.get("live_duration_min") or 120),
            privacy=user.get("live_privacy") or "public",
            layout=user.get("live_layout") or "landscape",
            workdir=workdir, on_live=went_live,
        )
        if res.get("token_json"):
            upd(youtube_token=encrypt_key(res["token_json"]))
            print("YouTube OAuth token refreshed and saved.")
        upd(live_status=f"Finished OK: {res['url']}")
        ok = True



    except LiveScopeError as e:

        print("LIVE SCOPE ERROR:", repr(e))

        traceback.print_exc()

        upd(live_status=f"Failed: {str(e)[:300]}")


    except HttpError as e:

        print("YOUTUBE HTTP ERROR:", repr(e))

        print("DETAILS:", explain_http_error(e))

        traceback.print_exc()

        upd(live_status=f"Failed: {explain_http_error(e)}"[:300])




    except Exception as e:

        traceback.print_exc()

        error_message = f"Failed: {str(e)[:250]}"

        upd(live_status=error_message)

        print(f"✗ Live streaming failed: {repr(e)}")


    finally:

        upd(live_lock_until=None)

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "plan":
        plan()
    elif cmd == "run" and len(sys.argv) > 2:
        run(sys.argv[2])
    else:
        sys.exit(__doc__)