"""Explanation synthesis: engine findings -> human narrative, citation-verified.

Pipeline per narrative (RARR-style minimal revision, RESEARCH.md §5):
  generate -> verify (llm.citations) -> if errors: ONE repair pass with explicit
  error feedback -> verify -> accept if hard-clean, else deterministic fallback.
Hard = cited IDs exist (Layer A) + every money figure traces to the payload
(Layer B). Soft = per-sentence lexical support (Layer C, ALCE recall/precision).
Every attempt is traced to data/traces/ (llm/traces.py).
"""

from __future__ import annotations

import json
from pathlib import Path

from engine.linkage import Indexes
from engine.types import Bundle
from llm.citations import VerificationReport, repair_feedback, verify_narrative
from llm.traces import log_event

KB_PATH = Path(__file__).parent / "domain_knowledge.md"


def build_payload(ix: Indexes, b: Bundle, verdict_dict: dict) -> dict:
    def row(table: str, ident: str) -> dict:
        return ix.__dict__.get(table, {}).get(ident, {})

    members = []
    for m in verdict_dict.get("members", []):
        kind, ident = m.split(":", 1)
        table = {
            "order": "orders",
            "pay": "payments",
            "settle": "settlements",
            "bank": "banks",
            "adj": "adjustments",
        }.get(kind)
        if table:
            r = getattr(ix, table).get(ident)
            if r:
                members.append(r)
    return {
        "work_key": verdict_dict["work_key"],
        "verdict": {
            "cls": verdict_dict.get("cls"),
            "reason_code": verdict_dict.get("reason_code"),
            "checks_run": verdict_dict.get("checks_run", []),
        },
        "findings": verdict_dict.get("findings", []),
        "records": members,
    }


def validate_citations(text: str, payload: dict) -> tuple[bool, list[str]]:
    """Back-compat wrapper: Layer-A ID check only (see llm.citations for full)."""
    report = verify_narrative(text, payload)
    return (not report.id_errors), report.id_errors


FALLBACK_TEMPLATES = {
    # Each sentence deliberately repeats the work_key (a payload ID) and quotes
    # payload vocabulary (cls/reason/checks/delta), so deterministic narratives
    # pass the citation verifier at recall 1.0 — no free-floating prose.
    "matched": (
        "{key}: verdict cls matched; all amounts reconcile to delta {delta} paise; "
        "{key} has no exception."
    ),
    "matched_after_reasoning": (
        "{key}: verdict cls matched_after_reasoning; delta {delta} paise explained by "
        "reason {reason}; evidence {ev}; {key} reconciles after reasoning."
    ),
    "genuine_discrepancy": (
        "{key}: verdict cls genuine_discrepancy; reason {reason}; evidence {ev}; "
        "{key} needs escalation for review."
    ),
    "unresolved": (
        "{key}: verdict cls unresolved; reason {reason}; checks_run {checks}; "
        "no payload record explains the delta; evidence insufficient; escalate {key}."
    ),
    "data_quality": (
        "{key}: verdict cls data_quality; reason {reason}; {key} flagged for repair."
    ),
}


def deterministic_fallback(payload: dict) -> str:
    v = payload["verdict"]
    ev = ", ".join(e for e in v.get("evidence_ids", [])[:3]) or "see records"
    deltas = [abs(f["delta_paise"]) for f in payload["findings"] if f.get("delta_paise")]
    return FALLBACK_TEMPLATES.get(v["cls"], "{key}: verdict cls {cls}; see findings.").format(
        key=payload["work_key"],
        cls=v.get("cls", "unknown"),
        delta=max(deltas, default=0),
        reason=v.get("reason_code", "none"),
        ev=ev,
        checks=", ".join(v.get("checks_run", [])) or "all applicable checks",
    )


SYSTEM_PROMPT = """You are the explanation voice of a finance reconciliation controller.
Rules:
1. Use ONLY facts from the JSON payload. Never invent IDs, amounts, dates, fees.
2. Cite record IDs verbatim (e.g., PAY-500013) for every claim.
3. UNITS: every amount in the payload is integer PAISE (INR). ₹1 = 100 paise.
   When you state a rupee figure, keep the paise source visible, e.g.
   "₹3,969.00 (396900 paise)". Never rescale digits silently; never treat paise
   as rupees.
4. ≤110 words. Plain finance language. State the verdict first.
5. If unresolved: list which checks ran and say evidence is insufficient.
6. If matched_after_reasoning: name the cause and the exact amount it explains.
"""


def explain_verified(
    ix: Indexes, b: Bundle, verdict_dict: dict, provider=None
) -> tuple[str, dict]:
    """Generate a narrative and return (text, verification_report_dict)."""
    payload = build_payload(ix, b, verdict_dict)
    work_key = payload["work_key"]

    if provider is None:
        text = deterministic_fallback(payload)
        rep = verify_narrative(text, payload).to_dict()
        rep["source"] = "deterministic"
        log_event("explain", work_key, "verification", rep)
        return text, rep

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + KB_PATH.read_text()},
        {"role": "user", "content": json.dumps(payload, default=str)},
    ]
    last_report: VerificationReport | None = None
    for attempt in range(2):
        resp = provider.chat(messages, temperature=0.2)
        report = verify_narrative(resp.content, payload)
        last_report = report
        if report.verified and report.fully_supported:
            out = report.to_dict() | {"source": "llm", "attempts": attempt + 1}
            log_event("explain", work_key, "verification", out)
            return resp.content, out
        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content": repair_feedback(report)})

    # After retries: accept hard-clean text (soft flags stay visible in the
    # report), otherwise fall back to the deterministic narrative.
    assert last_report is not None
    if last_report.verified:
        out = last_report.to_dict() | {"source": "llm", "attempts": 2}
        log_event("explain", work_key, "verification", out)
        return messages[-2]["content"], out
    text = deterministic_fallback(payload)
    out = last_report.to_dict() | {"source": "deterministic_fallback", "attempts": 2}
    log_event("explain", work_key, "verification", out)
    return text, out


def explain(ix: Indexes, b: Bundle, verdict_dict: dict, provider=None) -> str:
    """Back-compat wrapper returning just the narrative text."""
    return explain_verified(ix, b, verdict_dict, provider)[0]
