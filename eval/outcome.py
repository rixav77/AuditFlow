"""Layer 1 — extended OUTCOME evaluation (deterministic, GT-backed).

Extends eval/run.py's class-level match rate with:
  - root-cause accuracy (per-cause correctness)
  - evidence-ID precision/recall and fabricated-link detection
  - decoy avoidance (a linked record from the GT `decoys` list is a hard fail)
  - abstention precision AND recall (from GT `abstention_expected`)
  - unsupported resolution rate (URR): confident resolution of a case the GT
    marks as truly-unresolvable (no evidence exists) — the metric that most
    matters for a finance controller.
  - per-cause trap breakdown (a "trap suite" computed from real causes)
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from eval.run import load_results

CONFIDENT = {"matched", "matched_after_reasoning", "genuine_discrepancy"}


def _member_ids(verdict: dict) -> set[str]:
    members = verdict.get("members")
    if isinstance(members, str):
        try:
            members = json.loads(members)
        except json.JSONDecodeError:
            members = []
    ids = set()
    for m in members or []:
        ids.add(m.split(":", 1)[1] if ":" in m else m)
    return ids


def _confusion_key(g: str, p: str) -> str:
    return f"{g}->{p}"


def evaluate_outcome(db: Path, gt_path: Path, results_path: Path) -> dict:
    gt = json.loads(Path(gt_path).read_text())
    pred = load_results(results_path)
    txns = gt["transactions"]

    cause_correct: Counter[str] = Counter()
    cause_total: Counter[str] = Counter()
    cause_mix: dict[str, Counter[str]] = defaultdict(Counter)

    # Global "expected universe" = every GT expected link + every GT work key.
    # Precision measures whether predicted members are real, known records
    # (cross-bundle linked records are legitimate even if they belong to another
    # work_key's bundle, so they must NOT be counted as fabrications).
    expected_universe: set[str] = set()
    per_case_exp: dict[str, set[str]] = {}
    for t in txns:
        exp = set()
        for ids in t.get("expected_links", {}).values():
            exp.update(ids)
        per_case_exp[t["work_key"]] = exp
        expected_universe.update(exp)
        expected_universe.add(t["work_key"])

    ev_exp = 0
    ev_correct = 0
    ev_pred_total = 0
    ev_pred_in_universe = 0
    fabricated: list[str] = []
    decoy_hits: list[str] = []

    abstain_pred = abstain_correct = abstain_gt = 0
    urr_num = urr_den = 0

    per_case: list[dict] = []

    for t in txns:
        wk = t["work_key"]
        v = pred.get(wk) or {}
        _internal = v.get("internal_status")
        pcls = v.get("cls") or (_internal or "MISSING")
        gcls = t["expected_class"]
        cause = t.get("cause_code", "UNKNOWN")

        cause_total[cause] += 1
        if pcls == gcls:
            cause_correct[cause] += 1
        cause_mix[cause][_confusion_key(gcls, pcls)] += 1

        g_abst = bool(t.get("abstention_expected"))
        if g_abst:
            abstain_gt += 1
        if pcls == "unresolved":
            abstain_pred += 1
            if g_abst:
                abstain_correct += 1

        if pcls in CONFIDENT:
            urr_den += 1
            if g_abst:
                urr_num += 1

        pred_ids = _member_ids(v)
        exp_ids = per_case_exp.get(wk, set())
        ev_correct += len(exp_ids & pred_ids)
        ev_exp += len(exp_ids)
        ev_pred_total += len(pred_ids)
        ev_pred_in_universe += len(pred_ids & expected_universe)
        decoys = set(t.get("decoys") or [])
        extra = (pred_ids - expected_universe) - decoys
        for x in extra:
            fabricated.append(f"{wk}:{x}")
        hits = [d for d in decoys if d in pred_ids]
        decoy_hits.extend(f"{wk}:{d}" for d in hits)

        per_case.append(
            {
                "work_key": wk,
                "expected_class": gcls,
                "predicted_class": pcls,
                "cause": cause,
                "abstention_expected": g_abst,
                "evidence_recall": round(len(exp_ids & pred_ids) / max(1, len(exp_ids)), 4),
                "evidence_precision": round(
                    len(pred_ids & expected_universe) / max(1, len(pred_ids)), 4
                )
                if pred_ids
                else None,
                "decoy_hit": bool(hits),
            }
        )

    ev_precision = round(ev_pred_in_universe / max(1, ev_pred_total), 4)
    ev_recall = round(ev_correct / max(1, ev_exp), 4)

    return {
        "root_cause_accuracy": round(
            sum(cause_correct[c] / max(1, cause_total[c]) for c in cause_total)
            / max(1, len(cause_total)),
            4,
        ),
        "root_cause_per_cause": {
            c: round(cause_correct[c] / max(1, cause_total[c]), 4) for c in cause_total
        },
        "cause_confusion": {c: dict(m) for c, m in cause_mix.items()},
        "abstention_precision": round(abstain_correct / max(1, abstain_pred), 4),
        "abstention_recall": round(abstain_correct / max(1, abstain_gt), 4),
        "abstention_gt": abstain_gt,
        "abstention_predicted": abstain_pred,
        "unsupported_resolution_rate": round(urr_num / max(1, urr_den), 4),
        "unsupported_resolutions": urr_num,
        "evidence_precision": ev_precision,
        "evidence_recall": ev_recall,
        "fabricated_link_count": len(fabricated),
        "fabricated_links": fabricated[:25],
        "decoy_hit_cases": len(decoy_hits),
        "decoy_hits": decoy_hits,
        "trap_breakdown": {
            c: round(cause_correct[c] / max(1, cause_total[c]), 4) for c in cause_total
        },
        "per_case": per_case,
    }
