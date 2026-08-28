"""Batch orchestrator: causes -> records -> SQLite + ground_truth.json + manifest."""

from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from generator.config import (
    BASE_DATE,
    EXTRA_BANK_ONLY,
    GENERATOR_VERSION,
    ID_OFFSETS,
    NOISE_COUNTS,
    GeneratorConfig,
)
from generator.entities import minter, synth_date
from generator.inject import BUILDERS, NOISE_BUILDERS, Built, Ctx, merge
from generator.schema import DDL, INDICES

TABLE_ORDER = ["orders", "payments", "settlements", "bank_txns", "adjustments"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_dump(db_path: Path) -> str:
    con = sqlite3.connect(db_path)
    try:
        lines: list[str] = []
        for table, *_ in [(t.strip().split()[2],) for t in DDL]:
            cur = con.execute(f"SELECT * FROM {table} ORDER BY 1")
            cols = [d[0] for d in cur.description]
            for row in cur.fetchall():
                lines.append(
                    table + "\t" + "\t".join(f"{c}={v!r}" for c, v in zip(cols, row, strict=True))
                )
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()
    finally:
        con.close()


def _write_db(
    db_path: Path, cfg: GeneratorConfig, built: Built, row_counts: dict[str, int]
) -> None:
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    try:
        for stmt in DDL:
            con.execute(stmt)
        meta = {
            "batch_id": f"batch_seed{cfg.seed}",
            "seed": str(cfg.seed),
            "generator_version": GENERATOR_VERSION,
            "created_at": synth_date(BASE_DATE, 0).isoformat(),
            "difficulty": cfg.difficulty,
        }
        con.executemany(
            "INSERT INTO batch_meta(key,value) VALUES(:k,:v)",
            [{"k": k, "v": v} for k, v in sorted(meta.items())],
        )
        for table in TABLE_ORDER:
            rows = getattr(built, table)
            if not rows:
                continue
            cols = [d[1] for d in con.execute(f"PRAGMA table_info({table})")]
            placeholders = ",".join(f":{c}" for c in cols)
            con.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})", rows)
        con.executemany(
            "INSERT INTO audit_events(work_key,stage,event,payload_json)"
            " VALUES(:work_key,:stage,:event,:payload_json)",
            built.audits,
        )
        for stmt in INDICES:
            con.execute(stmt)
        con.commit()
    finally:
        con.close()


def generate(cfg: GeneratorConfig, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(cfg.seed)
    dp = cfg.dp()
    ids = minter(ID_OFFSETS, rng=rng, max_step=7)

    causes = cfg.cause_list()
    rng.shuffle(causes)
    span = max(14, math.ceil(cfg.size * 0.75))

    built = Built()

    def ctx_for(day_offset: int) -> Ctx:
        return Ctx(rng=rng, ids=ids, day=synth_date(BASE_DATE, day_offset), dp=dp)

    for i, cause in enumerate(causes):
        merge(built, BUILDERS[cause](ctx_for(i * span // cfg.size)))

    for _ in range(EXTRA_BANK_ONLY):
        merge(built, BUILDERS["BANK_ONLY_CREDIT"](ctx_for(rng.randrange(span // 2, span + 5))))

    noise_histogram: Counter[str] = Counter()
    for code, cnt in NOISE_COUNTS.items():
        for _ in range(round(cnt * dp.noise_mult)):
            merge(built, NOISE_BUILDERS[code](ctx_for(rng.randrange(0, span + 7))))
            noise_histogram[code] += 1

    histogram: Counter[str] = Counter(t["cause_code"] for t in built.gt)

    row_counts = {
        "orders": len(built.orders),
        "payments": len(built.payments),
        "settlements": len(built.settlements),
        "bank_txns": len(built.bank_txns),
        "adjustments": len(built.adjustments),
        "audit_events": len(built.audits),
    }

    db_path = out_dir / f"batch_seed{cfg.seed}.db"
    gt_path = out_dir / f"ground_truth_seed{cfg.seed}.json"
    manifest_path = out_dir / f"manifest_seed{cfg.seed}.json"

    _write_db(db_path, cfg, built, row_counts)

    gt = {
        "batch_id": f"batch_seed{cfg.seed}",
        "seed": cfg.seed,
        "size": cfg.size,
        "generator_version": GENERATOR_VERSION,
        "created_at": synth_date(BASE_DATE, 0).isoformat(),
        "difficulty": cfg.difficulty,
        "composition": dict(sorted(histogram.items())),
        "noise_counts": dict(sorted(noise_histogram.items())),
        "transactions": sorted(built.gt, key=lambda t: t["work_key"]),
        "metrics_note": "definitions: docs/DATA.md section 5.3",
    }
    gt_path.write_text(json.dumps(gt, indent=2, sort_keys=True))

    manifest = {
        "batch_id": gt["batch_id"],
        "seed": cfg.seed,
        "difficulty": cfg.difficulty,
        "generator_version": GENERATOR_VERSION,
        "row_counts": row_counts,
        "cause_histogram": gt["composition"],
        "noise_counts": gt["noise_counts"],
        "files": {
            db_path.name: _sha256(db_path),
            gt_path.name: _sha256(gt_path),
        },
        "generated_by_run_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

    return {"db": db_path, "gt": gt_path, "manifest": manifest_path}
