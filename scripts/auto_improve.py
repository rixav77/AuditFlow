"""AutoAgent-style overnight self-improvement loop (fenced, eval-gated).

Usage:
  # live (real provider, real cost — capped):
  uv run python -m scripts.auto_improve --db data/synthetic/batch_seed42.db \
      --gt data/synthetic/ground_truth_seed42.json --iters 4

  # dry: only capture the baseline gate report
  uv run python -m scripts.auto_improve --db ... --gt ... --baseline-only
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--iters", type=int, default=4)
    ap.add_argument("--hint", default="tighten tool-usage guidance; keep honesty rules intact")
    ap.add_argument("--baseline-only", action="store_true")
    args = ap.parse_args()

    from llm.provider import FallbackProvider, MockProvider, available_providers
    from memory.improve import auto_improve, run_gate

    if available_providers():
        provider = FallbackProvider()
        mode = "live"
    else:
        provider = MockProvider()
        mode = "mock (gate only — proposals will be static)"

    with tempfile.TemporaryDirectory() as td:
        if args.baseline_only:
            gate = run_gate(args.db, args.gt, provider, Path(td))
            print(json.dumps(gate, indent=2, default=str))
            return 0
        report = auto_improve(
            args.db, args.gt, provider, Path(td), iterations=args.iters, hint=args.hint
        )
    print(json.dumps({"mode": mode, **report}, indent=2, default=str))
    print(f"log: data/traces/improvement_log.jsonl — kept {report['kept']}/{args.iters}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
