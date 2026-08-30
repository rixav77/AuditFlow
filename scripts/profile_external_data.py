"""Profile external reference datasets -> prints findings used in docs/DATA.md.

Usage: uv run python -m scripts.profile_external_data
"""

from __future__ import annotations

import collections
import glob
import json
import random
from datetime import datetime
from pathlib import Path

import pandas as pd

RAW = Path("data/raw")
random.seed(0)


def profile_reconriver() -> None:
    base = RAW / "reconriver" / "generated" / "mixed-exceptions"
    gt = pd.read_csv(base / "expected_reconciliation.csv")
    print("[reconriver] outcome x scope:")
    print(gt.groupby(["result_scope", "expected_outcome"]).size().to_string())
    print("[reconriver] reason codes:")
    for code, n in gt.expected_reason_code.value_counts().items():
        print(f"  {n:5d}  {code}")
    manifest = json.loads((base / "scenario_manifest.json").read_text())
    print("[reconriver] injected:", manifest["injected_exception_counts"])


def profile_agami() -> None:
    files = glob.glob(str(RAW / "agami-indian-bank-statements" / "train" / "*" / "*.json"))
    txns: list[dict] = []
    schemas: collections.Counter[str] = collections.Counter()
    for f in files:
        stmt = json.loads(Path(f).read_text())
        txns += stmt["transactions"]
        for t in stmt["transactions"]:
            schemas["type1(debit/credit)" if "credit" in t else "type2(cr_dr)"] += 1

    print(f"[agami] {len(files)} statements, {len(txns)} txns, schemas={dict(schemas)}")

    def chan(d: str) -> str:
        for p in ("NEFT", "RTGS", "IMPS", "UPI", "By Clg", "Chq Paid"):
            if d.startswith(p):
                return p
        return "SVC" if d.startswith("Service Charges") else "OTHER"

    counts = collections.Counter(chan(t.get("description", "?")) for t in txns)
    print("[agami] channels:", dict(counts))

    def credit(t: dict) -> float | None:
        if t.get("failed"):
            return None
        if t.get("credit") is not None:
            return t["credit"]
        if t.get("cr_dr") == "CR":
            return t.get("transaction_amount")
        return None

    deltas: collections.Counter[int] = collections.Counter()
    for t in txns:
        c = credit(t)
        if not c:
            continue
        try:
            vd = str(t["value_date"])
            vd_dt = (
                datetime.fromisoformat(vd) if "-" in vd[4:] else datetime.strptime(vd, "%d/%m/%Y")
            )
            deltas[(vd_dt - datetime.fromisoformat(str(t["date"])[:10])).days] += 1
        except ValueError:
            pass
    print(f"[agami] value_date-posted_date days (credits): {dict(sorted(deltas.items()))}")

    failed = sum(1 for t in txns if t.get("failed"))
    rev = sum(1 for t in txns if "REVERSAL" in t.get("description", ""))
    svc = sum(1 for t in txns if "Service Charges" in t.get("description", ""))
    print(
        f"[agami] failed={failed} ({failed / len(txns):.2%}) reversals={rev} service_charges={svc}"
    )


def profile_r3nova() -> None:
    sample = RAW / "synthetic-accounting-generator" / "data" / "samples"
    dq = pd.read_csv(sample / "dq_issue_manifest.csv")
    print("[r3nova] dq_issue_manifest columns:", list(dq.columns))
    rules_doc = (
        RAW / "synthetic-accounting-generator" / "docs" / "04_GENERATION_RULES.md"
    ).read_text()
    import re

    rules = sorted(set(re.findall(r"`([A-Z][A-Z_]{5,})`", rules_doc)))[:15]
    print("[r3nova] rule tokens found:", rules)


if __name__ == "__main__":
    profile_reconriver()
    profile_agami()
    profile_r3nova()
