"""Generator self-validation gates (DATA.md §8)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from generator import narrate
from generator.config import DEAD_ORDER_RANGE, RTGS_MIN_PAISE
from generator.fees import compute_net

EXPLAINED_CHAIN = {
    "CLEAN_MATCH",
    "LATE_SETTLEMENT",
    "SPLIT_SETTLEMENT",
    "AMBIGUOUS_CANDIDATES",
}


def _load(db_path: Path) -> dict[str, list[dict]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return {
            t: [dict(r) for r in con.execute(f"SELECT * FROM {t}")]
            for t in ("orders", "payments", "settlements", "bank_txns", "adjustments")
        }
    finally:
        con.close()


def _by_id(rows: list[dict]) -> dict[str, dict]:
    return {r[next(iter(r))]: r for r in rows}


def _payment_method(payment: dict | None, proc_ref: str, payments_by_proc: dict) -> str | None:
    if payment:
        return payment["method"]
    hit = payments_by_proc.get(proc_ref)
    return hit["method"] if hit else None


def _check_txn(t: dict, tabs: dict, problems: list[str], dp) -> None:
    cause = t["cause_code"]
    links = t["expected_links"]
    if cause == "COMBINED_SETTLEMENT":
        return
    ord_rows = [_by_id(tabs["orders"]).get(i) for i in links["orders"]]
    P = [_by_id(tabs["payments"]).get(i) for i in links["payments"]]
    S = [_by_id(tabs["settlements"]).get(i) for i in links["settlements"]]
    B = [_by_id(tabs["bank_txns"]).get(i) for i in links["bank_txns"]]
    A = [_by_id(tabs["adjustments"]).get(i) for i in links["adjustments"]]
    ord_rows = [x for x in ord_rows if x]
    P = [x for x in P if x]
    S = [x for x in S if x]
    B = [x for x in B if x]
    A = [x for x in A if x]
    wk = t["work_key"]
    delta = t["expected_delta_paise"]
    payments_by_proc = {p["processor_ref"]: p for p in tabs["payments"] if p["processor_ref"]}

    def order() -> dict:
        return ord_rows[0]

    if cause == "BANK_ONLY_CREDIT":
        if len(B) != 1 or delta != B[0]["amount_paise"]:
            problems.append(f"{wk}: bank-only mismatch")
        return

    if cause == "MALFORMED_SOURCE_ROW":
        if len(P) != 1:
            problems.append(f"{wk}: malformed row must have exactly one payment")
            return
        amt = P[0]["amount_paise"]
        mojibake = any(any(ch in narrate.MOJIBAKE_CHARS for ch in b["narration"]) for b in B)
        if amt is not None and amt >= 0 and not mojibake:
            problems.append(f"{wk}: malformed flavor missing (null/negative/mojibake)")
        return

    if not ord_rows or len(P) != 1:
        problems.append(f"{wk}: expected one order + one payment")
        return
    gross = order()["amount_paise"]
    method = P[0]["method"]

    if (
        len(P) == 1
        and P[0]["order_id"] is not None
        and P[0]["order_id"] != wk
        and cause != "AMBIGUOUS_CANDIDATES"
    ):
        problems.append(f"{wk}: payment points at wrong order")

    if cause == "REFUND_FULL":
        if S or B or not A or A[0]["amount_paise"] != P[0]["amount_paise"]:
            problems.append(f"{wk}: refund_full shape wrong")
        return

    if cause == "MISSING_SETTLEMENT":
        if S or B or A:
            problems.append(f"{wk}: missing_settlement must have no downstream rows")
        _, _, net = compute_net(gross, method)
        if delta != net:
            problems.append(f"{wk}: missing delta != net")
        return

    if cause in {"DUPLICATE_BANK_CREDIT", "SHORT_SETTLED", "UNEXPLAINED_DELTA"}:
        if len(P) != 1 or len(S) != 1:
            problems.append(f"{wk}: expected single chain")
            return
        s = S[0]
        fee, tax, net = compute_net(gross, method)
        if (s["fee_paise"], s["tax_paise"], s["net_paise"]) != (fee, tax, net):
            problems.append(f"{wk}: settlement fee math wrong")
            return
        credits = [b for b in B if b["amount_paise"] > 0]
        if cause == "DUPLICATE_BANK_CREDIT":
            if (
                len(credits) != 2
                or abs(credits[0]["amount_paise"] - credits[1]["amount_paise"]) > 100
                or max(credits[0]["amount_paise"], credits[1]["amount_paise"]) < s["net_paise"]
            ):
                problems.append(f"{wk}: duplicate credits shape wrong")
            elif delta != s["net_paise"]:
                problems.append(f"{wk}: dup delta != settlement net")
            return
        if len(credits) != 1:
            problems.append(f"{wk}: expected one credit")
            return
        if cause == "SHORT_SETTLED" and credits[0]["amount_paise"] != net - delta:
            problems.append(f"{wk}: short amount mismatch")
        if cause == "UNEXPLAINED_DELTA":
            if credits[0]["amount_paise"] != net - delta:
                problems.append(f"{wk}: unexplained delta mismatch")
            if delta % 100 != 0 or not (
                dp.unexplained_min_paise <= delta <= dp.unexplained_max_paise
            ):
                problems.append(f"{wk}: delta out of designed range")
        return

    if cause == "FEE_EXPLAINED":
        if S or A:
            problems.append(f"{wk}: fee-explained must have no settlement/adjustment rows")
            return
        fee, tax, net = compute_net(gross, method)
        credits = [b for b in B if b["amount_paise"] > 0]
        if len(credits) != 1 or credits[0]["amount_paise"] != net:
            problems.append(f"{wk}: fee-explained credit != schedule net")
            return
        if delta != fee + tax:
            problems.append(f"{wk}: fee-explained delta wrong")
        return

    if cause in EXPLAINED_CHAIN:
        if sum(p["amount_paise"] for p in P) != gross:
            problems.append(f"{wk}: payments != order amount")
            return
        credits = [b for b in B if b["amount_paise"] > 0]
        debits = [b for b in B if b["amount_paise"] < 0]
        if cause == "SPLIT_SETTLEMENT":
            _, _, net = compute_net(gross, method)
            if sum(s["net_paise"] for s in S) != net:
                problems.append(f"{wk}: split legs != net")
            for s in S:
                if s["fee_paise"] or s["tax_paise"] or s["net_paise"] != s["gross_paise"]:
                    problems.append(f"{wk}: split leg {s['settlement_id']} shape wrong")
            if sum(c["amount_paise"] for c in credits) != net:
                problems.append(f"{wk}: split credits != net")
        else:
            if len(S) != 1:
                problems.append(f"{wk}: expected exactly one settlement")
                return
            s = S[0]
            if s["gross_paise"] != gross:
                problems.append(f"{wk}: settlement gross != order amount")
                return
            m = _payment_method(
                next((p for p in P if p["payment_id"] == s["payment_id"]), None),
                s["processor_ref"],
                payments_by_proc,
            )
            if m is None:
                problems.append(f"{wk}: settlement {s['settlement_id']} unanchorable method")
                return
            fee, tax, net = compute_net(gross, m)
            if (s["fee_paise"], s["tax_paise"], s["net_paise"]) != (fee, tax, net):
                problems.append(f"{wk}: settlement {s['settlement_id']} fee math wrong")
                return
            if sum(c["amount_paise"] for c in credits) != net:
                problems.append(f"{wk}: credits != settlement net")
        if cause == "FEE_EXPLAINED":
            fee, tax = compute_net(gross, method)[:2]
            if delta != fee + tax:
                problems.append(f"{wk}: fee-explained delta wrong")
        if cause == "REFUND_PARTIAL":
            if (
                len(debits) != 1
                or len(A) != 1
                or abs(debits[0]["amount_paise"]) != A[0]["amount_paise"]
            ):
                problems.append(f"{wk}: refund_partial debit/adjustment mismatch")
        elif debits:
            problems.append(f"{wk}: unexpected debit rows")


def _check_times(t: dict, tabs: dict, problems: list[str]) -> None:
    links = t["expected_links"]
    if t["cause_code"] in {"BANK_ONLY_CREDIT", "MALFORMED_SOURCE_ROW"}:
        return
    orders = [_by_id(tabs["orders"])[i] for i in links["orders"] if i in _by_id(tabs["orders"])]
    payments = [
        _by_id(tabs["payments"])[i] for i in links["payments"] if i in _by_id(tabs["payments"])
    ]
    settles = [
        _by_id(tabs["settlements"])[i]
        for i in links["settlements"]
        if i in _by_id(tabs["settlements"])
    ]
    banks = [
        _by_id(tabs["bank_txns"])[i] for i in links["bank_txns"] if i in _by_id(tabs["bank_txns"])
    ]
    adjs = [
        _by_id(tabs["adjustments"])[i]
        for i in links["adjustments"]
        if i in _by_id(tabs["adjustments"])
    ]
    wk = t["work_key"]
    if orders and payments:
        if orders[0]["created_at"] >= payments[0]["paid_at"]:
            problems.append(f"{wk}: order created after payment")
    if payments and settles:
        if any(p["paid_at"][:10] >= s["settled_at"][:10] for p in payments for s in settles):
            problems.append(f"{wk}: settlement not after payment date")
    if settles and banks:
        credits = [b for b in banks if b["amount_paise"] > 0]
        if credits and any(
            s["settled_at"][:10] > max(b["posted_at"][:10] for b in credits) for s in settles
        ):
            problems.append(f"{wk}: bank credit before settlement date")
    if adjs and payments:
        if any(a["created_at"] <= p["paid_at"] for a in adjs for p in payments):
            problems.append(f"{wk}: adjustment before payment")
    if t["cause_code"] == "REFUND_PARTIAL":
        debit = next((b for b in banks if b["amount_paise"] < 0), None)
        if debit and adjs and debit["posted_at"] <= adjs[0]["created_at"]:
            problems.append(f"{wk}: refund debit before adjustment")


def _check_unexplainable(gt: dict, tabs: dict, problems: list[str]) -> None:
    for t in gt["transactions"]:
        if t["cause_code"] not in {"MISSING_SETTLEMENT", "UNEXPLAINED_DELTA", "SHORT_SETTLED"}:
            continue
        wk = t["work_key"]
        expected_bank = set(t["expected_links"]["bank_txns"])
        hits = {
            b["bank_txn_id"]
            for b in tabs["bank_txns"]
            if wk in narrate.extract_order_refs(b["narration"])
            and b["bank_txn_id"] not in expected_bank
        }
        if hits:
            problems.append(f"{wk}: unexpected narration references {sorted(hits)}")
        expected_adjs = set(t["expected_links"]["adjustments"])
        expected_pays = set(t["expected_links"]["payments"])
        stray_adj = [
            a["adjustment_id"]
            for a in tabs["adjustments"]
            if a["payment_id"] in expected_pays and a["adjustment_id"] not in expected_adjs
        ]
        if stray_adj:
            problems.append(f"{wk}: unexpected adjustments {stray_adj}")


def _check_noise(gt: dict, tabs: dict, problems: list[str]) -> None:
    captured_order_ids = {
        p["order_id"] for p in tabs["payments"] if p["status"] == "captured" and p["order_id"]
    }
    noise_orders = []
    for o in tabs["orders"]:
        if o["status"] == "cancelled":
            noise_orders.append(o["order_id"])
        else:
            its = [p for p in tabs["payments"] if p["order_id"] == o["order_id"]]
            if its and all(p["status"] == "failed" for p in its):
                noise_orders.append(o["order_id"])
    for oid in noise_orders:
        if oid in captured_order_ids:
            continue
        pay_ids = [p["payment_id"] for p in tabs["payments"] if p["order_id"] == oid]
        for s in tabs["settlements"]:
            if s["payment_id"] in pay_ids:
                problems.append(
                    f"noise {oid}: settlement references failed payment {s['settlement_id']}"
                )
        for b in tabs["bank_txns"]:
            if oid in narrate.extract_order_refs(b["narration"]):
                problems.append(f"noise {oid}: narration references {b['bank_txn_id']}")
        for a in tabs["adjustments"]:
            if a["payment_id"] in pay_ids:
                problems.append(f"noise {oid}: adjustment references failed payment")


def _check_combined(gt: dict, tabs: dict, problems: list[str]) -> None:
    bank_members: dict[str, list[dict]] = {}
    for t in gt["transactions"]:
        if t["cause_code"] != "COMBINED_SETTLEMENT":
            continue
        for bid in t["expected_links"]["bank_txns"]:
            bank_members.setdefault(bid, []).append(t)
    by_settle = _by_id(tabs["settlements"])
    by_bank = _by_id(tabs["bank_txns"])
    for bid, txns in bank_members.items():
        credit = by_bank.get(bid)
        if credit is None:
            problems.append(f"combined {bid}: shared credit missing")
            continue
        net_sum = 0
        wk_refs = set()
        for t in txns:
            wk = t["work_key"]
            wk_refs.add(wk)
            order = _by_id(tabs["orders"]).get(wk)
            pay = _by_id(tabs["payments"]).get(t["expected_links"]["payments"][0])
            if not order or not pay or pay["amount_paise"] != order["amount_paise"]:
                problems.append(f"{wk}: combined member payment/order mismatch")
                continue
            net_sum += sum(
                s["net_paise"]
                for s in (by_settle.get(i) for i in t["expected_links"]["settlements"])
                if s
            )
        if credit["amount_paise"] != net_sum:
            problems.append(f"{bid}: combined payout {credit['amount_paise']} != nets {net_sum}")
        found = narrate.extract_order_refs(credit["narration"]) & wk_refs
        if not found:
            problems.append(f"{bid}: combined narration carries no member ref")


def _check_bank_gates(tabs: dict, problems: list[str]) -> None:
    live_orders = {o["order_id"] for o in tabs["orders"]}
    live_nums = {oid.split("-")[1] for oid in live_orders}
    lo, hi = DEAD_ORDER_RANGE
    seen_canon: set[str] = set()
    seen_raw: list[str] = []
    for b in tabs["bank_txns"]:
        if b["narration"].startswith("RTGS") and b["amount_paise"] > 0:
            if b["amount_paise"] < RTGS_MIN_PAISE:
                problems.append(
                    f"{b['bank_txn_id']}: RTGS below Rs2L minimum ({b['amount_paise']})"
                )
        seen_canon |= narrate.extract_order_refs(b["narration"])
        seen_raw.extend(narrate.extract_raw_ref_tokens(b["narration"]))

    def swap_variants(num: str) -> set[str]:
        out = set()
        d = list(num)
        for i in range(len(d) - 1):
            d[i], d[i + 1] = d[i + 1], d[i]
            out.add("".join(d))
            d[i], d[i + 1] = d[i + 1], d[i]
        return out

    live_swaps = {v for n in live_nums for v in swap_variants(n)}
    for raw in seen_raw:
        canon = f"ORD-{int(raw)}"
        if canon in live_orders:
            continue
        if lo <= int(raw) <= hi:
            continue
        if raw in live_nums or raw in live_swaps:
            continue
        if any(n.startswith(raw) for n in live_nums):
            continue
        problems.append(f"narration ref ORD-{raw} neither live, decoy, nor typo/truncation noise")


def _check_sparse_ids(tabs: dict, problems: list[str]) -> None:
    nums = sorted(int(o["order_id"].split("-")[1]) for o in tabs["orders"])
    if len(nums) < 5:
        return
    gaps = sum(1 for a, b in zip(nums, nums[1:], strict=False) if b - a > 1)
    if gaps == 0:
        problems.append("order ids are contiguous; sparse-id scheme violated")


def run(db_path: Path, gt_path: Path) -> dict:
    tabs = _load(db_path)
    import json

    gt = json.loads(Path(gt_path).read_text())
    ids = {t: {r[list(r.keys())[0]] for r in rows} for t, rows in tabs.items()}
    problems: list[str] = []

    for t in gt["transactions"]:
        links = t["expected_links"]
        for table, id_list in links.items():
            for i in id_list:
                if i not in ids[table]:
                    problems.append(f"{t['work_key']}: linked {i} missing from {table}")
        for d in t.get("decoys", []):
            found = any(d in v for v in ids.values())
            if not found:
                problems.append(f"{t['work_key']}: decoy {d} missing")

    from generator.config import DIFFICULTY_PRESETS

    dp = DIFFICULTY_PRESETS[gt.get("difficulty", "NORMAL")]
    by_cause: dict[str, int] = {}
    for t in gt["transactions"]:
        by_cause[t["cause_code"]] = by_cause.get(t["cause_code"], 0) + 1
        _check_txn(t, tabs, problems, dp)

    comp_ok = all(by_cause.get(k, 0) == v for k, v in gt["composition"].items())
    if not comp_ok:
        problems.append("composition histogram mismatch")

    for t in gt["transactions"]:
        _check_times(t, tabs, problems)

    _check_unexplainable(gt, tabs, problems)
    _check_noise(gt, tabs, problems)
    _check_combined(gt, tabs, problems)
    _check_bank_gates(tabs, problems)
    _check_sparse_ids(tabs, problems)

    return {
        "passed": not problems,
        "problem_count": len(problems),
        "problems": problems[:50],
        "checked_transactions": len(gt["transactions"]),
    }
