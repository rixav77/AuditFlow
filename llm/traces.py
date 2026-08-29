"""JSONL trace export (docs/CONTEXT_MEMORY.md format): one event per line.

{"ts": "...", "stage": "...", "work_key": "...", "event": "...", "payload": {...}}

Tracing must NEVER break the pipeline — every failure is swallowed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

TRACE_DIR = Path("data/traces")


def log_event(
    stage: str,
    work_key: str | None,
    event: str,
    payload: dict,
    batch: str | None = None,
) -> None:
    try:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{batch or stage}_{time.strftime('%Y%m%d')}.jsonl"
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "stage": stage,
            "work_key": work_key,
            "event": event,
            "payload": payload,
        }
        with open(TRACE_DIR / name, "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except Exception:  # noqa: BLE001 — traces are best-effort by design
        pass
