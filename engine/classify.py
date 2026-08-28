"""Stage 4 — deterministic verdict rules.

Priority ladder (RESEARCH.md §6.5, refined):
  data_quality > unmatched-inflow > noise/orphan internals >
  duplicate(genuine) > missing(unresolved) > short-fall(genuine) >
  refund/split-combine/late (reasoned) > undocumented-fee (reasoned) >
  zero-delta (matched)
Documented standard processor fees (settlement present, flows exact) = plain matched.
"""

from __future__ import annotations

from engine.fees_ext import compute_net_with, merged_schedule
from engine.investigate import dq_flavor
from engine.linkage import Indexes
from engine.reconcile import _captured_payments
from engine.types import AMBIENT_DEBIT_MAX_PAISE, Bundle, CheckResult, Finding


def _bundle_status(ix: Indexes, b: Bundle) -> str | None:
    if not b.orders:
        if b.payments or b.settlements or b.adjustments:
            return "orphan_chain"
        if any(ix.banks[c]["amount_paise"] != 0 for c in b.bank_txns):
            return "bank_only"
        return None
    orders = [ix.orders[o] for o in b.orders]
    if any(o["status"] == "cancelled" for o in orders):
        return "ignored_noise"
    pays = [ix.payments[p] for p in b.payments]
    if pays and all(p["status"] == "failed" for p in pays):
        return "ignored_noise"
    if not pays:
        return "ignored_noise"
    return None


def _ambient_debit_only(ix: Indexes, b: Bundle) -> bool:
    return (
        not b.orders
        and not b.payments
        and bool(b.bank_txns)
        and all(ix.banks[c]["amount_paise"] < 0 for c in b.bank_txns)
        and all(-ix.banks[c]["amount_paise"] <= AMBIENT_DEBIT_MAX_PAISE for c in b.bank_txns)
    )


def classify(
    ix: Indexes,
    b: Bundle,
    findings: list[Finding],
    checks: list[CheckResult],
) -> tuple[str | None, str, list[str], str | None, dict | None]:
    if _ambient_debit_only(ix, b):
        return None, "", [], "ignored_noise", None

    status = _bundle_status(ix, b)
    if status == "ignored_noise":
        return None, "", [], "ignored_noise", None
    if status == "bank_only":
        credit_ids = sorted(c for c in b.bank_txns if ix.banks[c]["amount_paise"] > 0)
        return "genuine_discrepancy", "INV_UNMATCHED_INFLOW", credit_ids[:2], None, None
    if status == "orphan_chain":
        return None, "", [], "orphan_chain", None

    flavor = dq_flavor(ix, b)
    if flavor:
        bank_ids = sorted(b.bank_txns)[:1]
        pay_ids = sorted(b.payments)[:2]
        return "data_quality", flavor, (bank_ids or pay_ids), None, None

    supported = {c.check: c for c in checks if c.supported}

    def f_by(kind: str) -> Finding | None:
        return next((f for f in findings if f.kind == kind), None)

    bank_f = f_by("BANK_VS_NET_MINUS_ADJ")
    settle_f = f_by("SETTLE_NET_VS_EXPECTED")
    naive_f = f_by("NAIVE_BANK_VS_ORDER")
    bank_d = bank_f.delta_paise if bank_f else 0
    settle_d = settle_f.delta_paise if settle_f else 0
    naive_d = naive_f.delta_paise if naive_f else 0

    evidence: list[str] = []
    for c in checks:
        if c.supported:
            evidence.extend(c.evidence_ids)
    evidence = list(dict.fromkeys(evidence))[:6]

    payments = _captured_payments(ix, b)

    # --- per-leg attribution in shared batches ------------------------------
    if settle_d != 0 and "LEG_ATTRIBUTION" in supported and len(b.orders) > 1:
        culprit_payments = {e.split(":")[0] for e in supported["LEG_ATTRIBUTION"].evidence_ids}
        overrides: dict[str, tuple[str, str]] = {}
        for oid in b.orders:
            pay = next((q for q in b.payments if ix.payments[q].get("order_id") == oid), None)
            if pay is not None and pay in culprit_payments:
                overrides[oid] = ("genuine_discrepancy", "INV_FEE_MISMATCH")
            else:
                overrides[oid] = ("matched", "REC_ZERO_DELTA")
        return None, "BUNDLE_SPLIT", [], None, overrides

    # --- break paths -------------------------------------------------------
    if bank_d != 0 or settle_d != 0:
        if "DUPLICATE_SCAN" in supported:
            return "genuine_discrepancy", "INV_DUPLICATE", evidence, None, None

        if b.adjustments and "REFUND_ADJUSTMENT_LOOKUP" in supported:
            return "matched_after_reasoning", "INV_REFUND_ADJ", evidence, None, None

        if not b.settlements and not any(ix.banks[c]["amount_paise"] > 0 for c in b.bank_txns):
            return (
                "unresolved",
                "INV_EXHAUSTIVE_NO_EVIDENCE",
                evidence or sorted(b.payments)[:1],
                None,
                None,
            )

        gross = sum(p["amount_paise"] for p in payments)
        shortfall = -(min(bank_d, 0))
        if gross > 0 and bank_d < 0 and shortfall / gross >= 0.20:
            return (
                "genuine_discrepancy",
                "INV_SHORT_FALL",
                sorted(b.settlements)[:1] + sorted(b.bank_txns)[:1],
                None,
                None,
            )

        if "FEE_SCHEDULE_MATCH" in supported:
            return "matched_after_reasoning", "INV_FEE_MATCH", evidence, None, None

        return (
            "unresolved",
            "INV_EXHAUSTIVE_NO_EVIDENCE",
            evidence or sorted(b.payments)[:1],
            None,
            None,
        )

    # --- structurally clean paths -----------------------------------------
    if naive_d != 0 and payments:
        sched = merged_schedule(ix.extra_schedule)
        fee_total = 0
        for p in payments:
            rule = sched.get(p["method"])
            if rule:
                fee_total += sum(compute_net_with(p["amount_paise"], rule)[:2])
        documented = bool(b.settlements)
        if -naive_d == fee_total and documented:
            pass
        elif -naive_d == fee_total and "FEE_SCHEDULE_MATCH" in supported:
            return "matched_after_reasoning", "INV_FEE_MATCH", evidence, None, None

    for code in (
        "AMBIGUOUS_TWIN_SCAN",
        "REFUND_ADJUSTMENT_LOOKUP",
        "SPLIT_COMBINE_TEST",
        "TIMING_WINDOW",
    ):
        if code in supported:
            return "matched_after_reasoning", _reason_for(code), evidence, None, None

    return "matched", "REC_ZERO_DELTA", sorted(bank_txns_safe(b)), None, None


def bank_txns_safe(b: Bundle) -> list[str]:
    return list(b.bank_txns)[:1]


def _reason_for(check: str) -> str:
    return {
        "REFUND_ADJUSTMENT_LOOKUP": "INV_REFUND_ADJ",
        "SPLIT_COMBINE_TEST": "INV_SPLIT_COMBINE",
        "TIMING_WINDOW": "INV_TIMING_OK",
        "AMBIGUOUS_TWIN_SCAN": "INV_AMBIGUOUS_TWIN",
    }[check]
