import os
import sys
import json
import requests
import feedparser
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(override=True)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

_IN_CLOUD_RUN = bool(os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"))

def startup_check():
    required = ["ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        print(f"❌ Missing env vars: {', '.join(missing)}")
        sys.exit(1)
    print(f"✅ Env check passed ({len(required)} vars present)")

client = Anthropic(api_key=ANTHROPIC_API_KEY)

SCORE_SYSTEM_PROMPT = """You are a job fit scoring assistant for M Thirumal Reddy,
a senior AI/GenAI engineer with 18 years experience in
Python, GCP, Claude API, RAG, and full-stack development.
Based in Hyderabad India. Open to remote work globally.
Freelance rate: $20-50/hour. Full-time CTC: 3L/month.

Score this job 0-100 based on:
- Skills match (Python, AI, GCP, RAG, GenAI?)
- Seniority level (senior/lead preferred)
- Remote friendly?
- Budget reasonable for his rates?

Return ONLY valid JSON, nothing else:
{"score": 85, "reason": "one line explanation"}"""


# ── Step 1: Fetch jobs ────────────────────────────────────────────────────────

def fetch_remotive():
    print("📡 Fetching from Remotive...")
    try:
        resp = requests.get(
            "https://remotive.com/api/remote-jobs?category=software-dev&limit=10",
            timeout=15,
        )
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
        results = [
            {
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "url": j.get("url", ""),
                "description": j.get("description", ""),
                "source": "Remotive",
            }
            for j in jobs
        ]
        print(f"   ✔ Got {len(results)} jobs from Remotive")
        return results
    except Exception as e:
        print(f"   ✗ Remotive failed: {e}")
        return []


def fetch_weworkremotely():
    print("📡 Fetching from WeWorkRemotely...")
    try:
        feed = feedparser.parse(
            "https://weworkremotely.com/categories/remote-programming-jobs.rss"
        )
        results = []
        for entry in feed.entries[:10]:
            title = entry.get("title", "")
            # WWR titles are often "Company: Job Title" — split them apart
            if ": " in title:
                company, job_title = title.split(": ", 1)
            else:
                company, job_title = "", title
            results.append(
                {
                    "title": job_title.strip(),
                    "company": company.strip(),
                    "url": entry.get("link", ""),
                    "description": entry.get("summary", ""),
                    "source": "WeWorkRemotely",
                }
            )
        print(f"   ✔ Got {len(results)} jobs from WeWorkRemotely")
        return results
    except Exception as e:
        print(f"   ✗ WeWorkRemotely failed: {e}")
        return []


def fetch_freelancer():
    print("📡 Fetching from Freelancer.com...")
    try:
        resp = requests.get(
            "https://www.freelancer.com/api/projects/0.1/projects/active/"
            "?job_details=true&limit=10&query=python+ai",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        projects = resp.json().get("result", {}).get("projects", [])
        results = []
        for p in projects:
            budget = p.get("budget", {})
            budget_str = ""
            if budget:
                budget_str = f"${budget.get('minimum', '?')}–${budget.get('maximum', '?')}"
            results.append(
                {
                    "title": p.get("title", ""),
                    "company": "Freelancer client",
                    "url": f"https://www.freelancer.com/projects/{p.get('seo_url', '')}",
                    "description": (p.get("description") or "") + f" Budget: {budget_str}",
                    "source": "Freelancer",
                }
            )
        print(f"   ✔ Got {len(results)} jobs from Freelancer")
        return results
    except Exception as e:
        print(f"   ✗ Freelancer failed: {e}")
        return []


# ── Step 2: Score with Claude Haiku ──────────────────────────────────────────

def score_job(job):
    user_message = (
        f"Job title: {job['title']}\n"
        f"Company: {job['company']}\n"
        f"Description: {job['description'][:500]}"
    )
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=100,
            system=SCORE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        return int(data.get("score", 0)), data.get("reason", "")
    except Exception as e:
        print(f"   ✗ Scoring failed for '{job['title']}': {e}")
        return 0, "scoring error"


# ── Step 3: Filter and display ───────────────────────────────────────────────

def filter_and_display(scored_jobs):
    strong = [j for j in scored_jobs if j["score"] >= 70]
    strong.sort(key=lambda x: x["score"], reverse=True)

    if strong:
        print("\n" + "=" * 50)
        print("STRONG MATCHES (score ≥ 70)")
        print("=" * 50)
        for j in strong:
            print(f"\n✅ [{j['score']}] {j['title']} at {j['company']}")
            print(f"   Reason: {j['reason']}")
            print(f"   URL: {j['url']}")
    else:
        print("\nNo strong matches found today.")

    print(f"\nTotal fetched: {len(scored_jobs)}")
    print(f"Strong matches (70+): {len(strong)}")
    return strong


# ── Step 4: Save cache + send Telegram job cards ─────────────────────────────

_CACHE_DIR = Path("/tmp") if _IN_CLOUD_RUN else Path(__file__).parent
JOBS_CACHE_FILE = str(_CACHE_DIR / "jobs_cache.json")


GCS_BUCKET = "rational-autumn-494910-p2-job-agent"
GCS_CACHE_OBJECT = "jobs_cache.json"


def save_jobs_cache(strong_jobs):
    """Persist strong jobs locally and upload to GCS for writer_agent."""
    with open(JOBS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(strong_jobs, f, ensure_ascii=False, indent=2)
    print(f"   💾 Saved {len(strong_jobs)} jobs to jobs_cache.json")

    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(GCS_CACHE_OBJECT)
        blob.upload_from_string(
            json.dumps(strong_jobs, ensure_ascii=False, indent=2),
            content_type="application/json",
        )
        print(f"   ☁️  Uploaded jobs_cache.json to gs://{GCS_BUCKET}/")
    except Exception as e:
        print(f"   ⚠ GCS upload failed: {e}")


def _tg_post(payload):
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    return resp


def send_telegram(strong_jobs, total_count):
    print("\n📱 Sending Telegram notifications...")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("   ✗ Telegram credentials missing — skipping")
        return

    # Header summary
    if not strong_jobs:
        _tg_post({"chat_id": TELEGRAM_CHAT_ID,
                  "text": "🔍 Job Scout Report\n───────────────────\nNo strong matches today. Try again tomorrow."})
        print("   ✔ Sent empty-result message")
        return

    _tg_post({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"🔍 Job Scout Report\n───────────────────\nFound {len(strong_jobs)} strong match{'es' if len(strong_jobs) != 1 else ''} today 👇",
    })

    # One card per job with Write Proposal button
    # callback_data uses index into jobs_cache.json (Telegram limit = 64 bytes)
    for idx, job in enumerate(strong_jobs):
        text = (
            f"🎯 [{job['score']}] {job['title']} at {job['company']}\n"
            f"Reason: {job['reason']}\n"
            f"🔗 {job['url']}"
        )
        keyboard = {
            "inline_keyboard": [[
                {"text": "✍️ Write Proposal", "callback_data": f"write:{idx}"}
            ]]
        }
        try:
            _tg_post({
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "reply_markup": keyboard,
            })
            print(f"   ✔ Sent card {idx + 1}/{len(strong_jobs)}: {job['title'][:40]}")
        except Exception as e:
            print(f"   ✗ Failed to send card {idx}: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("🚀 Job Scout Agent starting...\n")
    startup_check()

    # Step 1 — fetch
    all_jobs = []
    all_jobs.extend(fetch_remotive())
    all_jobs.extend(fetch_weworkremotely())
    all_jobs.extend(fetch_freelancer())

    if not all_jobs:
        print("\n❌ No jobs fetched from any source. Exiting.")
        return

    print(f"\n📊 Total jobs to score: {len(all_jobs)}")

    # Step 2 — score
    print("\n🤖 Scoring with Claude Haiku...")
    scored = []
    for i, job in enumerate(all_jobs, 1):
        print(f"   Scoring {i}/{len(all_jobs)}: {job['title'][:50]}...", end="\r")
        score, reason = score_job(job)
        scored.append({**job, "score": score, "reason": reason})
    print()  # newline after progress line

    # Step 3 — filter and display
    strong = filter_and_display(scored)

    # Step 4 — save cache + send Telegram cards
    if strong:
        save_jobs_cache(strong)
    send_telegram(strong, len(scored))

    print("\n✅ Scout agent done.")


if __name__ == "__main__":
    main()
