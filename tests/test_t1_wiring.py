"""T1 ambiguous-linkage adjudication wiring (engine/linkage.py, P3b)."""

from __future__ import annotations

import json

from engine.linkage import build_bundles


def _tabs() -> dict:
    """Minimal batch engineered so P3b sees an exact score tie between two
    settlement candidates (same net, same window, no UTR, no refs)."""
    return {
        "orders": [
            {
                "order_id": "ORD-1",
                "amount_paise": 10000,
                "status": "confirmed",
                "created_at": "2026-06-01T09:00:00Z",
            }
        ],
        "payments": [
            {
                "payment_id": "PAY-1",
                "order_id": "ORD-1",
                "processor_ref": "PG-1",
                "amount_paise": 10000,
                "method": "upi",
                "status": "captured",
                "paid_at": "2026-06-01T10:00:00Z",
            }
        ],
        "settlements": [
            {
                "settlement_id": "SET-1",
                "payment_id": None,
                "processor_ref": None,
                "gross_paise": 10000,
                "fee_paise": 200,
                "tax_paise": 0,
                "net_paise": 9800,
                "utr": None,
                "settled_at": "2026-06-03T09:00:00Z",
            },
            {
                "settlement_id": "SET-2",
                "payment_id": None,
                "processor_ref": None,
                "gross_paise": 10000,
                "fee_paise": 200,
                "tax_paise": 0,
                "net_paise": 9800,
                "utr": None,
                "settled_at": "2026-06-03T11:00:00Z",
            },
        ],
        "bank_txns": [
            {
                "bank_txn_id": "BANK-1",
                "narration": "NEFT CREDIT GENERIC NO REF",
                "amount_paise": 9800,
                "posted_at": "2026-06-03T12:00:00Z",
                "value_date": "2026-06-03T12:00:00Z",
            }
        ],
        "adjustments": [],
        # batch_aggregated keeps P4's strict gates closed so the tie survives
        # into P3b (per_payment mode lets the sequential merger consume it).
        "_policy": json.dumps({"aggregation": "batch_aggregated"}),
    }


def test_t1_live_assist_resolves_the_tie(monkeypatch):
    class Live:
        name = "live_assist"

        def adjudicate_linkage(self, candidates):
            assert {c["id"] for c in candidates} == {"SET-1", "SET-2"}
            return {
                "choice": "SET-2",
                "ambiguous": False,
                "rationale": "tie broken by test",
                "cited_ids": ["SET-2"],
            }

    monkeypatch.setattr("engine.linkage.get_assist", lambda: Live())
    bundles, links = build_bundles(_tabs())
    t1_links = [lnk for lnk in links if lnk.score_breakdown.get("t1_adjudicated")]
    assert len(t1_links) == 1
    assert t1_links[0].dst == "settle:SET-2"
    assert t1_links[0].score_breakdown["rationale"] == "tie broken by test"
    host = next(x for x in bundles if "BANK-1" in x.bank_txns)
    assert "SET-2" in host.settlements and "SET-1" not in host.settlements


def test_t1_null_assist_keeps_tie_unlinked(monkeypatch):
    """Null fallback = status quo: ties stay unlinked (low_confidence signal)."""

    class Null:
        name = "null_assist"

        def adjudicate_linkage(self, candidates):
            best = max(candidates, key=lambda c: c["score"])
            return {
                "choice": best["id"],
                "ambiguous": True,
                "low_confidence": True,
                "cited_ids": [best["id"]],
            }

    monkeypatch.setattr("engine.linkage.get_assist", lambda: Null())
    bundles, links = build_bundles(_tabs())
    assert not any(lnk.score_breakdown.get("t1_adjudicated") for lnk in links)
    host = next(x for x in bundles if "BANK-1" in x.bank_txns)
    assert not host.settlements  # tie left for investigate/abstain path


def test_default_engine_is_null_and_deterministic():
    bundles, links = build_bundles(_tabs())
    assert not any(lnk.score_breakdown.get("t1_adjudicated") for lnk in links)
