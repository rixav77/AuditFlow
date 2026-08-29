"""Layer 2.5 — ROBUSTNESS / STRESS evaluation.

1. Perturbation suite (deterministic, offline): copy a batch, apply single-axis
   narration mutations that must NOT change ground-truth verdicts (reference
   canonicalization — one of ToolSandbox's hard cases), re-run the engine, compare
   each work_key's verdict vs baseline. Instabilities are published, not hidden.
2. pass^k (needs a provider): ask the same question k times; measure citation-set
   consistency (tau-bench pass^k inspiration). Mocked in tests; live when EVAL_LIVE.
3. Error-recovery probe (needs a provider): a tool fails at runtime; we record what
   the agent says (human-reviewable, not a strict wording assertion in CI).
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

import llm.tools as tools_mod
from engine.runner import run_pipeline

ID_RE = re.compile(r"\b(?:ORD|PAY|SET|BANK|ADJ)[-_]?\d+\b", re.IGNORECASE)


def _sig(ident: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", ident.upper())


def _apply_narration(mutation: str):
    if mutation == "narration_hyphen":
        return lambda s: re.sub(ID_RE, lambda m: m.group(0).replace("-", ""), s)
    if mutation == "narration_lower":
        return lambda s: re.sub(ID_RE, lambda m: m.group(0).replace("ORD-", "ord#"), s)
    if mutation == "narration_spaced":
        return lambda s: " " + " ".join(s.split()) + " "
    if mutation == "narration_prefix":
        return lambda s: "NEFT CR " + s
    return lambda s: s


MUTATION_NAMES = ["narration_hyphen", "narration_lower", "narration_spaced", "narration_prefix"]


def run_pipeline_verdicts(db: Path) -> dict[str, tuple[str, str]]:
    verdicts, _, _ = run_pipeline(db)
    return {v.work_key: (v.cls or "", v.reason_code or "") for v in verdicts}


def _make_mutated_copy(src: Path, mutation: str) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix=f"eval_mut_{mutation}_"))
    out = tmp / src.name
    shutil.copy(src, out)
    mapper = _apply_narration(mutation)
    con = sqlite3.connect(out)
    rows = con.execute("SELECT bank_txn_id, narration FROM bank_txns").fetchall()
    for bid, narr in rows:
        new = mapper(narr)
        if new != narr:
            con.execute(
                "UPDATE bank_txns SET narration = ? WHERE bank_txn_id = ?", (new, bid)
            )
    con.commit()
    con.close()
    return out


def perturbation_suite(batch: Path, mutations=None) -> dict:
    mutations = mutations or MUTATION_NAMES
    baseline = run_pipeline_verdicts(batch)
    out = {}
    for name in mutations:
        copy = _make_mutated_copy(batch, name)
        try:
            var = run_pipeline_verdicts(copy)
        finally:
            shutil.rmtree(copy.parent, ignore_errors=True)
        changed = {
            k: {"baseline": baseline.get(k), "mutated": var.get(k, ("MISSING", "MISSING"))}
            for k in baseline
            if var.get(k) != baseline.get(k)
        }
        out[name] = {
            "cases": len(baseline),
            "changed": len(changed),
            "stability": round((len(baseline) - len(changed)) / max(1, len(baseline)), 4),
            "details": changed,
        }
    return {"baseline_cases": len(baseline), "per_mutation": out}


# ---- pass^i & error recovery (need a chat provider) -------------------------


def pass_k(db: str, question: str, k: int = 3, provider=None) -> dict:
    from llm.chat_agent import iter_chat

    runs = []
    sets = []
    for i in range(k):
        events = list(iter_chat(db, question, provider))
        answer = next((e.get("content", "") for e in events if e.get("type") == "answer"), "")
        cited = frozenset(_sig(m) for m in ID_RE.findall(answer))
        sets.append(cited)
        runs.append({"run": i + 1, "answer": answer[:180], "cited_ids": sorted(cited)})
    majority = Counter(sets).most_common(1)
    consistency = (majority[0][1] / k) if majority else 0.0
    return {
        "k": k,
        "question": question[:120],
        "citation_set_consistency": round(consistency, 4),
        "per_run": runs,
    }


def error_recovery_probe(db: str, question: str, fail_tool: str, provider=None) -> dict:
    """Route the chosen tool to a hard failure, run the agent, record the
    trajectory — best-effort and human-reviewable, never a false CI gate."""
    from llm.chat_agent import iter_chat

    def _failing(*_a, **_kw):
        return {"ok": False, "error": f"simulated failure in {fail_tool}"}

    original = getattr(tools_mod.ToolBox, fail_tool, None)
    if original is None:
        return {"fail_tool": fail_tool, "error": "unknown tool", "events": []}
    setattr(tools_mod.ToolBox, fail_tool, _failing)
    try:
        events = list(iter_chat(db, question, provider))
    finally:
        if original is not None:
            setattr(tools_mod.ToolBox, fail_tool, original)
    answer = next((e.get("content", "") for e in events if e.get("type") == "answer"), "")
    lowered = " " + answer.lower() + " "
    acknowledges = any(tok in lowered for tok in ("could not", "failed", "error", "unable"))
    return {
        "fail_tool": fail_tool,
        "acknowledges_failure": acknowledges,
        "answer": answer[:300],
        "tool_calls": [e for e in events if e.get("type") == "tool_call"],
    }