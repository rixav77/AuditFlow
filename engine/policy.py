"""Per-source reconciliation policy: rules travel WITH the data, not baked in.

Adapters attach `tabs["_policy"]`; engine resolves defaults for anything absent,
so our own batches behave exactly as before while foreign sources carry their
own rulebook (fee schedule incl. GST%, settlement windows, materiality).
"""

from __future__ import annotations

DEFAULT_POLICY: dict = {
    # fee schedule entries: method -> [rate_bps, fixed_paise, gst_bps]
    # (empty = fall back to canonical generator/config.FEE_SCHEDULE @ 18% GST)
    "fee_schedule": {},
    # settlement timing windows (calendar days after payment capture)
    "settle_min_days": 1,
    "settle_max_days": 3,
    "late_max_days": 7,
    # candidate scan horizon for linkage (days around settled date)
    "window_scan_days": 7,
    # shortfall ratio above which we name a genuine discrepancy (vs unresolved)
    "short_pct_threshold": 0.20,
    # how bank credits aggregate settlements: per_payment | batch_aggregated
    "aggregation": "per_payment",
}


def resolve_policy(tabs: dict) -> dict:
    policy = dict(DEFAULT_POLICY)
    override = tabs.get("_policy")
    if isinstance(override, str):
        import json

        try:
            override = json.loads(override)
        except json.JSONDecodeError:
            override = {}
    if isinstance(override, dict):
        policy.update(override)

    # legacy channel: _fee_schedule wins over policy.fee_schedule if present
    legacy = tabs.get("_fee_schedule")
    if isinstance(legacy, str):
        import json

        try:
            legacy = json.loads(legacy)
        except json.JSONDecodeError:
            legacy = None
    if legacy:
        policy["fee_schedule"] = legacy
    elif not policy["fee_schedule"]:
        from generator.config import FEE_SCHEDULE

        policy["fee_schedule"] = {
            m: [r.rate_bps, r.fixed_paise, 1800] for m, r in FEE_SCHEDULE.items()
        }
    return policy


def fee_rule(policy: dict, method: str):
    return policy["fee_schedule"].get(method)
