import json

from anthropic import Anthropic

import upwork_api
from common import (
    esc, make_job_id, read_blob, save_job, startup_check, tg_send, write_blob,
)

client = Anthropic()

SEARCH_TERMS = [
    "generative AI",
    "RAG",
    "LLM application",
    "AI agent",
    "Claude API",
    "AI chatbot python",
]
MIN_RATE_USD = 20      # skip hourly jobs whose max rate is below this
STRONG_SCORE = 70
SEEN_BLOB = "seen_jobs.json"
SEEN_LIMIT = 3000

SCORE_SYSTEM_PROMPT = """You are a job fit scoring assistant for M Thirumal Reddy,
a senior AI/GenAI engineer with 18 years experience in
Python, GCP, Claude API, RAG, and full-stack development.
Based in Hyderabad India. Freelancing on Upwork.
Freelance rate: $20-50/hour.

Score this Upwork job 0-100 based on:
- Skills match (Python, AI, GCP, RAG, GenAI, AI agents?)
- Budget reasonable for his rates?
- Client quality (payment verified, money spent, hire rate)
- Competition (fewer proposals is better)
- Clear scope (vague one-liners score lower)

Return ONLY valid JSON, nothing else:
{"score": 85, "reason": "one line explanation"}"""


def parse_json_reply(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def job_summary(job):
    return (
        f"Job title: {job['title']}\n"
        f"Budget: {job.get('budget', '?')}\n"
        f"Client: {job.get('client', '?')}\n"
        f"Proposals so far: {job.get('proposals', '?')}\n"
        f"Description: {job['description'][:1500]}"
    )


def score_job(job):
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=150,
            system=SCORE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": job_summary(job)}],
        )
        data = parse_json_reply(response.content[0].text)
        return int(data.get("score", 0)), data.get("reason", "")
    except Exception as e:
        print(f"   ✗ Scoring failed for '{job['title']}': {e}")
        return 0, "scoring error"


def job_card(job):
    text = (
        f"🎯 <b>[{job['score']}] {esc(job['title'])}</b>\n"
        f"💵 {esc(job.get('budget', '?'))}\n"
        f"👤 {esc(job.get('client', '?'))}\n"
        f"📨 Proposals: {esc(job.get('proposals', '?'))}\n"
        f"Reason: {esc(job['reason'])}"
    )
    buttons = [{"text": "✍️ Write Proposal", "callback_data": f"write:{job['id']}"}]
    if job.get("url"):
        buttons.append({"text": "👁 View on Upwork", "url": job["url"]})
    return text, {"inline_keyboard": [buttons]}


def main():
    print("🚀 Job Scout Agent (Upwork) starting...\n")
    startup_check(["ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"])

    if not upwork_api.is_configured():
        print("⏭ Upwork API not connected yet — nothing to scout.")
        print("   Paste Upwork jobs into the Telegram bot instead.")
        tg_send("🔍 <b>Job Scout</b>\nUpwork API not connected yet.\n"
                "Paste an Upwork job (link + description) into this chat to get a proposal.")
        return

    # Step 1 — fetch
    print("📡 Searching Upwork...")
    try:
        jobs = upwork_api.search_jobs(SEARCH_TERMS)
    except Exception as e:
        print(f"❌ Upwork search failed: {e}")
        tg_send(f"❌ <b>Job Scout</b>: Upwork search failed\n{esc(e)}")
        return

    # Step 2 — drop jobs already seen and jobs below the rate floor
    seen = json.loads(read_blob(SEEN_BLOB) or "[]")
    seen_set = set(seen)
    fresh = []
    for job in jobs:
        if job["upwork_id"] in seen_set:
            continue
        if job["contract"] == "HOURLY" and job["max_rate"] and job["max_rate"] < MIN_RATE_USD:
            continue
        fresh.append(job)
    print(f"\n📊 {len(jobs)} found, {len(fresh)} new and within budget")

    # Step 3 — score
    print("\n🤖 Scoring with Claude Haiku...")
    scored = []
    for i, job in enumerate(fresh, 1):
        print(f"   Scoring {i}/{len(fresh)}: {job['title'][:50]}")
        score, reason = score_job(job)
        scored.append({**job, "score": score, "reason": reason})

    strong = sorted((j for j in scored if j["score"] >= STRONG_SCORE),
                    key=lambda j: j["score"], reverse=True)

    # Step 4 — enrich + store strong matches, send cards
    for job in strong:
        job["id"] = make_job_id(job["url"])
        try:
            job["screening_questions"] = upwork_api.screening_questions(job["upwork_id"])
        except Exception as e:
            print(f"   ⚠ Screening questions failed for {job['title'][:40]}: {e}")
            job["screening_questions"] = []
        save_job(job)

    if strong:
        tg_send(f"🔍 <b>Upwork Scout</b>\nFound {len(strong)} strong match"
                f"{'es' if len(strong) != 1 else ''} today 👇")
        for job in strong:
            text, keyboard = job_card(job)
            tg_send(text, reply_markup=keyboard)
            print(f"   ✔ [{job['score']}] {job['title'][:60]}")
    else:
        tg_send("🔍 <b>Upwork Scout</b>\nNo strong matches today.")
        print("\nNo strong matches today.")

    # Remember everything we scored so it isn't re-sent tomorrow
    seen.extend(j["upwork_id"] for j in scored)
    write_blob(SEEN_BLOB, json.dumps(seen[-SEEN_LIMIT:]))

    print(f"\nScored: {len(scored)} | Strong (≥{STRONG_SCORE}): {len(strong)}")
    print("✅ Scout agent done.")


if __name__ == "__main__":
    main()
