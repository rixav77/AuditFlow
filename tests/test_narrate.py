import random

from generator import narrate


def test_mutate_ref_variants():
    rng = random.Random(1)
    seen = set()
    for _ in range(200):
        seen.add(narrate.mutate_ref(rng, "ORD-1001"))
    assert "ORD-1001" in seen
    assert "ORD1001" in seen
    assert "ord#1001" in seen
    assert "Ord 1001" in seen
    assert "" in seen


def test_extract_refs_roundtrip_all_mutations():
    for token in ["ORD-1042", "ORD1042", "ord#1042", "Ord 1042"]:
        text = f"NEFT Cr-HDFC260601123456-HDFC0000123-RAZORPAY SOFTWARE-PAYOUT {token}--"
        assert narrate.extract_order_refs(text) == {"ORD-1042"}, token


def test_extract_refs_empty_when_absent():
    text = "NEFT Cr-HDFC260601123456-HDFC0000123-RAZORPAY SOFTWARE-PAYOUT --"
    assert narrate.extract_order_refs(text) == set()


def test_narration_templates_embed_utr_or_digits():
    rng = random.Random(7)
    from datetime import date

    for _ in range(50):
        n = narrate.credit_narration(rng, date(2026, 6, 4), "ORD-1002", "HDFC260604000123")
        assert any(k in n for k in ("NEFT", "ACH", "IMPS", "RTGS", "UPI", "By Clg"))
