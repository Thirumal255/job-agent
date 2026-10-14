# Job Agent 🤖

An AI-powered Upwork assistant — finds Upwork jobs every morning, scores them for fit, writes tailored proposals (including screening answers), and puts them on your phone ready to submit. You review and click Submit on Upwork.

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat&logo=python&logoColor=white)
![Claude AI](https://img.shields.io/badge/Claude_AI-Anthropic-D4A027?style=flat)
![GCP](https://img.shields.io/badge/Google_Cloud-Cloud_Run-4285F4?style=flat&logo=googlecloud&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4?style=flat&logo=telegram&logoColor=white)
![Google Sheets](https://img.shields.io/badge/Google_Sheets-Database-34A853?style=flat&logo=googlesheets&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Containerized-2496ED?style=flat&logo=docker&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-CI%2FCD-2088FF?style=flat&logo=githubactions&logoColor=white)

---

## What it does

Every day at 8am IST, the system wakes up and:

1. **Scouts** Upwork through the official API for AI / RAG / LLM / Python jobs
2. **Scores** each job 0–100 with Claude Haiku: skills match, budget, client quality, competition
3. **Sends** the best matches to Telegram with a "Write Proposal" button
4. **Writes** a tailored proposal from the full job description, plus screening answers
5. **Queues** approved proposals and sends "submit on Upwork" cards to Telegram
6. **Monitors** Gmail for replies, classifies them, and drafts responses

You can also **paste any Upwork job** (link + description) into the Telegram chat
to get it scored and a proposal written immediately — this is how it works until
Upwork API access is approved.

Nothing is submitted to Upwork automatically — Upwork's rules ban bots that
submit proposals or scrape the site. You open the job, paste, and click Submit.

---

## Architecture

```
Cloud Scheduler (8am IST)
        │
        ▼
  scout_agent.py ──── Upwork GraphQL API
  (Cloud Run Job)     (official, OAuth 2.0)
        │
        │  Claude Haiku scores each job
        │
        ▼
   Telegram Bot ◄──── Job cards with buttons
        │              ◄── or paste an Upwork job
        │  Tap "Write Proposal"
        ▼
 writer_agent.py ──── Claude Sonnet writes proposal
 (Cloud Run Service)
        │
        │  Tap "Looks Good"
        ▼
  Google Sheets ◄──── Status: pending
  (shared state)
        │
        │  Send /apply in Telegram
        ▼
 applier_agent.py ─── Reads pending rows from Sheets
  (Cloud Run Job)      Sends apply cards to Telegram
        │
        ▼
   You submit on Upwork

Cloud Scheduler (every 2hrs)
        │
        ▼
  comms_agent.py ──── Gmail API
  (Cloud Run Job)     Classifies replies with Claude Haiku
                      Auto-replies or sends draft to Telegram
```

---

## Agents

| Agent | Type | Trigger | What it does |
|-------|------|---------|--------------|
| `scout_agent.py` | Cloud Run Job | 8am IST daily | Searches Upwork, scores jobs, sends cards to Telegram |
| `writer_agent.py` | Cloud Run Service | Telegram button tap or pasted job | Writes proposals with Claude Sonnet |
| `applier_agent.py` | Cloud Run Job | `/apply` Telegram command | Sends "submit on Upwork" cards from pending queue |
| `comms_agent.py` | Cloud Run Job | Every 2 hours | Monitors Gmail, drafts replies |

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| AI scoring | Claude Haiku (`claude-haiku-4-5`) |
| AI writing | Claude Sonnet (`claude-sonnet-4-6`) |
| Notifications | Telegram Bot API |
| Shared state | Google Sheets via gspread |
| Job store | GCS bucket (one JSON per job, Upwork token, seen-jobs list) |
| Job source | Upwork GraphQL API |
| Secrets | GCP Secret Manager |
| Containers | Docker + Artifact Registry |
| Deployment | GCP Cloud Run (Jobs + Services) |
| Scheduling | GCP Cloud Scheduler |
| CI/CD | GitHub Actions + Workload Identity Federation |

---

## Upwork API setup (once approved)

1. Register an app at developers.upwork.com with the **Read marketplace Job Postings** permission
2. Add `UPWORK_CLIENT_ID`, `UPWORK_CLIENT_SECRET`, `UPWORK_REDIRECT_URI` to `.env` and Secret Manager
3. Run `python upwork_api.py login` locally, then copy `data/upwork_token.json` to the GCS bucket

Until then the scout sends a reminder and you paste jobs into Telegram.

---

## CI/CD

Every push to `main` automatically:
- Builds a new Docker image tagged with commit SHA
- Pushes to Artifact Registry
- Updates all 4 Cloud Run agents
- Sends a Telegram confirmation

No manual deployment steps needed.

---

## Project by

**M Thirumal Reddy** — Senior AI/GenAI Engineer, Hyderabad India  
18 years experience · Python · GCP · Claude API · RAG · Full-stack
