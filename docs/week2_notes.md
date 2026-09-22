# Week 2 Notes — Tracking + Reminder Loop

**Milestone:** Build the tracking + reminder loop: per-client checklist
state, escalating reminder generation, automated completeness check on
incoming documents, flagging of missing/ambiguous items.

## Assumptions
- Reminder channel (email/WhatsApp sandbox) is available.
- Escalation rules agreed in Week 1 remain stable and unchanged.
- The hackathon's shared LLM Gateway key had not yet been received at the
  start of this phase — LLM-dependent steps (reminder wording) are
  implemented behind a stub function so the rest of the system can be
  built and tested without it, and swapped for a live call once available.

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
- **LLM calls:** stubbed via `draft_reminder()`, returning templated
  strings per escalation tier. Will be swapped for a live Bedrock/Claude
  call once the team's LLM Gateway credentials are available — no other
  code should need to change when that happens.

## What Was Built
- `load_clients()` — loads `data/synthetic_clients.csv` into Turso
- `get_tier()` — pure function mapping days-since-request + reminders-sent
  to escalation Tier 0–3, per `docs/week1_notes.md`
- `check_completeness()` — parses each client's submitted PDFs, flags
  missing or ambiguous documents
- `draft_reminder()` — stubbed reminder text generator, Tiers 0–2 only
- `run_cycle()` — orchestrates the full loop across all clients; Tier 3
  and ambiguous-document cases route to a review queue instead of an
  automatic client-facing message

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
