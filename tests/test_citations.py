"""Citation verifier layer tests (llm/citations.py + explain integration)."""

from pathlib import Path

from llm.citations import repair_feedback, verify_narrative
from llm.explain import deterministic_fallback, explain_verified
from llm.provider import ChatResponse, MockProvider

PAYLOAD = {
    "work_key": "ORD-100021",
    "verdict": {
        "cls": "unresolved",
        "reason_code": "INV_EXHAUSTIVE_NO_EVIDENCE",
        "checks_run": ["EXHAUSTIVE_SEARCH"],
    },
    "findings": [
        {"kind": "PAY_VS_ORDER", "expected_paise": 396900, "actual_paise": 396900, "delta_paise": 0}
    ],
    "records": [
        {
            "order_id": "ORD-100021",
            "amount_paise": 396900,
            "customer_name": "Nikhil Chopra",
            "status": "confirmed",
        },
        {
            "payment_id": "PAY-500018",
            "order_id": "ORD-100021",
            "amount_paise": 396900,
            "method": "upi",
            "status": "captured",
        },
    ],
}


def test_layer_a_fabricated_id_flagged():
    r = verify_narrative("Payment captured via PAY-500018 but ORD-999999 never existed.", PAYLOAD)
    assert not r.verified
    assert any("ORD-999999" in e for e in r.id_errors)


def test_layer_a_valid_ids_pass():
    r = verify_narrative("Order ORD-100021 paid via PAY-500018 remains unresolved.", PAYLOAD)
    assert r.verified and not r.id_errors


def test_layer_b_catches_paise_written_as_rupees():
    """The S10 incident: 396900 paise narrated as ₹3,96,900 must be flagged."""
    r = verify_narrative("Shortfall of ₹3,96,900 for ORD-100021.", PAYLOAD)
    assert not r.verified
    assert r.amount_errors and "3,96,900" in r.amount_errors[0]


def test_layer_b_accepts_dual_form():
    r = verify_narrative("Shortfall of ₹3,969.00 (396900 paise) for ORD-100021.", PAYLOAD)
    assert r.verified and not r.amount_errors


def test_layer_b_accepts_indian_grouping():
    r = verify_narrative("Amount ₹3,969 with 396900 paise reference on PAY-500018.", PAYLOAD)
    assert r.verified and not r.amount_errors


def test_layer_c_flags_unsupported_sentence_but_stays_soft():
    text = (
        "Payment PAY-500018 stays unresolved. "
        "The moon market rallied yesterday with pink elephants."
    )
    r = verify_narrative(text, PAYLOAD)
    assert r.verified  # hard layers clean
    assert not r.fully_supported
    assert r.citation_recall is not None and r.citation_recall < 1.0
    assert any("moon" in s for s in r.unsupported_sentences)


def test_citation_precision_with_mixed_ids():
    r = verify_narrative("See PAY-500018 and ORD-999999.", PAYLOAD)
    assert r.citation_precision == 0.5


def test_repair_feedback_lists_all_problems():
    r = verify_narrative("ORD-999999 got ₹5,000.", PAYLOAD)
    fb = repair_feedback(r)
    assert "ORD-999999" in fb
    assert "PAISE" in fb.upper()


def test_deterministic_fallback_always_verifies():
    text = deterministic_fallback(PAYLOAD)
    r = verify_narrative(text, PAYLOAD)
    assert r.verified, r.to_dict()


class _IX:
    orders = {"ORD-100021": PAYLOAD["records"][0]}
    payments = {"PAY-500018": PAYLOAD["records"][1]}
    settlements = {}
    banks = {}
    adjustments = {}


def _verdict_dict(members):
    return {
        "work_key": "ORD-100021",
        "bundle_bid": "b1",
        "cls": "unresolved",
        "reason_code": "INV_EXHAUSTIVE_NO_EVIDENCE",
        "evidence_ids": ["PAY-500018"],
        "checks_run": ["EXHAUSTIVE_SEARCH"],
        "findings": PAYLOAD["findings"],
        "llm_assists": [],
        "members": members,
        "internal_status": None,
    }


def test_explain_verified_repair_loop_with_mock():
    bad = ChatResponse(
        content="Payment PAY-500018 for ORD-100021 involved ₹3,96,900 via ORD-999999."
    )
    good = ChatResponse(
        content=(
            "Payment PAY-500018 for ORD-100021 shows a 396900 paise (₹3,969.00) "
            "shortfall after all checks; the case is unresolved."
        )
    )
    prov = MockProvider([bad, good])
    text, rep = explain_verified(
        _IX(), None, _verdict_dict(["order:ORD-100021", "pay:PAY-500018"]), prov
    )
    assert rep["source"] == "llm"
    assert rep["attempts"] == 2
    assert rep["verified"] is True
    assert "₹3,969" in text
    assert len(prov.calls) == 2  # bad attempt + repair attempt


def test_explain_verified_falls_back_after_two_bad_attempts():
    prov = MockProvider(
        [
            ChatResponse(content="Mystery ORD-888888 took ₹1,11,111."),
            ChatResponse(content="Mystery ORD-777777 took ₹2,22,222."),
        ]
    )
    text, rep = explain_verified(_IX(), None, _verdict_dict([]), prov)
    assert rep["source"] == "deterministic_fallback"
    assert text.startswith("ORD-100021:")


def test_verification_is_traced():
    trace_dir = Path("data/traces")
    before = set(trace_dir.glob("explain_*.jsonl"))
    prov = MockProvider(
        [ChatResponse(content="Order ORD-100021 and PAY-500018: unresolved, 396900 paise.")]
    )
    explain_verified(
        _IX(), None, _verdict_dict(["order:ORD-100021", "pay:PAY-500018"]), prov
    )
    after = set(trace_dir.glob("explain_*.jsonl"))
    assert after >= before and after  # at least one trace file exists

