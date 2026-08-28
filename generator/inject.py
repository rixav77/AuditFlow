"""Per-cause record builders. Each returns rows + GT entry + generation audit events.

Anti-leakage: builders never emit cause codes into DB rows or audit payloads.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from generator import narrate
from generator.config import (
    EXPECTED_CLASS,
    FEE_SCHEDULE,
    LATE_MAX_DAYS,
    LATE_MIN_DAYS,
    METHODS_WITH_FEE,
    SETTLE_MAX_DAYS,
    SETTLE_MIN_DAYS,
    DifficultyParams,
)
from generator.entities import (
    BUSINESSES,
    ITEMS,
    PERSONS,
    plus_minutes,
    rand_amount,
    rand_daytime,
    rand_processor_ref,
    rand_utr,
)
from generator.fees import compute_net


@dataclass
class Built:
    orders: list[dict] = field(default_factory=list)
    payments: list[dict] = field(default_factory=list)
    settlements: list[dict] = field(default_factory=list)
    bank_txns: list[dict] = field(default_factory=list)
    adjustments: list[dict] = field(default_factory=list)
    audits: list[dict] = field(default_factory=list)
    gt: list[dict] = field(default_factory=list)


def merge(dst: Built, src: Built) -> None:
    for k in (
        "orders",
        "payments",
        "settlements",
        "bank_txns",
        "adjustments",
        "audits",
        "gt",
    ):
        getattr(dst, k).extend(getattr(src, k))


@dataclass
class Ctx:
    rng: random.Random
    ids: object
    day: date
    dp: DifficultyParams

    def live_orders(self) -> set[str]:
        try:
            return self.ids.seen("order")
        except AttributeError:
            return set()


EMPTY_LINKS = {
    "orders": [],
    "payments": [],
    "settlements": [],
    "bank_txns": [],
    "adjustments": [],
}


def _links(orders=None, payments=None, settlements=None, bank_txns=None, adjustments=None):
    return {
        "orders": orders or [],
        "payments": payments or [],
        "settlements": settlements or [],
        "bank_txns": bank_txns or [],
        "adjustments": adjustments or [],
    }


def _gt(work_key: str, scope: str, cause: str, links: dict, delta, expl: str, decoys=None):
    cls = EXPECTED_CLASS[cause]
    merged = {**{k: list(v) for k, v in EMPTY_LINKS.items()}, **(links or {})}
    return {
        "work_key": work_key,
        "scope": scope,
        "cause_code": cause,
        "expected_class": cls,
        "abstention_expected": cls == "unresolved",
        "expected_links": merged,
        "decoys": decoys or [],
        "expected_delta_paise": delta,
        "explanation_human": expl,
    }


def _audit(work_key: str, **id_lists) -> dict:
    flat = {t: ids for t, ids in id_lists.items() if ids}
    return {
        "work_key": work_key,
        "stage": "generation",
        "event": "records_emitted",
        "payload_json": json.dumps({"records": flat}, sort_keys=True),
    }


def _mk_order(ctx: Ctx, status: str = "confirmed") -> tuple[dict, int]:
    amount = rand_amount(ctx.rng)
    row = {
        "order_id": ctx.ids.next("order"),
        "amount_paise": amount,
        "customer_name": ctx.rng.choice(PERSONS),
        "item_desc": ctx.rng.choice(ITEMS),
        "status": status,
        "created_at": rand_daytime(ctx.rng, ctx.day),
    }
    return row, amount


def _mk_payment(
    ctx: Ctx,
    order_id: str | None,
    amount,
    method: str,
    status: str,
    paid_at: str,
) -> dict:
    roll = ctx.rng.random()
    if roll < 0.08:
        ref = None
    elif roll < 0.14:
        ref = rand_processor_ref(ctx.rng)[:9]
    else:
        ref = rand_processor_ref(ctx.rng)
    return {
        "payment_id": ctx.ids.next("payment"),
        "order_id": order_id,
        "processor_ref": ref,
        "amount_paise": amount,
        "method": method,
        "status": status,
        "paid_at": paid_at,
    }


def _mk_settlement(
    ctx: Ctx,
    payment: dict | None,
    gross: int,
    settled_at: str,
    paylink: bool = True,
    utr: str | None = None,
) -> tuple[dict, int, int, int]:
    method = payment["method"]
    fee, tax, net = compute_net(gross, method)
    row = {
        "settlement_id": ctx.ids.next("settlement"),
        "payment_id": payment["payment_id"] if (payment and paylink) else None,
        "processor_ref": payment["processor_ref"] if payment else rand_processor_ref(ctx.rng),
        "gross_paise": gross,
        "fee_paise": fee,
        "tax_paise": tax,
        "net_paise": net,
        "utr": utr,
        "settled_at": settled_at,
    }
    return row, fee, tax, net


def _mk_credit(ctx: Ctx, amount: int, posted_at: str, narration: str) -> dict:
    return {
        "bank_txn_id": ctx.ids.next("bank"),
        "narration": narration,
        "amount_paise": amount,
        "posted_at": posted_at,
        "value_date": posted_at[:10],
    }


def _full_chain(
    ctx: Ctx,
    method: str,
    settle_days: int,
    drop_order_link: bool = False,
    drop_paylink: bool = False,
    drop_utr: bool = False,
    credit_amount: int | None = None,
):
    order, gross = _mk_order(ctx)
    paid_at = plus_minutes(order["created_at"], ctx.rng.randrange(2, 91))
    payment = _mk_payment(
        ctx, None if drop_order_link else order["order_id"], gross, method, "captured", paid_at
    )
    settle_day = ctx.day + timedelta(days=settle_days)
    utr = None if drop_utr else rand_utr(ctx.rng, settle_day)
    if drop_paylink and payment["processor_ref"] is None:
        payment["processor_ref"] = rand_processor_ref(ctx.rng)
    settlement, fee, tax, net = _mk_settlement(
        ctx, payment, gross, rand_daytime(ctx.rng, settle_day), paylink=not drop_paylink, utr=utr
    )
    post_day = settle_day if ctx.rng.random() < 0.75 else settle_day + timedelta(days=1)
    credit_amt = net if credit_amount is None else credit_amount
    credit = _mk_credit(
        ctx,
        credit_amt,
        rand_daytime(ctx.rng, post_day),
        narrate.ensure_identifier(
            narrate.credit_narration(
                ctx.rng,
                settle_day,
                order["order_id"],
                utr,
                credit_amt,
                ctx.dp,
                ctx.live_orders(),
            ),
            order["order_id"],
            utr,
        ),
    )
    return {
        "order": order,
        "gross": gross,
        "payment": payment,
        "settlement": settlement,
        "fee": fee,
        "tax": tax,
        "net": net,
        "credit": credit,
    }


def b_clean(ctx: Ctx) -> Built:
    c = _full_chain(
        ctx,
        ctx.rng.choice(list(FEE_SCHEDULE)),
        ctx.rng.randrange(SETTLE_MIN_DAYS, SETTLE_MAX_DAYS + 1),
        drop_order_link=ctx.rng.random() < 0.05,
        drop_paylink=ctx.rng.random() < 0.10,
        drop_utr=ctx.rng.random() < 0.10,
    )
    o, p, s, b = c["order"], c["payment"], c["settlement"], c["credit"]
    out = Built(orders=[o], payments=[p], settlements=[s], bank_txns=[b])
    out.gt.append(
        _gt(
            o["order_id"],
            "order",
            "CLEAN_MATCH",
            _links([o["order_id"]], [p["payment_id"]], [s["settlement_id"]], [b["bank_txn_id"]]),
            0,
            "end-to-end exact",
        )
    )
    out.audits.append(
        _audit(
            o["order_id"],
            orders=[o["order_id"]],
            payments=[p["payment_id"]],
            settlements=[s["settlement_id"]],
            bank_txns=[b["bank_txn_id"]],
        )
    )
    return out


def b_fee(ctx: Ctx) -> Built:
    """Fee-explained: NO settlement leg documents the fee; bank pays schedule-net.
    Engine must reason the gap from the fee schedule (evidence: payment + schedule)."""
    from datetime import timedelta

    method = ctx.rng.choice(METHODS_WITH_FEE)
    order, gross = _mk_order(ctx)
    paid_at = plus_minutes(order["created_at"], ctx.rng.randrange(2, 91))
    payment = _mk_payment(ctx, order["order_id"], gross, method, "captured", paid_at)
    fee, tax, net = compute_net(gross, method)
    settle_day = ctx.day + timedelta(days=ctx.rng.randrange(SETTLE_MIN_DAYS, SETTLE_MAX_DAYS + 1))
    utr = rand_utr(ctx.rng, settle_day)
    post_day = settle_day if ctx.rng.random() < 0.75 else settle_day + timedelta(days=1)
    credit = _mk_credit(
        ctx,
        net,
        rand_daytime(ctx.rng, post_day),
        narrate.ensure_identifier(
            narrate.credit_narration(
                ctx.rng, settle_day, order["order_id"], utr, net, ctx.dp, ctx.live_orders()
            ),
            order["order_id"],
            utr,
        ),
    )
    o, p, b = order, payment, credit
    rule = FEE_SCHEDULE[method]
    expl = f"{method} fee {rule.rate_bps}bps+Rs{rule.fixed_paise // 100} + GST18% => net {net}"
    out = Built(orders=[o], payments=[p], bank_txns=[b])
    out.gt.append(
        _gt(
            o["order_id"],
            "order",
            "FEE_EXPLAINED",
            _links([o["order_id"]], [p["payment_id"]], [], [b["bank_txn_id"]]),
            fee + tax,
            expl,
        )
    )
    out.audits.append(
        _audit(
            o["order_id"],
            orders=[o["order_id"]],
            payments=[p["payment_id"]],
            bank_txns=[b["bank_txn_id"]],
        )
    )
    return out


def b_late(ctx: Ctx) -> Built:
    c = _full_chain(
        ctx,
        ctx.rng.choice(list(FEE_SCHEDULE)),
        ctx.rng.randrange(LATE_MIN_DAYS, LATE_MAX_DAYS + 1),
    )
    o, p, s, b = c["order"], c["payment"], c["settlement"], c["credit"]
    lag = (date.fromisoformat(s["settled_at"][:10]) - date.fromisoformat(p["paid_at"][:10])).days
    out = Built(orders=[o], payments=[p], settlements=[s], bank_txns=[b])
    out.gt.append(
        _gt(
            o["order_id"],
            "order",
            "LATE_SETTLEMENT",
            _links([o["order_id"]], [p["payment_id"]], [s["settlement_id"]], [b["bank_txn_id"]]),
            0,
            f"settled T+{lag}",
        )
    )
    out.audits.append(
        _audit(
            o["order_id"],
            orders=[o["order_id"]],
            payments=[p["payment_id"]],
            settlements=[s["settlement_id"]],
            bank_txns=[b["bank_txn_id"]],
        )
    )
    return out


def b_split(ctx: Ctx) -> Built:
    k = ctx.rng.choice([2, 2, 3])
    method = ctx.rng.choice(list(FEE_SCHEDULE))
    core_order, gross = _mk_order(ctx)
    paid_at = plus_minutes(core_order["created_at"], ctx.rng.randrange(2, 91))
    payment = _mk_payment(ctx, core_order["order_id"], gross, method, "captured", paid_at)
    _, _, net = compute_net(gross, method)

    if k == 2:
        w = [ctx.rng.uniform(0.40, 0.60)]
    else:
        w = [ctx.rng.uniform(0.25, 0.45), ctx.rng.uniform(0.25, 0.45)]
    legs = [max(100, round(net * x)) for x in w]
    legs.append(max(100, net - sum(legs)))

    offsets = sorted(ctx.rng.sample(range(2, 8), k))
    settlements, credits = [], []
    for i, leg in enumerate(legs):
        sd = ctx.day + timedelta(days=offsets[i])
        utr = rand_utr(ctx.rng, sd)
        s, _, _, _ = _mk_settlement(
            ctx, payment, leg, rand_daytime(ctx.rng, sd), paylink=True, utr=utr
        )
        s["gross_paise"], s["fee_paise"], s["tax_paise"], s["net_paise"] = leg, 0, 0, leg
        settlements.append(s)
        post_day = sd if ctx.rng.random() < 0.75 else sd + timedelta(days=1)
        credits.append(
            _mk_credit(
                ctx,
                leg,
                rand_daytime(ctx.rng, post_day),
                narrate.ensure_identifier(
                    narrate.credit_narration(
                        ctx.rng,
                        sd,
                        core_order["order_id"],
                        utr,
                        leg,
                        ctx.dp,
                        ctx.live_orders(),
                    ),
                    core_order["order_id"],
                    utr,
                ),
            )
        )

    out = Built(orders=[core_order], payments=[payment], settlements=settlements, bank_txns=credits)
    out.gt.append(
        _gt(
            core_order["order_id"],
            "order",
            "SPLIT_SETTLEMENT",
            _links(
                [core_order["order_id"]],
                [payment["payment_id"]],
                [s["settlement_id"] for s in settlements],
                [b["bank_txn_id"] for b in credits],
            ),
            0,
            f"net {net} split into {k} legs summing to {sum(legs)}",
        )
    )
    out.audits.append(
        _audit(
            core_order["order_id"],
            orders=[core_order["order_id"]],
            payments=[payment["payment_id"]],
            settlements=[s["settlement_id"] for s in settlements],
            bank_txns=[b["bank_txn_id"] for b in credits],
        )
    )
    return out


def b_combined(ctx: Ctx) -> Built:
    k = ctx.rng.choice([2, 2, 3, 4])
    members = []
    for _ in range(k):
        order, gross = _mk_order(ctx)
        paid_at = plus_minutes(order["created_at"], ctx.rng.randrange(2, 300))
        method = ctx.rng.choice(list(FEE_SCHEDULE))
        payment = _mk_payment(ctx, order["order_id"], gross, method, "captured", paid_at)
        members.append({"order": order, "gross": gross, "payment": payment})

    settle_day = ctx.day + timedelta(days=ctx.rng.randrange(SETTLE_MIN_DAYS, SETTLE_MAX_DAYS + 1))
    settlements, total_net = [], 0
    for m in members:
        s, _, _, net = _mk_settlement(
            ctx,
            m["payment"],
            m["gross"],
            rand_daytime(ctx.rng, settle_day),
            paylink=True,
            utr=rand_utr(ctx.rng, settle_day),
        )
        m["settlement"] = s
        total_net += net
        settlements.append(s)

    post_day = settle_day if ctx.rng.random() < 0.75 else settle_day + timedelta(days=1)
    anchor = members[0]["order"]["order_id"]
    narration = narrate.ensure_identifier(
        narrate.credit_narration(
            ctx.rng, settle_day, anchor, None, total_net, ctx.dp, ctx.live_orders()
        ),
        anchor,
        None,
    )
    for m in members[1:]:
        if ctx.rng.random() < 0.45:
            narration += (
                f" {narrate.mutate_ref(ctx.rng, m['order']['order_id'], ctx.dp.ref_absent_p)}"
            )
    credit = _mk_credit(ctx, total_net, rand_daytime(ctx.rng, post_day), narration)

    out = Built()
    for m in members:
        out.orders.append(m["order"])
        out.payments.append(m["payment"])
    out.settlements = settlements
    out.bank_txns = [credit]

    for m in members:
        out.gt.append(
            _gt(
                m["order"]["order_id"],
                "order",
                "COMBINED_SETTLEMENT",
                _links(
                    [m["order"]["order_id"]],
                    [m["payment"]["payment_id"]],
                    [m["settlement"]["settlement_id"]],
                    [credit["bank_txn_id"]],
                ),
                0,
                f"one payout Rs{total_net // 100} covering {k} payments",
            )
        )
    out.audits.append(
        _audit(
            anchor,
            orders=[m["order"]["order_id"] for m in members],
            payments=[m["payment"]["payment_id"] for m in members],
            settlements=[s["settlement_id"] for s in settlements],
            bank_txns=[credit["bank_txn_id"]],
        )
    )
    return out


def b_refund_partial(ctx: Ctx) -> Built:
    c = _full_chain(
        ctx,
        ctx.rng.choice(list(FEE_SCHEDULE)),
        ctx.rng.randrange(SETTLE_MIN_DAYS, SETTLE_MAX_DAYS + 1),
    )
    o, p, s, b = c["order"], c["payment"], c["settlement"], c["credit"]
    refund = ((c["gross"] * ctx.rng.uniform(0.15, 0.35)) // 100) * 100
    settle_date = date.fromisoformat(s["settled_at"][:10])
    adj_at = rand_daytime(ctx.rng, settle_date + timedelta(days=1))
    adj = {
        "adjustment_id": ctx.ids.next("adjustment"),
        "adj_type": "refund_partial",
        "payment_id": p["payment_id"],
        "amount_paise": refund,
        "created_at": adj_at,
        "reason": f"customer refund for {o['order_id']}",
    }
    debit = _mk_credit(
        ctx,
        -refund,
        plus_minutes(adj_at, ctx.rng.randrange(5, 61)),
        narrate.refund_debit_narration(ctx.rng, o["order_id"], rand_utr(ctx.rng, settle_date)),
    )
    out = Built(orders=[o], payments=[p], settlements=[s], bank_txns=[b, debit], adjustments=[adj])
    out.gt.append(
        _gt(
            o["order_id"],
            "order",
            "REFUND_PARTIAL",
            _links(
                [o["order_id"]],
                [p["payment_id"]],
                [s["settlement_id"]],
                [b["bank_txn_id"], debit["bank_txn_id"]],
                [adj["adjustment_id"]],
            ),
            0,
            f"partial refund Rs{refund // 100} explained by {adj['adjustment_id']} + bank debit",
        )
    )
    out.audits.append(
        _audit(
            o["order_id"],
            orders=[o["order_id"]],
            payments=[p["payment_id"]],
            settlements=[s["settlement_id"]],
            bank_txns=[b["bank_txn_id"], debit["bank_txn_id"]],
            adjustments=[adj["adjustment_id"]],
        )
    )
    return out


def b_refund_full(ctx: Ctx) -> Built:
    order, gross = _mk_order(ctx)
    paid_at = plus_minutes(order["created_at"], ctx.rng.randrange(2, 91))
    payment = _mk_payment(
        ctx, order["order_id"], gross, ctx.rng.choice(list(FEE_SCHEDULE)), "captured", paid_at
    )
    adj_at = rand_daytime(ctx.rng, ctx.day + timedelta(days=ctx.rng.randrange(1, 3)))
    adj = {
        "adjustment_id": ctx.ids.next("adjustment"),
        "adj_type": "refund_full",
        "payment_id": payment["payment_id"],
        "amount_paise": gross,
        "created_at": adj_at,
        "reason": f"full pre-settlement refund for {order['order_id']}",
    }
    out = Built(orders=[order], payments=[payment], adjustments=[adj])
    out.gt.append(
        _gt(
            order["order_id"],
            "order",
            "REFUND_FULL",
            _links([order["order_id"]], [payment["payment_id"]], [], [], [adj["adjustment_id"]]),
            0,
            f"refunded Rs{gross // 100} before settlement; nothing further expected",
        )
    )
    out.audits.append(
        _audit(
            order["order_id"],
            orders=[order["order_id"]],
            payments=[payment["payment_id"]],
            adjustments=[adj["adjustment_id"]],
        )
    )
    return out


def b_duplicate_credit(ctx: Ctx) -> Built:
    c = _full_chain(
        ctx,
        ctx.rng.choice(list(FEE_SCHEDULE)),
        ctx.rng.randrange(SETTLE_MIN_DAYS, SETTLE_MAX_DAYS + 1),
    )
    o, p, s, b = c["order"], c["payment"], c["settlement"], c["credit"]
    dup_day = date.fromisoformat(b["posted_at"][:10]) + timedelta(days=1)
    dup_amt = b["amount_paise"]
    note = "exact duplicate"
    if ctx.rng.random() < 0.35:
        dup_amt += ctx.rng.choice([100, -100])
        note = "near-duplicate (Rs1 off)"
    dup = _mk_credit(
        ctx,
        dup_amt,
        rand_daytime(ctx.rng, dup_day),
        narrate.ensure_identifier(
            narrate.credit_narration(
                ctx.rng, dup_day, o["order_id"], s["utr"], dup_amt, ctx.dp, ctx.live_orders()
            ),
            o["order_id"],
            s["utr"],
        ),
    )
    out = Built(orders=[o], payments=[p], settlements=[s], bank_txns=[b, dup])
    out.gt.append(
        _gt(
            o["order_id"],
            "order",
            "DUPLICATE_BANK_CREDIT",
            _links(
                [o["order_id"]],
                [p["payment_id"]],
                [s["settlement_id"]],
                [b["bank_txn_id"], dup["bank_txn_id"]],
            ),
            b["amount_paise"],
            f"{note}: {dup['bank_txn_id']} repeats {b['bank_txn_id']}",
        )
    )
    out.audits.append(
        _audit(
            o["order_id"],
            orders=[o["order_id"]],
            payments=[p["payment_id"]],
            settlements=[s["settlement_id"]],
            bank_txns=[b["bank_txn_id"], dup["bank_txn_id"]],
        )
    )
    return out


def b_short_settled(ctx: Ctx) -> Built:
    method = ctx.rng.choice(METHODS_WITH_FEE)
    c = _full_chain(
        ctx, method, ctx.rng.randrange(SETTLE_MIN_DAYS, SETTLE_MAX_DAYS + 1), credit_amount=-1
    )
    o, p, s, b = c["order"], c["payment"], c["settlement"], c["credit"]
    lo, hi = ctx.dp.short_pct_lo, ctx.dp.short_pct_hi
    pct = ctx.rng.randrange(lo, hi + 1)
    cut = ((c["gross"] * pct / 100) // 100) * 100
    cut = min(cut, c["net"] - 100)
    b["amount_paise"] = c["net"] - cut
    out = Built(orders=[o], payments=[p], settlements=[s], bank_txns=[b])
    out.gt.append(
        _gt(
            o["order_id"],
            "order",
            "SHORT_SETTLED",
            _links([o["order_id"]], [p["payment_id"]], [s["settlement_id"]], [b["bank_txn_id"]]),
            cut,
            f"bank short by Rs{cut // 100} vs settlement net; no evidence explains it",
        )
    )
    out.audits.append(
        _audit(
            o["order_id"],
            orders=[o["order_id"]],
            payments=[p["payment_id"]],
            settlements=[s["settlement_id"]],
            bank_txns=[b["bank_txn_id"]],
        )
    )
    return out


def b_missing_settlement(ctx: Ctx) -> Built:
    order, gross = _mk_order(ctx)
    method = ctx.rng.choice(list(FEE_SCHEDULE))
    paid_at = plus_minutes(order["created_at"], ctx.rng.randrange(2, 91))
    payment = _mk_payment(ctx, order["order_id"], gross, method, "captured", paid_at)
    fee, tax, net = compute_net(gross, method)
    out = Built(orders=[order], payments=[payment])
    out.gt.append(
        _gt(
            order["order_id"],
            "order",
            "MISSING_SETTLEMENT",
            _links([order["order_id"]], [payment["payment_id"]]),
            net,
            f"captured payment Rs{gross // 100}; "
            f"no settlement/bank trace (expected ~Rs{net // 100})",
        )
    )
    out.audits.append(
        _audit(order["order_id"], orders=[order["order_id"]], payments=[payment["payment_id"]])
    )
    return out


def b_unexplained_delta(ctx: Ctx) -> Built:
    method = ctx.rng.choice(METHODS_WITH_FEE)
    c = _full_chain(
        ctx, method, ctx.rng.randrange(SETTLE_MIN_DAYS, SETTLE_MAX_DAYS + 1), credit_amount=-1
    )
    o, p, s, b = c["order"], c["payment"], c["settlement"], c["credit"]
    lo = ctx.dp.unexplained_min_paise
    hi = min(ctx.dp.unexplained_max_paise, max(lo, c["net"] - 10_000), int(c["net"] * 0.15))
    hi = max(hi, lo)
    d = ctx.rng.randrange(lo // 100, hi // 100 + 1) * 100
    d = min(d, c["net"] - 100)
    b["amount_paise"] = c["net"] - d
    out = Built(orders=[o], payments=[p], settlements=[s], bank_txns=[b])
    out.gt.append(
        _gt(
            o["order_id"],
            "order",
            "UNEXPLAINED_DELTA",
            _links([o["order_id"]], [p["payment_id"]], [s["settlement_id"]], [b["bank_txn_id"]]),
            d,
            f"settlement says net {c['net']}, bank sent {b['amount_paise']}; "
            f"no record explains Rs{d // 100}",
        )
    )
    out.audits.append(
        _audit(
            o["order_id"],
            orders=[o["order_id"]],
            payments=[p["payment_id"]],
            settlements=[s["settlement_id"]],
            bank_txns=[b["bank_txn_id"]],
        )
    )
    return out


def b_malformed(ctx: Ctx) -> Built:
    flavor = ctx.rng.choice(["null", "negative", "mojibake"])
    order, gross = _mk_order(ctx)
    paid_at = plus_minutes(order["created_at"], ctx.rng.randrange(2, 91))

    if flavor == "mojibake":
        method = ctx.rng.choice(METHODS_WITH_FEE)
        c = _full_chain(ctx, method, ctx.rng.randrange(SETTLE_MIN_DAYS, SETTLE_MAX_DAYS + 1))
        o, p, s, b = c["order"], c["payment"], c["settlement"], c["credit"]
        b["narration"] += narrate.mojibake_glitch(ctx.rng)
        expl = f"corrupt narration glyphs on {b['bank_txn_id']}; source encoding issue"
        out = Built(orders=[o], payments=[p], settlements=[s], bank_txns=[b])
        links = _links([o["order_id"]], [p["payment_id"]], [s["settlement_id"]], [b["bank_txn_id"]])
    else:
        amount = None if flavor == "null" else -ctx.rng.randrange(100, 5_001) * 100
        payment = _mk_payment(
            ctx, order["order_id"], amount, ctx.rng.choice(list(FEE_SCHEDULE)), "captured", paid_at
        )
        expl = (
            "payment amount is NULL"
            if flavor == "null"
            else f"payment amount is negative ({amount})"
        )
        out = Built(orders=[order], payments=[payment])
        links = _links([order["order_id"]], [payment["payment_id"]])

    out.gt.append(
        _gt(
            order["order_id"] if not out.bank_txns else out.orders[0]["order_id"],
            "order",
            "MALFORMED_SOURCE_ROW",
            links,
            None,
            expl,
        )
    )
    out.audits.append(
        _audit(
            out.orders[0]["order_id"],
            orders=[x["order_id"] for x in out.orders],
            payments=[x["payment_id"] for x in out.payments],
            settlements=[x["settlement_id"] for x in out.settlements],
            bank_txns=[x["bank_txn_id"] for x in out.bank_txns],
        )
    )
    return out


def b_bank_only(ctx: Ctx) -> Built:
    amount = rand_amount(ctx.rng)
    business = ctx.rng.choice([b for b in BUSINESSES if b != "AARAV ENTERPRISES"])
    credit = _mk_credit(
        ctx,
        amount,
        rand_daytime(ctx.rng, ctx.day),
        narrate.bank_only_narration(ctx.rng, business, ctx.day),
    )
    out = Built(bank_txns=[credit])
    out.gt.append(
        _gt(
            credit["bank_txn_id"],
            "bank",
            "BANK_ONLY_CREDIT",
            _links(bank_txns=[credit["bank_txn_id"]]),
            amount,
            f"unmatched inflow Rs{amount // 100} ({business}); no source chain",
        )
    )
    out.audits.append(_audit(credit["bank_txn_id"], bank_txns=[credit["bank_txn_id"]]))
    return out


def b_ambiguous(ctx: Ctx) -> Built:
    method_real = ctx.rng.choice(METHODS_WITH_FEE)
    c = _full_chain(ctx, method_real, ctx.rng.randrange(SETTLE_MIN_DAYS, SETTLE_MAX_DAYS + 1))
    o, p, s, b = c["order"], c["payment"], c["settlement"], c["credit"]

    twin_paid = plus_minutes(p["paid_at"], ctx.rng.choice([-1, 1]) * ctx.rng.randrange(3, 31))
    twin_method = ctx.rng.choice(METHODS_WITH_FEE)
    twin = _mk_payment(ctx, None, c["gross"], twin_method, "captured", twin_paid)
    t_sd = ctx.day + timedelta(days=ctx.rng.randrange(SETTLE_MIN_DAYS, SETTLE_MAX_DAYS + 1))
    t_settle, _, _, _ = _mk_settlement(
        ctx,
        twin,
        c["gross"],
        rand_daytime(ctx.rng, t_sd),
        paylink=True,
        utr=rand_utr(ctx.rng, t_sd),
    )

    out = Built(orders=[o], payments=[p, twin], settlements=[s, t_settle], bank_txns=[b])
    out.gt.append(
        _gt(
            o["order_id"],
            "order",
            "AMBIGUOUS_CANDIDATES",
            _links([o["order_id"]], [p["payment_id"]], [s["settlement_id"]], [b["bank_txn_id"]]),
            0,
            f"twin payment {twin['payment_id']} same amount/date; "
            f"explicit order link resolves to {p['payment_id']}",
            decoys=[twin["payment_id"], t_settle["settlement_id"]],
        )
    )
    out.audits.append(
        _audit(
            o["order_id"],
            orders=[o["order_id"]],
            payments=[x["payment_id"] for x in (p, twin)],
            settlements=[x["settlement_id"] for x in (s, t_settle)],
            bank_txns=[b["bank_txn_id"]],
        )
    )
    return out


BUILDERS = {
    "CLEAN_MATCH": b_clean,
    "FEE_EXPLAINED": b_fee,
    "LATE_SETTLEMENT": b_late,
    "SPLIT_SETTLEMENT": b_split,
    "COMBINED_SETTLEMENT": b_combined,
    "REFUND_PARTIAL": b_refund_partial,
    "REFUND_FULL": b_refund_full,
    "DUPLICATE_BANK_CREDIT": b_duplicate_credit,
    "SHORT_SETTLED": b_short_settled,
    "MISSING_SETTLEMENT": b_missing_settlement,
    "UNEXPLAINED_DELTA": b_unexplained_delta,
    "MALFORMED_SOURCE_ROW": b_malformed,
    "BANK_ONLY_CREDIT": b_bank_only,
    "AMBIGUOUS_CANDIDATES": b_ambiguous,
}


def n_failed_payment(ctx: Ctx) -> Built:
    order, gross = _mk_order(ctx)
    paid_at = plus_minutes(order["created_at"], ctx.rng.randrange(2, 91))
    payment = _mk_payment(
        ctx, order["order_id"], gross, ctx.rng.choice(list(FEE_SCHEDULE)), "failed", paid_at
    )
    return Built(orders=[order], payments=[payment])


def n_cancelled_order(ctx: Ctx) -> Built:
    order, _ = _mk_order(ctx, status="cancelled")
    return Built(orders=[order])


def n_svc_debit(ctx: Ctx) -> Built:
    amt = ctx.rng.randrange(20, 501) * 100
    row = _mk_credit(
        ctx, -amt, rand_daytime(ctx.rng, ctx.day), narrate.svc_debit_narration(ctx.rng)
    )
    return Built(bank_txns=[row])


def n_reversal_debit(ctx: Ctx) -> Built:
    amt = ctx.rng.randrange(50, 300) * 100
    row = _mk_credit(
        ctx, -amt, rand_daytime(ctx.rng, ctx.day), narrate.reversal_debit_narration(ctx.rng)
    )
    return Built(bank_txns=[row])


NOISE_BUILDERS = {
    "FAILED_PAYMENT": n_failed_payment,
    "CANCELLED_ORDER": n_cancelled_order,
    "SVC_DEBIT": n_svc_debit,
    "REVERSAL_DEBIT": n_reversal_debit,
}
