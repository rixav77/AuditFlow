"""Ablation harness: deterministic (null assist) vs LLM-assisted pipeline.

Usage: uv run python -m eval.ablation --db data/synthetic/batch_seed42.db \
         --gt data/synthetic/ground_truth_seed42.json
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from eval.run import evaluate


def run_mode(mode: str, db: Path, gt: Path) -> dict:
    os.environ["ASSIST_MODE"] = mode
    import importlib

    import engine.assist as assist_mod

    importlib.reload(assist_mod)
    from engine.runner import run_pipeline as rp  # re-import under patched module

    verdicts, links, elapsed = rp(db)
    results = {"verdicts": [v.__dict__ for v in verdicts]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(results, fh)
        rpath = Path(fh.name)
    rep = evaluate(db, gt, rpath)
    rep["assist_stats"] = dict(assist_mod.STATS)
    rep["elapsed_ms"] = round(elapsed * 1000)
    os.unlink(rpath)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(prog="ablation")
    ap.add_argument("--db", required=True)
    ap.add_argument("--gt", required=True)
    args = ap.parse_args()

    null = run_mode("null", Path(args.db), Path(args.gt))
    try:
        live = run_mode("live", Path(args.db), Path(args.gt))
    except Exception as e:
        live = {"error": str(e)}

    keys = (
        "match_rate",
        "exception_precision",
        "exception_recall",
        "correct_abstention_rate",
        "false_match_rate",
        "exact_bundle_rate",
    )
    print("== ABLATION null vs live ==")
    for k in keys:
        nn, ll = null.get(k), live.get(k)
        delta = "" if ll is None or nn is None else f"Δ{ll - nn:+.4f}"
        print(f" {k:26} null={nn}  live={ll}  {delta}")
    print(" triggers:", "null:", null.get("assist_stats"), "| live:", live.get("assist_stats"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
