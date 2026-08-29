"""Eval harness v1: engine verdicts vs ground truth (DATA.md §5.3 definitions)."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

MATCHED = {"matched", "matched_after_reasoning"}
EXCEPTION = {"matched_after_reasoning", "genuine_discrepancy", "unresolved"}
GT_EXCEPTION = {"matched_after_reasoning", "genuine_discrepancy", "unresolved"}


def load_results(path: Path) -> dict[str, dict]:
    data = json.loads(Path(path).read_text())
    return {v["work_key"]: v for v in data["verdicts"]}


def noise_keys(db: Path) -> set[str]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        keys: set[str] = set()
        captured = {
            r["order_id"]
            for r in con.execute(
                "SELECT order_id FROM payments WHERE status='captured' AND order_id IS NOT NULL"
            )
        }
        for o in con.execute("SELECT order_id,status FROM orders"):
            if o["status"] == "cancelled" or o["order_id"] not in captured:
                keys.add(o["order_id"])
        return keys
    finally:
        con.close()


def evaluate(db: Path, gt_path: Path, results_path: Path) -> dict:
    gt = json.loads(Path(gt_path).read_text())
    pred = load_results(results_path)
    _ = noise_keys(db)

    gt_txns = {t["work_key"]: t for t in gt["transactions"]}
    confusion: Counter[tuple[str, str]] = Counter()
    matched_keys = 0
    abstain_hits = 0
    false_matches = 0
    gt_unres = gt_exc = 0
    pred_noise_fp = 0

    linkage_p = 0
    pred_pairs_total = gt_pairs_total = 0
    exact_bundles = bundle_count = 0

    bid_clusters: dict[str, set[str]] = {}
    for k, v in pred.items():
        if v.get("internal_status"):
            continue
        if v.get("bundle_bid"):
            bid_clusters.setdefault(v["bundle_bid"], set()).add(k)

    for wk, t in gt_txns.items():
        gcls = t["expected_class"]
        v = pred.get(wk)
        pcls = v["cls"] if v else "MISSING"
        if v and v.get("internal_status"):
            pcls = "internal"
        confusion[(gcls, pcls)] += 1
        if gcls == "unresolved":
            gt_unres += 1
            abstain_hits += pcls == "unresolved"
        if gcls in GT_EXCEPTION:
            gt_exc += 1
        matched_keys += gcls in MATCHED and pcls in MATCHED
        false_matches += pcls in MATCHED and gcls in {"unresolved", "genuine_discrepancy"}

        universe: set[str] = {wk}
        partial_causes = {
            "DUPLICATE_BANK_CREDIT",
            "SHORT_SETTLED",
            "MALFORMED_SOURCE_ROW",
            "UNEXPLAINED_DELTA",
            "MISSING_SETTLEMENT",
        }
        for typ, ids in t["expected_links"].items():
            if t["cause_code"] in partial_causes and typ not in {"orders", "payments"}:
                continue
            universe.update(ids)
        predicted_cluster = set(v.get("members", [])) if v else set()
        predicted_cluster = {m.split(":", 1)[1] if ":" in m else m for m in predicted_cluster}
        predicted_cluster &= universe
        predicted_cluster.add(wk)

        def pairs(members: set[str]) -> set[tuple[str, str]]:
            ms = sorted(members)
            return {(a, b) for i, a in enumerate(ms) for b in ms[i + 1 :]}

        exp_pairs = pairs(universe)
        prd_pairs = pairs(predicted_cluster)
        linkage_p += len(exp_pairs & prd_pairs)
        pred_pairs_total += len(prd_pairs)
        gt_pairs_total += len(exp_pairs)
        bundle_count += 1
        exact_bundles += exp_pairs == prd_pairs

    for k, v in pred.items():
        if k in gt_txns or v.get("internal_status") in ("ignored_noise", "orphan_chain"):
            continue
        if v["cls"] in EXCEPTION:
            pred_noise_fp += 1

    denom = len(gt_txns)
    match_rate = matched_keys / max(
        1, sum(1 for t in gt_txns.values() if t["expected_class"] in MATCHED)
    )
    pred_exc = sum(c for (g, p), c in confusion.items() if p in EXCEPTION and g != "MISSING")
    exc_precision = sum(
        c for (g, p), c in confusion.items() if p in EXCEPTION and g in GT_EXCEPTION
    ) / max(1, pred_exc)
    exc_recall = sum(
        c for (g, p), c in confusion.items() if g in GT_EXCEPTION and p in EXCEPTION
    ) / max(1, gt_exc)
    report = {
        "batch": gt.get("batch_id"),
        "difficulty": gt.get("difficulty"),
        "transactions": denom,
        "match_rate": round(match_rate, 4),
        "exception_precision": round(exc_precision, 4),
        "exception_recall": round(exc_recall, 4),
        "correct_abstention_rate": round(abstain_hits / max(1, gt_unres), 4),
        "false_match_rate": round(
            false_matches
            / max(
                1,
                sum(
                    1
                    for t in gt_txns.values()
                    if t["expected_class"] in {"unresolved", "genuine_discrepancy"}
                ),
            ),
            4,
        ),
        "linkage_pair_precision": round(linkage_p / max(1, pred_pairs_total), 4),
        "linkage_pair_recall": round(linkage_p / max(1, gt_pairs_total), 4),
        "exact_bundle_rate": round(exact_bundles / max(1, bundle_count), 4),
        "noise_false_positives": pred_noise_fp,
        "confusion": {f"{g}->{p}": c for (g, p), c in sorted(confusion.items()) if c},
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser(prog="eval")
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--gt", required=True, type=Path)
    ap.add_argument("--results", required=True, type=Path)
    args = ap.parse_args()
    rep = evaluate(args.db, args.gt, args.results)
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
