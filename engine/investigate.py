"""Stage 3 — deterministic evidence checks. Gated on meaningful breaks or
reason-markers; unresolved requires all applicable checks ran (anti-post-hoc gate).
"""

from __future__ import annotations

from datetime import UTC, datetime

from engine.fees_ext import compute_net_with, merged_schedule
from engine.linkage import Indexes
from engine.reconcile import _captured_payments
from engine.types import (
    DUPLICATE_TOLERANCE_PAISE,
    LATE_MAX_DAYS,
    SHORT_PCT_THRESHOLD,
    Bundle,
    CheckResult,
    Finding,
)
from generator.narrate import MOJIBAKE_CHARS, extract_order_refs


def _ts(s: str | None):
    if not s:
        return datetime(1970, 1, 1, tzinfo=UTC)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def has_markers(ix: Indexes, b: Bundle) -> bool:
    """Split/combined/refund/late markers or any nonzero finding → run checks."""
    if b.adjustments:
        return True
    if len(b.settlements) > 1 and len(b.payments) <= 1:
        return True
    if len(b.orders) > 1 and len([c for c in b.bank_txns if ix.banks[c]["amount_paise"] > 0]) == 1:
        return True
    if b.payments and b.settlements:
        lag = max(
            (
                _ts(ix.settlements[s]["settled_at"]).date() - _ts(ix.payments[p]["paid_at"]).date()
            ).days
            for s in b.settlements
            for p in b.payments
            if ix.payments[p]["paid_at"]
        )
        if lag > 3:
            return True
    return False


def meaningful_break(findings: list[Finding]) -> bool:
    return any(f.delta_paise != 0 for f in findings)


def run_checks(ix: Indexes, b: Bundle, findings: list[Finding]) -> list[CheckResult]:
    results: list[CheckResult] = []
    payments = _captured_payments(ix, b)
    sched = merged_schedule(ix.extra_schedule)
    rules = {pid: sched.get(p["method"]) for pid, p in ((x["payment_id"], x) for x in payments)}
    settle_net = sum(ix.settlements[s]["net_paise"] for s in b.settlements)
    expected_net = sum(
        compute_net_with(p["amount_paise"], rules[p["payment_id"]])[2]
        for p in payments
        if rules.get(p["payment_id"])
    )
    adj_total = sum(ix.adjustments[a]["amount_paise"] for a in b.adjustments)
    credits = sum(
        ix.banks[c]["amount_paise"] for c in b.bank_txns if ix.banks[c]["amount_paise"] > 0
    )
    debits = sum(
        -ix.banks[c]["amount_paise"] for c in b.bank_txns if ix.banks[c]["amount_paise"] < 0
    )
    bank_delta = (credits - debits) - (settle_net - adj_total)

    fee_total = sum(
        sum(compute_net_with(p["amount_paise"], rules[p["payment_id"]])[:2])
        for p in payments
        if rules.get(p["payment_id"])
    )
    naive_f = next((f for f in findings if f.kind == "NAIVE_BANK_VS_ORDER"), None)
    supported_fee = (
        not b.settlements
        and bool(payments)
        and naive_f is not None
        and -naive_f.delta_paise == fee_total
        and credits - debits == expected_net
    )
    results.append(
        CheckResult(
            check="FEE_SCHEDULE_MATCH",
            supported=supported_fee,
            evidence_ids=sorted(b.payments)[:2],
            note=f"fees+gst={fee_total} inferred from schedule" if supported_fee else "",
        )
    )

    refund_ok = False
    if b.adjustments:
        refund_debits = [ix.banks[c] for c in b.bank_txns if ix.banks[c]["amount_paise"] < 0]
        debit_sum = sum(-d["amount_paise"] for d in refund_debits)
        consistent_flows = (credits - debits) == (settle_net - adj_total)
        presettle = not b.settlements and not credits and adj_total > 0
        refund_ok = (debit_sum == adj_total or not refund_debits) and (
            consistent_flows or presettle
        )
    results.append(
        CheckResult(
            check="REFUND_ADJUSTMENT_LOOKUP",
            supported=refund_ok,
            evidence_ids=sorted(b.adjustments)[:4],
            note=f"adjusted Rs{adj_total // 100}" if refund_ok else "",
        )
    )

    split_ok = len(b.settlements) > 1 and len(b.payments) <= 1
    combine_ok = (
        len(b.orders) > 1 and len([c for c in b.bank_txns if ix.banks[c]["amount_paise"] > 0]) == 1
    )
    results.append(
        CheckResult(
            check="SPLIT_COMBINE_TEST",
            supported=split_ok or combine_ok,
            evidence_ids=[bid for bid in sorted(b.bank_txns)][:2],
            note=("split legs" if split_ok else "") + ("combined payout" if combine_ok else ""),
        )
    )

    credit_rows = sorted(
        (ix.banks[c] for c in b.bank_txns if ix.banks[c]["amount_paise"] > 0),
        key=lambda r: r["bank_txn_id"],
    )
    dup_pairs = []
    for i in range(len(credit_rows)):
        for j in range(i + 1, len(credit_rows)):
            if (
                abs(credit_rows[i]["amount_paise"] - credit_rows[j]["amount_paise"])
                <= DUPLICATE_TOLERANCE_PAISE
            ):
                dup_pairs.append((credit_rows[i]["bank_txn_id"], credit_rows[j]["bank_txn_id"]))
    dup_ok = bool(dup_pairs) and bank_delta > 0
    results.append(
        CheckResult(
            check="DUPLICATE_SCAN",
            supported=dup_ok,
            evidence_ids=[x for pair in dup_pairs[:2] for x in pair],
            note=f"dup credits {dup_pairs[:2]}" if dup_ok else "",
        )
    )

    lag_days = None
    if b.payments and b.settlements:
        lags = [
            (
                _ts(ix.settlements[s]["settled_at"]).date() - _ts(ix.payments[p]["paid_at"]).date()
            ).days
            for s in b.settlements
            for p in b.payments
            if ix.payments[p]["paid_at"]
        ]
        lag_days = max(lags) if lags else None
    late_max = ix.policy.get("late_max_days", LATE_MAX_DAYS)
    timing_ok = lag_days is not None and 3 < lag_days <= late_max and bank_delta == 0
    results.append(
        CheckResult(
            check="TIMING_WINDOW",
            supported=bool(timing_ok),
            evidence_ids=sorted(b.settlements)[:2],
            note=f"T+{lag_days}" if timing_ok else "",
        )
    )

    gross = sum(p["amount_paise"] for p in payments)
    residual_short = bank_delta < 0 and gross > 0 and (-bank_delta) / gross >= SHORT_PCT_THRESHOLD
    exhaustive_supported = False
    evidence: list[str] = []
    if bank_delta != 0:
        own_refs = set(b.orders)
        for other in ix.banks.values():
            if other["bank_txn_id"] in b.bank_txns:
                continue
            hits = extract_order_refs(other["narration"]) & own_refs
            if hits and abs(other["amount_paise"]) == abs(bank_delta):
                exhaustive_supported = True
                evidence.append(other["bank_txn_id"])
        if not exhaustive_supported and not residual_short and not dup_ok:
            exhaustive_supported = False
    settle_finding_leg = next((f for f in findings if f.kind == "SETTLE_NET_VS_EXPECTED"), None)
    leg_supported = False
    culprits: list[str] = []
    if (
        settle_finding_leg is not None
        and settle_finding_leg.delta_paise != 0
        and len(b.payments) > 1
    ):
        acc = 0
        for sid in b.settlements:
            srow = ix.settlements[sid]
            prow = ix.payments.get(srow["payment_id"])
            rule = sched.get(prow["method"]) if prow else None
            if prow is None or rule is None:
                continue
            calc = compute_net_with(prow["amount_paise"], rule)[2]
            diff = calc - srow["net_paise"]
            acc += diff
            if diff != 0:
                culprits.append(f"{prow['payment_id']}:{srow['settlement_id']}")
        leg_supported = acc == -settle_finding_leg.delta_paise
    results.append(
        CheckResult(
            check="LEG_ATTRIBUTION",
            supported=leg_supported,
            evidence_ids=culprits[:3],
            note=f"culprit legs: {culprits[:2]}" if leg_supported else "",
        )
    )

    twin_supported = False
    twin_evidence: list[str] = []
    if b.payments:
        amounts = {p["amount_paise"] for p in payments}
        pay_dates = {_ts(p["paid_at"]).date() for p in payments}
        for other in ix.payments.values():
            oid = other["payment_id"]
            if oid in b.payments or other["order_id"] or other["status"] != "captured":
                continue
            if other["amount_paise"] in amounts and _ts(other["paid_at"]).date() in pay_dates:
                twin_supported = True
                twin_evidence.append(oid)
    results.append(
        CheckResult(
            check="AMBIGUOUS_TWIN_SCAN",
            supported=twin_supported,
            evidence_ids=twin_evidence[:2],
            note="twin captured payment without order link" if twin_supported else "",
        )
    )

    results.append(
        CheckResult(
            check="EXHAUSTIVE_SEARCH",
            supported=exhaustive_supported,
            evidence_ids=evidence[:3],
            note=(
                f"residual {'short' if bank_delta < 0 else 'extra'} "
                f"Rs{abs(bank_delta) // 100}; short_pct={residual_short}"
            ),
        )
    )
    return results


def dq_flavor(ix: Indexes, b: Bundle) -> str | None:
    sched = merged_schedule(ix.extra_schedule)
    for pid in b.payments:
        row = ix.payments[pid]
        if row["status"] == "captured" and row["method"] not in sched:
            return "DQ_UNKNOWN_METHOD"
        amt = row["amount_paise"]
        if amt is None:
            return "DQ_NULL_AMOUNT"
        if amt < 0:
            return "DQ_NEGATIVE_AMOUNT"
    for cid in b.bank_txns:
        if any(ch in MOJIBAKE_CHARS for ch in ix.banks[cid]["narration"]):
            return "DQ_MOJIBAKE"
    return None
