import hmac
import json
import os
import re
from datetime import datetime, timezone

from anthropic import Anthropic
from flask import Flask, jsonify, request

from common import (
    BASE_DIR, GOOGLE_SHEETS_ID, IN_CLOUD_RUN, TELEGRAM_CHAT_ID,
    applications_ws, esc, load_job, make_job_id, save_job, startup_check,
    tg_call, tg_send, write_oauth_files,
)
from scout_agent import job_card, parse_json_reply, score_job

_GCP_PROJECT = os.getenv("GCP_PROJECT_ID", "rational-autumn-494910-p2")
_GCP_REGION = "asia-south1"
# Set the same value via set_webhook.py so Telegram sends it on every update
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")

client = Anthropic()
app = Flask(__name__)

PROFILE_FILE = BASE_DIR / "my-profile-draft.txt"
UPWORK_URL_RE = re.compile(r"https?://(?:www\.)?upwork\.com/\S+")

PROPOSAL_SYSTEM = """You are an Upwork proposal writer for M Thirumal Reddy,
a senior AI/GenAI engineer with 18 years experience.

Cover letter rules:
- Open with a direct reference to THEIR specific problem from the job description
- Never start with "I am" or "My name is"
- Mention ONE specific past project by name that fits this job:
  (AI Mutual Fund Advisor App, Intelligent Chatbot,
   or Data-Driven Recommendation Engine)
- Briefly say how you'd approach their problem
- Ask ONE smart question showing domain understanding
- Keep it under 150 words
- Sound human — not like AI wrote it
- No buzzwords like "passionate" or "innovative"
- Never invent facts about the client or claim experience not in the profile

If screening questions are given, after the cover letter add a line
"SCREENING ANSWERS" and then answer each one in 2-4 sentences, numbered
to match the questions.

Output only the proposal text — no preamble, no word count."""

PARSE_SYSTEM = """The user pasted an Upwork job post (often a messy copy of the whole page).
Extract its details. Return ONLY valid JSON, nothing else:
{
  "is_job": true,
  "title": "job title",
  "budget": "e.g. Hourly $30-60/hr or Fixed $500, or ? if not shown",
  "client": "country, payment verified or not, total spent, hires — whatever is shown",
  "proposals": "e.g. 10 to 15, or ?",
  "screening_questions": ["question 1", "question 2"]
}
If the text is not a job post, return {"is_job": false}."""

HELP_TEXT = (
    "🤖 <b>Upwork Job Agent</b>\n\n"
    "• Paste an Upwork job here (link + the full description) and I'll score it "
    "and write a proposal.\n"
    "• Tap <b>Looks Good</b> on a proposal to queue it.\n"
    "• Send /apply to get your queued proposals with Upwork links."
)


def load_profile():
    try:
        return PROFILE_FILE.read_text(encoding="utf-8")
    except Exception as e:
        print(f"load_profile failed: {e}")
        return ""


def write_proposal(job):
    questions = job.get("screening_questions") or []
    q_text = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1)) or "(none)"
    user_message = (
        f"Job title: {job['title']}\n"
        f"Budget: {job.get('budget', '?')}\n"
        f"Client: {job.get('client', '?')}\n\n"
        f"Job description:\n{job.get('description', '')[:6000]}\n\n"
        f"Screening questions:\n{q_text}\n\n"
        f"My profile:\n{load_profile()}\n\n"
        f"Write the proposal now."
    )
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=PROPOSAL_SYSTEM,
        messages=[{"role": "user", "content": user_message}],
    )
    lines = response.content[0].text.strip().splitlines()
    # Drop any wrapper Claude adds (e.g. "Here's the proposal:\n---")
    while lines and (lines[0].lower().startswith("here") or lines[0].strip() in ("---", "***", "")):
        lines.pop(0)
    while lines and (lines[-1].strip().startswith(("---", "**Word")) or lines[-1].strip() in ("***", "")):
        lines.pop()
    return "\n".join(lines).strip()


# ── Handlers ──────────────────────────────────────────────────────────────────

def handle_write(job_id):
    job = load_job(job_id)
    if not job:
        tg_send("❌ Job not found — it may be from an old card. Paste the job again.")
        return

    tg_send(f"✍️ Writing proposal for <b>{esc(job['title'])}</b>...\n<i>(takes ~15 seconds)</i>")
    tg_call("sendChatAction", {"chat_id": TELEGRAM_CHAT_ID, "action": "typing"}, timeout=5)

    try:
        job["proposal"] = write_proposal(job)
        save_job(job)
    except Exception as e:
        tg_send(f"❌ Failed to write proposal: {esc(e)}")
        return

    text = (
        f"✍️ <b>Proposal Draft</b>\n"
        f"──────────────────\n"
        f"{esc(job['proposal'][:3600])}\n"
        f"──────────────────\n"
        f"Job: {esc(job['title'][:150])}"
    )
    keyboard = {"inline_keyboard": [[
        {"text": "✅ Looks Good", "callback_data": f"send:{job_id}"},
        {"text": "🔄 Regenerate", "callback_data": f"regen:{job_id}"},
    ]]}
    tg_send(text, reply_markup=keyboard)
    print(f"[write] Sent proposal for {job_id}: {job['title']}")


def handle_send(job_id):
    job = load_job(job_id)
    if not job or not job.get("proposal"):
        tg_send("❌ No proposal found for this job — tap Regenerate or paste the job again.")
        return

    if not GOOGLE_SHEETS_ID:
        tg_send("⚠️ GOOGLE_SHEETS_ID not set — can't queue the application.")
        return
    try:
        ws = applications_ws()
        records = ws.get_all_records()
        already = job.get("url") and any(
            r.get("URL") == job["url"] and r.get("Status") == "pending" for r in records
        )
        if not already:
            ws.append_row([
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                job.get("title", ""),
                job.get("company", "Upwork client"),
                "Upwork",
                job.get("url", ""),
                str(job.get("score", "")),
                "pending",
                job["proposal"],
            ])
        print(f"[send] Queued {job_id}: {job['title']}")
    except Exception as e:
        print(f"[send] Sheets save failed: {e}")
        tg_send("⚠️ Sheets save failed — check logs.")
        return

    tg_send(f"✅ <b>{esc(job['title'])}</b> added to your application queue.\n"
            f"Send /apply when ready to submit on Upwork.")


def handle_paste(text):
    """User pasted an Upwork job into the chat: parse, score, store, write proposal."""
    url_match = UPWORK_URL_RE.search(text)
    url = url_match.group(0).rstrip(").,") if url_match else ""
    body = UPWORK_URL_RE.sub("", text).strip()

    if len(body) < 150:
        tg_send("📋 I can't open Upwork pages myself. Please paste the job link "
                "<b>and</b> the full job description (title, budget, client info, "
                "screening questions) in one message.")
        return

    tg_send("📥 Reading the job...")
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=600,
            system=PARSE_SYSTEM,
            messages=[{"role": "user", "content": body[:8000]}],
        )
        info = parse_json_reply(resp.content[0].text)
    except Exception as e:
        print(f"[paste] Parse failed: {e}")
        tg_send("❌ Couldn't read that job post. Try pasting it again.")
        return

    if not info.get("is_job"):
        tg_send(HELP_TEXT)
        return

    job = {
        "id": make_job_id(url or body),
        "title": info.get("title") or "Upwork job",
        "description": body[:8000],
        "url": url,
        "budget": info.get("budget", "?"),
        "client": info.get("client", "?"),
        "company": "Upwork client",
        "proposals": info.get("proposals", "?"),
        "screening_questions": info.get("screening_questions") or [],
        "source": "Upwork (pasted)",
    }
    job["score"], job["reason"] = score_job(job)
    save_job(job)

    card_text, keyboard = job_card(job)
    tg_send(card_text, reply_markup=keyboard)
    handle_write(job["id"])


def handle_apply_command():
    """Trigger the applier Cloud Run Job via REST API."""
    if not IN_CLOUD_RUN:
        tg_send("⚠️ /apply only works when deployed to Cloud Run. Locally run: python applier_agent.py")
        return
    try:
        import urllib.request as urlreq
        token_req = urlreq.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
        )
        token = json.loads(urlreq.urlopen(token_req, timeout=5).read())["access_token"]
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
        tg_send("🚀 Applier started — Upwork cards will arrive shortly.")
        print("[apply] Triggered applier-agent Cloud Run Job")
    except Exception as e:
        tg_send(f"❌ Failed to trigger applier: {esc(e)}")
        print(f"[apply] Error: {e}")


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return "Writer Agent running ✓", 200


def _is_authorized(update):
    """Only accept updates that come from Telegram and from Thirumal's chat."""
    if WEBHOOK_SECRET:
        header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(header, WEBHOOK_SECRET):
            print("[webhook] rejected: bad secret token")
            return False
    msg = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    if chat_id != str(TELEGRAM_CHAT_ID):
        print(f"[webhook] ignored update from chat {chat_id}")
        return False
    return True


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        update = request.get_json(force=True, silent=True) or {}
        if not _is_authorized(update):
            # 200 so Telegram doesn't keep retrying; the update is simply dropped
            return jsonify({"ok": True})

        if "message" in update:
            text = (update["message"].get("text") or "").strip()
            print(f"[webhook] message: {text[:80]!r}")
            if text.startswith("/apply"):
                handle_apply_command()
            elif text.startswith(("/start", "/help")) or not text:
                tg_send(HELP_TEXT)
            else:
                handle_paste(text)
            return jsonify({"ok": True})

        cq = update.get("callback_query")
        if not cq:
            return jsonify({"ok": True})

        # Acknowledge immediately so Telegram stops the loading spinner
        tg_call("answerCallbackQuery", {"callback_query_id": cq["id"]}, timeout=5)

        action, _, job_id = cq.get("data", "").partition(":")
        print(f"[webhook] callback: {action} {job_id}")
        if action == "write":
            handle_write(job_id)
        elif action == "send":
            handle_send(job_id)
        elif action == "regen":
            tg_send("🔄 Regenerating proposal...")
            handle_write(job_id)
        else:
            print(f"[webhook] unknown callback data: {cq.get('data')}")

    except Exception as e:
        print(f"[webhook] error: {e}")

    return jsonify({"ok": True})


if __name__ == "__main__":
    startup_check(["ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"])
    write_oauth_files()
    if not WEBHOOK_SECRET:
        print("⚠️  TELEGRAM_WEBHOOK_SECRET not set — webhook only checks chat id. "
              "Set it and re-run set_webhook.py.")
    port = int(os.getenv("PORT", 8080))
    print(f"🚀 Writer Agent starting on port {port}")
    print("   Webhook endpoint: POST /webhook")
    print("   Health check:     GET  /")
    app.run(host="0.0.0.0", port=port, debug=False)
