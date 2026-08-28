"""Engine core types: links, bundles, findings, check results, verdicts, constants."""

from __future__ import annotations

from dataclasses import dataclass, field

NORMAL_WINDOW_DAYS = 3
LATE_MAX_DAYS = 7
SHORT_PCT_THRESHOLD = 0.20
AMBIENT_DEBIT_MAX_PAISE = 500_000
DUPLICATE_TOLERANCE_PAISE = 100
P3_MIN_SCORE = 4.0

VERDICT_CLASSES = [
    "matched",
    "matched_after_reasoning",
    "genuine_discrepancy",
    "unresolved",
    "data_quality",
]
INTERNAL_STATUSES = ["ignored_noise", "orphan_chain"]

REASON_CODES = [
    "LNK_P1_EXPLICIT_REF",
    "LNK_P2_NORM_REF",
    "LNK_P3_WINDOW_MATCH",
    "LNK_P3_LLM_ADJUDICATED",
    "REC_ZERO_DELTA",
    "INV_FEE_MATCH",
    "INV_REFUND_ADJ",
    "INV_SPLIT_COMBINE",
    "INV_DUPLICATE",
    "INV_TIMING_OK",
    "INV_AMBIGUOUS_TWIN",
    "INV_FEE_MISMATCH",
    "INV_SHORT_FALL",
    "INV_EXHAUSTIVE_NO_EVIDENCE",
    "INV_UNMATCHED_INFLOW",
    "DQ_NULL_AMOUNT",
    "DQ_NEGATIVE_AMOUNT",
    "DQ_UNKNOWN_METHOD",
    "DQ_MOJIBAKE",
]


@dataclass
class Link:
    src: str
    dst: str
    rule_pass: str
    evidence_ids: list[str] = field(default_factory=list)
    score_breakdown: dict = field(default_factory=dict)


@dataclass
class Bundle:
    bid: str
    orders: set[str] = field(default_factory=set)
    payments: set[str] = field(default_factory=set)
    settlements: set[str] = field(default_factory=set)
    bank_txns: set[str] = field(default_factory=set)
    adjustments: set[str] = field(default_factory=set)
    links: list[Link] = field(default_factory=list)


@dataclass
class Finding:
    kind: str
    expected_paise: int
    actual_paise: int

    @property
    def delta_paise(self) -> int:
        return self.actual_paise - self.expected_paise


@dataclass
class CheckResult:
    check: str
    supported: bool
    evidence_ids: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class Verdict:
    work_key: str
    bundle_bid: str
    cls: str
    reason_code: str
    evidence_ids: list[str] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    llm_assists: list[dict] = field(default_factory=list)
    members: list[str] = field(default_factory=list)
    internal_status: str | None = None
