# Job Agent 🤖

An AI-powered job search assistant that runs on autopilot — finds remote jobs every morning, scores them for fit, writes tailored proposals, and puts apply links on your phone. You just tap.

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

1. **Scouts** job boards (Remotive, WeWorkRemotely, Freelancer) for remote AI/Python roles
2. **Scores** each job 0–100 using Claude AI based on skills match, seniority, and budget fit
3. **Sends** the best matches to Telegram with a "Write Proposal" button
4. **Writes** a tailored proposal in seconds when you tap the button
5. **Queues** approved proposals and sends apply-ready cards to Telegram
6. **Monitors** Gmail for replies, classifies them, and drafts responses

You never open a job board. Your phone is the interface.

---

## Architecture

```
Cloud Scheduler (8am IST)
        │
        ▼
  scout_agent.py ──── Remotive API
  (Cloud Run Job)  ── WeWorkRemotely RSS
                   ── Freelancer API
        │
        │  Claude Haiku scores each job
        │
        ▼
   Telegram Bot ◄──── Job cards with buttons
        │
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
   Mark submitted

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
| `scout_agent.py` | Cloud Run Job | 8am IST daily | Fetches and scores jobs, sends cards to Telegram |
| `writer_agent.py` | Cloud Run Service | Telegram button tap | Writes proposals with Claude Sonnet |
| `applier_agent.py` | Cloud Run Job | `/apply` Telegram command | Sends apply-ready cards from pending queue |
| `comms_agent.py` | Cloud Run Job | Every 2 hours | Monitors Gmail, drafts replies |

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| AI scoring | Claude Haiku (`claude-haiku-4-5`) |
| AI writing | Claude Sonnet (`claude-sonnet-4-6`) |
| Notifications | Telegram Bot API |
| Shared state | Google Sheets via gspread |
| File sharing | GCS bucket (jobs cache between agents) |
| Secrets | GCP Secret Manager |
| Containers | Docker + Artifact Registry |
| Deployment | GCP Cloud Run (Jobs + Services) |
| Scheduling | GCP Cloud Scheduler |
| CI/CD | GitHub Actions + Workload Identity Federation |

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
