# bob-clientdoc-agent

# Client Document Collection Agent

**NUS-ISS × AWS "Show Me Your Agents" Hackathon — Public Category**
**Problem Statement:** Client Document Collection

> Accounting firms require clients to submit invoices, receipts, bank statements, and supporting documents before monthly bookkeeping can begin. Many clients submit incomplete information or respond only after repeated reminders, delaying bookkeeping work and extending financial closing cycles.

## What This Project Does

An agent-driven assistant that tracks each client's required-document checklist for the monthly close, sends reminders that escalate in tone and frequency the longer a client stays unresponsive, checks incoming submissions for completeness, and flags missing or ambiguous items automatically. Account managers get a real-time view of who's outstanding — the agent only escalates to a human when a client is unresponsive past a set threshold or submits something it can't confidently classify.

## Tech Stack

| Layer | Tool |
|---|---|
| Development environment | Kiro (spec-driven IDE) |
| Agent orchestration | OpenClaw |
| Hosting / deployment | AWS Lightsail |
| Model | AWS Bedrock (Claude) via the hackathon LLM gateway |

## Repository Structure

```
├── .gitignore
├── .env.example          # copy to .env and fill in real credentials (never commit .env)
├── README.md              # this file
├── data/
│   ├── synthetic_clients.csv       # 20 anonymised sample client records
│   └── client_documents/           # matching sample invoices/receipts/statements
│       └── _manifest.csv
├── docs/
│   └── week1_notes.md    # checklist, persona, escalation rules, assumptions
├── src/                    # agent code (Week 2 onward)
└── tests/                  # tests (Week 2 onward)
```

## Progress

- [x] **Week 1** — Document checklist per client type, 20 synthetic client records, sample documents, account-manager persona and escalation rules (`docs/week1_notes.md`)
- [ ] **Week 2** — Tracking + reminder loop, completeness checker, OpenClaw orchestration
- [ ] **Week 3** — Account-manager dashboard, escalation history/audit trail, human review handoff

## Setup

1. Clone the repo and `cd` into it
2. Copy `.env.example` to `.env` and fill in your team's LLM gateway credentials (from Slack) — never commit this file
3. See `docs/week1_notes.md` for the checklist and escalation rules this build follows

## Guardrails

The agent recommends and nudges only. An account manager always makes the final call on any client outreach beyond the first reminder tier, and remains responsible for any compliance-sensitive follow-up.

## Team Bob

Team code: 0VHPNQUI

Members: 
- Goh An Jun
- Lee Ying Xuan
- Loh Kok Hao
- Nay Htet Thar