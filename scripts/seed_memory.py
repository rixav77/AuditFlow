"""Seed the global memory store from standing eval results (deterministic).

Usage:
  uv run python -m scripts.seed_memory --report data/synthetic/eval_report.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="data/synthetic/eval_report.json")
    args = ap.parse_args()

    from memory.ingest import ingest_deterministic
    from memory.store import MemoryStore

    report = json.loads(Path(args.report).read_text())
    store = MemoryStore()
    total = 0
    for row in report.get("batches", []):
        db = Path("data/synthetic") / str(row.get("batch", ""))
        if not db.exists() or not (row.get("failed_cases") or row.get("match_rate")):
            continue
        if "memory" in row and row["batch"] == "ReconRiver(mixed-exceptions)":
            continue
        ops = ingest_deterministic(store, db, row)
        total += len(ops)
    print(json.dumps({"ops": total, "stats": store.stats()}, indent=2))
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
