"""Fee-policy resolution allowing adapters to inject source-specific schedules."""

from __future__ import annotations

from generator.config import FEE_SCHEDULE
from generator.fees import round_half_up


def merged_schedule(extra: dict | None) -> dict:
    sched = {
        m: (r.rate_bps, r.fixed_paise) if hasattr(r, "rate_bps") else r
        for m, r in FEE_SCHEDULE.items()
    }
    if extra:
        for method, rule in extra.items():
            sched[method] = rule
    return sched


def compute_net_with(gross_paise: int, rule, gst_bps_default: int = 1800):
    if len(rule) == 3:
        rate_bps, fixed_paise, gst_bps = rule
    else:
        rate_bps, fixed_paise = rule
        gst_bps = gst_bps_default
    fee = round_half_up(gross_paise * rate_bps, 10_000) + fixed_paise
    tax = round_half_up(fee * gst_bps, 10_000)
    return fee, tax, gross_paise - fee - tax
