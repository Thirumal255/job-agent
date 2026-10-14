"""Shared helpers for all agents: env, Telegram, Google Sheets and the job store.

Storage: in Cloud Run, small JSON blobs live in the GCS bucket so every agent
and every writer instance sees the same data. Locally they live in ./data/.
"""
import hashlib
import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(override=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID")

IN_CLOUD_RUN = bool(os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"))
BASE_DIR = Path(__file__).parent
LOCAL_DATA_DIR = BASE_DIR / "data"
GCS_BUCKET = os.getenv("GCS_BUCKET", "rational-autumn-494910-p2-job-agent")

APPLICATION_HEADERS = ["Date", "Title", "Company", "Platform", "URL", "Score", "Status", "Proposal"]


def startup_check(required):
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        print(f"❌ Missing env vars: {', '.join(missing)}")
        sys.exit(1)
    print(f"✅ Env check passed ({len(required)} vars present)")


# ── Telegram ──────────────────────────────────────────────────────────────────

def esc(value):
    """Escape text for Telegram HTML parse mode."""
    return html.escape(str(value or ""), quote=False)


def tg_call(method, payload, timeout=15):
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}",
            json=payload,
            timeout=timeout,
        )
        if not resp.ok:
            print(f"   ✗ Telegram {method} failed: {resp.status_code} {resp.text[:200]}")
        return resp.ok
    except Exception as e:
        print(f"   ✗ Telegram {method} failed: {e}")
        return False


def tg_send(text, reply_markup=None, chat_id=None):
    """Send an HTML message. Escape any untrusted text with esc() first."""
    payload = {
        "chat_id": chat_id or TELEGRAM_CHAT_ID,
        "text": text[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_call("sendMessage", payload)


# ── Google Sheets ─────────────────────────────────────────────────────────────

def write_oauth_files():
    """Write OAuth JSON from env vars (Secret Manager) to /tmp in Cloud Run."""
    for env_var, path in [
        ("GSPREAD_CREDENTIALS", "/tmp/credentials.json"),
        ("GSPREAD_TOKEN", "/tmp/gspread_token.json"),
        ("GMAIL_TOKEN", "/tmp/gmail_token.json"),
    ]:
        val = os.environ.get(env_var)
        if val:
            Path(path).write_text(val.lstrip("﻿"), encoding="utf-8")
            print(f"   📄 {env_var} → {path}")


_gc = None

def gspread_client():
    global _gc
    if _gc is None:
        import gspread
        if IN_CLOUD_RUN:
            _gc = gspread.oauth(
                credentials_filename=Path("/tmp/credentials.json"),
                authorized_user_filename=Path("/tmp/gspread_token.json"),
            )
        else:
            appdata = Path(os.environ.get("APPDATA", Path.home()))
            _gc = gspread.oauth(
                credentials_filename=BASE_DIR / "credentials.json",
                authorized_user_filename=appdata / "gspread" / "authorized_user.json",
            )
    return _gc


def applications_ws():
    import gspread.exceptions
    sh = gspread_client().open_by_key(GOOGLE_SHEETS_ID)
    try:
        return sh.worksheet("applications")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="applications", rows=1000, cols=len(APPLICATION_HEADERS))
        ws.append_row(APPLICATION_HEADERS)
        return ws


# ── Blob storage (GCS in Cloud Run, ./data locally) ───────────────────────────

def _bucket():
    from google.cloud import storage
    return storage.Client().bucket(GCS_BUCKET)


def read_blob(name):
    """Return the blob's text, or None if it doesn't exist."""
    try:
        if IN_CLOUD_RUN:
            blob = _bucket().blob(name)
            return blob.download_as_text() if blob.exists() else None
        path = LOCAL_DATA_DIR / name
        return path.read_text(encoding="utf-8") if path.exists() else None
    except Exception as e:
        print(f"   ⚠ read_blob({name}) failed: {e}")
        return None


def write_blob(name, text):
    if IN_CLOUD_RUN:
        _bucket().blob(name).upload_from_string(text, content_type="application/json")
    else:
        path = LOCAL_DATA_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


# ── Job store ─────────────────────────────────────────────────────────────────
# Each job is one JSON blob at jobs/<id>.json. Telegram buttons carry the id,
# so a button always maps to the same job no matter when it's tapped.

def make_job_id(key):
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def save_job(job):
    now = datetime.now(timezone.utc).isoformat()
    job.setdefault("created_at", now)
    job["updated_at"] = now
    write_blob(f"jobs/{job['id']}.json", json.dumps(job, ensure_ascii=False, indent=2))


def load_job(job_id):
    raw = read_blob(f"jobs/{job_id}.json")
    return json.loads(raw) if raw else None


def jobs_updated_since(since):
    """Return all stored jobs changed at or after `since` (aware datetime)."""
    jobs = []
    try:
        if IN_CLOUD_RUN:
            for blob in _bucket().client.list_blobs(GCS_BUCKET, prefix="jobs/"):
                if blob.updated and blob.updated >= since:
                    jobs.append(json.loads(blob.download_as_text()))
        else:
            for path in (LOCAL_DATA_DIR / "jobs").glob("*.json"):
                mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                if mtime >= since:
                    jobs.append(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:
        print(f"   ⚠ Listing jobs failed: {e}")
    return jobs
