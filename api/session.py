"""SQLite run history for the API (audit of every batch run triggered via HTTP)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any


class SessionDB:
    """Records each pipeline run triggered through the API."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS run_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT UNIQUE NOT NULL,
                    batch_name TEXT NOT NULL,
                    seed TEXT NOT NULL,
                    status TEXT NOT NULL,
                    elapsed_ms REAL,
                    class_mix_json TEXT,
                    created_at TEXT NOT NULL,
                    error TEXT
                )
                """
            )
            con.commit()
        finally:
            con.close()

    def record_run(
        self,
        batch_name: str,
        seed: str,
        status: str,
        elapsed_ms: float | None = None,
        class_mix: dict[str, int] | None = None,
        error: str | None = None,
    ) -> str:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO run_history(run_id,batch_name,seed,status,elapsed_ms,"
                "class_mix_json,created_at,error) VALUES (?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    batch_name,
                    seed,
                    status,
                    elapsed_ms,
                    json.dumps(class_mix or {}),
                    datetime.now(UTC).isoformat(),
                    error,
                ),
            )
            con.commit()
        finally:
            con.close()
        return run_id

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT * FROM run_history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            con.close()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["class_mix"] = json.loads(d.pop("class_mix_json") or "{}")
            except json.JSONDecodeError:
                d["class_mix"] = {}
            out.append(d)
        return out
