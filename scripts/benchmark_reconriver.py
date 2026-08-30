"""External benchmark: ReconRiver mixed-exceptions pack through adapter+engine.

Usage: uv run python -m scripts.benchmark_reconriver_benchmark
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from engine.adapters.reconriver import ReconRiverAdapter
from engine.runner import persist, run_pipeline

PACK = Path("data/raw/reconriver/generated/benchmark-standard")
OUT = Path("data/synthetic/reconriver_benchmark")

MATCHED = {"matched", "matched_after_reasoning"}
EXCEPTION = {"matched_after_reasoning", "genuine_discrepancy", "unresolved"}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    import json as _json

    adapter = ReconRiverAdapter()
    tabs, report = adapter.load(PACK)
    meta = {"_fee_schedule": _json.dumps(tabs.get("_fee_schedule"))}
    tabs.pop("_fee_schedule", None)
    db = adapter.write_canonical_db(tabs, OUT / "canonical.db", meta=meta)
    gt_entries = adapter.convert_ground_truth(PACK)
    
    # Run with LLM assistance to investigate anomalies
    verdicts, links, elapsed = run_pipeline(db, )
    
    persist(db, verdicts, links)

    con = sqlite3.connect(db)
    existing_orders = {r[0] for r in con.execute("SELECT order_id FROM orders")}
    existing_banks = {r[0] for r in con.execute("SELECT bank_txn_id FROM bank_txns")}
    con.close()
    pred = {v.work_key: v for v in verdicts}
    not_representable = 0
    confusion: Counter[tuple[str, str]] = Counter()
    per_cause: dict[str, Counter] = {}
    correct = total = 0
    for t in gt_entries:
        key = t["work_key"]
        if key not in pred and key not in existing_orders and key not in existing_banks:
            not_representable += 1
            continue
        gcls = t["expected_class"]
        v = pred.get(key)
        pcls = (v.cls if v else "MISSING") or (
            "internal:" + (v.internal_status or "") if v else "MISSING"
        )
        if v and v.internal_status:
            pcls = f"internal:{v.internal_status}"
        confusion[(gcls, pcls)] += 1
        total += 1
        ok = (gcls in MATCHED and pcls in MATCHED) or (gcls == pcls)
        correct += ok
        cause = t["cause_code"][:28]
        per_cause.setdefault(cause, Counter())[f"{pcls}{'✓' if ok else '✗'}"] += 1

    print(f"=== ReconRiver external benchmark ({PACK.name}) ===")
    print(f"entries scored against : {total}  | engine time {elapsed * 1000:.0f} ms")
    print(f"non-representable keys excluded: {not_representable}")
    print(f"agreement              : {correct}/{total} = {correct / max(1, total):.1%}\n")
    for cause, c in sorted(per_cause.items(), key=lambda kv: -sum(kv[1].values())):
        agree = sum(n for k, n in c.items() if k.endswith("✓"))
        print(f"  {cause:30} {agree:>4}/{sum(c.values()):<4}  {dict(c.most_common(3))}")
    div = {
        f"{g}->{p}": n
        for (g, p), n in confusion.items()
        if not ((g in MATCHED and p in MATCHED) or g == p)
    }
    print("\ntop divergences:", dict(sorted(div.items(), key=lambda kv: -kv[1])[:6]))

    (OUT / "benchmark_report.json").write_text(
        json.dumps(
            {
                "pack": PACK.name,
                "agreement_rate": round(correct / max(1, total), 4),
                "engine_ms": round(elapsed * 1000),
                "confusion": {f"{g}->{p}": n for (g, p), n in confusion.items()},
                "adapter_report": report,
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(f"\nreport saved: {OUT / 'benchmark_report.json'}")


if __name__ == "__main__":
    main()
