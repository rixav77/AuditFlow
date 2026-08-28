"""CLI: python -m engine.adapters --source reconriver --in DIR --out canonical.db [--gt-out f]"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from engine.adapters.reconriver import ReconRiverAdapter


def main() -> int:
    ap = argparse.ArgumentParser(prog="adapters")
    ap.add_argument("--source", default="reconriver")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--gt-out", default=None)
    args = ap.parse_args()

    adapter = ReconRiverAdapter()
    tabs, report = adapter.load(args.src)
    meta = {"_fee_schedule": json.dumps(tabs.get("_fee_schedule"))}
    tabs.pop("_fee_schedule", None)
    out = Path(args.out)
    if out.suffix != ".db":
        out = out / "canonical.db"
        out.parent.mkdir(parents=True, exist_ok=True)
    adapter.write_canonical_db(tabs, out, meta=meta)
    print(f"canonical db : {out}")
    print(json.dumps(report, indent=2)[:400])
    if args.gt_out:
        entries = adapter.convert_ground_truth(args.src)
        Path(args.gt_out).write_text(
            json.dumps(
                {
                    "batch_id": f"reconriver_{Path(args.src).name}",
                    "seed": 0,
                    "size": len(entries),
                    "generator_version": "reconriver-1.1.0",
                    "difficulty": "EXTERNAL",
                    "composition": {},
                    "noise_counts": {},
                    "transactions": entries,
                    "metrics_note": "external benchmark; mapping in adapters/reconriver.py",
                },
                indent=2,
                sort_keys=True,
            )
        )
        print(f"gt mapped    : {args.gt_out} ({len(entries)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
