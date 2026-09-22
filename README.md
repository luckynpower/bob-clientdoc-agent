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
- [x] **Week 2 (Phase 2)** — Tracking + reminder loop, tier decision logic, completeness checker, Turso persistence, guardrails (`src/`, `run_cycle.py`)
- [ ] **Week 3** — Account-manager dashboard, escalation history/audit trail, human review handoff

## Phase 2 — Tracking + Reminder Loop

The core agentic loop lives in `src/` and is driven by `run_cycle.py`.

```
src/
├── config.py         # paths + .env (Turso) credential loading
├── storage.py        # Turso (libSQL): clients, documents, reminder_log, review_queue
├── clients.py        # load_clients() — read synthetic_clients.csv, upsert into Turso
├── tiers.py          # get_tier() — pure function, thresholds from week1_notes.md
├── completeness.py   # check_completeness() — pdfplumber (no OCR); submitted/missing/ambiguous
├── reminders.py      # draft_reminder() — STUB (Tiers 0-2 templates, None for Tier 3)
└── agent.py          # run_cycle() / process_client() — orchestration + guardrails
```

### Run it

```
pip install -r requirements.txt
# ensure TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are set in .env
python run_cycle.py            # runs the loop over all 20 clients + prints a verification table
python -m tests.test_guardrails   # unit checks for tier logic + both hard guardrails
```

### Guardrails (enforced in code, not just prompt)

- **Tier 3 never gets a client-facing message.** `draft_reminder()` hard-returns
  `None` for Tier 3, and `run_cycle()` routes Tier-3 clients to `review_queue`
  (`tier_3_escalation`) as a dedicated code branch.
- **Any ambiguous document routes to `review_queue` regardless of tier.** A
  submitted PDF that yields no extractable text is treated as unverifiable
  (no OCR this phase) and never triggers an auto-message.
- **Sending is stubbed** — the "send" step is a log line only; no real
  email/WhatsApp is dispatched.

### Notes on the data model

- The GST input/output summary is a *derived* tax figure, not a client-uploaded
  file. It is tracked as a required line item (`not_tracked` status) but excluded
  from the submitted/missing file comparison, matching the CSV's own
  `missing_documents` ground truth.
- `draft_reminder()` is the single seam for a later real Bedrock/Claude call —
  swapping in an LLM only touches that one function.

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