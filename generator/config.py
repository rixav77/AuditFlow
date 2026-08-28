"""Generator configuration: sizes, cause mix, fee schedule, timing windows, difficulty."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

GENERATOR_VERSION = "0.2.0"

BASE_DATE = (2026, 6, 1)


class FeeRule(BaseModel):
    rate_bps: int
    fixed_paise: int


FEE_SCHEDULE: dict[str, FeeRule] = {
    "upi": FeeRule(rate_bps=0, fixed_paise=0),
    "debit_card": FeeRule(rate_bps=90, fixed_paise=200),
    "credit_card": FeeRule(rate_bps=200, fixed_paise=200),
    "netbanking": FeeRule(rate_bps=180, fixed_paise=500),
    "wallet": FeeRule(rate_bps=160, fixed_paise=200),
}
METHODS_WITH_FEE = [m for m, r in FEE_SCHEDULE.items() if r.rate_bps > 0 or r.fixed_paise > 0]
GST_BPS = 1800

RTGS_MIN_PAISE = 20_000_000  # Rs 2,00,000

FIXED_CAUSE_MIX: dict[str, int] = {
    "FEE_EXPLAINED": 8,
    "LATE_SETTLEMENT": 4,
    "SPLIT_SETTLEMENT": 4,
    "COMBINED_SETTLEMENT": 3,
    "REFUND_PARTIAL": 3,
    "REFUND_FULL": 2,
    "AMBIGUOUS_CANDIDATES": 2,
    "DUPLICATE_BANK_CREDIT": 3,
    "SHORT_SETTLED": 2,
    "MISSING_SETTLEMENT": 4,
    "UNEXPLAINED_DELTA": 4,
    "MALFORMED_SOURCE_ROW": 1,
}
EXTRA_BANK_ONLY = 2

NOISE_COUNTS: dict[str, int] = {
    "FAILED_PAYMENT": 4,
    "CANCELLED_ORDER": 2,
    "SVC_DEBIT": 3,
    "REVERSAL_DEBIT": 1,
}

EXPECTED_CLASS = {
    "CLEAN_MATCH": "matched",
    "FEE_EXPLAINED": "matched_after_reasoning",
    "LATE_SETTLEMENT": "matched_after_reasoning",
    "SPLIT_SETTLEMENT": "matched_after_reasoning",
    "COMBINED_SETTLEMENT": "matched_after_reasoning",
    "REFUND_PARTIAL": "matched_after_reasoning",
    "REFUND_FULL": "matched_after_reasoning",
    "AMBIGUOUS_CANDIDATES": "matched_after_reasoning",
    "DUPLICATE_BANK_CREDIT": "genuine_discrepancy",
    "SHORT_SETTLED": "genuine_discrepancy",
    "MISSING_SETTLEMENT": "unresolved",
    "UNEXPLAINED_DELTA": "unresolved",
    "MALFORMED_SOURCE_ROW": "data_quality",
    "BANK_ONLY_CREDIT": "genuine_discrepancy",
}

SETTLE_MIN_DAYS = 1
SETTLE_MAX_DAYS = 3
LATE_MIN_DAYS = 4
LATE_MAX_DAYS = 7

AMOUNT_MIN_PAISE = 50_000
AMOUNT_MAX_PAISE = 30_000_000

ID_OFFSETS = {
    "order": 100_000,
    "payment": 500_000,
    "settlement": 800_000,
    "bank": 3_000_000,
    "adjustment": 40_000,
}
DEAD_ORDER_RANGE = (90_000, 99_999)  # decoy refs live only here; never minted


class DifficultyParams(BaseModel):
    ref_absent_p: float = 0.08
    typo_ref_p: float = 0.08
    decoy_ref_p: float = 0.12
    junk_suffix_p: float = 0.15
    truncate_p: float = 0.05
    unexplained_min_paise: int = 1_000
    unexplained_max_paise: int = 500_000
    short_pct_lo: int = 15
    short_pct_hi: int = 45
    noise_mult: float = 1.0


DIFFICULTY_PRESETS: dict[str, DifficultyParams] = {
    "EASY": DifficultyParams(
        ref_absent_p=0.02,
        typo_ref_p=0.03,
        decoy_ref_p=0.05,
        junk_suffix_p=0.08,
        truncate_p=0.02,
        unexplained_min_paise=2_000,
        unexplained_max_paise=20_000,
        short_pct_lo=25,
        short_pct_hi=35,
        noise_mult=0.5,
    ),
    "NORMAL": DifficultyParams(short_pct_lo=22),
    "HARD": DifficultyParams(
        ref_absent_p=0.18,
        typo_ref_p=0.15,
        decoy_ref_p=0.22,
        junk_suffix_p=0.25,
        truncate_p=0.10,
        unexplained_min_paise=1_000,
        unexplained_max_paise=999_900,
        short_pct_lo=8,
        short_pct_hi=60,
        noise_mult=2.0,
    ),
}

Difficulty = Literal["EASY", "NORMAL", "HARD"]


class GeneratorConfig(BaseModel):
    seed: int = 42
    size: int = Field(default=60, ge=45, le=500)
    difficulty: Difficulty = "NORMAL"

    @property
    def clean_count(self) -> int:
        return self.size - sum(FIXED_CAUSE_MIX.values())

    def cause_list(self) -> list[str]:
        causes: list[str] = ["CLEAN_MATCH"] * self.clean_count
        for code, n in FIXED_CAUSE_MIX.items():
            causes += [code] * n
        return causes

    def dp(self) -> DifficultyParams:
        return DIFFICULTY_PRESETS[self.difficulty]
