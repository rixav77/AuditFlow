import random

from generator.config import DIFFICULTY_PRESETS, RTGS_MIN_PAISE
from generator.narrate import extract_order_refs, pick_channel


def test_pick_channel_respects_rtgs_minimum():
    rng = random.Random(3)
    for _ in range(100):
        assert pick_channel(rng, 1_000_000) != "RTGS", "sub-2L must never be RTGS"
    for _ in range(50):
        assert pick_channel(rng, RTGS_MIN_PAISE + 1) in {"RTGS", "NEFT"}


def test_difficulty_presets_differ():
    easy, normal, hard = (
        DIFFICULTY_PRESETS["EASY"],
        DIFFICULTY_PRESETS["NORMAL"],
        DIFFICULTY_PRESETS["HARD"],
    )
    assert easy.unexplained_max_paise < normal.unexplained_max_paise < hard.unexplained_max_paise
    assert easy.ref_absent_p < hard.ref_absent_p
    assert hard.noise_mult > normal.noise_mult > easy.noise_mult


def test_extract_refs_canonicalizes_leading_zeros():
    assert extract_order_refs("PAYOUT ORD010212--") == {"ORD-10212"}
    assert extract_order_refs("PAYOUT ORD-90347 /IBL") == {"ORD-90347"}
