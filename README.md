# bob-clientdoc-agent

# Client Document Collection Agent

**NUS-ISS × AWS "Show Me Your Agents" Hackathon — Public Category**
**Problem Statement:** Client Document Collection

> Accounting firms require clients to submit invoices, receipts, bank statements, and supporting documents before monthly bookkeeping can begin. Many clients submit incomplete information or respond only after repeated reminders, delaying bookkeeping work and extending financial closing cycles.

## What This Project Does

An agent-driven assistant that tracks each client's required-document checklist for the monthly close, drafts reminders that escalate in tone the longer a client stays unresponsive, checks submissions for completeness, and flags missing or ambiguous items automatically. Account managers get a dashboard view of who's outstanding and a human review queue — the agent only escalates to a human when a client is unresponsive past a set threshold or submits something it can't confidently classify. All tier and escalation decisions are made by deterministic Python; the LLM only drafts reminder wording.

## Tech Stack

| Layer | Tool | Status |
|---|---|---|
| Development environment | Kiro (spec-driven IDE) | in use |
| Language / runtime | Python 3 | in use |
| Persistence | Turso (libSQL) via `libsql-client` | in use |
| PDF parsing | pdfplumber (text extraction, no OCR) | in use |
| Reminder drafting | LLM gateway (Bedrock/Claude proxy) with template fallback | in use |
| Dashboard | Flask + Jinja2 (server-rendered) | in use |
| Agent orchestration | OpenClaw | in use — runs the full cycle (see below) |
| Hosting / deployment | AWS Lightsail | in use — hosts the app + OpenClaw agent |

## Repository Structure

```
├── .env.example              # copy to .env and fill in credentials (never commit .env)
├── .gitignore / .kiroignore  # ignore .env, keys, caches
├── requirements.txt          # libsql-client, pdfplumber, python-dotenv, Flask
├── README.md                 # this file
├── data/
│   ├── synthetic_clients.csv                     # 20 anonymised sample client records
│   └── client_documents/client_documents/        # sample PDFs, one folder per client
│       └── _manifest.csv                          # maps each file to its client + doc type
├── docs/
│   ├── week1_notes.md            # checklist, persona, escalation rules, assumptions
│   ├── week2_notes.md            # tracking + reminder loop decisions
│   └── phase2_code_overview.md   # developer map of the agent
├── src/                      # the agent (see Phase 2 below)
├── dashboard/                # Flask account-manager dashboard (see Phase 3 below)
└── tests/                    # hermetic test suites (no live Turso / network)
```

## Progress

- [x] **Week 1** — Document checklist per client type, 20 synthetic client records, sample documents, account-manager persona and escalation rules (`docs/week1_notes.md`)
- [x] **Week 2 (Phase 2)** — Tracking + reminder loop, tier decision logic, completeness checker, LLM-drafted reminders with fallback, Turso persistence, guardrails (`src/`, `run_cycle.py`)
- [x] **Week 3 (Phase 3)** — Account-manager dashboard: client overview, merged escalation-history/audit trail, review queue with approve/dismiss, Basic auth + CSRF (`dashboard/`)

## Setup

1. Clone the repo and `cd` into it.
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in the credentials (see [Environment variables](#environment-variables)). **Never commit `.env`** — it is git- and Kiro-ignored.
4. See `docs/week1_notes.md` for the checklist and escalation rules this build follows.

## Phase 2 — Tracking + Reminder Loop

The core agentic loop lives in `src/` and is driven by `run_cycle.py`.

```
src/
├── config.py         # .env + path loading (Turso, LLM gateway, dashboard auth)
├── storage.py        # Turso (libSQL): clients, documents, reminder_log, review_queue
├── clients.py        # load_clients() — read synthetic_clients.csv, upsert into Turso
├── tiers.py          # get_tier() — pure deterministic function, thresholds from week1_notes.md
├── completeness.py   # check_completeness() — pdfplumber text-presence check (no OCR)
├── reminders.py      # draft_reminder() — LLM gateway with per-tier template fallback
└── agent.py          # run_cycle() / process_client() — ordered stages + guardrails
```

`run_cycle()` runs one ordered pass per client: **intake → text extraction + completeness → update checklist → decide tier → decide action**. Tier is computed *after* completeness, by deterministic Python only.

### Run it

```
# ensure TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are set in .env
python run_cycle.py            # human summary + CSV verification table
python run_cycle.py --json     # one JSON object per client on stdout (logs to stderr)
```

Each `--json` line: `{client_id, missing_docs, tier, action, reason}` where `action` is `none` / `remind` / `escalate_to_human`.

### Guardrails (enforced in code, not just prompt)

- **Tier 3 never gets a client-facing message.** `draft_reminder()` returns no text for Tier 3, and `process_client()` routes Tier-3 clients to `review_queue` (`tier_3_escalation`).
- **Complete clients are never reminded or escalated**, even if elapsed days would put them at Tier 3.
- **Any ambiguous document routes to `review_queue` regardless of tier.** A PDF that yields no extractable text is treated as unverifiable (no OCR) and never triggers an auto-message.
- **A client with an open review item is not reminded** while it awaits a human decision.
- **Reminders are deduped per `(client_id, tier)`** (`log_reminder_once`), and a re-run makes zero gateway calls for an already-reminded client.
- **Sending is stubbed** — `_send_stub()` logs a line; no real email/WhatsApp is dispatched.

### Deterministic vs LLM

The LLM (via `reminders.draft_reminder` → the gateway) drafts reminder *wording only*, and is sent only the client name, tier, and missing-item list. Tier assignment, whether to remind/escalate, completeness, and review routing are all deterministic and never read LLM output. On any gateway failure the code falls back to a per-tier template and records `source = "template_fallback"` (vs `"llm"`) in `reminder_log`.

### Notes on the data model

- The GST input/output summary is a *derived* tax figure, not a client-uploaded file. It is tracked as a required line item (`not_tracked`) but excluded from the missing/submitted comparison, matching the CSV's ground truth.

## Deployment & Orchestration

The app runs on an **AWS Lightsail** instance, with **OpenClaw** acting as the agent orchestrator.

- **OpenClaw** drives the agent by invoking `run_cycle.py`, which executes the full ordered cycle in one pass (intake → completeness → checklist update → tier/action + guardrails) and persists results to Turso. The OpenClaw agent/skill is configured on the Lightsail VPS rather than committed to this repo, so the repository itself contains no OpenClaw config.
- **AWS Lightsail** hosts both the OpenClaw agent and the Flask dashboard. Deployment/runtime config (service definitions, environment/secrets, scheduling) lives on the instance; `.env` is gitignored and set on the server.
- Because the dashboard and the agent communicate only through the Turso database, no code-level coupling is required between them — OpenClaw writes rows, the dashboard reads them.

Not yet done: the individual cycle stages are not exposed as separate OpenClaw-callable tools; OpenClaw currently calls the whole cycle as a single unit.

## Phase 3 — Account Manager Dashboard

A read-mostly Flask dashboard over the same Turso database Phase 2 populates. It never runs the agent loop, so it works the same whether the data was written by running the script directly or by the OpenClaw-hosted agent that invokes it.

```
dashboard/
├── app.py            # Flask routes, Basic auth, CSRF
├── check_decide.py   # manual check for the approve/dismiss flow
├── templates/        # base, overview, history (merged timeline), review_queue (Jinja2)
└── static/style.css
```

### Run it

```
# set DASHBOARD_USER + DASHBOARD_AUTH_TOKEN (+ DASHBOARD_SECRET_KEY) in .env first
python run_cycle.py            # (if needed) populate Turso with Phase 2 data
python -m dashboard.app        # serves http://127.0.0.1:5000
```

The **entire dashboard is gated by HTTP Basic auth**. Log in with `DASHBOARD_USER` as the username and `DASHBOARD_AUTH_TOKEN` as the password. If the token is unset the app returns HTTP 500 rather than serving anything unprotected.

Routes:
- `GET /` — client overview (id, name, type, live tier, status, missing/ambiguous counts); clients needing attention sorted to the top.
- `GET /client/<client_id>/history` — merged per-client audit timeline of reminders (with `source`) and review events (Tier 3 escalations / ambiguous documents), newest first, with decision + note + who/when.
- `GET /review-queue` — open (undecided) review items with client name and reason.
- `POST /review-queue/<id>/decide` — approve or dismiss one open item (`decision` = `approved`/`dismissed`, optional `note`). CSRF-protected. Rejects a second decision (409).

### Human-in-the-loop

- **Approve** = the manager confirms the flag and takes the case over. **Dismiss** = judged a false alarm. Both record `decision`, `note`, `decided_at`, `decided_by` and close the item.
- A decided `(client_id, reason, detail)` is not re-queued next cycle; a **changed `detail`** (a new ambiguous document, or a different tier) does re-queue.
- The dashboard's only write is the approve/dismiss decision (`decide_review_item`); every other table is read-only from here. No reopen/delete exists.

### Verify the approve/dismiss flow

```
python -m dashboard.check_decide
```

Dismisses one open item, confirms the open count drops by one and a second decision is rejected, then restores the item (clears the decision) so the data is left as found.

## Testing

Hermetic suites (in-memory sqlite / fakes + Flask test client; no live Turso, no network):

```
python -m tests.test_guardrails         # tier thresholds + hard guardrails
python -m tests.test_pipeline           # stage order, --json shape, idempotency, gateway fallback
python -m tests.test_timeline           # merged escalation-history timeline
python -m tests.test_review_decisions   # approve/dismiss, re-queue suppression, notes never affect tier
python -m tests.test_dashboard          # Basic auth, CSRF, decide route
```

## Environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Purpose |
|---|---|
| `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` | Turso (libSQL) database connection |
| `LLM_GATEWAY_URL`, `LLM_GATEWAY_API_KEY`, `LLM_MODEL` | LLM gateway for reminder drafting (optional — falls back to templates if unset/unreachable) |
| `DASHBOARD_USER`, `DASHBOARD_AUTH_TOKEN` | Dashboard Basic-auth login (token required to serve the dashboard) |
| `DASHBOARD_SECRET_KEY` | Signs the session cookie used for CSRF (set a long random string) |
| `APP_ENV` | Environment label (default `development`) |
| `AWS_*` | AWS credentials/region for the Lightsail deployment (configured on the VPS, not read by the application code itself) |
| `OPENROUTER_API_KEY` | Optional bring-your-own-key LLM alternative; not read by current code |

## Guardrails (product framing)

The agent recommends and nudges only, through Tier 2. An account manager always makes the final call on any client outreach beyond that, and remains responsible for any compliance-sensitive follow-up.

## Known limitations

**Functional**
- **No OCR / no field extraction** — `completeness.py` only checks whether a PDF yields text; scanned/photographed pages are flagged `ambiguous`, not read.
- **No real delivery** — reminders are logged by `_send_stub`, not sent by email/WhatsApp.
- **`days_since_first_request` is a CSV snapshot**, not computed from a live clock, so tiering is only as fresh as the input data.
- **Synthetic data only** — runs against `data/synthetic_clients.csv` (20 clients); no real upload ingestion.
- **Single shared dashboard login**, no per-user accounts or roles; `decided_by` records that one username.
- **Manager actions are limited** to approve/dismiss with a note — no reopen, delete, or record editing.
- **OpenClaw orchestration is coarse-grained** — OpenClaw invokes `run_cycle.py` as a single unit (intake → completeness → checklist → tier/action). The individual stages are not yet exposed as separate OpenClaw-callable tools. The OpenClaw agent/skill definition lives on the Lightsail VPS, not in this repo.

**Security / robustness**
- **Basic auth over plain HTTP** on Flask's dev server — safe on localhost only; production needs TLS and a real WSGI server.
- **Secret-key fallback** — if `DASHBOARD_SECRET_KEY` is unset, a random key is generated per process, so sessions/CSRF break across restarts or multiple workers.
- **Client PII + message text are logged** by `_send_stub` (contact channel, email/id, full reminder text) at INFO to stderr.
- **No explicit DB indexes** on `reminder_log` / `review_queue` (fine at this scale, not beyond).
- **Dependencies are unpinned** (`>=` floors) in `requirements.txt` — not reproducible builds.

## Team Bob

Team code: 0VHPNQUI

Members:
- Goh An Jun
- Lee Ying Xuan
- Loh Kok Hao
- Nay Htet Thar
