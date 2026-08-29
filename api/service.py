"""Service layer: every endpoint reads REAL engine artifacts (verdicts tables,
results JSON, ground truth). Nothing here fabricates numbers (AGENTS.md #1/#5).

This module is the ONLY place that knows where batches live; tests monkeypatch
BATCH_DIR to an isolated tmp directory.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from api.exceptions import NotFoundException

BATCH_DIR = Path("data/synthetic")
_SAFE_NAME = re.compile(r"^[\w][\w.-]*\.db$")
EXCEPTION_CLASSES = {"genuine_discrepancy", "unresolved", "data_quality"}


def batch_path(batch_name: str) -> Path:
    """Resolve a batch name safely (no path traversal)."""
    if not _SAFE_NAME.match(batch_name):
        raise NotFoundException(f"invalid batch name {batch_name!r}", "INVALID_NAME")
    p = BATCH_DIR / batch_name
    if not p.exists():
        raise NotFoundException(f"batch {batch_name} not found")
    return p


def seed_of(db_path: Path) -> str:
    return db_path.stem.removeprefix("batch_")


def all_batches() -> list[Path]:
    if not BATCH_DIR.exists():
        return []
    return sorted(BATCH_DIR.glob("batch_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)


def default_batch() -> Path:
    batches = all_batches()
    if not batches:
        raise NotFoundException("no batches available — generate one first")
    return batches[0]


def _table_counts(db_path: Path) -> dict[str, int]:
    con = sqlite3.connect(db_path)
    try:
        return {
            t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("orders", "payments", "settlements", "bank_txns", "adjustments")
        }
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()


def has_verdicts(db_path: Path) -> bool:
    con = sqlite3.connect(db_path)
    try:
        n = con.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0]
        return n > 0
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


def list_batches() -> list[dict]:
    out = []
    for db in all_batches():
        seed = seed_of(db)
        manifest = db.parent / f"manifest_{seed}.json"
        meta: dict = {}
        if manifest.exists():
            try:
                meta = json.loads(manifest.read_text())
            except json.JSONDecodeError:
                meta = {}
        out.append(
            {
                "batch_name": db.name,
                "seed": seed,
                "size_bytes": db.stat().st_size,
                "has_results": (db.parent / f"results_{seed}.json").exists(),
                "has_ground_truth": (db.parent / f"ground_truth_{seed}.json").exists(),
                "has_verdicts": has_verdicts(db),
                "row_counts": _table_counts(db),
                "generator_meta": {
                    k: v
                    for k, v in meta.items()
                    if k in ("batch_id", "seed", "difficulty", "generator_version", "created_at")
                },
            }
        )
    return out


def run_and_persist(db_path: Path) -> tuple[list[dict], float]:
    """Run the deterministic pipeline and persist verdicts into the batch db."""
    from engine.runner import persist, run_pipeline

    verdicts, links, elapsed = run_pipeline(db_path)
    persist(db_path, verdicts, links)
    return [v.__dict__ for v in verdicts], elapsed


def ensure_results(db_path: Path) -> Path:
    """Guarantee a results JSON next to the batch (regenerable artifact)."""
    seed = seed_of(db_path)
    results = db_path.parent / f"results_{seed}.json"
    if results.exists():
        return results
    verdict_dicts, elapsed = run_and_persist(db_path)
    payload = {
        "batch_db": db_path.name,
        "elapsed_sec": elapsed,
        "results_sha256": hashlib.sha256(
            json.dumps(verdict_dicts, sort_keys=True, default=str).encode()
        ).hexdigest(),
        "verdicts": verdict_dicts,
    }
    results.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return results


def metrics_for_batch(batch_name: str) -> dict:
    db_path = batch_path(batch_name)
    results = ensure_results(db_path)
    payload = json.loads(results.read_text())
    scored = [v for v in payload["verdicts"] if not v.get("internal_status")]
    mix = Counter(v["cls"] for v in scored if v["cls"])
    elapsed = payload.get("elapsed_sec", 0.0)
    reconciled = mix.get("matched", 0) + mix.get("matched_after_reasoning", 0)
    out = {
        "batch_name": db_path.name,
        "seed": seed_of(db_path),
        "transactions": len(scored),
        "class_mix": dict(mix),
        "reconciled_rate": round(reconciled / max(1, len(scored)), 4),
        "throughput_orders_per_sec": round(len(scored) / elapsed, 1) if elapsed else None,
        "elapsed_ms": round(elapsed * 1000, 1),
        "results_sha256": payload.get("results_sha256"),
        "honest_exception_list": [
            {"work_key": v["work_key"], "cls": v["cls"], "reason_code": v["reason_code"]}
            for v in sorted(scored, key=lambda x: x["work_key"])
            if v["cls"] in EXCEPTION_CLASSES
        ],
    }
    gt = db_path.parent / f"ground_truth_{seed_of(db_path)}.json"
    if gt.exists():
        from eval.run import evaluate

        out["eval"] = evaluate(db_path, gt, results)
    return out


def load_verdict_rows(db_path: Path) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        verdicts = [dict(r) for r in con.execute("SELECT * FROM verdicts")]
        orders = {
            r["order_id"]: dict(r)
            for r in con.execute(
                "SELECT order_id, amount_paise, customer_name, status, created_at FROM orders"
            )
        }
    except sqlite3.OperationalError:
        raise NotFoundException(
            f"batch {db_path.name} has no verdicts — run it first"
        ) from None
    finally:
        con.close()
    rows = []
    for v in verdicts:
        o = orders.get(v["work_key"]) or {}
        rows.append(
            {
                "work_key": v["work_key"],
                "cls": v["cls"],
                "reason_code": v["reason_code"],
                "internal_status": v["internal_status"],
                "bundle_bid": v["bundle_bid"],
                "order": o or None,
            }
        )
    return rows


def transactions_page(
    batch_name: str | None, cls: str | None, page: int, page_size: int
) -> dict:
    db_path = batch_path(batch_name) if batch_name else default_batch()
    rows = load_verdict_rows(db_path)
    scored = [r for r in rows if not r["internal_status"]]
    if cls:
        scored = [r for r in scored if r["cls"] == cls]
    scored.sort(key=lambda r: r["work_key"])
    total = len(scored)
    start = (page - 1) * page_size
    mix = Counter(r["cls"] for r in rows if not r["internal_status"] and r["cls"])
    return {
        "batch_name": db_path.name,
        "items": scored[start : start + page_size],
        "pagination": {"page": page, "page_size": page_size, "total": total},
        "class_mix": dict(mix),
    }


def find_batch_with_key(work_key: str, batch_name: str | None = None) -> Path:
    candidates = [batch_path(batch_name)] if batch_name else all_batches()
    for db in candidates:
        con = sqlite3.connect(db)
        try:
            hit = con.execute(
                "SELECT 1 FROM verdicts WHERE work_key = ? LIMIT 1", (work_key,)
            ).fetchone()
        except sqlite3.OperationalError:
            hit = None
        finally:
            con.close()
        if hit:
            return db
    raise NotFoundException(f"work_key {work_key} not found in any batch verdicts")
