import os
import sys
import json
import base64
import re
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

import requests
from anthropic import Anthropic
from dotenv import load_dotenv
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(override=True)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID")

_IN_CLOUD_RUN = bool(os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"))

claude = Anthropic(api_key=ANTHROPIC_API_KEY)

# File paths
BASE_DIR = Path(__file__).parent
APPDATA = Path(os.environ.get("APPDATA", Path.home()))
GSPREAD_DIR = APPDATA / "gspread"

def startup_check():
    required = ["ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
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
    if _write("GMAIL_TOKEN", "/tmp/gmail_token.json"):
        wrote.append("GMAIL_TOKEN → /tmp/gmail_token.json")
    for item in wrote:
        print(f"   📄 {item}")

if _IN_CLOUD_RUN:
    GMAIL_CREDS_FILE = Path("/tmp/credentials.json")
    GMAIL_TOKEN_FILE = Path("/tmp/gmail_token.json")
else:
    GMAIL_CREDS_FILE = BASE_DIR / "credentials.json"
    GMAIL_TOKEN_FILE = BASE_DIR / "gmail_token.json"

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

CLASSIFY_SYSTEM = """You are an email classifier for M Thirumal Reddy's job search inbox.

Classify this email and return ONLY valid JSON:
{
  "type": "interview_invite|rejection|question|offer|contract|follow_up|not_relevant",
  "urgency": "high|medium|low",
  "auto_reply": true|false,
  "draft_reply": "reply text under 80 words if auto_reply is true, else empty string",
  "summary": "one line about what this email is"
}

Auto reply ONLY for:
- Simple acknowledgements ("thanks for applying", "we received your application")
- Thank you responses to polite rejections

NEVER auto reply for:
- Interview invites (Thirumal must confirm his availability)
- Offers or contracts (Thirumal must review terms)
- Questions that need specific answers
- Anything mentioning dates, times, or money"""

URGENCY_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}
TYPE_EMOJI = {
    "interview_invite": "🎯", "offer": "💰", "contract": "📄",
    "question": "❓", "rejection": "👎", "follow_up": "🔄", "not_relevant": "🚫",
}


# ── Step 1: Gmail connection ──────────────────────────────────────────────────

def get_gmail_service():
    """Authenticate with Gmail using OAuth. Opens browser on first run only."""
    creds = None

    if GMAIL_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_FILE), GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("   🔄 Refreshing Gmail token...")
            creds.refresh(Request())
            GMAIL_TOKEN_FILE.write_text(creds.to_json())
            print(f"   💾 Token refreshed")
        elif _IN_CLOUD_RUN:
            raise RuntimeError(
                "Gmail token is missing or invalid. Re-run comms_agent.py locally "
                "to refresh gmail_token.json, then update the GMAIL_TOKEN secret:\n"
                "  python comms_agent.py\n"
                "  gcloud secrets versions add GMAIL_TOKEN --data-file=gmail_token.json"
            )
        else:
            print("   🌐 Opening browser for Gmail authorization...")
            flow = InstalledAppFlow.from_client_secrets_file(str(GMAIL_CREDS_FILE), GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
            GMAIL_TOKEN_FILE.write_text(creds.to_json())
            print(f"   💾 Token saved to {GMAIL_TOKEN_FILE}")

    return build("gmail", "v1", credentials=creds)


# ── Step 2: Read applied companies from Sheets ────────────────────────────────

def _gspread_client():
    import gspread
    if _IN_CLOUD_RUN:
        return gspread.oauth(
            credentials_filename=Path("/tmp/credentials.json"),
            authorized_user_filename=Path("/tmp/gspread_token.json"),
        )
    return gspread.oauth(
        credentials_filename=BASE_DIR / "credentials.json",
        authorized_user_filename=GSPREAD_DIR / "authorized_user.json",
    )

def get_companies_applied():
    """Get list of companies we applied to, to help prioritise emails."""
    if not GOOGLE_SHEETS_ID:
        return []
    try:
        gc = _gspread_client()
        sh = gc.open_by_key(GOOGLE_SHEETS_ID)
        ws = sh.worksheet("applications")
        records = ws.get_all_records()
        companies = [r.get("Company", "").strip() for r in records if r.get("Company")]
        print(f"   Found {len(companies)} companies in Sheets")
        return companies
    except Exception as e:
        print(f"   ⚠ Could not read companies from Sheets: {e}")
        return []


# ── Step 3: Fetch emails ──────────────────────────────────────────────────────

def extract_body(payload):
    """Recursively extract plain text from a Gmail message payload."""
    mime = payload.get("mimeType", "")

    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            return re.sub(r"<[^>]+>", " ", html).strip()

    for part in payload.get("parts", []):
        text = extract_body(part)
        if text.strip():
            return text

    return ""


def fetch_emails(service):
    """Fetch unread job-related emails from the last 48 hours."""
    query = (
        "is:unread newer_than:2d "
        "(subject:application OR subject:interview OR subject:opportunity "
        "OR subject:position OR subject:role OR subject:proposal OR subject:project)"
    )
    try:
        result = service.users().messages().list(
            userId="me", q=query, maxResults=20
        ).execute()
        message_refs = result.get("messages", [])
        print(f"   Gmail returned {len(message_refs)} message(s)")

        emails = []
        for ref in message_refs:
            try:
                full = service.users().messages().get(
                    userId="me", messageId=ref["id"], format="full"
                ).execute()
                headers = {h["name"].lower(): h["value"] for h in full["payload"]["headers"]}
                body = extract_body(full["payload"])
                emails.append({
                    "message_id": ref["id"],
                    "thread_id": full["threadId"],
                    "sender": headers.get("from", ""),
                    "subject": headers.get("subject", "(no subject)"),
                    "date": headers.get("date", ""),
                    "body": body[:1000],
                    "snippet": full.get("snippet", ""),
                })
            except Exception as e:
                print(f"   ⚠ Could not fetch message {ref['id']}: {e}")
        return emails
    except Exception as e:
        print(f"   ✗ Gmail fetch failed: {e}")
        return []


# ── Step 4: Classify with Claude Haiku ───────────────────────────────────────

def classify_email(email):
    """Classify an email and decide if/how to reply."""
    user_msg = (
        f"From: {email['sender']}\n"
        f"Subject: {email['subject']}\n"
        f"Body: {email['body'][:1000]}"
    )
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            system=CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f"   ⚠ Classification failed: {e}")
        return {
            "type": "not_relevant", "urgency": "low",
            "auto_reply": False, "draft_reply": "", "summary": "classification error",
        }


# ── Step 5: Handle emails ─────────────────────────────────────────────────────

def send_gmail_reply(service, email, reply_text):
    """Send a reply email via Gmail API."""
    msg = MIMEText(reply_text)
    msg["To"] = email["sender"]
    msg["Subject"] = f"Re: {email['subject']}"
    msg["In-Reply-To"] = email["message_id"]
    msg["References"] = email["message_id"]
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service.users().messages().send(
        userId="me",
        body={"raw": raw, "threadId": email["thread_id"]},
    ).execute()


def mark_as_read(service, message_id):
    service.users().messages().modify(
        userId="me", id=message_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


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
            json=payload, timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"   ✗ Telegram send failed: {e}")


def handle_email(service, email, classification):
    """Auto-reply or send Telegram attention card. Returns action string."""
    email_type = classification.get("type", "not_relevant")
    urgency = classification.get("urgency", "low")
    auto_reply = classification.get("auto_reply", False)
    draft_reply = classification.get("draft_reply", "")
    summary = classification.get("summary", "")

    if email_type == "not_relevant":
        return "skipped"

    if auto_reply and draft_reply:
        try:
            send_gmail_reply(service, email, draft_reply)
            mark_as_read(service, email["message_id"])
            tg_send(
                f"✅ <b>Auto-replied to email</b>\n"
                f"From: {email['sender']}\n"
                f"Subject: {email['subject']}\n"
                f"Type: {email_type}\n\n"
                f"Reply sent:\n<i>{draft_reply}</i>"
            )
            print(f"   ✉ Auto-reply sent")
            return "auto_replied"
        except Exception as e:
            print(f"   ⚠ Auto-reply failed: {e} — escalating to manual")

    # Needs manual attention
    urg_emoji = URGENCY_EMOJI.get(urgency, "🟡")
    type_emoji = TYPE_EMOJI.get(email_type, "📧")
    type_label = email_type.replace("_", " ").title()
    sender_name = email["sender"].split("<")[0].strip() or email["sender"]

    text = (
        f"📧 <b>Email needs your attention</b>\n"
        f"──────────────────────────────\n"
        f"<b>From:</b> {sender_name}\n"
        f"<b>Subject:</b> {email['subject']}\n"
        f"<b>Type:</b> {type_emoji} {type_label}\n"
        f"<b>Urgency:</b> {urg_emoji} {urgency.title()}\n\n"
        f"<b>Summary:</b> {summary}"
    )
    if draft_reply:
        text += f"\n\n<b>Suggested reply:</b>\n<i>{draft_reply[:300]}</i>"

    tg_send(text)
    return "needs_attention"


# ── Step 6: Log to Google Sheets ──────────────────────────────────────────────

def write_to_sheets(email, classification, action):
    if not GOOGLE_SHEETS_ID:
        return
    try:
        import gspread.exceptions
        gc = _gspread_client()
        sh = gc.open_by_key(GOOGLE_SHEETS_ID)
        try:
            ws = sh.worksheet("emails")
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title="emails", rows=1000, cols=9)
            ws.append_row(["Date", "From", "Subject", "Type", "Urgency", "Auto-replied", "Action", "Summary", "Message ID"])

        ws.append_row([
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            email["sender"][:60],
            email["subject"][:80],
            classification.get("type", ""),
            classification.get("urgency", ""),
            "yes" if action == "auto_replied" else "no",
            action,
            classification.get("summary", "")[:100],
            email["message_id"],
        ])
        print(f"   📊 Logged to Sheets")
    except Exception as e:
        print(f"   ⚠ Sheets log failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("🚀 Comms Agent starting...\n")
    startup_check()
    _write_oauth_files()

    # Step 1 — Gmail
    print("📧 Connecting to Gmail...")
    try:
        service = get_gmail_service()
        print("   ✔ Gmail connected\n")
    except Exception as e:
        print(f"   ✗ Gmail connection failed: {e}")
        return

    # Step 2 — Companies
    print("📊 Reading applied companies from Sheets...")
    companies = get_companies_applied()
    print()

    # Step 3 — Fetch emails
    print("📥 Fetching unread job-related emails (last 48h)...")
    emails = fetch_emails(service)
    print(f"   ✔ Fetched {len(emails)} email(s) to process\n")

    if not emails:
        tg_send("📬 <b>Comms Agent:</b> No relevant unread emails in the last 48 hours.")
        print("✅ Nothing to process.")
        return

    # Steps 4-6 — Classify, handle, log
    stats = {"auto_replied": 0, "needs_attention": 0, "skipped": 0}
    attention_items = []

    for i, email in enumerate(emails, 1):
        sender_short = email["sender"].split("<")[0].strip() or email["sender"]
        print(f"📩 [{i}/{len(emails)}] {email['subject'][:55]}")
        print(f"   From: {sender_short}")

        classification = classify_email(email)
        email_type = classification.get("type", "not_relevant")
        urgency = classification.get("urgency", "low")
        print(f"   → {email_type} | urgency: {urgency} | auto-reply: {classification.get('auto_reply')}")

        action = handle_email(service, email, classification)
        stats[action] = stats.get(action, 0) + 1
        print(f"   Action: {action}")

        if action == "needs_attention":
            urg_emoji = URGENCY_EMOJI.get(urgency, "🟡")
            attention_items.append(
                f"{urg_emoji} {email_type.replace('_', ' ').title()} from {sender_short}"
            )

        write_to_sheets(email, classification, action)

    # Step 7 — Summary
    total = len(emails)
    relevant = stats.get("auto_replied", 0) + stats.get("needs_attention", 0)
    summary_text = (
        f"📬 <b>Comms Agent Report</b>\n"
        f"──────────────────────\n"
        f"Emails scanned: {total}\n"
        f"Relevant found: {relevant}\n"
        f"Auto-replied: {stats.get('auto_replied', 0)}\n"
        f"Needs attention: {stats.get('needs_attention', 0)}\n"
        f"Not relevant: {stats.get('skipped', 0)}"
    )
    if attention_items:
        summary_text += "\n\n<b>Action needed:</b>\n" + "\n".join(attention_items)

    tg_send(summary_text)

    print(f"\n{'='*52}")
    print(f"Scanned: {total} | Relevant: {relevant} | "
          f"Auto-replied: {stats.get('auto_replied', 0)} | "
          f"Attention: {stats.get('needs_attention', 0)} | "
          f"Skipped: {stats.get('skipped', 0)}")
    print("✅ Comms Agent done.")


if __name__ == "__main__":
    main()
