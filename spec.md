# Job Agent — Build Specification

## Current task: Build scout_agent.py

### What it does
Fetches jobs from 3 sources, scores each with Claude Haiku,
prints strong matches, sends summary to Telegram.

### Step 1 — Fetch jobs from 3 sources

**Source 1 — Remotive API:**
```
URL: https://remotive.com/api/remote-jobs?category=software-dev&limit=10
Returns: JSON with jobs array
Fields to use: title, company_name, url, description
```

**Source 2 — WeWorkRemotely RSS:**
```
URL: https://weworkremotely.com/categories/remote-programming-jobs.rss
Returns: XML RSS feed
Parse: title, company, link from each item
Library: feedparser
```

**Source 3 — Freelancer.com:**
```
URL: https://www.freelancer.com/api/projects/0.1/projects/active/?job_details=true&limit=10&query=python+ai
Returns: JSON with result.projects array
Fields: title, description, budget
```

### Step 2 — Score each job with Claude Haiku

Model: claude-haiku-4-5
API key: from .env file (ANTHROPIC_API_KEY)

System prompt:
```
You are a job fit scoring assistant for M Thirumal Reddy,
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
{"score": 85, "reason": "one line explanation"}
```

User message:
```
Job title: {title}
Company: {company}
Description: {first 500 characters of description}
```

### Step 3 — Filter and display

- Only show jobs with score >= 70
- Print each match like this:
  ✅ [85] Senior Python Developer at TechCorp
     Reason: Strong Python + AI match
     URL: https://...

- Print summary at end:
  Total fetched: 28
  Strong matches (70+): 5

### Step 4 — Send Telegram notification

After scoring, send a Telegram message to TELEGRAM_CHAT_ID
using TELEGRAM_BOT_TOKEN from .env

Message format:
```
🔍 Job Scout Report
───────────────────
Found 5 strong matches today

1. [85] Senior Python Dev at TechCorp
   https://...

2. [78] AI Engineer at StartupX
   https://...

Tap a job to apply 👆
```

### Libraries needed
- requests
- anthropic
- python-dotenv
- feedparser

### Rules
- Load all keys from .env using python-dotenv
- Never hardcode secrets
- Handle errors gracefully (if one source fails, continue with others)
- Print status as it runs so I can follow along
- Test each step works before moving to next

### Definition of done
- Script runs without errors
- Fetches real jobs from at least 2 of 3 sources
- Claude scores each job and returns valid JSON
- Only shows jobs scoring 70+
- Telegram message arrives on my phone




---
---

## Next task: Build writer_agent.py

### What it does
When you tap a job in Telegram, this agent:
1. Reads the job details from the Telegram message
2. Reads your full profile from my-profile-draft.txt
3. Calls Claude Sonnet to write a tailored proposal
4. Sends the draft proposal back to Telegram
5. Gives you two buttons: ✅ Send It / ✏️ Edit First

### How it gets triggered
scout_agent.py sends Telegram messages with inline buttons.
Each job card has an "Apply" button.
When you tap Apply — writer_agent.py runs for that job.

To make this work:
- scout_agent.py needs to send inline keyboard buttons with each job
- Each button passes the job details as callback data
- writer_agent.py listens for these button taps via Telegram webhook

### Step 1 — Update scout_agent.py

Modify the Telegram message in scout_agent.py so each
job is sent as a separate message with an inline button:

```
🔍 [85] Senior AI Engineer at TechCorp
Reason: Strong Python + AI match
URL: https://remotive.com/...

[✅ Write Proposal]
```

The "Write Proposal" button should pass this data when tapped:
```
job_title|company|url|score
```

### Step 2 — Create writer_agent.py

This script runs as a Flask webhook server that listens
for Telegram button taps.

When a button is tapped:
1. Parse the job data from callback_data
2. Read my-profile-draft.txt for full profile context
3. Call Claude Sonnet (claude-sonnet-4-6) with this prompt:

System:
```
You are a proposal writer for M Thirumal Reddy,
a senior AI/GenAI engineer with 18 years experience.

Rules:
- Open with a direct reference to THEIR specific problem
- Never start with "I am" or "My name is"
- Mention ONE specific past project by name:
  (AI Mutual Fund Advisor App, Intelligent Chatbot, 
   or Data-Driven Recommendation Engine)
- Ask ONE smart question showing domain understanding
- Keep it under 150 words
- Sound human — not like AI wrote it
- No buzzwords like "passionate" or "innovative"
```

User:
```
Job title: {title}
Company: {company}
Job URL: {url}

My profile:
{contents of my-profile-draft.txt}

Write the proposal now.
```

4. Send the proposal back to Telegram like this:

```
✍️ Proposal Draft
──────────────────
{proposal text}
──────────────────
Job: Senior AI Engineer at TechCorp

[✅ Looks Good] [✏️ Regenerate]
```

### Step 3 — Run as webhook

writer_agent.py should:
- Use Flask to create a webhook endpoint
- Register the webhook URL with Telegram
- Listen on port 8080
- Be deployable to Cloud Run

### Libraries needed
- flask
- anthropic
- python-dotenv
- requests

### Webhook setup
After creating the Flask app, register it with Telegram:
```
https://api.telegram.org/bot{TOKEN}/setWebhook?url={WEBHOOK_URL}
```

For local testing use ngrok to expose local port:
```
ngrok http 8080
```

### Definition of done
- scout_agent.py sends individual job cards with buttons
- Tapping "Write Proposal" triggers writer_agent.py
- Claude Sonnet writes a tailored proposal under 150 words
- Proposal arrives in Telegram with Send/Regenerate buttons
- No errors, runs cleanly



---

## Next task: Build applier_agent.py

### What it does
Reads pending_applications.json and submits each application
to the right platform automatically.

### Trigger
Runs manually for now: python applier_agent.py
Later: triggered by Cloud Scheduler or Telegram command

### Step 1 — Read pending applications

Read pending_applications.json which looks like:
```json
[
  {
    "job_title": "Senior AI Engineer",
    "company": "TechCorp",
    "url": "https://remotive.com/...",
    "proposal": "Your tailored proposal text here...",
    "status": "pending",
    "timestamp": "2026-05-02T10:30:00"
  }
]
```

Only process jobs where status == "pending"

### Step 2 — Determine platform and submit

For each pending job, detect which platform it's from
based on the URL:

**Remotive jobs** (url contains remotive.com):
- Open the job URL using requests
- Parse the application link or email from the page
- If apply link exists: open it and note the URL
- Send Telegram message:
```
  📤 Ready to apply
  Job: {title} at {company}
  Platform: Remotive
  Apply URL: {apply_url}
  Proposal ready ✅
  
  [🚀 Open Application] [⏭️ Skip]
```

**Freelancer.com jobs** (url contains freelancer.com):
- Use Freelancer API to submit bid
- Requires FREELANCER_API_KEY in .env
- For now: send Telegram with job link and proposal ready to copy

**WeWorkRemotely jobs** (url contains weworkremotely.com):
- Parse application email from job page
- Draft email with proposal as body
- Send Telegram with draft:
```
  📧 Email Application Ready
  To: jobs@company.com
  Subject: Application — {title}
  Body: {proposal}
  
  [📧 Open Gmail Draft] [⏭️ Skip]
```

### Step 3 — Update status

After processing each job update pending_applications.json:
- "pending" → "submitted" (if submitted)
- "pending" → "skipped" (if skipped)
- Add submitted_at timestamp

Also write a row to Google Sheets applications tab:
```
app_id | job_id | platform | proposal_used | date_applied | status
```

Use gspread library to write to Google Sheets.
Google Sheets credentials from .env:
GOOGLE_SHEETS_CREDENTIALS_JSON — path to service account JSON

### Step 4 — Send daily summary to Telegram

At the end send:
```
📊 Applier Summary
──────────────────
Processed: 3 pending applications
Submitted: 2
Skipped: 1

Submitted to:
✅ Senior AI Engineer at TechCorp
✅ Python Developer at StartupX
⏭️ Skipped: Data Analyst (not a fit)
```

### Important rules
- NEVER auto-submit without human tap in Telegram
- Always show the proposal before submitting
- Always update status after processing
- Handle errors gracefully — mark as "error" not crash

### Libraries needed
- requests
- beautifulsoup4 (for parsing job pages)
- gspread (for Google Sheets)
- google-auth (for Sheets authentication)
- python-dotenv

### Google Sheets setup needed
Before running applier_agent.py:
1. Create a Google Cloud service account
2. Download credentials JSON
3. Share the Google Sheet with the service account email
4. Add path to credentials in .env as GOOGLE_SHEETS_CREDENTIALS_JSON
5. Add the spreadsheet ID to .env as GOOGLE_SHEETS_ID

### Definition of done
- Reads pending_applications.json correctly
- Detects platform from URL
- Sends Telegram message with apply link and proposal
- Updates status in pending_applications.json
- Writes row to Google Sheets applications tab
- Daily summary arrives in Telegram




---

## Next task: Build comms_agent.py

### What it does
Monitors your Gmail inbox for replies related to job applications.
Classifies each email and either auto-replies or sends you
a draft in Telegram for approval.

### How it runs
Scheduled: every 2 hours via Cloud Scheduler later
For now: python comms_agent.py runs manually

### Step 1 — Connect to Gmail

Use Gmail API with OAuth (same pattern as Google Sheets).
Enable Gmail API in GCP if not already enabled.

Use google-auth and googleapiclient libraries.
Scopes needed:
- https://www.googleapis.com/auth/gmail.readonly
- https://www.googleapis.com/auth/gmail.send
- https://www.googleapis.com/auth/gmail.modify

Store Gmail OAuth token separately:
~/.config/job-agent/gmail_token.json

### Step 2 — Read submitted applications from Google Sheets

Read the applications tab to get list of companies
we applied to. This helps filter relevant emails.

companies_applied = [row['company'] for row in applications_sheet]

### Step 3 — Fetch unread emails from last 48 hours

Search Gmail for:
- Unread emails
- From last 48 hours
- Related to job applications

Gmail search query:
```
is:unread newer_than:2d (subject:application OR subject:interview 
OR subject:opportunity OR subject:position OR subject:role)
```

For each email get:
- sender name and email
- subject
- body text (first 1000 chars)
- thread ID
- message ID

### Step 4 — Classify each email with Claude Haiku

Use claude-haiku-4-5 model.

System prompt:
```
You are an email classifier for M Thirumal Reddy's 
job search inbox.

Classify this email and return ONLY valid JSON:
{
  "type": "interview_invite|rejection|question|offer|contract|follow_up|not_relevant",
  "urgency": "high|medium|low",
  "auto_reply": true|false,
  "draft_reply": "reply text under 80 words if auto_reply is true, else empty string",
  "summary": "one line about what this email is"
}

Auto reply only for:
- Simple acknowledgements
- Confirming receipt of application
- Thank you responses to rejections

Never auto reply for:
- Interview invites (Thirumal must confirm availability)
- Offers or contracts (Thirumal must review)
- Questions needing specific answers
- Anything involving money or dates
```

User message:
```
From: {sender}
Subject: {subject}
Body: {body first 1000 chars}
```

### Step 5 — Handle each email based on classification

**If auto_reply is true:**
- Send the draft_reply via Gmail API
- Mark email as read
- Send Telegram notification:
```
  ✅ Auto-replied to email
  From: {sender}
  Subject: {subject}
  Classification: {type}
  Reply sent: {draft_reply}
```

**If auto_reply is false:**
- Send Telegram message with full context and draft:
```
  📧 Email needs your attention
  ──────────────────────────────
  From: {sender}
  Subject: {subject}
  Type: 🎯 Interview Invite
  Urgency: 🔴 High

  Summary: {summary}

  Suggested reply:
  {draft_reply if exists}

  [✅ Send This Reply] [✏️ Edit Reply] [⏭️ Skip]
```

**If type is not_relevant:**
- Skip silently
- Don't send Telegram notification

### Step 6 — Update Google Sheets emails tab

Write a row to emails tab for each processed email:
```
email_id | thread_id | from | subject | type | 
urgency | auto_replied | date_received | notes
```

### Step 7 — Send summary to Telegram

```
📬 Comms Agent Report
──────────────────────
Emails scanned: 12
Relevant found: 3
Auto-replied: 1
Needs attention: 2
Not relevant: 9

Action needed:
🔴 Interview invite from TechCorp
🟡 Question from StartupX
```

### Libraries needed
- google-auth
- google-auth-oauthlib  
- google-api-python-client
- anthropic
- python-dotenv

### Definition of done
- Connects to Gmail successfully
- Fetches unread emails from last 48 hours
- Classifies each with Claude Haiku
- Auto-replies to simple emails
- Sends attention-needed emails to Telegram with buttons
- Updates Google Sheets emails tab
- Summary arrives in Telegram


---

## Deployment: All agents to GCP Cloud Run

### Project details
- GCP Project: rational-autumn-494910-p2
- Region: asia-south1
- Docker registry: asia-south1-docker.pkg.dev/rational-autumn-494910-p2/job-agent/agents:latest

### What gets deployed where

| Agent | Type | Trigger |
|-------|------|---------|
| scout_agent.py | Cloud Run JOB | Cloud Scheduler 8am IST daily |
| writer_agent.py | Cloud Run SERVICE | Telegram webhook 24/7 |
| comms_agent.py | Cloud Run JOB | Cloud Scheduler every 2 hours |
| applier_agent.py | Cloud Run JOB | Telegram command /apply |

### Step 1 — Store secrets in GCP Secret Manager

Store these from .env as secrets:
- ANTHROPIC_API_KEY
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
- GOOGLE_SHEETS_ID

Store these OAuth JSON files as secrets (read file content as string):
- GSPREAD_CREDENTIALS → contents of ~/.config/gspread/credentials.json
- GSPREAD_TOKEN → contents of ~/.config/gspread/authorized_user.json
- GMAIL_TOKEN → contents of ~/.config/job-agent/gmail_token.json

### Step 2 — Update Python scripts for Cloud Run

All agents need these changes for Cloud Run:
- Read secrets from environment variables not .env file
- OAuth JSON files come from environment variables not local files
- Write OAuth JSON to /tmp/ folder at runtime (Cloud Run is read-only except /tmp)
- Add startup check: print all env vars exist before running

Add this to top of every agent:
```python
import os
import json
import tempfile

# Write OAuth files from environment variables to /tmp
if os.environ.get('GSPREAD_TOKEN'):
    token_path = '/tmp/gspread_token.json'
    with open(token_path, 'w') as f:
        f.write(os.environ['GSPREAD_TOKEN'])

if os.environ.get('GMAIL_TOKEN'):
    gmail_path = '/tmp/gmail_token.json'  
    with open(gmail_path, 'w') as f:
        f.write(os.environ['GMAIL_TOKEN'])
```

### Step 3 — Create Artifact Registry repository

```bash
gcloud artifacts repositories create job-agent \
  --repository-format=docker \
  --location=asia-south1 \
  --project=rational-autumn-494910-p2
```

### Step 4 — Create Dockerfile

Single Dockerfile for all agents:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default command (overridden per deployment)
CMD ["python", "scout_agent.py"]
```

### Step 5 — Build and push Docker image

```bash
gcloud builds submit \
  --tag asia-south1-docker.pkg.dev/rational-autumn-494910-p2/job-agent/agents:latest \
  --project=rational-autumn-494910-p2
```

### Step 6 — Deploy scout_agent as Cloud Run Job

```bash
gcloud run jobs create scout-agent \
  --image asia-south1-docker.pkg.dev/rational-autumn-494910-p2/job-agent/agents:latest \
  --region asia-south1 \
  --command python \
  --args scout_agent.py \
  --set-secrets=ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest \
  --set-secrets=TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest \
  --set-secrets=TELEGRAM_CHAT_ID=TELEGRAM_CHAT_ID:latest \
  --set-secrets=GOOGLE_SHEETS_ID=GOOGLE_SHEETS_ID:latest \
  --set-secrets=GSPREAD_TOKEN=GSPREAD_TOKEN:latest \
  --project=rational-autumn-494910-p2
```

### Step 7 — Create Cloud Scheduler for Scout

```bash
gcloud scheduler jobs create http scout-daily \
  --schedule="0 8 * * *" \
  --time-zone="Asia/Kolkata" \
  --uri=https://asia-south1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/rational-autumn-494910-p2/jobs/scout-agent:run \
  --message-body="" \
  --oauth-service-account-email=PROJECT_NUMBER-compute@developer.gserviceaccount.com \
  --location=asia-south1 \
  --project=rational-autumn-494910-p2
```

### Step 8 — Deploy writer_agent as Cloud Run Service

```bash
gcloud run deploy writer-agent \
  --image asia-south1-docker.pkg.dev/rational-autumn-494910-p2/job-agent/agents:latest \
  --region asia-south1 \
  --command python \
  --args writer_agent.py \
  --allow-unauthenticated \
  --min-instances=1 \
  --set-secrets=ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest \
  --set-secrets=TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest \
  --set-secrets=TELEGRAM_CHAT_ID=TELEGRAM_CHAT_ID:latest \
  --set-secrets=GOOGLE_SHEETS_ID=GOOGLE_SHEETS_ID:latest \
  --set-secrets=GSPREAD_TOKEN=GSPREAD_TOKEN:latest \
  --project=rational-autumn-494910-p2
```

After deployment:
1. Copy the Cloud Run URL
2. Register as Telegram webhook:
```
https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook?url={CLOUD_RUN_URL}/webhook
```
3. ngrok is no longer needed after this

### Step 9 — Deploy comms_agent as Cloud Run Job

```bash
gcloud run jobs create comms-agent \
  --image asia-south1-docker.pkg.dev/rational-autumn-494910-p2/job-agent/agents:latest \
  --region asia-south1 \
  --command python \
  --args comms_agent.py \
  --set-secrets=ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest \
  --set-secrets=TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest \
  --set-secrets=TELEGRAM_CHAT_ID=TELEGRAM_CHAT_ID:latest \
  --set-secrets=GOOGLE_SHEETS_ID=GOOGLE_SHEETS_ID:latest \
  --set-secrets=GMAIL_TOKEN=GMAIL_TOKEN:latest \
  --project=rational-autumn-494910-p2
```

### Step 9b — Deploy applier_agent as Cloud Run Job

```bash
gcloud run jobs create applier-agent \
  --image asia-south1-docker.pkg.dev/rational-autumn-494910-p2/job-agent/agents:latest \
  --region asia-south1 \
  --command python \
  --args applier_agent.py \
  --set-secrets=ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest \
  --set-secrets=TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest \
  --set-secrets=TELEGRAM_CHAT_ID=TELEGRAM_CHAT_ID:latest \
  --set-secrets=GOOGLE_SHEETS_ID=GOOGLE_SHEETS_ID:latest \
  --set-secrets=GSPREAD_TOKEN=GSPREAD_TOKEN:latest \
  --project=rational-autumn-494910-p2
```

### How Applier gets triggered from Telegram

Add a Telegram command handler to writer_agent.py:
When you send /apply in Telegram → it triggers the applier job:

```python
# In writer_agent.py webhook handler
if text == '/apply':
    os.system('gcloud run jobs execute applier-agent --region asia-south1')
    send_telegram("🚀 Applier agent started — checking pending applications...")
```

This means:
- You tap "Looks Good" on a proposal → saved to pending_applications.json
- You send /apply in Telegram from your phone
- Applier Cloud Run Job fires
- Apply cards arrive in Telegram
- You tap to open each application

### Applier Cloud Run specific changes

pending_applications.json needs to be shared between agents.
Problem: Cloud Run containers don't share files.

Solution — store pending applications in Google Sheets instead of JSON file:

1. writer_agent.py saves approved jobs to Google Sheets 
   "applications" tab with status "pending"
   instead of pending_applications.json

2. applier_agent.py reads from Google Sheets "applications" tab
   where status == "pending"
   processes them
   updates status to "submitted"

This way both agents share state via Google Sheets
even though they run in separate Cloud Run containers.

Update both writer_agent.py and applier_agent.py 
to use Google Sheets as the shared state store.

### Step 10 — Create Cloud Scheduler for Comms

```bash
gcloud scheduler jobs create http comms-every-2hrs \
  --schedule="0 */2 * * *" \
  --time-zone="Asia/Kolkata" \
  --uri=https://asia-south1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/rational-autumn-494910-p2/jobs/comms-agent:run \
  --message-body="" \
  --oauth-service-account-email=PROJECT_NUMBER-compute@developer.gserviceaccount.com \
  --location=asia-south1 \
  --project=rational-autumn-494910-p2
```

### Step 11 — Test all deployments

1. Manually trigger Scout:
```bash
gcloud run jobs execute scout-agent --region asia-south1
```
Confirm: Telegram job cards arrive

2. Tap a job card in Telegram
Confirm: Writer proposal arrives (from Cloud Run, not ngrok)

3. Manually trigger Comms:
```bash
gcloud run jobs execute comms-agent --region asia-south1
```
Confirm: Gmail scan runs, Telegram summary arrives

### Definition of done
- Scout runs automatically at 8am IST without laptop
- Writer webhook works 24/7 from Cloud Run URL
- Comms runs every 2 hours automatically
- All secrets stored in Secret Manager
- ngrok no longer needed
- All agents log to Google Sheets correctly







---

## Git Repository + CI/CD Pipeline

### Step 1 — Create GitHub repository

1. Go to https://github.com/new
2. Repository name: job-agent
3. Private repository (contains sensitive code)
4. Don't initialize with README (we have code already)
5. Click Create repository
6. Copy the repository URL

### Step 2 — Initialize Git locally

```bash
cd D:\Personal_Data\Projects_Self\job-agent
git init
git add .
git commit -m "Initial commit — 4 working agents"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/job-agent.git
git push -u origin main
```

### Step 3 — Create .gitignore BEFORE pushing

Critical — never push secrets to GitHub.
Create .gitignore with:
```
# Secrets — NEVER commit these
.env
*.json keys/
credentials.json
authorized_user.json
gmail_token.json
pending_applications.json

# Python
__pycache__/
*.pyc
*.pyo
.Python
venv/
env/
*.egg-info/

# OS
.DS_Store
Thumbs.db

# ngrok
ngrok.exe
```

### Step 4 — Add GitHub Actions CI/CD pipeline

Create this file:
.github/workflows/deploy.yml

```yaml
name: Deploy Job Agent to Cloud Run

on:
  push:
    branches:
      - main

env:
  PROJECT_ID: rational-autumn-494910-p2
  REGION: asia-south1
  IMAGE: asia-south1-docker.pkg.dev/rational-autumn-494910-p2/job-agent/agents

jobs:
  deploy:
    name: Build and Deploy
    runs-on: ubuntu-latest

    permissions:
      contents: read
      id-token: write

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Authenticate to GCP
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
          service_account: ${{ secrets.WIF_SERVICE_ACCOUNT }}

      - name: Set up Cloud SDK
        uses: google-github-actions/setup-gcloud@v2

      - name: Configure Docker
        run: gcloud auth configure-docker asia-south1-docker.pkg.dev

      - name: Build Docker image
        run: |
          docker build -t ${{ env.IMAGE }}:${{ github.sha }} .
          docker tag ${{ env.IMAGE }}:${{ github.sha }} ${{ env.IMAGE }}:latest

      - name: Push Docker image
        run: |
          docker push ${{ env.IMAGE }}:${{ github.sha }}
          docker push ${{ env.IMAGE }}:latest

      - name: Update Scout agent
        run: |
          gcloud run jobs update scout-agent \
            --image ${{ env.IMAGE }}:${{ github.sha }} \
            --region ${{ env.REGION }}

      - name: Update Writer agent
        run: |
          gcloud run deploy writer-agent \
            --image ${{ env.IMAGE }}:${{ github.sha }} \
            --region ${{ env.REGION }} \
            --no-traffic

          gcloud run services update-traffic writer-agent \
            --to-latest \
            --region ${{ env.REGION }}

      - name: Update Comms agent
        run: |
          gcloud run jobs update comms-agent \
            --image ${{ env.IMAGE }}:${{ github.sha }} \
            --region ${{ env.REGION }}

      - name: Update Applier agent
        run: |
          gcloud run jobs update applier-agent \
            --image ${{ env.IMAGE }}:${{ github.sha }} \
            --region ${{ env.REGION }}

      - name: Send Telegram notification
        run: |
          curl -s -X POST \
            "https://api.telegram.org/bot${{ secrets.TELEGRAM_BOT_TOKEN }}/sendMessage" \
            -d chat_id=${{ secrets.TELEGRAM_CHAT_ID }} \
            -d text="✅ Job Agent deployed successfully!
            
          Commit: ${{ github.sha }}
          Branch: ${{ github.ref_name }}
          Deployed by: ${{ github.actor }}
          
          All 4 agents updated and running 🚀"
```

### Step 5 — Set up Workload Identity Federation

CI/CD needs to authenticate with GCP without storing keys.
Use Workload Identity Federation (same as MFAdvisorApp).

```bash
# Create service account for GitHub Actions
gcloud iam service-accounts create github-actions \
  --display-name="GitHub Actions Deploy" \
  --project=rational-autumn-494910-p2

# Grant permissions needed
gcloud projects add-iam-policy-binding rational-autumn-494910-p2 \
  --member="serviceAccount:github-actions@rational-autumn-494910-p2.iam.gserviceaccount.com" \
  --role="roles/run.admin"

gcloud projects add-iam-policy-binding rational-autumn-494910-p2 \
  --member="serviceAccount:github-actions@rational-autumn-494910-p2.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"

gcloud projects add-iam-policy-binding rational-autumn-494910-p2 \
  --member="serviceAccount:github-actions@rational-autumn-494910-p2.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"

# Create Workload Identity Pool
gcloud iam workload-identity-pools create github-pool \
  --location=global \
  --display-name="GitHub Actions Pool" \
  --project=rational-autumn-494910-p2

# Create Workload Identity Provider
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global \
  --workload-identity-pool=github-pool \
  --display-name="GitHub Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --project=rational-autumn-494910-p2

# Allow GitHub repo to impersonate service account
gcloud iam service-accounts add-iam-policy-binding \
  github-actions@rational-autumn-494910-p2.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/attribute.repository/YOUR_GITHUB_USERNAME/job-agent" \
  --project=rational-autumn-494910-p2
```

### Step 6 — Add secrets to GitHub

Go to GitHub repo → Settings → Secrets and variables → Actions
Add these secrets:

```
WIF_PROVIDER      → projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider
WIF_SERVICE_ACCOUNT → github-actions@rational-autumn-494910-p2.iam.gserviceaccount.com
TELEGRAM_BOT_TOKEN → your token
TELEGRAM_CHAT_ID   → your chat ID
```

Note: ANTHROPIC_API_KEY and other runtime secrets
stay in GCP Secret Manager — not GitHub secrets.
GitHub secrets are only for deployment credentials.

### Step 7 — Test the pipeline

Make a small change to any agent file.
Add a comment line. Push to GitHub:

```bash
git add .
git commit -m "Test CI/CD pipeline"
git push
```

Go to GitHub → Actions tab.
Watch the pipeline run.
Should complete in 3-4 minutes.
Telegram message arrives confirming deployment.

### Definition of done
- Code pushed to private GitHub repository
- .gitignore prevents secrets from being committed
- Push to main branch triggers automatic deployment
- All 4 agents updated on Cloud Run automatically
- Telegram notification arrives after each deployment
- No manual deployment steps needed ever again




