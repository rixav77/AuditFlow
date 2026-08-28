"""Reconciliation explanations — the "explain" voice of the controller.

Stage 5a: batch summary (deterministic, from the verdicts table the runner persists).
Stage 5b: per-exception explanation: structured facts + citations, plus an optional
LLM narrative that goes through llm.explain (citation-validated, deterministic
fallback when the provider is unavailable or cites IDs not in the payload).

Facts ALWAYS come from structured records + verdicts (AGENTS.md principle 1);
the LLM only words the narrative and may never invent IDs or amounts.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from engine.linkage import Indexes, build_bundles
from engine.runner import load_tables

EXCEPTION_CLASSES = {
    "matched_after_reasoning",
    "genuine_discrepancy",
    "unresolved",
    "data_quality",
}


def load_verdicts(db_path: str | Path) -> dict[str, dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return {r["work_key"]: dict(r) for r in con.execute("SELECT * FROM verdicts")}
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()


def _verdict_dict(row: dict) -> dict:
    def _load(col: str) -> list:
        try:
            return json.loads(row.get(col) or "[]")
        except json.JSONDecodeError:
            return []

    return {
        "work_key": row["work_key"],
        "bundle_bid": row.get("bundle_bid"),
        "cls": row["cls"],
        "reason_code": row["reason_code"],
        "evidence_ids": _load("evidence_json"),
        "checks_run": _load("checks_run_json"),
        "findings": _load("findings_json"),
        "llm_assists": _load("llm_assists_json"),
        "members": _load("members_json"),
        "internal_status": row.get("internal_status"),
    }


def explain_batch(db_path: str | Path) -> dict:
    """Deterministic batch summary from persisted verdicts (Stage 5a)."""
    verdicts = load_verdicts(db_path)
    counted = [v for v in verdicts.values() if not v["internal_status"]]
    mix = Counter(v["cls"] for v in counted if v["cls"])
    exceptions = [
        {"work_key": v["work_key"], "cls": v["cls"], "reason": v["reason_code"]}
        for v in sorted(counted, key=lambda x: x["work_key"])
        if v["cls"] in EXCEPTION_CLASSES
    ]
    reconciled = mix.get("matched", 0) + mix.get("matched_after_reasoning", 0)
    return {
        "batch_db": Path(db_path).name,
        "total_scored": len(counted),
        "class_mix": dict(mix),
        "exception_count": len(exceptions),
        "exceptions": exceptions[:25],
        "fragment": f"{reconciled}/{len(counted)} transactions reconciled",
    }


def _find_bundle(bundles: list, work_key: str):
    for b in bundles:
        if (
            work_key in b.orders
            or work_key in b.payments
            or work_key in b.settlements
            or work_key in b.bank_txns
            or work_key in b.adjustments
        ):
            return b
    return None


def exception_payload(db_path: str | Path, work_key: str) -> dict | None:
    """Structured explanation payload for one work_key (facts only, no LLM)."""
    db_path = Path(db_path)
    row = load_verdicts(db_path).get(work_key)
    if row is None:
        return None
    verdict_dict = _verdict_dict(row)
    tabs = load_tables(db_path)
    ix = Indexes(tabs)
    bundles, _ = build_bundles(tabs)
    b = _find_bundle(bundles, work_key)
    records: list[dict] = []
    if b is not None:
        from llm.explain import build_payload

        records = build_payload(ix, b, verdict_dict)["records"]
    return {
        "work_key": work_key,
        "batch_db": db_path.name,
        "verdict": {
            "cls": verdict_dict["cls"],
            "reason_code": verdict_dict["reason_code"],
            "checks_run": verdict_dict["checks_run"],
            "evidence_ids": verdict_dict["evidence_ids"],
        },
        "findings": verdict_dict["findings"],
        "members": verdict_dict["members"],
        "records": records,
    }


def explain_exception(
    db_path: str | Path, work_key: str, provider=None
) -> dict | None:
    """Full exception explanation (Stage 5b): facts + narrative.

    provider=None (default) → deterministic narrative. Pass a provider for an
    LLM narrative; it is citation-validated and falls back deterministically.
    """
    db_path = Path(db_path)
    row = load_verdicts(db_path).get(work_key)
    if row is None:
        return None
    verdict_dict = _verdict_dict(row)
    tabs = load_tables(db_path)
    ix = Indexes(tabs)
    bundles, _ = build_bundles(tabs)
    b = _find_bundle(bundles, work_key)

    from llm.citations import verify_narrative
    from llm.explain import build_payload, deterministic_fallback

    payload = (
        build_payload(ix, b, verdict_dict)
        if b is not None
        else {
            "work_key": work_key,
            "verdict": verdict_dict,
            "findings": verdict_dict["findings"],
            "records": [],
        }
    )

    if provider is not None and b is not None:
        from llm.explain import explain_verified

        narrative, report = explain_verified(ix, b, verdict_dict, provider)
    else:
        narrative = deterministic_fallback(payload)
        report = verify_narrative(narrative, payload).to_dict()
        report["source"] = "deterministic"

    return {
        "work_key": work_key,
        "batch_db": db_path.name,
        "verdict": {
            "cls": verdict_dict["cls"],
            "reason_code": verdict_dict["reason_code"],
            "checks_run": verdict_dict["checks_run"],
            "evidence_ids": verdict_dict["evidence_ids"],
        },
        "findings": verdict_dict["findings"],
        "members": verdict_dict["members"],
        "records": payload["records"],
        "explanation": narrative,
        "explanation_source": report["source"],
        "verification": report,
    }
