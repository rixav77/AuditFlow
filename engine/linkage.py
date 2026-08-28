"""Multi-pass linkage: explicit refs → narration refs → scored window candidates →
combine-merge. Produces graph bundles with per-link provenance (RESEARCH.md §6.1)."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from engine.assist import get_assist
from engine.fees_ext import compute_net_with, merged_schedule
from engine.policy import resolve_policy
from engine.types import (
    P3_MIN_SCORE,
    Bundle,
    Link,
)
from generator.narrate import extract_order_refs


class Indexes:
    def __init__(self, tabs: dict[str, list[dict]]):
        self.tabs = tabs
        self.policy = resolve_policy(tabs)
        self.extra_schedule = self.policy["fee_schedule"]
        self._settled_date = {
            s["settlement_id"]: _ts(s["settled_at"]).date() for s in tabs["settlements"]
        }
        self._paid_date = {
            p["payment_id"]: _ts(p["paid_at"]).date() for p in tabs["payments"] if p["paid_at"]
        }
        self.orders = {o["order_id"]: o for o in tabs["orders"]}
        self.payments = {p["payment_id"]: p for p in tabs["payments"]}
        self.settlements = {s["settlement_id"]: s for s in tabs["settlements"]}
        self.banks = {b["bank_txn_id"]: b for b in tabs["bank_txns"]}
        self.adjustments = {a["adjustment_id"]: a for a in tabs["adjustments"]}
        self.payments_by_order: dict[str, list[str]] = defaultdict(list)
        for p in tabs["payments"]:
            if p["order_id"]:
                self.payments_by_order[p["order_id"]].append(p["payment_id"])
        self.settle_by_payment: dict[str, list[str]] = defaultdict(list)
        for s in tabs["settlements"]:
            if s["payment_id"]:
                self.settle_by_payment[s["payment_id"]].append(s["settlement_id"])
        self.payments_by_proc: dict[str, str] = {
            p["processor_ref"]: pid for pid, p in self.payments.items() if p["processor_ref"]
        }
        self.settle_by_utr: dict[str, str] = {
            s["utr"]: s["settlement_id"] for s in tabs["settlements"] if s["utr"]
        }
        self.adj_by_payment: dict[str, list[str]] = defaultdict(list)
        for a in tabs["adjustments"]:
            self.adj_by_payment[a["payment_id"]].append(a["adjustment_id"])

        self.orders = {o["order_id"]: o for o in tabs["orders"]}
        self.payments = {p["payment_id"]: p for p in tabs["payments"]}
        self.settlements = {s["settlement_id"]: s for s in tabs["settlements"]}
        self.banks = {b["bank_txn_id"]: b for b in tabs["bank_txns"]}
        self.adjustments = {a["adjustment_id"]: a for a in tabs["adjustments"]}
        self.payments_by_order: dict[str, list[str]] = defaultdict(list)
        for p in tabs["payments"]:
            if p["order_id"]:
                self.payments_by_order[p["order_id"]].append(p["payment_id"])
        self.settle_by_payment: dict[str, list[str]] = defaultdict(list)
        for s in tabs["settlements"]:
            if s["payment_id"]:
                self.settle_by_payment[s["payment_id"]].append(s["settlement_id"])
        self.payments_by_proc: dict[str, str] = {
            p["processor_ref"]: pid for pid, p in self.payments.items() if p["processor_ref"]
        }
        self.settle_by_utr: dict[str, str] = {
            s["utr"]: s["settlement_id"] for s in tabs["settlements"] if s["utr"]
        }
        self.adj_by_payment: dict[str, list[str]] = defaultdict(list)
        for a in tabs["adjustments"]:
            self.adj_by_payment[a["payment_id"]].append(a["adjustment_id"])

    def settled_date(self, sid: str):
        return self._settled_date.get(sid)

    def paid_date(self, pid: str):
        return self._paid_date.get(pid)


class _UF:
    def __init__(self):
        self.parent: dict[str, str] = {}
        self.bank_count: dict[str, int] = {}

    def note_bank(self, x: str) -> None:
        self.bank_count[self.find(x)] = self.bank_count.get(self.find(x), 0) + 1

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent[rb] = ra
        if self.bank_count.get(rb):
            self.bank_count[ra] = self.bank_count.get(ra, 0) + self.bank_count.pop(rb, 0)
        return True


def _ts(s: str | None):
    if not s:
        return datetime(1970, 1, 1, tzinfo=UTC)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def build_bundles(tabs: dict[str, list[dict]]) -> tuple[list[Bundle], list[Link]]:
    ix = Indexes(tabs)
    uf = _UF()
    links: list[Link] = []

    for oid in ix.orders:
        uf.find(f"order:{oid}")
    for pid in ix.payments:
        if not pid:
            continue
        uf.find(f"pay:{pid}")
    for sid in ix.settlements:
        if not sid:
            continue
        uf.find(f"settle:{sid}")
    for bid in ix.banks:
        if not bid:
            continue
        uf.find(f"bank:{bid}")
    for aid in ix.adjustments:
        if not aid:
            continue
        uf.find(f"adj:{aid}")

    # ---- P1: explicit references --------------------------------------
    for p in ix.payments.values():
        if p["order_id"]:
            uf.union(f"pay:{p['payment_id']}", f"order:{p['order_id']}")
            links.append(
                Link(
                    src=f"pay:{p['payment_id']}",
                    dst=f"order:{p['order_id']}",
                    rule_pass="P1_EXPLICIT_REF",
                    evidence_ids=[p["payment_id"], p["order_id"]],
                )
            )
    for s in ix.settlements.values():
        if s["payment_id"]:
            uf.union(f"pay:{s['payment_id']}", f"settle:{s['settlement_id']}")
            links.append(
                Link(
                    src=f"settle:{s['settlement_id']}",
                    dst=f"pay:{s['payment_id']}",
                    rule_pass="P1_EXPLICIT_REF",
                    evidence_ids=[s["settlement_id"], s["payment_id"]],
                )
            )
        elif s["processor_ref"]:
            peer = ix.payments_by_proc.get(s["processor_ref"])
            if peer:
                uf.union(f"pay:{peer}", f"settle:{s['settlement_id']}")
                links.append(
                    Link(
                        src=f"settle:{s['settlement_id']}",
                        dst=f"pay:{peer}",
                        rule_pass="P1_EXPLICIT_REF",
                        evidence_ids=[s["settlement_id"], peer],
                    )
                )
    for a in ix.adjustments.values():
        uf.union(f"pay:{a['payment_id']}", f"adj:{a['adjustment_id']}")
        links.append(
            Link(
                src=f"adj:{a['adjustment_id']}",
                dst=f"pay:{a['payment_id']}",
                rule_pass="P1_EXPLICIT_REF",
                evidence_ids=[a["adjustment_id"]],
            )
        )

    # ---- P2: narration references (credits AND refund debits) ---------
    credit_ids = [b["bank_txn_id"] for b in ix.banks.values() if b["amount_paise"] > 0]
    linked_credits: set[str] = set()
    for b in ix.banks.values():
        for ref in extract_order_refs(b["narration"]):
            if ref in ix.orders:
                uf.union(f"bank:{b['bank_txn_id']}", f"order:{ref}")
                if b["amount_paise"] > 0:
                    linked_credits.add(b["bank_txn_id"])
                links.append(
                    Link(
                        src=f"bank:{b['bank_txn_id']}",
                        dst=f"order:{ref}",
                        rule_pass="P2_NORM_REF",
                        evidence_ids=[b["bank_txn_id"], b["narration"]],
                    )
                )

    def component_has_credit(node: str) -> bool:
        return uf.bank_count.get(uf.find(node), 0) > 0

    sched = merged_schedule(ix.extra_schedule)
    scan_days = ix.policy.get("window_scan_days", 7)

    def payment_net(p: dict) -> int | None:
        amt = p.get("amount_paise")
        rule = sched.get(p.get("method"))
        if p["status"] != "captured" or not amt or amt < 0 or rule is None:
            return None
        return compute_net_with(amt, rule)[2]

    # ---- P3a: UTR fast path (unambiguous) -----------------------------
    unattached = [c for c in credit_ids if c not in linked_credits]
    remaining: list[str] = []
    for cid in unattached:
        narr = ix.banks[cid]["narration"]
        hits = [u for u in ix.settle_by_utr if u and u in narr]
        if len(hits) == 1:
            sid = ix.settle_by_utr[hits[0]]
            uf.union(f"settle:{sid}", f"bank:{cid}")
            uf.note_bank(f"bank:{cid}")
            links.append(
                Link(
                    src=f"bank:{cid}",
                    dst=f"settle:{sid}",
                    rule_pass="P3_WINDOW_MATCH",
                    evidence_ids=[cid, sid],
                    score_breakdown={"utr_hit": True},
                )
            )
        else:
            remaining.append(cid)

    # ---- derive bundles/nets directly from union-find -----------------
    def materialize():
        bmap: dict[str, Bundle] = {}
        kindf = {
            "order": "orders",
            "pay": "payments",
            "settle": "settlements",
            "bank": "bank_txns",
            "adj": "adjustments",
        }
        for node in uf.parent:
            root = uf.find(node)
            bmap.setdefault(root, Bundle(bid=root))
        for node in uf.parent:
            kind, ident = node.split(":", 1)
            root = uf.find(node)
            setattr(bmap[root], kindf[kind], getattr(bmap[root], kindf[kind]) | {ident})
        blist = list(bmap.values())
        nmap = {x.bid: sum(ix.settlements[s]["net_paise"] for s in x.settlements) for x in blist}
        return blist, nmap

    # ---- P4: subset-sum combine merge (UTR-evidenced only) ------------
    def p4_pass() -> None:
        rounds = 0
        while rounds < 60:
            rounds += 1
            progressed = False
            bundles_l, nets_l = materialize()
            consumed: set[str] = set()
            orphan_map = {
                other.bid: (
                    other,
                    nets_l[other.bid],
                    max((ix.settled_date(sid) for sid in other.settlements), default=None),
                )
                for other in bundles_l
                if other.settlements and not other.bank_txns
            }
            hosts_named, hosts_other = [], []
            for bb in bundles_l:
                if bb.bank_txns and bb.bid not in consumed:
                    narr_txt_ = " ".join(ix.banks[c]["narration"] for c in bb.bank_txns)
                    if extract_order_refs(narr_txt_) & {
                        oid for o2 in bundles_l for oid in o2.orders
                    }:
                        hosts_named.append(bb)
                    else:
                        hosts_other.append(bb)
            for b in hosts_named + hosts_other:
                if b.bid in consumed:
                    continue
                for cid in sorted(b.bank_txns):
                    credit = ix.banks[cid]
                    uncovered = credit["amount_paise"] - nets_l[b.bid]
                    if uncovered <= 0:
                        continue
                    posted_date = _ts(credit["posted_at"]).date()
                    eligibles = [
                        (other, n_, mx)
                        for bid, (other, n_, mx) in orphan_map.items()
                        if bid != b.bid
                        and bid not in consumed
                        and mx is not None
                        and mx <= posted_date
                        and 0 < n_ <= uncovered
                    ]
                    strong = [
                        e
                        for e in eligibles
                        if any(
                            (ix.settlements[sid]["utr"] or "")
                            and ix.settlements[sid]["utr"] in credit["narration"]
                            for sid in e[0].settlements
                        )
                    ]
                    narr_txt = credit["narration"]
                    provenance_ok = "RAZORPAY" in narr_txt or bool(extract_order_refs(narr_txt))
                    refs_in_narr = extract_order_refs(narr_txt) & b.orders | (
                        set() if b.orders else extract_order_refs(narr_txt)
                    )
                    mandatory: list[tuple[Bundle, int]] = []
                    optional: list[tuple[Bundle, int]] = []
                    for other, n_, _mx in eligibles:
                        if other.orders & refs_in_narr:
                            mandatory.append((other, n_))
                        else:
                            optional.append((other, n_))
                    if not strong and not mandatory and not b.orders and not provenance_ok:
                        continue
                    optional.sort(key=lambda t: -t[1])
                    seed_sum = sum(n_ for _, n_ in mandatory)
                    if seed_sum > uncovered:
                        continue
                    chosen: list[Bundle] = list(mandatory)
                    reach: dict[int, list[Bundle]] = {seed_sum: list(mandatory)}
                    for cand, n_ in optional[:120]:
                        if len(reach) > 8192:
                            break
                        for total_, path in list(reach.items()):
                            nt = total_ + n_
                            if nt > uncovered or nt in reach:
                                continue
                            reach[nt] = path + [cand]
                            if nt == uncovered:
                                chosen = reach[nt]
                                break
                        if chosen:
                            break
                    if chosen and uncovered in reach:
                        for other in reach[uncovered]:
                            b.orders |= other.orders
                            b.payments |= other.payments
                            b.settlements |= other.settlements
                            b.adjustments |= other.adjustments
                            consumed.add(other.bid)
                            progressed = True

            if not progressed:
                break

    def p4_simple() -> None:
        """Sequential subset-DP merger for per_payment aggregation sources."""
        import os as _os

        _dbg = _os.environ.get("LINK_DEBUG")
        bundles_l, nets_l = materialize()
        changed = True
        guard = 0
        while changed and guard < 10:
            changed = False
            guard += 1
            for b in list(bundles_l):
                if not b.bank_txns:
                    continue
                covered = sum(ix.settlements[st]["net_paise"] for st in b.settlements)
                for cid in sorted(b.bank_txns):
                    credit = ix.banks[cid]
                    uncovered = credit["amount_paise"] - covered
                    if _dbg and "BANK-3000006" in b.bank_txns:
                        print(f"[P4] {cid} covered={covered} uncovered={uncovered}", flush=True)
                    if uncovered <= 0:
                        continue
                    posted_date = _ts(credit["posted_at"]).date()
                    orphans = []
                    for o in list(bundles_l):
                        if o.bid == b.bid or not o.settlements or o.bank_txns:
                            continue
                        onet = sum(ix.settlements[x]["net_paise"] for x in o.settlements)
                        mx = max(_ts(ix.settlements[x]["settled_at"]).date() for x in o.settlements)
                        if 0 < onet <= uncovered and mx <= posted_date:
                            orphans.append((o, onet))
                    reach: dict[int, list] = {0: []}
                    chosen: list = []
                    for o, onet in orphans:
                        for tot, path in list(reach.items()):
                            nt = tot + onet
                            if nt > uncovered or nt in reach:
                                continue
                            reach[nt] = path + [o]
                            if nt == uncovered:
                                chosen = reach[nt]
                                break
                        if chosen:
                            break
                    if _dbg and "BANK-3000006" in b.bank_txns:
                        print(f"[P4] orphans={len(orphans)} merged={bool(chosen)}", flush=True)
                    if chosen:
                        for other in chosen:
                            b.orders |= other.orders
                            b.payments |= other.payments
                            b.settlements |= other.settlements
                            b.adjustments |= other.adjustments
                            other_node = next(
                                (f"pay:{x}" for x in other.payments),
                                next((f"settle:{x}" for x in other.settlements), None),
                            )
                            host_node = next((f"bank:{x}" for x in b.bank_txns), None) or next(
                                (f"pay:{x}" for x in b.payments),
                                next((f"settle:{x}" for x in b.settlements), None),
                            )
                            if other_node and host_node:
                                uf.union(other_node, host_node)
                            b.links.append(
                                Link(
                                    src=f"bundle:{other.bid}",
                                    dst=f"bank:{cid}",
                                    rule_pass="P4_COMBINE_MERGE",
                                    evidence_ids=sorted(other.settlements)[:3] + [cid],
                                    score_breakdown={"uncovered_net": uncovered},
                                )
                            )
                            bundles_l.remove(other)
                        covered += uncovered
                        changed = True

    # ---- P3b: scored amount/window fallback for leftover credits ------
    still = [
        c
        for c in credit_ids
        if component_has_credit(f"bank:{c}") is False and c not in linked_credits
    ]
    _dated = sorted(
        (
            (ix.settled_date(s["settlement_id"]), s)
            for s in ix.settlements.values()
            if ix.settled_date(s["settlement_id"]) is not None
        ),
        key=lambda t: t[0],
    )
    _sorted_dates = [d for d, _ in _dated]
    net_index: dict[int, list[dict]] = defaultdict(list)
    for s in ix.settlements.values():
        net_index[s["net_paise"]].append(s)

    from bisect import bisect_left, bisect_right

    for cid in still:
        b = ix.banks[cid]
        posted = _ts(b["posted_at"]).date()
        exact_pool = net_index.get(b["amount_paise"], [])
        if exact_pool:
            pool = [(ix.settled_date(x["settlement_id"]), x) for x in exact_pool]
        else:
            lo = posted - timedelta(days=scan_days)
            hi = posted
            i0 = bisect_left(_sorted_dates, lo)
            i1 = bisect_right(_sorted_dates, hi)
            pool = _dated[i0:i1]
        scored: list[tuple[float, dict, dict]] = []
        for sd, st in pool:
            lag = (posted - sd).days
            if lag < 0 or lag > scan_days:
                continue
            amt_diff = abs(b["amount_paise"] - st["net_paise"])
            utr_hit = bool(st["utr"] and st["utr"] in b["narration"])
            claimed = component_has_credit(f"settle:{st['settlement_id']}")
            if claimed and not utr_hit:
                continue
            if not (utr_hit or amt_diff == 0):
                continue
            score = (10.0 if utr_hit else 0.0) + (8.0 if amt_diff == 0 else 0.0)
            score += max(0.0, 2.0 - lag * 0.25)
            scored.append((score, st, {"utr_hit": utr_hit, "lag_days": lag}))
        scored.sort(key=lambda t: (-t[0], t[1]["settlement_id"]))
        unique_top = len(scored) == 1 or (len(scored) > 1 and scored[0][0] - scored[1][0] > 2.0)
        if scored and unique_top and scored[0][0] >= P3_MIN_SCORE:
            sc, st, br = scored[0]
            uf.union(f"settle:{st['settlement_id']}", f"bank:{cid}")
            uf.note_bank(f"bank:{cid}")
            links.append(
                Link(
                    src=f"bank:{cid}",
                    dst=f"settle:{st['settlement_id']}",
                    rule_pass="P3_WINDOW_MATCH",
                    evidence_ids=[cid, st["settlement_id"]],
                    score_breakdown={"score": sc, **br},
                )
            )
        elif scored and not unique_top and scored[0][0] >= P3_MIN_SCORE:
            # T1 touchpoint (RESEARCH.md §5): ambiguous-linkage adjudication.
            # Null assist keeps the tie unlinked → default engine behavior is
            # byte-identical to the pre-T1 engine (verified by result hashes).
            candidates = [
                {
                    "id": st["settlement_id"],
                    "score": round(sc, 2),
                    "utr_hit": br.get("utr_hit"),
                    "lag_days": br.get("lag_days"),
                    "net_paise": st["net_paise"],
                }
                for sc, st, br in scored[:5]
            ]
            decision = get_assist().adjudicate_linkage(candidates)
            choice = decision.get("choice")
            if not decision.get("ambiguous") and choice in {c["id"] for c in candidates}:
                sc, st, br = next(
                    (sc, s, br) for sc, s, br in scored if s["settlement_id"] == choice
                )
                uf.union(f"settle:{st['settlement_id']}", f"bank:{cid}")
                uf.note_bank(f"bank:{cid}")
                links.append(
                    Link(
                        src=f"bank:{cid}",
                        dst=f"settle:{st['settlement_id']}",
                        rule_pass="P3_WINDOW_MATCH",
                        evidence_ids=[cid, st["settlement_id"]],
                        score_breakdown={
                            "score": sc,
                            **br,
                            "t1_adjudicated": True,
                            "low_confidence": bool(decision.get("low_confidence")),
                            "rationale": str(decision.get("rationale", ""))[:120],
                        },
                    )
                )

    pay_by_net: dict[int, list[dict]] = defaultdict(list)
    for p in ix.payments.values():
        n = payment_net(p)
        if n is not None and not component_has_credit(f"pay:{p['payment_id']}"):
            pay_by_net[n].append(p)
    fee_orphans = [
        c for c in credit_ids if not component_has_credit(f"bank:{c}") and c not in linked_credits
    ]
    for cid in fee_orphans:
        b = ix.banks[cid]
        posted_date = _ts(b["posted_at"]).date()
        cands = [
            p
            for p in pay_by_net.get(b["amount_paise"], [])
            if not component_has_credit(f"pay:{p['payment_id']}")
        ]
        best_p, best_lag, matches = None, 99, 0
        for p in cands:
            pd_ = ix.paid_date(p["payment_id"])
            if pd_ is None:
                continue
            lag = (posted_date - pd_).days
            if 0 <= lag <= scan_days:
                matches += 1
                if lag < best_lag:
                    best_p, best_lag = p, lag
        if best_p is not None and matches == 1:
            uf.union(f"pay:{best_p['payment_id']}", f"bank:{cid}")
            uf.note_bank(f"bank:{cid}")
            links.append(
                Link(
                    src=f"bank:{cid}",
                    dst=f"pay:{best_p['payment_id']}",
                    rule_pass="P3_WINDOW_MATCH",
                    evidence_ids=[cid, best_p["payment_id"]],
                    score_breakdown={"mode": "payment_net", "lag_days": best_lag},
                )
            )

    # ---- combine merge: strategy chosen by source policy --------------
    if ix.policy.get("aggregation") == "batch_aggregated":
        p4_pass()  # subset-DP for batch-aggregated foreign sources
        p4_pass()
    else:
        p4_simple()  # proven sequential merger for per-payment sources

    # ---- P5: lone orders join by amount + date context ----------------
    bundles_l, _ = materialize()
    for b in bundles_l:
        if len(b.orders) != 1 or b.payments or b.settlements or b.bank_txns or b.adjustments:
            continue
        oid = next(iter(b.orders))
        order = ix.orders[oid]
        if order["status"] != "confirmed":
            continue
        created = _ts(order["created_at"]).date()
        for other in bundles_l:
            if other.bid == b.bid or other.orders or not other.payments:
                continue
            match_pid = None
            for pid in other.payments:
                p = ix.payments[pid]
                if p["status"] != "captured" or p["amount_paise"] != order["amount_paise"]:
                    continue
                if abs((_ts(p["paid_at"]).date() - created).days) > 2:
                    continue
                match_pid = pid
                break
            if match_pid is None:
                continue
            b.orders |= other.orders
            b.payments |= other.payments
            b.settlements |= other.settlements
            b.bank_txns |= other.bank_txns
            b.adjustments |= other.adjustments
            b.links.append(
                Link(
                    src=f"order:{oid}",
                    dst=f"bundle:{other.bid}",
                    rule_pass="P5_CONTEXT_MATCH",
                    evidence_ids=[oid, match_pid],
                    score_breakdown={"amount": order["amount_paise"]},
                )
            )
            break

    bundles, _ = materialize()
    bundles.sort(key=lambda x: x.bid)
    return bundles, links


def net_by_bundle_val(ix: Indexes, b: Bundle) -> int:
    return sum(ix.settlements[s]["net_paise"] for s in b.settlements)
