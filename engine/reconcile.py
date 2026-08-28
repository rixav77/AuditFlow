"""Stage 2 — exact paise arithmetic per bundle. No LLM. Produces Findings."""

from __future__ import annotations

from engine.fees_ext import compute_net_with, merged_schedule
from engine.linkage import Indexes
from engine.types import Bundle, Finding


def captured_with_rule(ix: Indexes, b: Bundle):
    sched = merged_schedule(ix.extra_schedule)
    out = []
    for pid in b.payments:
        p = ix.payments[pid]
        rule = sched.get(p["method"])
        if (
            p["status"] == "captured"
            and p["amount_paise"] is not None
            and p["amount_paise"] > 0
            and rule is not None
        ):
            out.append((p, rule))
    return out


def _captured_payments(ix: Indexes, b: Bundle) -> list[dict]:
    return [p for p, _ in captured_with_rule(ix, b)]


def reconcile(ix: Indexes, b: Bundle) -> list[Finding]:
    findings: list[Finding] = []
    payments = _captured_payments(ix, b)
    pay_total = sum(p["amount_paise"] for p in payments)
    order_total = sum(ix.orders[o]["amount_paise"] for o in b.orders)

    settle_net = sum(ix.settlements[s]["net_paise"] for s in b.settlements)
    expected_net = sum(
        compute_net_with(p["amount_paise"], rule)[2] for p, rule in captured_with_rule(ix, b)
    )
    adj_total = sum(ix.adjustments[a]["amount_paise"] for a in b.adjustments)
    credits = sum(
        ix.banks[c]["amount_paise"] for c in b.bank_txns if ix.banks[c]["amount_paise"] > 0
    )
    debits = sum(
        -ix.banks[c]["amount_paise"] for c in b.bank_txns if ix.banks[c]["amount_paise"] < 0
    )

    if b.orders:
        findings.append(Finding("PAY_VS_ORDER", order_total, pay_total))
    if b.payments:
        findings.append(Finding("SETTLE_NET_VS_EXPECTED", expected_net, settle_net))
    findings.append(Finding("BANK_VS_NET_MINUS_ADJ", settle_net - adj_total, credits - debits))
    if b.orders:
        findings.append(Finding("NAIVE_BANK_VS_ORDER", order_total, credits - debits))
    return findings
