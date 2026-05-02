import os
import sys
import json
import requests
from pathlib import Path
from flask import Flask, request, jsonify
from anthropic import Anthropic
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(override=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

_IN_CLOUD_RUN = bool(os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"))
_GCP_PROJECT = "rational-autumn-494910-p2"
_GCP_REGION = "asia-south1"

def startup_check():
    required = ["ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        print(f"❌ Missing env vars: {', '.join(missing)}")
        sys.exit(1)
    print(f"✅ Env check passed ({len(required)} vars present)")

client = Anthropic(api_key=ANTHROPIC_API_KEY)
app = Flask(__name__)

BASE_DIR = Path(__file__).parent
_CACHE_DIR = Path("/tmp") if _IN_CLOUD_RUN else BASE_DIR
JOBS_CACHE_FILE = str(_CACHE_DIR / "jobs_cache.json")
PROPOSALS_CACHE_FILE = str(_CACHE_DIR / "proposals_cache.json")
PROFILE_FILE = str(BASE_DIR / "my-profile-draft.txt")

PROPOSAL_SYSTEM = """You are a proposal writer for M Thirumal Reddy,
a senior AI/GenAI engineer with 18 years experience.

Rules:
- Open with a direct reference to THEIR specific problem
- Never start with "I am" or "My name is"
- Mention ONE specific past project by name:
  (AI Mutual Fund Advisor App, Intelligent Chatbot,
   or Data-Driven Recommendation Engine)
- Ask ONE smart question showing domain understanding
- Keep it under 150 words
- Sound human — not like AI wrote it
- No buzzwords like "passionate" or "innovative\""""


# ── Helpers ───────────────────────────────────────────────────────────────────

def tg_send(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"tg_send failed: {e}")


def tg_answer_callback(callback_query_id, text=""):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=5,
        )
    except Exception as e:
        print(f"answerCallbackQuery failed: {e}")


def tg_send_typing(chat_id):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=5,
        )
    except Exception:
        pass


GCS_BUCKET = "rational-autumn-494910-p2-job-agent"
GCS_CACHE_OBJECT = "jobs_cache.json"

GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID")

# ── gspread helpers ────────────────────────────────────────────────────────────

def _write_oauth_files():
    def _write(env_var, path):
        val = os.environ.get(env_var)
        if val:
            Path(path).write_text(val.lstrip('﻿'), encoding='utf-8')
            return True
        return False
    if _write("GSPREAD_CREDENTIALS", "/tmp/credentials.json"):
        print("[oauth] GSPREAD_CREDENTIALS → /tmp/credentials.json")
    if _write("GSPREAD_TOKEN", "/tmp/gspread_token.json"):
        print("[oauth] GSPREAD_TOKEN → /tmp/gspread_token.json")

_gc = None
def _gspread_client():
    global _gc
    if _gc is None:
        import gspread
        if _IN_CLOUD_RUN:
            _gc = gspread.oauth(
                credentials_filename=Path("/tmp/credentials.json"),
                authorized_user_filename=Path("/tmp/gspread_token.json"),
            )
        else:
            _appdata = Path(os.environ.get("APPDATA", Path.home()))
            _gc = gspread.oauth(
                credentials_filename=BASE_DIR / "credentials.json",
                authorized_user_filename=_appdata / "gspread" / "authorized_user.json",
            )
    return _gc

def _get_applications_ws():
    import gspread.exceptions
    gc = _gspread_client()
    sh = gc.open_by_key(GOOGLE_SHEETS_ID)
    try:
        return sh.worksheet("applications")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="applications", rows=1000, cols=8)
        ws.append_row(["Date", "Title", "Company", "Platform", "URL", "Score", "Status", "Proposal"])
        return ws


def _fetch_jobs_cache_from_gcs():
    """Download jobs_cache.json from GCS into local cache path."""
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(GCS_CACHE_OBJECT)
        data = blob.download_as_text()
        with open(JOBS_CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(data)
        print(f"[cache] Downloaded jobs_cache.json from GCS")
    except Exception as e:
        print(f"[cache] GCS download failed: {e}")


def load_job(idx):
    if not os.path.exists(JOBS_CACHE_FILE):
        _fetch_jobs_cache_from_gcs()
    try:
        with open(JOBS_CACHE_FILE, "r", encoding="utf-8") as f:
            jobs = json.load(f)
        return jobs[idx] if 0 <= idx < len(jobs) else None
    except Exception as e:
        print(f"load_job failed: {e}")
        return None


def save_proposal(idx, proposal):
    try:
        cache = {}
        if os.path.exists(PROPOSALS_CACHE_FILE):
            with open(PROPOSALS_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        cache[str(idx)] = proposal
        with open(PROPOSALS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"save_proposal failed: {e}")


def load_proposal(idx):
    try:
        if os.path.exists(PROPOSALS_CACHE_FILE):
            with open(PROPOSALS_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get(str(idx))
    except Exception as e:
        print(f"load_proposal failed: {e}")
    return None


def load_profile():
    try:
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"load_profile failed: {e}")
        return ""


def write_proposal(job):
    profile = load_profile()
    user_message = (
        f"Job title: {job['title']}\n"
        f"Company: {job['company']}\n"
        f"Job URL: {job['url']}\n\n"
        f"My profile:\n{profile}\n\n"
        f"Write the proposal now."
    )
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        system=PROPOSAL_SYSTEM,
        messages=[{"role": "user", "content": user_message}],
    )
    text = response.content[0].text.strip()

    # Strip any wrapper Claude adds (e.g. "Here's the proposal:\n---\n...\n---")
    lines = text.splitlines()
    # Drop leading lines before the actual proposal body
    while lines and (
        lines[0].lower().startswith("here") or
        lines[0].strip() in ("---", "***", "")
    ):
        lines.pop(0)
    # Drop trailing separator / word-count lines
    while lines and (
        lines[-1].strip().startswith("---") or
        lines[-1].strip().startswith("**Word") or
        lines[-1].strip() in ("---", "***", "")
    ):
        lines.pop()

    return "\n".join(lines).strip()


# ── Callback handlers ─────────────────────────────────────────────────────────

def handle_write(chat_id, idx):
    job = load_job(idx)
    if not job:
        tg_send(chat_id, "❌ Job not found in cache. Please run scout_agent.py again.")
        return

    tg_send(chat_id, f"✍️ Writing proposal for *{job['title']}* at *{job['company']}*...\n_(this takes ~10 seconds)_")
    tg_send_typing(chat_id)

    try:
        proposal = write_proposal(job)
        save_proposal(idx, proposal)
    except Exception as e:
        tg_send(chat_id, f"❌ Failed to write proposal: {e}")
        return

    text = (
        f"✍️ Proposal Draft\n"
        f"──────────────────\n"
        f"{proposal}\n"
        f"──────────────────\n"
        f"Job: {job['title']} at {job['company']}"
    )
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Looks Good", "callback_data": f"send:{idx}"},
            {"text": "✏️ Regenerate", "callback_data": f"regen:{idx}"},
        ]]
    }
    tg_send(chat_id, text, reply_markup=keyboard)
    print(f"[write] Sent proposal for job {idx}: {job['title']}")


def handle_send(chat_id, idx):
    job = load_job(idx)
    if not job:
        tg_send(chat_id, "❌ Job not found in cache.")
        return

    proposal = load_proposal(idx) or ""
    saved = False

    if GOOGLE_SHEETS_ID:
        try:
            from datetime import datetime, timezone
            ws = _get_applications_ws()
            # Avoid duplicates — check if URL already pending
            records = ws.get_all_records()
            already = any(r.get("URL") == job.get("url") and r.get("Status") == "pending"
                          for r in records)
            if not already:
                ws.append_row([
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                    job.get("title", ""),
                    job.get("company", ""),
                    job.get("source", ""),
                    job.get("url", ""),
                    str(job.get("score", "")),
                    "pending",
                    proposal[:500],
                ])
                saved = True
                print(f"[send] Saved to Sheets: {job['title']}")
            else:
                print(f"[send] Already pending in Sheets: {job['title']}")
                saved = True
        except Exception as e:
            print(f"[send] Sheets save failed: {e}")

    status = "added to your application queue" if saved else "⚠️ Sheets save failed — check logs"
    tg_send(
        chat_id,
        f"✅ Saved!\n\n<b>{job['title']}</b> at {job['company']} {status}.\n"
        f"Send /apply when ready to submit.",
    )
    print(f"[send] Queued job {idx}: {job['title']}")


def handle_regen(chat_id, idx):
    tg_send(chat_id, "🔄 Regenerating proposal...")
    handle_write(chat_id, idx)


def handle_apply_command(chat_id):
    """Trigger the applier Cloud Run Job via REST API."""
    if not _IN_CLOUD_RUN:
        tg_send(chat_id, "⚠️ /apply only works when deployed to Cloud Run.")
        return
    try:
        import urllib.request as urlreq
        # Get access token from metadata server
        token_req = urlreq.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
        )
        token = json.loads(urlreq.urlopen(token_req, timeout=5).read())["access_token"]

        # Trigger applier-agent job
        run_url = (
            f"https://{_GCP_REGION}-run.googleapis.com/apis/run.googleapis.com/v1/"
            f"namespaces/{_GCP_PROJECT}/jobs/applier-agent:run"
        )
        job_req = urlreq.Request(
            run_url, data=b"{}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        urlreq.urlopen(job_req, timeout=10)
        tg_send(chat_id, "🚀 Applier agent started — apply cards will arrive shortly.")
        print("[apply] Triggered applier-agent Cloud Run Job")
    except Exception as e:
        tg_send(chat_id, f"❌ Failed to trigger applier: {e}")
        print(f"[apply] Error: {e}")


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return "Writer Agent running ✓", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        update = request.get_json(force=True)
        print(f"[webhook] received: {json.dumps(update)[:200]}")

        # Handle plain messages (e.g. /apply command)
        if "message" in update:
            msg = update["message"]
            text = msg.get("text", "").strip()
            chat_id = msg["chat"]["id"]
            if text == "/apply":
                handle_apply_command(chat_id)
            return jsonify({"ok": True})

        if "callback_query" not in update:
            return jsonify({"ok": True})

        cq = update["callback_query"]
        cq_id = cq["id"]
        chat_id = cq["message"]["chat"]["id"]
        data = cq.get("data", "")

        # Acknowledge immediately so Telegram stops the loading spinner
        tg_answer_callback(cq_id)

        if data.startswith("write:"):
            idx = int(data.split(":")[1])
            handle_write(chat_id, idx)

        elif data.startswith("send:"):
            idx = int(data.split(":")[1])
            handle_send(chat_id, idx)

        elif data.startswith("regen:"):
            idx = int(data.split(":")[1])
            handle_regen(chat_id, idx)

        else:
            print(f"[webhook] unknown callback data: {data}")

    except Exception as e:
        print(f"[webhook] error: {e}")

    return jsonify({"ok": True})


if __name__ == "__main__":
    startup_check()
    _write_oauth_files()
    port = int(os.getenv("PORT", 8080))
    print(f"🚀 Writer Agent starting on port {port}")
    print(f"   Webhook endpoint: POST /webhook")
    print(f"   Health check:     GET  /")
    app.run(host="0.0.0.0", port=port, debug=False)
