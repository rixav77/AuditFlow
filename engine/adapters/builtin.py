"""Built-in adapter for our own generated batch SQLite files (passthrough)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from engine.adapters.base import SourceAdapter, empty_report, scan_report


class OurBatchAdapter(SourceAdapter):
    name = "builtin_batch"

    def load(self, source: str | Path) -> tuple[dict[str, list[dict]], dict]:
        con = sqlite3.connect(source)
        con.row_factory = sqlite3.Row
        try:
            tabs = {
                t: [dict(r) for r in con.execute(f"SELECT * FROM {t}")]
                for t in ("orders", "payments", "settlements", "bank_txns", "adjustments")
            }
        finally:
            con.close()
        return tabs, scan_report(tabs, empty_report())
