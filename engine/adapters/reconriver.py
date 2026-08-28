"""ReconRiver adapter: maps their internal/processor/bank CSVs into canonical form.

Semantics documented divergences (kept honest in benchmark report):
- currency-mismatch exceptions are invisible to us (we convert all money to paise)
- their "missing record" outcomes map to our `unresolved` (engine must abstain)
- duplicate/malformed/amount-breaks map to `genuine_discrepancy` / `data_quality`
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from engine.adapters.base import SourceAdapter, empty_report, empty_tabs, scan_report

METHOD_MAP = {
    "WALLET_SYNTHETIC": "wallet",
    "CARD_SYNTHETIC": "credit_card",
    "TRANSFER_SYNTHETIC": "netbanking",
}

GT_CLASS_MAP = {
    "MATCHED": "matched",
    "LATE_SETTLEMENT": "matched_after_reasoning",
    "PARTIAL_REFUND": "matched_after_reasoning",
    "REFUND_MATCHED": "matched_after_reasoning",
    "AMBIGUOUS_MATCH": "unresolved",
    "MISSING_INTERNAL": "unresolved",
    "MISSING_PROCESSOR": "unresolved",
    "MISSING_BANK_SETTLEMENT": "unresolved",
    "DUPLICATE_INTERNAL": "genuine_discrepancy",
    "DUPLICATE_PROCESSOR": "genuine_discrepancy",
    "DUPLICATE_BANK_ENTRY": "genuine_discrepancy",
    "AMOUNT_MISMATCH": "genuine_discrepancy",
    "CURRENCY_MISMATCH": "genuine_discrepancy",
    "FEE_MISMATCH": "genuine_discrepancy",
    "INVALID_SOURCE_ROW": "data_quality",
}


def _paise(x) -> int | None:
    try:
        return int(round(float(str(x).replace(",", "")) * 100))
    except (TypeError, ValueError):
        return None


def _iso(x: str) -> str:
    s = str(x).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt.isoformat(timespec="seconds").replace("+00:00", "Z")
    except ValueError:
        return s


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class ReconRiverAdapter(SourceAdapter):
    name = "reconriver"

    def load(self, source: str | Path):
        src = Path(source)
        tabs, report = empty_tabs(), empty_report()
        tabs["_fee_schedule"] = {
            "wallet": (290, 30, 0),  # their documented policy:
            "credit_card": (290, 30, 0),  # 2.90% + 0.30 flat, HALF_UP, NO GST
            "netbanking": (290, 30, 0),
        }
        internal = _read(src / "internal_transactions.csv")
        processor = _read(src / "processor_transactions.csv")
        bank = _read(src / "bank_settlements.csv")

        seen_orders: set[str] = set()
        pay_by_order: dict[str, str] = {}
        id_seen: dict[str, set[str]] = {t: set() for t in ("payments", "settlements", "bank_txns")}

        def dedupe(table: str, rid: str) -> str:
            if not rid:
                return rid
            if rid in id_seen[table]:
                n = 2
                while f"{rid}#{n}" in id_seen[table]:
                    n += 1
                rid = f"{rid}#{n}"
            id_seen[table].add(rid)
            return rid

        for r in internal:
            oid = (r.get("merchant_order_id") or "").strip()
            raw_pid = (r.get("internal_payment_id") or "").strip()
            amt = _paise(r.get("gross_amount"))
            if not oid or not raw_pid or amt is None:
                report["broken_refs"].append(f"internal:{raw_pid or oid}")
                continue
            pst = (r.get("payment_status") or "").upper()
            status = (
                "captured" if pst in {"CAPTURED", "REFUNDED", "PARTIALLY_REFUNDED"} else "failed"
            )
            if oid and oid not in seen_orders:
                seen_orders.add(oid)
                tabs["orders"].append(
                    {
                        "order_id": oid,
                        "amount_paise": amt,
                        "customer_name": r.get("synthetic_customer_reference") or "",
                        "item_desc": "reconriver import",
                        "status": "confirmed" if status == "captured" else "cancelled",
                        "created_at": _iso(r.get("occurred_at", "")),
                    }
                )
            if raw_pid:
                pid = dedupe("payments", raw_pid)
                tabs["payments"].append(
                    {
                        "payment_id": pid,
                        "order_id": oid,
                        "processor_ref": None,
                        "amount_paise": amt,
                        "method": METHOD_MAP.get(r.get("payment_method", ""), "wallet"),
                        "status": status,
                        "paid_at": _iso(r.get("occurred_at", "")),
                    }
                )
                if status == "captured":
                    pay_by_order.setdefault(oid, pid)

        for r in processor:
            etype = (r.get("processor_event_type") or "").upper()
            oid = (r.get("merchant_order_id") or "").strip()
            gross = _paise(r.get("gross_amount"))
            fee = _paise(r.get("fee_amount")) or 0
            net = _paise(r.get("net_amount"))
            sid_raw = (r.get("processor_transaction_id") or "").strip()
            sid = dedupe("settlements", sid_raw)
            if not sid or not oid:
                report["broken_refs"].append(f"proc:{sid_raw}")
                continue
            if etype == "CAPTURE":
                tabs["settlements"].append(
                    {
                        "settlement_id": sid,
                        "payment_id": pay_by_order.get(oid),
                        "processor_ref": None,
                        "gross_paise": gross if gross is not None else 0,
                        "fee_paise": fee,
                        "tax_paise": 0,
                        "net_paise": net if net is not None else 0,
                        "utr": (r.get("settlement_batch_id") or "").strip(),
                        "settled_at": _iso(r.get("processor_event_time", "")),
                    }
                )
            elif "REFUND" in etype:
                pay_ref = pay_by_order.get(oid)
                if not pay_ref:
                    report["broken_refs"].append(f"refund:{sid}")
                    continue
                tabs["adjustments"].append(
                    {
                        "adjustment_id": f"ADJ-{sid}",
                        "adj_type": "refund_full" if net == gross else "refund_partial",
                        "payment_id": pay_ref,
                        "amount_paise": abs(net or 0),
                        "created_at": _iso(r.get("processor_event_time", "")),
                        "reason": f"{etype} imported from reconriver",
                    }
                )

        for r in bank:
            bid_raw = (r.get("bank_entry_id") or "").strip()
            if not bid_raw:
                report["broken_refs"].append("bank:<empty>")
                continue
            desc = (r.get("description") or "").strip()
            batch = (r.get("settlement_batch_id") or "").strip()
            ref = (r.get("bank_reference") or "").strip()
            amt = _paise(r.get("credited_amount"))
            tabs["bank_txns"].append(
                {
                    "bank_txn_id": dedupe("bank_txns", bid_raw),
                    "narration": f"{desc} | {ref} | {batch}",
                    "amount_paise": amt if amt is not None else 0,
                    "posted_at": _iso(r.get("booked_at", "")),
                    "value_date": str(r.get("booked_at", ""))[:10],
                }
            )

        scan_report(tabs, report)
        return tabs, report

    @staticmethod
    def convert_ground_truth(source: str | Path) -> list[dict]:
        """Map expected_reconciliation.csv -> our gt transaction entries."""
        src = Path(source)
        out: list[dict] = []
        for r in _read(src / "expected_reconciliation.csv"):
            scope = (r.get("result_scope") or "").upper()
            outcome = (r.get("expected_outcome") or "").upper()
            reason = (r.get("expected_reason_code") or "").upper()
            if scope == "ORDER":
                key = (r.get("work_key") or "").strip()
            elif reason == "L2_BANK_WITHOUT_PROCESSOR_BATCH":
                key = (r.get("bank_entry_id") or "").strip()
            else:
                continue
            if not key:
                continue
            cls = GT_CLASS_MAP.get(outcome, "genuine_discrepancy")
            out.append(
                {
                    "work_key": key,
                    "scope": "order" if scope == "ORDER" else "bank",
                    "cause_code": reason,
                    "expected_class": cls,
                    "abstention_expected": cls == "unresolved",
                    "expected_links": {
                        "orders": [key] if key.startswith("ORD") else [],
                        "payments": [],
                        "settlements": [],
                        "bank_txns": [key] if key.startswith("BANK") else [],
                        "adjustments": [],
                    },
                    "decoys": [],
                    "expected_delta_paise": None,
                    "explanation_human": r.get("explanation", ""),
                }
            )
        return sorted(out, key=lambda t: t["work_key"])
