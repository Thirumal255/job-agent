import os
import sys
import json
import requests
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(override=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID")

_IN_CLOUD_RUN = bool(os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"))
_BASE_DIR = Path(__file__).parent
_CACHE_DIR = Path("/tmp") if _IN_CLOUD_RUN else _BASE_DIR

def startup_check():
    required = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        print(f"❌ Missing env vars: {', '.join(missing)}")
        sys.exit(1)
    print(f"✅ Env check passed ({len(required)} vars present)")

def _write_oauth_files():
    """Write OAuth JSON from env vars to /tmp (used in Cloud Run)."""
    def _write(env_var, path):
        val = os.environ.get(env_var)
        if val:
            Path(path).write_text(val.lstrip('﻿'), encoding='utf-8')
            return True
        return False

    wrote = []
    if _write("GSPREAD_CREDENTIALS", "/tmp/credentials.json"):
        wrote.append("GSPREAD_CREDENTIALS → /tmp/credentials.json")
    if _write("GSPREAD_TOKEN", "/tmp/gspread_token.json"):
        wrote.append("GSPREAD_TOKEN → /tmp/gspread_token.json")
    for item in wrote:
        print(f"   📄 {item}")

PENDING_FILE = str(_CACHE_DIR / "pending_applications.json")  # local fallback only

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# ── Platform detection ────────────────────────────────────────────────────────

def detect_platform(url):
    if "remotive.com" in url:
        return "Remotive"
    if "freelancer.com" in url:
        return "Freelancer"
    if "weworkremotely.com" in url:
        return "WeWorkRemotely"
    return "Other"


# ── Apply link scrapers ───────────────────────────────────────────────────────

def scrape_remotive_apply_url(job_url):
    """Scrape the external apply link from a Remotive job page."""
    try:
        resp = requests.get(job_url, timeout=15, headers=HEADERS)
        soup = BeautifulSoup(resp.text, "html.parser")
        # Remotive uses an <a> with text "Apply for this job"
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            if "apply" in text:
                href = a["href"]
                if href.startswith("http") and "remotive.com" not in href:
                    return href
        # Fallback: any external link in the job actions area
        for a in soup.select(".apply-button, .job-apply, [data-action='apply']"):
            href = a.get("href", "")
            if href.startswith("http"):
                return href
    except Exception as e:
        print(f"   ⚠ Remotive scrape failed: {e}")
    return job_url  # fall back to job page itself


def scrape_wwr_apply(job_url):
    """Scrape apply email or URL from a WeWorkRemotely job page."""
    try:
        resp = requests.get(job_url, timeout=15, headers=HEADERS)
        soup = BeautifulSoup(resp.text, "html.parser")
        # Look for mailto links
        for a in soup.find_all("a", href=True):
            if a["href"].startswith("mailto:"):
                return "email", a["href"].replace("mailto:", "").split("?")[0]
        # Look for external apply links
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            if "apply" in text:
                href = a["href"]
                if href.startswith("http") and "weworkremotely.com" not in href:
                    return "url", href
    except Exception as e:
        print(f"   ⚠ WWR scrape failed: {e}")
    return "url", job_url


# ── Telegram ──────────────────────────────────────────────────────────────────

def tg_send(text, reply_markup=None):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
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
        print(f"   ✗ Telegram send failed: {e}")


def send_apply_card(job):
    """Send an application card to Telegram with proposal + apply button."""
    platform = detect_platform(job["url"])
    proposal = job.get("proposal", "").strip()
    proposal_display = proposal if proposal else "(no proposal — tap Write Proposal first)"
    title = job.get("title", "Unknown")
    company = job.get("company", "Unknown")

    print(f"   🔍 Detecting apply link for {platform}...")

    if platform == "Remotive":
        apply_url = scrape_remotive_apply_url(job["url"])
        print(f"   🔗 Apply URL: {apply_url}")
        text = (
            f"📤 <b>Ready to Apply — Remotive</b>\n"
            f"──────────────────\n"
            f"<b>{title}</b> at {company}\n\n"
            f"<b>Your Proposal:</b>\n{proposal_display}\n\n"
            f"──────────────────\n"
            f"Proposal ready ✅"
        )
        keyboard = {"inline_keyboard": [[
            {"text": "🚀 Open Application", "url": apply_url},
            {"text": "👁 View Job", "url": job["url"]},
        ]]}

    elif platform == "Freelancer":
        text = (
            f"📤 <b>Ready to Apply — Freelancer</b>\n"
            f"──────────────────\n"
            f"<b>{title}</b> at {company}\n\n"
            f"<b>Your Proposal:</b>\n{proposal_display}\n\n"
            f"──────────────────\n"
            f"Copy the proposal above and paste it as your bid."
        )
        keyboard = {"inline_keyboard": [[
            {"text": "🚀 Open Freelancer Job", "url": job["url"]},
        ]]}

    elif platform == "WeWorkRemotely":
        kind, apply_target = scrape_wwr_apply(job["url"])
        if kind == "email":
            import urllib.parse
            subject = urllib.parse.quote(f"Application — {title}")
            body = urllib.parse.quote(proposal[:1000]) if proposal else ""
            gmail_url = f"https://mail.google.com/mail/?view=cm&to={apply_target}&su={subject}&body={body}"
            print(f"   📧 Apply email: {apply_target}")
            text = (
                f"📧 <b>Email Application Ready — WeWorkRemotely</b>\n"
                f"──────────────────\n"
                f"<b>{title}</b> at {company}\n"
                f"<b>To:</b> {apply_target}\n\n"
                f"<b>Your Proposal:</b>\n{proposal_display}\n\n"
                f"──────────────────"
            )
            keyboard = {"inline_keyboard": [[
                {"text": "📧 Open Gmail Draft", "url": gmail_url},
                {"text": "👁 View Job", "url": job["url"]},
            ]]}
        else:
            print(f"   🔗 Apply URL: {apply_target}")
            text = (
                f"📤 <b>Ready to Apply — WeWorkRemotely</b>\n"
                f"──────────────────\n"
                f"<b>{title}</b> at {company}\n\n"
                f"<b>Your Proposal:</b>\n{proposal_display}\n\n"
                f"──────────────────"
            )
            keyboard = {"inline_keyboard": [[
                {"text": "🚀 Open Application", "url": apply_target},
                {"text": "👁 View Job", "url": job["url"]},
            ]]}
    else:
        text = (
            f"📤 <b>Ready to Apply</b>\n"
            f"──────────────────\n"
            f"<b>{title}</b> at {company}\n\n"
            f"<b>Your Proposal:</b>\n{proposal_display}\n\n"
            f"──────────────────"
        )
        keyboard = {"inline_keyboard": [[
            {"text": "🚀 Open Job Page", "url": job["url"]},
        ]]}

    tg_send(text, reply_markup=keyboard)


# ── Pending applications — Google Sheets ─────────────────────────────────────

def _get_applications_ws():
    import gspread.exceptions
    gc = _get_sheets_client()
    sh = gc.open_by_key(GOOGLE_SHEETS_ID)
    try:
        return sh.worksheet("applications")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="applications", rows=1000, cols=8)
        ws.append_row(["Date", "Title", "Company", "Platform", "URL", "Score", "Status", "Proposal"])
        return ws


def load_pending():
    """Read rows from Sheets where Status == 'pending'. Returns list of (row_num, job_dict)."""
    if not GOOGLE_SHEETS_ID:
        # Local fallback
        if not os.path.exists(PENDING_FILE):
            print("❌ No pending_applications.json and no GOOGLE_SHEETS_ID.")
            return []
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            jobs = json.load(f)
        return [(i, j) for i, j in enumerate(jobs) if j.get("status") == "pending"]

    try:
        ws = _get_applications_ws()
        all_rows = ws.get_all_values()
        if len(all_rows) < 2:
            return []
        headers = all_rows[0]
        pending = []
        for i, row in enumerate(all_rows[1:], start=2):  # row 2 = first data row
            record = dict(zip(headers, row))
            if record.get("Status", "").lower() == "pending":
                job = {
                    "title": record.get("Title", ""),
                    "company": record.get("Company", ""),
                    "url": record.get("URL", ""),
                    "score": record.get("Score", ""),
                    "proposal": record.get("Proposal", ""),
                    "source": record.get("Platform", ""),
                    "_row": i,
                }
                pending.append((i, job))
        return pending
    except Exception as e:
        print(f"❌ Failed to load pending from Sheets: {e}")
        return []


def mark_submitted(row_num, status="submitted"):
    """Update the Status cell for a row in the applications sheet."""
    if not GOOGLE_SHEETS_ID:
        return
    try:
        ws = _get_applications_ws()
        headers = ws.row_values(1)
        status_col = headers.index("Status") + 1  # 1-indexed
        ws.update_cell(row_num, status_col, status)
    except Exception as e:
        print(f"   ⚠ Failed to update row {row_num} status: {e}")


# ── Google Sheets — OAuth ─────────────────────────────────────────────────────
# Uses gspread.oauth() — one-time browser auth, token saved automatically.
# credentials.json goes to: C:\Users\<you>\AppData\Roaming\gspread\credentials.json
# After first auth, authorized_user.json is saved there and never prompted again.

_gc = None  # cached gspread client

if _IN_CLOUD_RUN:
    _CREDS_FILE = Path("/tmp/credentials.json")
    _TOKEN_FILE = Path("/tmp/gspread_token.json")
else:
    _APPDATA = Path(os.environ.get("APPDATA", Path.home()))
    _CREDS_FILE = _BASE_DIR / "credentials.json"
    _TOKEN_FILE = _APPDATA / "gspread" / "authorized_user.json"


def _get_sheets_client():
    global _gc
    if _gc is None:
        import gspread
        _gc = gspread.oauth(
            credentials_filename=_CREDS_FILE,
            authorized_user_filename=_TOKEN_FILE,
        )
    return _gc


def write_to_sheets(job):
    """Append one application row to the Google Sheet. Skips if not configured."""
    if not GOOGLE_SHEETS_ID:
        return

    try:
        gc = _get_sheets_client()
        import gspread.exceptions
        sh = gc.open_by_key(GOOGLE_SHEETS_ID)

        # Use existing "applications" tab (lowercase — matches the sheet)
        try:
            ws = sh.worksheet("applications")
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title="applications", rows=1000, cols=8)
            ws.append_row(["Date", "Title", "Company", "Platform", "URL", "Score", "Status", "Proposal"])

        ws.append_row([
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            job.get("title", ""),
            job.get("company", ""),
            detect_platform(job.get("url", "")),
            job.get("url", ""),
            str(job.get("score", "")),
            "submitted",
            job.get("proposal", "")[:500],
        ])
        print(f"   📊 Written to Google Sheets")
    except Exception as e:
        print(f"   ⚠ Google Sheets write failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("🚀 Applier Agent starting...\n")
    startup_check()
    _write_oauth_files()

    pending = load_pending()

    if not pending:
        print("✅ No pending applications to process.")
        tg_send("📊 Applier Agent: no pending applications today.")
        return

    print(f"📋 Found {len(pending)} pending application(s)\n")

    processed = []
    for row_num, job in pending:
        title = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        platform = detect_platform(job.get("url", ""))
        print(f"📤 {title} at {company} [{platform}]")

        try:
            send_apply_card(job)
            mark_submitted(row_num, "submitted")
            processed.append(("submitted", job))
            print(f"   ✔ Card sent to Telegram")
        except Exception as e:
            jobs[idx]["status"] = "error"
            mark_submitted(row_num, "error")
            processed.append(("error", job))
            print(f"   ✗ Error: {e}")

    # Summary
    submitted = [j for s, j in processed if s == "submitted"]
    errors = [j for s, j in processed if s == "error"]

    lines = [f"✅ {j['title']} at {j['company']}" for j in submitted]
    lines += [f"❌ Error: {j['title']}" for j in errors]

    summary = (
        f"📊 <b>Applier Summary</b>\n"
        f"──────────────────\n"
        f"Processed: {len(pending)}\n"
        f"Cards sent: {len(submitted)}\n"
        f"Errors: {len(errors)}\n\n"
        + "\n".join(lines)
    )
    tg_send(summary)

    print(f"\n{'='*50}")
    print(f"Processed: {len(pending)} | Sent: {len(submitted)} | Errors: {len(errors)}")
    print("✅ Applier Agent done.")


if __name__ == "__main__":
    main()
