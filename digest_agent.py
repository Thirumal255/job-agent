"""Digest agent — emails a once-a-day summary of the last 24 hours.

Sections: new Upwork matches, proposals drafted, proposals queued.
Sent to your own Gmail and labelled "job-agent" (kept in the inbox).
Telegram stays the place for actions; this is the readable record.

Run daily after the scout, e.g. Cloud Scheduler at 9am IST.
"""
import base64
import os
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

from common import esc, jobs_updated_since, startup_check, write_oauth_files
from comms_agent import get_gmail_service

DIGEST_HOURS = int(os.getenv("DIGEST_HOURS", "24"))
DIGEST_TO = os.getenv("DIGEST_TO")  # defaults to the Gmail account itself
LABEL_NAME = "job-agent"


def _title_link(job):
    title = esc(job.get("title", "Untitled"))
    return f'<a href="{esc(job["url"])}">{title}</a>' if job.get("url") else title


def _match_row(job):
    return (
        f"<li><b>[{esc(job.get('score', '?'))}]</b> {_title_link(job)}<br>"
        f"<small>💵 {esc(job.get('budget', '?'))} · 👤 {esc(job.get('client', '?'))}"
        f" · 📨 {esc(job.get('proposals', '?'))} proposals</small><br>"
        f"<small><i>{esc(job.get('reason', ''))}</i></small></li>"
    )


def _proposal_block(job):
    return (
        f"<h3 style='margin-bottom:4px'>{_title_link(job)}</h3>"
        f"<small>💵 {esc(job.get('budget', '?'))} · score {esc(job.get('score', '?'))}</small>"
        f"<pre style='white-space:pre-wrap;font-family:inherit;background:#f6f6f6;"
        f"padding:10px;border-radius:6px'>{esc(job.get('proposal', ''))}</pre>"
    )


def build_digest(jobs):
    """Return (subject, html) or (None, None) if there's nothing to report."""
    queued = [j for j in jobs if j.get("status") == "queued"]
    drafted = [j for j in jobs if j.get("proposal") and j.get("status") != "queued"]
    matches = [j for j in jobs if not j.get("proposal")]
    if not jobs:
        return None, None

    for group in (queued, drafted, matches):
        group.sort(key=lambda j: j.get("score") or 0, reverse=True)

    day = datetime.now(timezone.utc).strftime("%d %b")
    subject = (f"Upwork digest — {day}: {len(matches)} new, "
               f"{len(drafted)} drafted, {len(queued)} queued")

    parts = [f"<h2>Upwork digest — {day}</h2>"]
    if queued:
        parts.append("<h2>📤 Queued — submit these on Upwork</h2>")
        parts.extend(_proposal_block(j) for j in queued)
    if drafted:
        parts.append("<h2>✍️ Proposals drafted (not queued yet)</h2>")
        parts.extend(_proposal_block(j) for j in drafted)
    if matches:
        parts.append("<h2>🎯 New matches</h2><ul>")
        parts.extend(_match_row(j) for j in matches)
        parts.append("</ul><p><small>Tap ✍️ Write Proposal on the Telegram card to draft one.</small></p>")
    parts.append("<hr><p><small>Sent by job-agent digest_agent.py</small></p>")
    return subject, "\n".join(parts)


def _label_id(service):
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"] == LABEL_NAME:
            return label["id"]
    created = service.users().labels().create(
        userId="me", body={"name": LABEL_NAME, "labelListVisibility": "labelShow",
                           "messageListVisibility": "show"},
    ).execute()
    return created["id"]


def send_digest(service, subject, html_body):
    to = DIGEST_TO or service.users().getProfile(userId="me").execute()["emailAddress"]
    msg = MIMEText(html_body, "html", "utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    service.users().messages().modify(
        userId="me", id=sent["id"], body={"addLabelIds": [_label_id(service)]},
    ).execute()
    return to


def main():
    print("🚀 Digest Agent starting...\n")
    startup_check(["ANTHROPIC_API_KEY"])  # comms_agent import needs it
    write_oauth_files()

    since = datetime.now(timezone.utc) - timedelta(hours=DIGEST_HOURS)
    jobs = jobs_updated_since(since)
    print(f"📊 {len(jobs)} job(s) changed in the last {DIGEST_HOURS}h")

    subject, html_body = build_digest(jobs)
    if not subject:
        print("✅ Nothing new — no digest sent.")
        return

    try:
        service = get_gmail_service()
        to = send_digest(service, subject, html_body)
        print(f"   ✉ Sent '{subject}' to {to}")
    except Exception as e:
        print(f"❌ Digest email failed: {e}")
        return
    print("✅ Digest Agent done.")


if __name__ == "__main__":
    main()
