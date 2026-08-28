"""Batch runner: load batch db → LINKAGE → RECONCILE → INVESTIGATE → CLASSIFY →
persist verdicts/bundle_links tables into the same db + export results JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from engine.assist import get_assist
from engine.classify import classify
from engine.investigate import has_markers, meaningful_break, run_checks
from engine.linkage import build_bundles
from engine.reconcile import reconcile
from engine.types import Verdict

RESULT_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS verdicts (
        work_key TEXT PRIMARY KEY,
        bundle_bid TEXT,
        cls TEXT,
        reason_code TEXT,
        evidence_json TEXT,
        checks_run_json TEXT,
        findings_json TEXT,
        llm_assists_json TEXT,
        members_json TEXT,
        internal_status TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bundle_links (
        link_id INTEGER PRIMARY KEY AUTOINCREMENT,
        src TEXT,
        dst TEXT,
        rule_pass TEXT,
        evidence_json TEXT,
        score_json TEXT
    )
    """,
]


def load_tables(db_path: Path) -> dict[str, list[dict]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        tabs = {
            t: [dict(r) for r in con.execute(f"SELECT * FROM {t}")]
            for t in ("orders", "payments", "settlements", "bank_txns", "adjustments")
        }
        try:
            meta = {
                k: v
                for k, v in con.execute("SELECT key,value FROM batch_meta")
                if k.startswith("_")
            }
            for k, v in meta.items():
                try:
                    tabs[k] = json.loads(v)
                except json.JSONDecodeError:
                    tabs[k] = v
        except sqlite3.OperationalError:
            pass
        return tabs
    finally:
        con.close()


def run_pipeline(db_path: Path) -> tuple[list[Verdict], list[dict], float]:
    t0 = time.perf_counter()
    tabs = load_tables(db_path)
    bundles, links = build_bundles(tabs)

    from engine.linkage import Indexes

    ix = Indexes(tabs)
    verdicts: list[Verdict] = []
    for b in bundles:
        findings = reconcile(ix, b)
        needs_checks = meaningful_break(findings) or has_markers(ix, b)
        checks = run_checks(ix, b, findings) if needs_checks else []
        cls, reason, evidence, internal, overrides = classify(ix, b, findings, checks)
        members = (
            [f"order:{o}" for o in sorted(b.orders)]
            + [f"pay:{p}" for p in sorted(b.payments)]
            + [f"settle:{s}" for s in sorted(b.settlements)]
            + [f"bank:{c}" for c in sorted(b.bank_txns)]
            + [f"adj:{a}" for a in sorted(b.adjustments)]
        )
        keys = sorted(b.orders) or sorted(b.bank_txns) or sorted(b.payments)
        for key in keys:
            k_cls, k_reason, k_ev = cls, reason, evidence
            if overrides and key in overrides:
                k_cls, k_reason = overrides[key]
            verdicts.append(
                Verdict(
                    work_key=key,
                    bundle_bid=b.bid,
                    cls=k_cls or "",
                    reason_code=k_reason,
                    evidence_ids=k_ev,
                    checks_run=[c.check for c in checks],
                    findings=[f.__dict__ | {"delta_paise": f.delta_paise} for f in findings],
                    llm_assists=[{"assist": get_assist().name}],
                    members=members,
                    internal_status=internal,
                )
            )
    elapsed = time.perf_counter() - t0
    return verdicts, links, elapsed


def persist(db_path: Path, verdicts: list[Verdict], links: list[dict]) -> None:
    con = sqlite3.connect(db_path)
    try:
        for ddl in RESULT_TABLES:
            con.execute(ddl)
        cols = {r[1] for r in con.execute("PRAGMA table_info(verdicts)")}
        if "members_json" not in cols:
            con.execute("ALTER TABLE verdicts ADD COLUMN members_json TEXT")
        con.execute("DELETE FROM verdicts")
        con.execute("DELETE FROM bundle_links")
        con.executemany(
            "INSERT INTO verdicts(work_key,bundle_bid,cls,reason_code,evidence_json,"
            "checks_run_json,findings_json,llm_assists_json,members_json,internal_status)"
            " VALUES(:work_key,:bundle_bid,:cls,:reason_code,:evidence_json,"
            ":checks_run_json,:findings_json,:llm_assists_json,:members_json,:internal_status)",
            [
                {
                    "work_key": v.work_key,
                    "bundle_bid": v.bundle_bid,
                    "cls": v.cls,
                    "reason_code": v.reason_code,
                    "evidence_json": json.dumps(v.evidence_ids),
                    "checks_run_json": json.dumps(v.checks_run),
                    "findings_json": json.dumps(v.findings),
                    "llm_assists_json": json.dumps(v.llm_assists),
                    "members_json": json.dumps(v.members),
                    "internal_status": v.internal_status,
                }
                for v in verdicts
            ],
        )
        con.executemany(
            "INSERT INTO bundle_links(src,dst,rule_pass,evidence_json,score_json)"
            " VALUES(?,?,?,?,?)",
            [
                (
                    lk.src,
                    lk.dst,
                    lk.rule_pass,
                    json.dumps(lk.evidence_ids),
                    json.dumps(lk.score_breakdown),
                )
                for lk in links
            ],
        )
        con.commit()
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(prog="engine")
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    verdicts, links, elapsed = run_pipeline(args.db)
    persist(args.db, verdicts, links)

    counted = [v for v in verdicts if not v.internal_status]
    from collections import Counter

    mix = Counter(v.cls for v in counted)
    n_orders = sum(1 for v in verdicts if not v.internal_status)
    print(f"bundles processed : {len({v.bundle_bid for v in verdicts})}")
    n_scored = len(counted)
    print(f"verdict work-keys : {len(verdicts)} (scored {n_scored})")
    print(f"class mix         : {dict(mix)}")
    print(
        f"throughput        : {n_orders / elapsed:.0f} orders/sec ({elapsed * 1000:.0f} ms total)"
    )

    if args.json_out:
        payload = {
            "batch_db": args.db.name,
            "elapsed_sec": elapsed,
            "results_sha256": hashlib.sha256(
                json.dumps([v.__dict__ for v in verdicts], sort_keys=True, default=str).encode()
            ).hexdigest(),
            "verdicts": [v.__dict__ for v in verdicts],
        }
        Path(args.json_out).write_text(json.dumps(payload, indent=2, sort_keys=True))
        print(f"results json      : {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
