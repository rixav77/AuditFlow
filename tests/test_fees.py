from generator.config import FEE_SCHEDULE
from generator.fees import compute_fee, compute_net, round_half_up


def test_round_half_up():
    assert round_half_up(1, 2) == 1
    assert round_half_up(3, 2) == 2
    assert round_half_up(5, 2) == 3
    assert round_half_up(10_001, 10_000) == 1
    assert round_half_up(14_999, 10_000) == 1
    assert round_half_up(15_000, 10_000) == 2


def test_credit_card_known_vector():
    fee, tax = compute_fee(300_000, FEE_SCHEDULE["credit_card"])
    assert (fee, tax) == (6_200, 1_116)
    _, _, net = compute_net(300_000, "credit_card")
    assert net == 300_000 - 6_200 - 1_116


def test_netbanking_vector():
    gross = 1_713_500
    fee, tax = compute_fee(gross, FEE_SCHEDULE["netbanking"])
    assert fee == 31_343
    assert tax == 5_642
    assert gross - fee - tax == 1_676_515


def test_upi_zero_mdr():
    assert compute_net(999_999, "upi") == (0, 0, 999_999)
