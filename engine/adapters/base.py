"""SourceAdapter interface: foreign sources -> canonical tables + report."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from generator.schema import DDL, INDICES

CANONICAL_TABLES = ("orders", "payments", "settlements", "bank_txns", "adjustments")


def empty_tabs() -> dict[str, list[dict]]:
    return {t: [] for t in CANONICAL_TABLES}


def empty_report() -> dict:
    return {
        "unknown_methods": [],
        "negative_amounts": 0,
        "null_amounts": 0,
        "broken_refs": [],
        "row_counts": {},
    }


class SourceAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def load(self, source: str | Path) -> tuple[dict[str, list[dict]], dict]:
        """Returns (canonical_tables, validation_report)."""

    def write_canonical_db(
        self, tabs: dict[str, list[dict]], out_db: Path, meta: dict | None = None
    ) -> Path:
        meta = dict(meta or {})
        for k, v in tabs.items():
            if k.startswith("_"):
                meta[k] = v if isinstance(v, str) else json.dumps(v)
        out_db = Path(out_db)
        out_db.parent.mkdir(parents=True, exist_ok=True)
        if out_db.exists():
            out_db.unlink()
        import sqlite3

        con = sqlite3.connect(out_db)
        try:
            for stmt in DDL:
                con.execute(stmt)
            for table in CANONICAL_TABLES:
                rows = tabs.get(table, [])
                if not rows:
                    continue
                info = list(con.execute(f"PRAGMA table_info({table})"))
                col_names = [d[1] for d in info]
                notnull = {d[1] for d in info if d[3] == 1 and d[4] is None}
                textish = {
                    "order_id",
                    "payment_id",
                    "settlement_id",
                    "bank_txn_id",
                    "adjustment_id",
                    "customer_name",
                    "item_desc",
                    "narration",
                    "method",
                    "status",
                    "created_at",
                    "paid_at",
                    "settled_at",
                    "posted_at",
                    "value_date",
                    "processor_ref",
                    "utr",
                    "adj_type",
                    "reason",
                    "currency",
                }
                filled: list[list] = []
                for r in rows:
                    vals = []
                    for c in col_names:
                        v = r.get(c)
                        if v is None and c in notnull:
                            v = "" if c in textish else 0
                        vals.append(v)
                    filled.append(vals)
                placeholders = ",".join("?" for _ in col_names)
                con.executemany(
                    f"INSERT INTO {table} ({','.join(col_names)}) VALUES ({placeholders})",
                    filled,
                )
            for stmt in INDICES:
                con.execute(stmt)
            for k, v in (meta or {}).items():
                con.execute("INSERT OR REPLACE INTO batch_meta(key,value) VALUES(?,?)", (k, v))
            con.commit()
        finally:
            con.close()
        return out_db


def scan_report(tabs: dict[str, list[dict]], report: dict) -> dict:
    """Fill common report fields from canonical rows."""
    methods = {"upi", "debit_card", "credit_card", "netbanking", "wallet"}
    neg = null_ = unk = 0
    for p in tabs["payments"]:
        amt = p.get("amount_paise")
        if amt is None:
            null_ += 1
        elif amt < 0:
            neg += 1
        if p.get("method") not in methods and p.get("method") is not None:
            unk = 0
            report["unknown_methods"].append(p["method"])
    del unk
    report["negative_amounts"] += neg
    report["null_amounts"] += null_
    report["row_counts"] = {t: len(rows) for t, rows in tabs.items()}
    return report
