# Job Agent Project — Thirumal Reddy

## What this project is
An AI-powered job search and application automation system.
It automatically finds jobs, scores them, writes proposals,
handles email communication, and builds a portfolio.

## Owner
Name: M Thirumal Reddy
Location: Hyderabad, India
Experience: 18 years
Skills: Python, GCP, Claude API, React Native, PostgreSQL

## Project folder
D:\Personal_Data\Projects_Self\job-agent

## Key files already created
- .env — all API keys (NEVER hardcode secrets)
- my-profile-draft.txt — Thirumal's full profile

## Environment variables in .env
- ANTHROPIC_API_KEY — Claude API access
- TELEGRAM_BOT_TOKEN — Telegram bot token
- TELEGRAM_CHAT_ID — Thirumal's Telegram chat ID
- GCP_PROJECT_ID — rational-autumn-494910-p2
- N8N_WEBHOOK_URL — not needed anymore

## Architecture
5 Python scripts, each is one agent:
1. scout_agent.py — fetches and scores jobs daily
2. writer_agent.py — writes proposals (triggered by Telegram)
3. applier_agent.py — submits applications (after approval)
4. comms_agent.py — monitors Gmail, drafts replies
5. portfolio_agent.py — builds case studies weekly

## Tech stack
- Language: Python 3
- Job source: Remotive API (free, no auth needed)
- AI: Anthropic Claude API (Haiku for scoring, Sonnet for writing)
- Database: Google Sheets (gspread library)
- Notifications: Telegram Bot API
- Deployment: GCP Cloud Run Jobs
- Scheduling: GCP Cloud Scheduler
- Profile storage: Notion API

## Coding rules
- Always read API keys from .env file using python-dotenv
- Never hardcode any secrets
- Always test locally before deploying
- Use claude-haiku-4-5 for scoring (cheap, fast)
- Use claude-sonnet-4-6 for writing proposals (quality matters)
- Print clear status messages so I can follow what's happening
- Handle errors gracefully — don't crash silently

## Build order
Build and test one agent at a time:
1. scout_agent.py first
2. Test it works — see real jobs scored
3. Then writer_agen