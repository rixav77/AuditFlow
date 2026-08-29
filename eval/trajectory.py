"""Layer 2 — TRAJECTORY behavior evaluation (deterministic, no LLM judge).

A. Engine trajectory: from persisted verdicts we measure how the deterministic
   investigator arrived at each verdict — checks per case, and whether an
   `unresolved` verdict actually ran the EXHAUSTIVE_SEARCH gate (anti-
   premature-conclusion / anti-post-hoc-abstention).
B. Chat trajectory: from an events list (run_chat/iter_chat output) or a JSONL
   trace file we measure tool selection, redundant repeats, and — the tau-bench
   principle — whether every record ID cited in the final answer is a record ID
   that appeared in some tool result (judge the state, not the claim).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ID_RE = re.compile(r"\b(?:ORD|PAY|SET|BANK|ADJ)[-_]?\d+\b", re.IGNORECASE)
EXHAUSTIVE = "EXHAUSTIVE_SEARCH"


def sig(ident: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", ident.upper())


def _load_events(source) -> list[dict]:
    if isinstance(source, (str, Path)):
        out = []
        for line in Path(source).read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
    return list(source)


def eval_engine_trajectory(verdicts: list[dict], links: list[dict] | None = None) -> dict:
    scored = [v for v in verdicts if not v.get("internal_status")]
    per_case = []
    total_checks = 0
    for v in scored:
        checks = v.get("checks_run") or []
        total_checks += len(checks)
        per_case.append(
            {
                "work_key": v.get("work_key"),
                "cls": v.get("cls"),
                "n_checks": len(checks),
                "reason": v.get("reason_code"),
                "exhaustive_gate_ran": EXHAUSTIVE in checks,
            }
        )
    unresolved_without_gate = sum(
        1 for c in per_case if c["cls"] == "unresolved" and not c["exhaustive_gate_ran"]
    )
    return {
        "cases": len(scored),
        "avg_checks_per_case": round(total_checks / max(1, len(scored)), 2),
        "max_checks": max((c["n_checks"] for c in per_case), default=0),
        "min_checks": min((c["n_checks"] for c in per_case), default=0),
        "unresolved_without_exhaustive_gate": unresolved_without_gate,
        "per_case": per_case,
    }


def eval_chat_trajectory(source) -> dict:
    events = _load_events(source)
    tool_calls = [e for e in events if e.get("type") == "tool_call"]
    tool_results = [e for e in events if e.get("type") == "tool_result"]
    answers = [e for e in events if e.get("type") == "answer"]

    tool_seq = [tc.get("name", "?") for tc in tool_calls]
    redundant = sum(1 for i in range(1, len(tool_seq)) if tool_seq[i] == tool_seq[i - 1])
    tool_counts = Counter(tool_seq)

    verified = set()
    for res in tool_results:
        verified.update(sig(c) for c in res.get("citations", []))

    unsupported = []
    for ans in answers:
        for m in ID_RE.findall(ans.get("content", "")):
            if sig(m) not in verified:
                unsupported.append(m)

    return {
        "n_tool_calls": len(tool_calls),
        "n_tool_results": len(tool_results),
        "tool_calls_by_name": dict(tool_counts),
        "max_turns": len(tool_seq),
        "redundant_repeats": redundant,
        "unverified_citations": unsupported,
        "citation_state_ok": not unsupported,
        "distinct_tools_used": len(tool_counts),
    }
