"""Diversity evidence report for a generated batch.

Usage: uv run python -m scripts.diversity_report --db data/synthetic/batch_seed7.db \
       --gt data/synthetic/ground_truth_seed7.json
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--gt", required=True)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    narrs = [r["narration"] for r in con.execute("SELECT narration FROM bank_txns")]
    chan = collections.Counter()
    shapes = set()
    for n in narrs:
        m = re.match(r"^([A-Za-z/ ]+?)[-:/]", n)
        chan[m.group(1) if m else n[:12]] += 1
        shapes.add(re.sub(r"\d+", "#", n)[:44])

    amts = sorted(
        r[0] / 100 for r in con.execute("SELECT amount_paise FROM orders WHERE amount_paise>0")
    )
    methods = collections.Counter(r[0] for r in con.execute("SELECT method FROM payments"))
    statuses = collections.Counter(r[0] for r in con.execute("SELECT status FROM payments"))

    ids = sorted(int(r[0].split("-")[1]) for r in con.execute("SELECT order_id FROM orders"))
    gaps = [b - a for a, b in zip(ids, ids[1:], strict=False) if b > a]
    contiguous = not any(g > 1 for g in gaps)

    rtgs_small = sum(
        1
        for r in con.execute("SELECT narration, amount_paise FROM bank_txns")
        if r["narration"].startswith("RTGS") and r["amount_paise"] < 20_000_000
    )

    gt = json.loads(Path(args.gt).read_text())
    cls = collections.Counter(t["expected_class"] for t in gt["transactions"])
    causes = gt.get("composition", {})
    fee_deltas = [
        t["expected_delta_paise"] for t in gt["transactions"] if t["cause_code"] == "FEE_EXPLAINED"
    ]
    unexp = [
        t["expected_delta_paise"] // 100
        for t in gt["transactions"]
        if t["cause_code"] == "UNEXPLAINED_DELTA"
    ]

    lags: collections.Counter[int] = collections.Counter()
    settle_by_pid = {
        r["payment_id"]: r["settled_at"]
        for r in con.execute("SELECT payment_id,settled_at FROM settlements")
    }
    paid_by_pid = {
        r["payment_id"]: r["paid_at"]
        for r in con.execute("SELECT payment_id,paid_at FROM payments")
    }
    for pid, sd in settle_by_pid.items():
        if pid in paid_by_pid:
            d = (
                datetime.fromisoformat(sd.replace("Z", "+00:00"))
                - datetime.fromisoformat(paid_by_pid[pid].replace("Z", "+00:00"))
            ).days
            lags[d] += 1

    print(
        "=== DIVERSITY REPORT:", Path(args.db).name, f"(difficulty={gt.get('difficulty', '?')}) ==="
    )
    print(f"bank rows {len(narrs)} | distinct narration shapes {len(shapes)}")
    print(f"channels: {dict(chan.most_common())}")
    print(f"methods: {dict(methods)} | statuses: {dict(statuses)}")
    p50 = statistics.median(amts)
    print(f"order amounts: min Rs{amts[0]:,.0f} p50 Rs{p50:,.0f} max Rs{amts[-1]:,.0f}")
    bands = collections.Counter(
        "small<5k" if a < 5000 else "mid5-50k" if a < 50_000 else "large>=50k" for a in amts
    )
    print(f"amount bands: {dict(bands)}")
    gap_summary = collections.Counter(gaps)
    print(f"id span {ids[0]}..{ids[-1]} | gaps: {not contiguous} | sizes {gap_summary}")
    print(f"RTGS credits below Rs2L: {rtgs_small} (must be 0)")
    print(f"class mix: {dict(cls)}")
    print(f"cause histogram: {causes}")
    print(f"FEE deltas unique: {len(set(fee_deltas))}/{len(fee_deltas)}")
    if unexp:
        print(f"UNEXPLAINED band: Rs{min(unexp)}-{max(unexp)}")
    print(f"settlement lags (days): {dict(sorted(lags.items()))}")


if __name__ == "__main__":
    main()
