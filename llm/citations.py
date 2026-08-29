"""Citation verification layer — the "trust but verify" gate on LLM narratives.

Design (docs/HARNESS_EVALS.md §citations; RESEARCH.md §5 contracts):

  Layer A — ID validity (hard):  every ORD/PAY/SET/BANK/ADJ ref cited in the
            narrative must exist in the payload records.
  Layer B — amount consistency (hard): every money figure (₹ Indian-grouped,
            "Rs." prefix, or explicit "N paise") must equal a payload amount or
            its paise/rupee counterpart. Catches silent unit errors like
            writing ₹3,96,900 for 396900 paise (the S10 incident).
  Layer C — per-sentence lexical support (soft): each sentence must cite a valid
            ID or share enough content tokens with the payload; yields ALCE-style
            citation recall/precision. Soft = flagged, not fatal.
  Layer D — semantic judge (stub): env CITATION_SEMANTIC=1 enables one bounded
            NLI-style call per flagged sentence. Off by default — we do not
            chase perfection; the deterministic layers carry the guarantee.

Principles honored: facts come from structured records (AGENTS.md #1); the
narrative may be wrong, the report never lies.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

ID_RE = re.compile(r"\b(?:ORD|PAY|SET|BANK|ADJ)[-_]?\d+\b", re.IGNORECASE)
MONEY_RE = re.compile(r"(?:₹|Rs\.?|INR)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.IGNORECASE)
PAISE_RE = re.compile(r"\b([0-9][0-9,]*)\s*paise\b", re.IGNORECASE)

_ID_COLS = ("order_id", "payment_id", "settlement_id", "bank_txn_id", "adjustment_id")
_PAISE_KEYS = (
    "amount_paise",
    "gross_paise",
    "fee_paise",
    "tax_paise",
    "net_paise",
    "expected_paise",
    "actual_paise",
    "delta_paise",
)

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be", "been",
    "being", "to", "of", "in", "on", "at", "by", "for", "with", "from", "as", "it",
    "its", "this", "that", "these", "those", "there", "their", "they", "them",
    "which", "into", "than", "then", "so", "such", "thus", "due", "any", "all",
    "no", "not", "nor", "also", "has", "have", "had", "did", "do", "does", "can",
    "will", "would", "should", "must", "may", "after", "before", "between",
    "via", "per", "each", "both", "his", "her", "him", "she", "he", "you", "your",
    "our", "we", "us", "one", "two", "still", "yet", "out", "up", "down", "off",
}


@dataclass
class VerificationReport:
    """Outcome of verifying one narrative against one payload."""

    verified: bool = True  # hard layers (A+B) clean
    fully_supported: bool = True  # soft layer (C) clean too
    id_errors: list[str] = field(default_factory=list)
    amount_errors: list[str] = field(default_factory=list)
    unsupported_sentences: list[str] = field(default_factory=list)
    citation_recall: float | None = None
    citation_precision: float | None = None
    n_sentences: int = 0
    n_citations: int = 0
    semantic_judge_used: bool = False

    @property
    def hard_errors(self) -> list[str]:
        return self.id_errors + self.amount_errors

    def to_dict(self) -> dict:
        return {
            "verified": self.verified,
            "fully_supported": self.fully_supported,
            "id_errors": self.id_errors,
            "amount_errors": self.amount_errors,
            "unsupported_sentences": self.unsupported_sentences,
            "citation_recall": self.citation_recall,
            "citation_precision": self.citation_precision,
            "n_sentences": self.n_sentences,
            "n_citations": self.n_citations,
            "semantic_judge_used": self.semantic_judge_used,
            "verifier": "llm.citations.v1",
        }


def _norm_id(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _payload_ids(payload: dict) -> set[str]:
    return {
        _norm_id(r[c]) for r in payload.get("records", []) for c in _ID_COLS if r.get(c)
    }


def _payload_amount_pool(payload: dict) -> set[int]:
    pool = {0}
    for r in payload.get("records", []):
        for k, v in r.items():
            if k in _PAISE_KEYS and isinstance(v, int):
                pool.add(abs(v))
    for f in payload.get("findings", []):
        for k in ("expected_paise", "actual_paise", "delta_paise"):
            if isinstance(f.get(k), int):
                pool.add(abs(f[k]))
    return pool


def _parse_money(s: str) -> float:
    return float(s.replace(",", "").strip() or "0")


def _tokens(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9_]+", text.lower())
        if len(t) >= 3 and t not in STOPWORDS
    }


def _payload_token_blob(payload: dict) -> set[str]:
    blob = json.dumps(payload, default=str).lower()
    toks = set(re.findall(r"[a-z0-9_]+", blob))
    # split snake_case / reason codes so "checks_run" also yields "checks","run"
    for t in list(toks):
        if "_" in t:
            toks.update(p for p in t.split("_") if len(p) >= 3)
    return toks


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def verify_narrative(text: str, payload: dict) -> VerificationReport:
    """Verify an LLM (or deterministic) narrative against a payload."""
    report = VerificationReport()

    # ---- Layer A: IDs (hard) -------------------------------------------
    payload_ids = _payload_ids(payload)
    cited_orig: dict[str, str] = {}
    for m in ID_RE.findall(text):
        cited_orig.setdefault(_norm_id(m), m)
    report.n_citations = len(cited_orig)
    report.id_errors = [cited_orig[k] for k in sorted(cited_orig) if k not in payload_ids]

    # ---- Layer B: amounts (hard) ---------------------------------------
    pool = _payload_amount_pool(payload)
    for m in MONEY_RE.finditer(text):
        rupees = _parse_money(m.group(1))
        paise = round(rupees * 100)
        if paise not in pool:
            report.amount_errors.append(f"{m.group(0).strip()} (= {paise} paise)")
    for m in PAISE_RE.finditer(text):
        paise = int(_parse_money(m.group(1)))
        if paise not in pool:
            report.amount_errors.append(f"{m.group(0).strip()}")

    # ---- Layer C: per-sentence lexical support (soft) -------------------
    blob_tokens = _payload_token_blob(payload)
    valid_ids = payload_ids - {_norm_id(e) for e in report.id_errors}
    supported = 0
    sents = _sentences(text)
    report.n_sentences = len(sents)
    for s in sents:
        s_ids = {_norm_id(m) for m in ID_RE.findall(s)}
        has_valid_cite = bool(s_ids) and s_ids.issubset(valid_ids)
        overlap = _tokens(s) & blob_tokens
        if has_valid_cite or len(overlap) >= 2:
            supported += 1
        else:
            report.unsupported_sentences.append(s[:160])
    report.citation_recall = round(supported / len(sents), 4) if sents else None
    report.citation_precision = (
        round(
            (report.n_citations - len({_norm_id(e) for e in report.id_errors}))
            / report.n_citations,
            4,
        )
        if report.n_citations
        else None
    )

    # ---- Layer D: semantic judge (env-gated stub) ------------------------
    if os.environ.get("CITATION_SEMANTIC", "0") == "1" and report.unsupported_sentences:
        # A real implementation would run one bounded NLI-style call per flagged
        # sentence against the payload and drop false alarms. Left unimplemented
        # on purpose (RESEARCH.md §5: deterministic-first, no perfection chase).
        report.semantic_judge_used = False

    report.verified = not report.hard_errors
    report.fully_supported = report.verified and not report.unsupported_sentences
    return report


def repair_feedback(report: VerificationReport) -> str:
    """Explicit feedback for one minimal-revision retry (RARR-style)."""
    lines = [
        "Your draft contains unsupported claims. Rewrite it keeping the verdict,",
        "fixing ONLY these problems, citing ONLY IDs/amounts from the payload:",
    ]
    for e in report.id_errors:
        lines.append(f"- unknown record ID cited: {e}")
    for e in report.amount_errors:
        lines.append(f"- amount not present in payload: {e}")
    for s in report.unsupported_sentences[:3]:
        lines.append(f'- sentence not supported by payload: "{s}"')
    lines.append(
        "Amounts are integer PAISE (₹1 = 100 paise); show both forms when stating rupees."
    )
    return "\n".join(lines)

