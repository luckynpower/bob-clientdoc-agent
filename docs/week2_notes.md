# Week 2 Notes — Tracking + Reminder Loop

**Milestone:** Build the tracking + reminder loop: per-client checklist
state, escalating reminder generation, automated completeness check on
incoming documents, flagging of missing/ambiguous items.

## Assumptions
- Reminder channel (email/WhatsApp sandbox) is available; delivery is still
  stubbed (logged, not sent).
- Escalation rules agreed in Week 1 remain stable and unchanged.
- The hackathon's shared LLM Gateway may or may not be reachable at run time.
  `draft_reminder()` now calls the gateway when configured and falls back to
  a per-tier template on any failure, so the loop runs identically with or
  without live credentials.

## Technical Decisions Made This Phase
- **Persistence:** Turso (hosted libSQL/SQLite-compatible) chosen over a
  full Postgres server — gives networked, remote-accessible state without
  the setup overhead of running/managing a Postgres instance, appropriate
  for the build timeline. See README "Known Limitations" for the
  Postgres-vs-SQLite tradeoff if scaled beyond this build.
- **Document parsing:** pdfplumber (digital PDF text extraction), not
  OCR. The synthetic test documents are clean digital PDFs; OCR for
  scanned/photographed real-world submissions is scoped as a future
  extension, not built here.
- **LLM calls:** `draft_reminder()` calls the hackathon LLM Gateway
  (Bedrock/Claude proxy) using `LLM_GATEWAY_URL` / `LLM_GATEWAY_API_KEY` /
  `LLM_MODEL`. The request payload is deliberately small — only client name,
  tier, and the missing-item list — because the gateway WAF rejects large
  requests, and there is no need to send document contents to draft a chase
  message. If document text is ever included, it is wrapped in an explicit
  UNTRUSTED-DATA envelope so external text is treated as data, not
  instructions. Any failure (not configured, unreachable, timeout, bad
  status/shape) is logged to stderr and falls back to the per-tier template;
  `draft_reminder()` never raises, so one bad gateway call cannot crash a run.

## Pipeline Stage Order (audited)
`run_cycle()` is one clean loop running these stages in order for every
client:
1. **intake** — load client records + the index of their uploaded documents
2. **text extraction + completeness** — pdfplumber text-presence check per
   PDF (no OCR, no field extraction), compared to the required-document list
   (GST summary is `not_tracked`, excluded from missing checks). Pure: it
   computes statuses and writes nothing.
3. **update checklist** — a distinct stage that writes the stage-2 statuses to
   the `documents` table (`agent.stage_update_checklist`). Separated from
   stage 2 so completeness has no storage side effects.
4. **decide reminder tier** — deterministic Python only (`tiers.get_tier`),
   never the LLM; computed *after* completeness
5. **decide action** — enforce guardrails and act

## Idempotency
Running the cycle twice in a row produces no duplicate rows:
- `clients` / `documents` — upsert.
- `review_queue` — one open row per (client_id, reason); once an item is
  decided (approved or dismissed), the same (client_id, reason, detail) is not
  re-queued, but a changed `detail` re-queues.
- `reminder_log` — `log_reminder_once()` records **one reminder per
  (client_id, tier)**. It deliberately does not key on message text (the
  wording changes once the LLM is live). This caps client-facing reminders at
  one per tier (0/1/2), i.e. at most 3 lifetime, consistent with the
  "3+ reminders → Tier 3" rule (Tier 3 sends no reminder at all).
- On a re-run, a client already reminded at its current tier reports JSON
  action `none` with reason **"already reminded at this tier"** (not
  `remind`), so a second run neither re-sends nor mislabels the action.

## reminder_log.source
Each reminder row records where its wording came from in a `source` column:
`llm` (gateway produced the text) or `template_fallback` (gateway
unconfigured/unreachable/failed, per-tier template used). The column is added
by a safe migration — `init_schema()` runs `ALTER TABLE reminder_log ADD
COLUMN source TEXT` only if the column is absent, so it is non-destructive on
an existing table and existing rows simply get a NULL source.

## What Was Built
- `load_clients()` — loads `data/synthetic_clients.csv` into Turso
- `load_manifest()` — explicit intake of the uploaded-document index
- `get_tier()` — pure function mapping days-since-request + reminders-sent
  to escalation Tier 0–3, per `docs/week1_notes.md`
- `check_completeness()` — text-extraction + completeness check; flags each
  required document submitted / missing / ambiguous / not_tracked
- `draft_reminder()` — LLM-gateway-backed reminder text, Tiers 0–2 only,
  with safe template fallback
- `run_cycle()` — the ordered loop across all clients; Tier 3 and
  ambiguous-document cases route to `review_queue` instead of a client message
- `run_cycle.py` — default human summary table; `--json` emits one JSON
  object per client (`client_id`, `missing_docs`, `tier`,
  `action` ∈ none/remind/escalate_to_human, `reason`) on stdout, logs on stderr

## Guardrail Confirmed
No client-facing message is ever generated for a Tier 3 (unresponsive)
client — enforced as a hard branch in `run_cycle()`, not left to prompt
instruction alone.

Tier is acted on only when a client has at least one missing or ambiguous document — a client with nothing outstanding is never reminded or escalated no matter how high `days_since_first_request` or `reminders_sent` push its tier, since `days_since_first_request` was generated independently of submission status in the synthetic data.

## Explicitly Deferred to Week 3
- Account-manager dashboard / UI
- Real email/WhatsApp delivery integration
- OCR support

## Next: Week 3
Build the dashboard surfacing overdue clients and escalation history from
the `reminder_log` and `review_queue` tables, plus the human-review
handoff flow.
