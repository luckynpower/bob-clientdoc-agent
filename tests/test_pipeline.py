"""Phase-2 audit tests: chain/stage order, JSON shape, idempotency,
already-reminded -> none, reminder source (llm/template_fallback), guardrails,
and safe LLM-gateway failure.

Hermetic — no Turso, no network. Uses an in-memory FakeStorage and
monkeypatches the completeness stage and the gateway call.

Run:  python -m tests.test_pipeline
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import agent, reminders
from src.agent import (
    JSON_ACTION_ESCALATE,
    JSON_ACTION_NONE,
    JSON_ACTION_REMIND,
    process_client,
    run_cycle,
)
from src.clients import Client
from src.reminders import SOURCE_LLM, SOURCE_TEMPLATE, DraftedReminder

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")


class FakeStorage:
    """In-memory stand-in that records call order and enforces idempotency."""

    def __init__(self):
        self.documents = []
        self.reminders = []          # (client_id, tier, message, source)
        self.review_queue = []       # (client_id, reason)
        self.calls = []              # ordered method-name trace
        self.schema_inited = False

    def _t(self, name):
        self.calls.append(name)

    def init_schema(self):
        self._t("init_schema")
        self.schema_inited = True

    def upsert_client(self, **kw):
        self._t("upsert_client")

    def upsert_document(self, client_id, label, status, file_path=None):
        self._t("upsert_document")
        self.documents.append((client_id, label, status, file_path))

    def has_open_review_item(self, client_id):
        self._t("has_open_review_item")
        return any(rq[0] == client_id for rq in self.review_queue)

    def reminder_exists(self, client_id, tier):
        self._t("reminder_exists")
        return any(r[0] == client_id and r[1] == tier for r in self.reminders)

    def log_reminder_once(self, client_id, tier, message_text, source=None):
        self._t("log_reminder_once")
        if any(r[0] == client_id and r[1] == tier for r in self.reminders):
            return False
        self.reminders.append((client_id, tier, message_text, source))
        return True

    def add_to_review_queue(self, client_id, reason, detail=None):
        self._t("add_to_review_queue")
        if (client_id, reason) in self.review_queue:
            return
        self.review_queue.append((client_id, reason))

    def close(self):
        self._t("close")


def _client(**kw) -> Client:
    base = dict(
        client_id="CLTEST",
        client_name="Test Co",
        client_type="Sole Proprietor",
        gst_registered=False,
        required_documents=["Bank statement"],
        submitted_documents=[],
        missing_documents=["Bank statement"],
        submission_status="Partial",
        days_since_first_request=0,
        reminders_sent=0,
        last_contact_channel="Email",
        contact_email="test@example.com",
    )
    base.update(kw)
    return Client(**base)


# Records the order in which the patched completeness stage ran, per test.
_completeness_marker = {"ran": False}


def _patch_completeness(status_map):
    """Force stage 2 to return {label: (status, file_path)}; no DB writes."""
    shaped = {label: (status, None) for label, status in status_map.items()}

    def fake(client):
        _completeness_marker["ran"] = True
        return shaped

    agent.check_completeness = fake


def _force_source(source):
    """Force draft_reminder's source: SOURCE_LLM or SOURCE_TEMPLATE."""
    if source == SOURCE_LLM:
        reminders._call_gateway = lambda prompt: "LLM-generated reminder text."
    else:
        reminders._call_gateway = lambda prompt: None  # -> template fallback


class GatewayCounter:
    """Replacement for reminders._call_gateway that counts invocations."""

    def __init__(self, return_text="LLM-generated reminder text."):
        self.calls = 0
        self.return_text = return_text

    def __call__(self, prompt):
        self.calls += 1
        return self.return_text


def main() -> int:
    _force_source(SOURCE_TEMPLATE)

    # ---- 1. Chain / stage order --------------------------------------
    print("stage order (extract -> update checklist -> action):")
    _patch_completeness({"Bank statement": "missing"})
    st = FakeStorage()
    c = _client(days_since_first_request=8, reminders_sent=0)  # tier 2
    _completeness_marker["ran"] = False
    process_client(c, st)
    # update checklist (upsert_document) must precede the reminder write.
    order_ok = (
        _completeness_marker["ran"]
        and "upsert_document" in st.calls
        and "log_reminder_once" in st.calls
        and st.calls.index("upsert_document") < st.calls.index("log_reminder_once")
    )
    check("update-checklist precedes reminder write", order_ok)
    check("completeness stage ran before writes", _completeness_marker["ran"])

    print("\nrun_cycle stage order (intake before per-client work):")
    _patch_completeness({"Bank statement": "submitted"})
    st = FakeStorage()
    agent.load_clients = lambda storage: [_client(missing_documents=[])]
    agent.load_manifest = lambda: {}
    run_cycle(st)
    check("init_schema is first call in run_cycle", st.calls[0] == "init_schema")

    # ---- 2. JSON output shape ----------------------------------------
    print("\nJSON output shape:")
    _patch_completeness({"Bank statement": "missing"})
    st = FakeStorage()
    c = _client(days_since_first_request=8, reminders_sent=0)  # tier 2 -> remind
    r = process_client(c, st)
    j = r.to_json_dict()
    check("keys are exactly the 5 required", set(j.keys()) ==
          {"client_id", "missing_docs", "tier", "action", "reason"})
    check("action is 'remind' for tier-2 missing", j["action"] == JSON_ACTION_REMIND)
    check("missing_docs is a list", isinstance(j["missing_docs"], list))
    check("tier is an int", isinstance(j["tier"], int))
    check("reason is a non-empty string", isinstance(j["reason"], str) and j["reason"])

    print("\nJSON action mapping for the cases:")
    _patch_completeness({"Bank statement": "submitted"})
    st = FakeStorage()
    r = process_client(_client(missing_documents=[]), st)
    check("complete -> action none", r.to_json_dict()["action"] == JSON_ACTION_NONE)

    _patch_completeness({"Bank statement": "ambiguous"})
    st = FakeStorage()
    r = process_client(_client(), st)
    check("ambiguous -> action escalate_to_human",
          r.to_json_dict()["action"] == JSON_ACTION_ESCALATE)
    check("ambiguous reason names the document", "Bank statement" in r.reason)

    _patch_completeness({"Bank statement": "missing"})
    st = FakeStorage()
    r = process_client(_client(days_since_first_request=20, reminders_sent=3), st)
    check("tier3 -> action escalate_to_human",
          r.to_json_dict()["action"] == JSON_ACTION_ESCALATE)

    # ---- 3. Idempotency ----------------------------------------------
    print("\nidempotency (run twice, no duplicate rows):")
    _patch_completeness({"Bank statement": "missing"})
    st = FakeStorage()
    c = _client(days_since_first_request=8, reminders_sent=0)  # tier 2
    process_client(c, st)
    process_client(c, st)   # second identical run
    check("only one reminder_log row for (client, tier)", len(st.reminders) == 1)

    _patch_completeness({"Bank statement": "missing"})
    st = FakeStorage()
    c = _client(days_since_first_request=20, reminders_sent=3)
    process_client(c, st)
    process_client(c, st)
    check("only one review_queue row on repeat tier-3", len(st.review_queue) == 1)

    print("\nidempotency vs '3+ reminders -> Tier 3' rule:")
    _patch_completeness({"Bank statement": "missing"})
    st = FakeStorage()
    base = dict(missing_documents=["Bank statement"])
    process_client(_client(days_since_first_request=0, reminders_sent=0, **base), st)  # t0
    process_client(_client(days_since_first_request=4, reminders_sent=1, **base), st)  # t1
    process_client(_client(days_since_first_request=8, reminders_sent=2, **base), st)  # t2
    r4 = process_client(_client(days_since_first_request=8, reminders_sent=3, **base), st)
    tiers_logged = sorted(t for _, t, _, _ in st.reminders)
    check("exactly 3 reminders across tiers 0,1,2", tiers_logged == [0, 1, 2])
    check("no tier-3 reminder logged", all(t != 3 for _, t, _, _ in st.reminders))
    check("4th touch escalates to human",
          r4.to_json_dict()["action"] == JSON_ACTION_ESCALATE)

    # ---- 3b. Already-reminded -> action none (follow-up #1) ----------
    print("\nsecond run at same tier -> action none:")
    _patch_completeness({"Bank statement": "missing"})
    st = FakeStorage()
    c = _client(days_since_first_request=8, reminders_sent=0)  # tier 2
    r_first = process_client(c, st)
    r_second = process_client(c, st)
    check("first run action is remind",
          r_first.to_json_dict()["action"] == JSON_ACTION_REMIND)
    check("second run action is none",
          r_second.to_json_dict()["action"] == JSON_ACTION_NONE)
    check("second run reason is 'already reminded at this tier'",
          r_second.reason == "already reminded at this tier")
    check("still only one reminder row", len(st.reminders) == 1)

    # ---- 3b-ii. Re-run makes ZERO gateway calls (follow-up) ----------
    print("\nre-run makes no gateway calls for already-reminded clients:")
    counter = GatewayCounter()
    reminders._call_gateway = counter
    _patch_completeness({"Bank statement": "missing"})
    st = FakeStorage()
    c = _client(days_since_first_request=8, reminders_sent=0)  # tier 2
    process_client(c, st)                       # first run -> 1 gateway call
    check("first run makes exactly one gateway call", counter.calls == 1)
    process_client(c, st)                       # second run -> should draft nothing
    check("second run makes zero additional gateway calls", counter.calls == 1)
    check("reminder_exists is consulted", "reminder_exists" in st.calls)
    # restore for later sections
    _force_source(SOURCE_TEMPLATE)

    # ---- 3c. Reminder source recorded (follow-up #2) -----------------
    print("\nreminder source (llm vs template_fallback):")
    _force_source(SOURCE_LLM)
    _patch_completeness({"Bank statement": "missing"})
    st = FakeStorage()
    process_client(_client(days_since_first_request=8, reminders_sent=0), st)
    check("source recorded as 'llm' when gateway succeeds",
          st.reminders and st.reminders[0][3] == SOURCE_LLM)

    _force_source(SOURCE_TEMPLATE)
    _patch_completeness({"Bank statement": "missing"})
    st = FakeStorage()
    process_client(_client(days_since_first_request=8, reminders_sent=0), st)
    check("source recorded as 'template_fallback' when gateway fails",
          st.reminders and st.reminders[0][3] == SOURCE_TEMPLATE)

    # draft_reminder itself reports source correctly.
    _force_source(SOURCE_LLM)
    d_llm = reminders.draft_reminder(_client(), 1)
    check("draft_reminder reports source llm", d_llm.source == SOURCE_LLM)
    _force_source(SOURCE_TEMPLATE)
    d_tpl = reminders.draft_reminder(_client(), 1)
    check("draft_reminder reports source template_fallback",
          d_tpl.source == SOURCE_TEMPLATE)
    check("tier3 drafted has None text and None source",
          reminders.draft_reminder(_client(), 3) == DraftedReminder(None, None))

    # ---- 4. Complete-client gate -------------------------------------
    print("\ncomplete-client gate (no reminder / no escalation even at tier 3):")
    _patch_completeness({"Bank statement": "submitted"})
    st = FakeStorage()
    c = _client(days_since_first_request=30, reminders_sent=0, missing_documents=[])
    r = process_client(c, st)
    check("complete client: no reminder", st.reminders == [])
    check("complete client: no review_queue", st.review_queue == [])
    check("complete client: json action none", r.json_action == JSON_ACTION_NONE)

    # ---- 5. Tier 3 routing -------------------------------------------
    print("\nTier 3 routing:")
    _patch_completeness({"Bank statement": "missing"})
    st = FakeStorage()
    c = _client(days_since_first_request=14, reminders_sent=0)  # tier 3 by days
    r = process_client(c, st)
    check("tier3 goes to review_queue", ("CLTEST", "tier_3_escalation") in st.review_queue)
    check("tier3 sends no reminder", st.reminders == [])
    check("tier3 reminder_text is None", r.reminder_text is None)

    # ---- 6. Gateway failure is safe ----------------------------------
    print("\ndraft_reminder safe failure when gateway unreachable:")
    from urllib import request as _urlreq
    from src import config as _cfg

    saved_urlopen = _urlreq.urlopen
    saved_url = _cfg.LLM_GATEWAY_URL
    _cfg.LLM_GATEWAY_URL = "https://gateway.invalid/v1/chat"  # make it "configured"

    def _raise(*a, **k):
        raise ConnectionError("gateway down")

    _urlreq.urlopen = _raise
    gw_crashed = False
    gw_result = "sentinel"
    try:
        gw_result = reminders._call_gateway("hi")
    except Exception:
        gw_crashed = True
    check("_call_gateway swallows network error (no raise)", not gw_crashed)
    check("_call_gateway returns None on failure", gw_result is None)
    _urlreq.urlopen = saved_urlopen
    _cfg.LLM_GATEWAY_URL = saved_url

    saved = reminders._call_gateway
    reminders._call_gateway = lambda prompt: None  # caught failure -> None
    drafted = reminders.draft_reminder(_client(), 2)
    check("falls back to template string on gateway failure",
          isinstance(drafted.text, str) and drafted.text
          and drafted.source == SOURCE_TEMPLATE)

    _patch_completeness({"Bank statement": "missing"})
    st = FakeStorage()
    crashed = False
    try:
        process_client(_client(days_since_first_request=8, reminders_sent=0), st)
    except Exception:
        crashed = True
    check("process_client does not crash on gateway failure", not crashed)
    reminders._call_gateway = saved

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} checks passed.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
