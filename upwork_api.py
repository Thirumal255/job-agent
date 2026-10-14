"""Upwork official GraphQL API client (read-only job search).

⚠ Untested against the live API — written before API access was approved.
Query shape follows Upwork's GraphQL docs (marketplaceJobPostingsSearch).
Verify field names on the first real run.

Setup once you have API access (developers.upwork.com):
  1. Add to .env: UPWORK_CLIENT_ID, UPWORK_CLIENT_SECRET, UPWORK_REDIRECT_URI
     (the redirect URI must match the one registered on your Upwork app)
  2. Run:  python upwork_api.py login
     It prints a URL — open it, approve, then paste the URL you were
     redirected to. The token is saved to data/upwork_token.json.
  3. For Cloud Run, copy that token to GCS:
     gcloud storage cp data/upwork_token.json gs://<bucket>/upwork_token.json

Upwork refresh tokens rotate, so the token is stored as a blob (GCS in Cloud
Run) and rewritten after every refresh rather than kept in Secret Manager.
"""
import json
import os
import sys
import time
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from common import read_blob, write_blob

AUTHORIZE_URL = "https://www.upwork.com/ab/account-security/oauth2/authorize"
TOKEN_URL = "https://www.upwork.com/api/v3/oauth2/token"
GRAPHQL_URL = "https://api.upwork.com/graphql"
TOKEN_BLOB = "upwork_token.json"

CLIENT_ID = os.getenv("UPWORK_CLIENT_ID")
CLIENT_SECRET = os.getenv("UPWORK_CLIENT_SECRET")
REDIRECT_URI = os.getenv("UPWORK_REDIRECT_URI")

SEARCH_QUERY = """
query Search($filter: MarketplaceJobPostingsSearchFilter) {
  marketplaceJobPostingsSearch(
    marketPlaceJobFilter: $filter, sortAttributes: [{field: RECENCY}]
  ) {
    edges { node {
      id title description ciphertext publishedDateTime totalApplicants
      amount { rawValue currency }
      hourlyBudgetMin { rawValue currency }
      hourlyBudgetMax { rawValue currency }
      job { contractTerms { contractType } }
      client {
        totalSpent { rawValue currency } totalHires totalPostedJobs
        verificationStatus location { country }
      }
    } }
  }
}
"""

SCREENING_QUERY = """
query Screening($id: ID!) {
  marketplaceJobPosting(id: $id) {
    contractorSelection { proposalRequirement {
      screeningQuestions { question sequenceNumber }
    } }
  }
}
"""


class UpworkError(Exception):
    pass


def is_configured():
    return bool(CLIENT_ID and CLIENT_SECRET and read_blob(TOKEN_BLOB))


def _save_token(data):
    token = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": time.time() + float(data.get("expires_in", 86400)),
    }
    write_blob(TOKEN_BLOB, json.dumps(token))


def _access_token():
    raw = read_blob(TOKEN_BLOB)
    if not raw:
        raise UpworkError("No Upwork token — run: python upwork_api.py login")
    token = json.loads(raw)
    if token["expires_at"] - time.time() > 300:
        return token["access_token"]

    print("   🔄 Refreshing Upwork token...")
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }, timeout=15)
    if not resp.ok:
        raise UpworkError(f"Token refresh failed ({resp.status_code}) — run: python upwork_api.py login")
    data = resp.json()
    data.setdefault("refresh_token", token["refresh_token"])
    _save_token(data)
    return data["access_token"]


def _query(query, variables):
    resp = requests.post(
        GRAPHQL_URL,
        headers={"Authorization": f"Bearer {_access_token()}"},
        json={"query": query, "variables": variables},
        timeout=30,
    )
    if not resp.ok:
        raise UpworkError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    if body.get("errors"):
        raise UpworkError(body["errors"][0].get("message", "unknown GraphQL error"))
    return body["data"]


def _money(m):
    return float(m["rawValue"]) if m and m.get("rawValue") else None


def _to_job(node):
    contract = ((node.get("job") or {}).get("contractTerms") or {}).get("contractType")
    if contract == "HOURLY":
        lo, hi = _money(node.get("hourlyBudgetMin")), _money(node.get("hourlyBudgetMax"))
        fmt = lambda v: f"{v:g}" if v else "?"
        budget = f"Hourly ${fmt(lo)}–${fmt(hi)}/hr" if (lo or hi) else "Hourly (rate not set)"
    else:
        amount = _money(node.get("amount"))
        hi = amount
        budget = f"Fixed ${amount:,.0f}" if amount else "Fixed (budget not set)"

    client = node.get("client") or {}
    spent = _money(client.get("totalSpent"))
    country = (client.get("location") or {}).get("country") or "?"
    client_str = (
        f"{country} · payment {'verified' if client.get('verificationStatus') == 'VERIFIED' else 'NOT verified'}"
        f" · ${spent or 0:,.0f} spent · {client.get('totalHires') or 0} hires"
        f" / {client.get('totalPostedJobs') or 0} jobs posted"
    )

    cipher = node.get("ciphertext") or ""
    url = "https://www.upwork.com/jobs/" + (cipher if cipher.startswith("~") else "~" + cipher)
    return {
        "upwork_id": node["id"],
        "title": node.get("title", ""),
        "description": node.get("description") or "",
        "url": url,
        "budget": budget,
        "max_rate": hi,  # hourly max or fixed amount, used for pre-filtering
        "contract": contract or "?",
        "client": client_str,
        "company": f"Upwork client ({country})",
        "proposals": str(node.get("totalApplicants") or "?"),
        "posted": node.get("publishedDateTime", ""),
        "source": "Upwork",
    }


def search_jobs(terms, per_term=50, verified_payment_only=True):
    """Run one search per term, newest first. Returns de-duplicated job dicts."""
    jobs = {}
    for term in terms:
        data = _query(SEARCH_QUERY, {"filter": {
            "searchExpression_eq": term,
            "verifiedPaymentOnly_eq": verified_payment_only,
            "pagination_eq": {"first": per_term},
        }})
        for edge in data["marketplaceJobPostingsSearch"]["edges"]:
            job = _to_job(edge["node"])
            jobs.setdefault(job["upwork_id"], job)
        print(f"   ✔ '{term}': {len(data['marketplaceJobPostingsSearch']['edges'])} results")
    return list(jobs.values())


def screening_questions(upwork_id):
    data = _query(SCREENING_QUERY, {"id": upwork_id})
    req = (((data.get("marketplaceJobPosting") or {}).get("contractorSelection") or {})
           .get("proposalRequirement") or {})
    qs = req.get("screeningQuestions") or []
    return [q["question"] for q in sorted(qs, key=lambda q: q.get("sequenceNumber", 0))]


def login():
    """One-time OAuth authorization-code flow, run locally."""
    if not (CLIENT_ID and CLIENT_SECRET and REDIRECT_URI):
        print("❌ Set UPWORK_CLIENT_ID, UPWORK_CLIENT_SECRET and UPWORK_REDIRECT_URI in .env first.")
        sys.exit(1)
    url = AUTHORIZE_URL + "?" + urlencode({
        "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
    })
    print(f"1. Open this URL and approve access:\n\n   {url}\n")
    redirected = input("2. Paste the full URL you were redirected to: ").strip()
    code = parse_qs(urlparse(redirected).query).get("code", [None])[0]
    if not code:
        print("❌ No ?code= found in that URL.")
        sys.exit(1)
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
    }, timeout=15)
    if not resp.ok:
        print(f"❌ Token exchange failed: {resp.status_code} {resp.text[:300]}")
        sys.exit(1)
    _save_token(resp.json())
    print("✅ Upwork token saved to data/upwork_token.json")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        login()
    else:
        print(__doc__)
