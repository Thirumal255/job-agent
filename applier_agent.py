"""Applier agent — sends one Telegram card per queued Upwork proposal.

It never submits anything to Upwork itself (Upwork's rules forbid bots
submitting proposals). Each card has the proposal ready to copy and a button
that opens the job on Upwork, where you paste it and click Submit.
"""
from common import (
    GOOGLE_SHEETS_ID, applications_ws, esc, startup_check, tg_send, write_oauth_files,
)


def send_apply_card(job):
    proposal = job.get("proposal", "").strip() or "(no proposal — tap Write Proposal first)"
    text = (
        f"📤 <b>Ready to submit on Upwork</b>\n"
        f"──────────────────\n"
        f"<b>{esc(job.get('title', 'Unknown')[:150])}</b>\n\n"
        f"<b>Your proposal</b> (tap to copy):\n<code>{esc(proposal[:3500])}</code>\n"
        f"──────────────────\n"
        f"Open the job → Apply Now → paste → Submit."
    )
    keyboard = None
    if job.get("url"):
        keyboard = {"inline_keyboard": [[{"text": "🚀 Open on Upwork", "url": job["url"]}]]}
    else:
        text += "\n⚠️ No link saved — search Upwork for the job title."
    if not tg_send(text, reply_markup=keyboard):
        raise RuntimeError("Telegram send failed")


def load_pending(ws):
    """Return (row_number, job) for every row whose Status is 'pending'."""
    rows = ws.get_all_values()
    if len(rows) < 2:
        return []
    headers = rows[0]
    pending = []
    for row_num, row in enumerate(rows[1:], start=2):  # row 2 = first data row
        record = dict(zip(headers, row))
        if record.get("Status", "").lower() == "pending":
            pending.append((row_num, {
                "title": record.get("Title", ""),
                "url": record.get("URL", ""),
                "proposal": record.get("Proposal", ""),
            }))
    return pending


def main():
    print("🚀 Applier Agent starting...\n")
    startup_check(["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GOOGLE_SHEETS_ID"])
    write_oauth_files()

    try:
        ws = applications_ws()
        pending = load_pending(ws)
        status_col = ws.row_values(1).index("Status") + 1
    except Exception as e:
        print(f"❌ Failed to read pending applications from Sheets: {e}")
        tg_send(f"❌ <b>Applier</b>: couldn't read the Sheet\n{esc(e)}")
        return

    if not pending:
        print("✅ No pending applications to process.")
        tg_send("📊 Applier: no pending applications. Tap ✅ Looks Good on a proposal first.")
        return

    print(f"📋 Found {len(pending)} pending application(s)\n")
    sent, errors = [], []
    for row_num, job in pending:
        print(f"📤 {job['title']}")
        try:
            send_apply_card(job)
            ws.update_cell(row_num, status_col, "card_sent")
            sent.append(job)
            print("   ✔ Card sent to Telegram")
        except Exception as e:
            try:
                ws.update_cell(row_num, status_col, "error")
            except Exception as e2:
                print(f"   ⚠ Failed to mark row {row_num} as error: {e2}")
            errors.append(job)
            print(f"   ✗ Error: {e}")

    lines = [f"✅ {esc(j['title'][:80])}" for j in sent]
    lines += [f"❌ {esc(j['title'][:80])}" for j in errors]
    tg_send(
        f"📊 <b>Applier Summary</b>\n"
        f"──────────────────\n"
        f"Cards sent: {len(sent)} | Errors: {len(errors)}\n\n"
        + "\n".join(lines)
        + "\n\nAfter submitting on Upwork, set the row's Status to <b>submitted</b> in the Sheet."
    )
    print(f"\nSent: {len(sent)} | Errors: {len(errors)}")
    print("✅ Applier Agent done.")


if __name__ == "__main__":
    main()
