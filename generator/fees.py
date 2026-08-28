"""Fee math in integer paise. All rounding HALF_UP. Pure functions."""

from __future__ import annotations

from generator.config import FEE_SCHEDULE, GST_BPS, FeeRule


def round_half_up(num: int, den: int) -> int:
    """Positive-int division rounded half up: (2n + d) // (2d)."""
    assert num >= 0 and den > 0
    return (2 * num + den) // (2 * den)


def compute_fee(gross_paise: int, rule: FeeRule) -> tuple[int, int]:
    fee = round_half_up(gross_paise * rule.rate_bps, 10_000) + rule.fixed_paise
    tax = round_half_up(fee * GST_BPS, 10_000)
    return fee, tax


def compute_net(gross_paise: int, method: str) -> tuple[int, int, int]:
    """Returns (fee_paise, tax_paise, net_paise)."""
    fee, tax = compute_fee(gross_paise, FEE_SCHEDULE[method])
    return fee, tax, gross_paise - fee - tax
