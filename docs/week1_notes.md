# Week 1 Notes — Data & Rules Foundation

**Problem statement:** Client Document Collection
**Milestone:** Define required-document checklist per client type; construct synthetic anonymised sample client records; agree reminder tone/escalation rules with a simulated account-manager persona.

## Assumptions
- No real accounting firm partner available for this build — client persona, sample data, and account-manager persona are self-constructed and assumed representative of a small SME bookkeeping practice.
- Document types stay fixed for the duration of the build (no new document categories introduced mid-project).
- All client names, companies, and figures below are synthetic/fictitious.

---

## 1. Document Checklist per Client Type

| Client Type | Required Documents |
|---|---|
| Sole Proprietor / Freelancer | Sales invoices/receipts issued; purchase receipts/invoices; business bank statement; expense receipts |
| Partnership | All of the above, plus partnership capital contribution/drawing records |
| Private Limited Company (Pte Ltd) | Sales & purchase invoices; bank statements (all accounts); payroll register + CPF contribution records; fixed asset purchase invoices; prior month's management accounts |
| Retail / Trading (holds inventory) | Everything under Pte Ltd, plus supplier invoices, POS daily sales summary, month-end stock count |
| Service-based Business (no inventory) | Sales invoices; bank statements; contractor invoices (if any); business expense receipts |
| GST-registered (any type above) | Add: GST input/output tax summary |

## 2. Synthetic Client Dataset

- 20 anonymised client records (`data/synthetic_clients.csv`), generated with the Faker library — all names/companies are fictitious.
- Each record includes: client type, GST-registration flag, required vs. submitted vs. missing documents, submission status, days since first document request, number of reminders sent, and preferred contact channel.
- Submission states are deliberately distributed to cover every case the agent needs to handle:
  - **Complete** — nothing outstanding
  - **Partial** — some documents in, some missing
  - **Not Started** — no submissions yet
  - **Overdue / Unresponsive** — 3+ reminders sent, no response (the Week 3 human-handoff test case)
- Matching sample documents (`data/client_documents/`) were generated for every document a client actually "submitted" — clients with missing documents correctly have no file for that item, so the Week 2 completeness checker has real gaps to detect. A `_manifest.csv` maps every file back to its client and document type.

## 3. Account Manager Persona & Escalation Rules

**Persona:** Priya, Senior Account Manager at *Maple & Co Bookkeeping* — manages ~30 SME clients, professional and warm in tone but firm about deadlines, prioritises preserving the client relationship while keeping the monthly close on schedule.

| Tier | Trigger | Tone | Example Message |
|---|---|---|---|
| 0 — Initial request | Day 0 (start of monthly close) | Friendly, informative | "Hi [Client], time for our monthly close! Could you send over [missing docs] by [date]? Let us know if anything's unclear." |
| 1 — Gentle nudge | Day 3–5, no response | Warm, no pressure | "Hi [Client], just a friendly reminder — we're still waiting on [missing docs]. No rush, just don't want it to slip!" |
| 2 — Firm reminder | Day 7–10, still missing | Direct, states impact | "Hi [Client], we still need [missing docs] to complete this month's bookkeeping. Delaying further will push back your financial reports — could you send these by [date]?" |
| 3 — Escalate to human | Day 14+ OR 3 reminders sent, no response | Agent stops nudging; internal alert only | "CL0XX has not responded after 3 reminders (14+ days). Recommend personal outreach — possible relationship or workload issue." |

The agent only recommends/nudges automatically through Tier 2. Tier 3 always routes to Priya for manual follow-up — the agent does not contact an unresponsive client further on its own.

## Files Produced This Week
- `data/synthetic_clients.csv` — 20 client records
- `data/client_documents/` — matching sample PDFs + `_manifest.csv`
- `docs/week1_notes.md` — this document

## Next: Week 2
Build the tracking + reminder loop using this checklist, dataset, and escalation logic as inputs.