"""LLM assist touchpoints T1/T2/T3 with deterministic fallbacks + trigger counters."""

from __future__ import annotations

import json
import os

from generator.narrate import extract_order_refs

STATS = {"t1_calls": 0, "t1_used": 0, "t2_calls": 0, "t3_calls": 0}


def assist_mode() -> str:
    return os.environ.get("ASSIST_MODE", "null")


class NullAssist:
    name = "null_assist"

    def adjudicate_linkage(self, candidates: list[dict]) -> dict:
        STATS["t1_calls"] += 1
        if not candidates:
            return {"choice": None, "ambiguous": True, "cited_ids": []}
        best = max(candidates, key=lambda c: c.get("score", 0))
        return {
            "choice": best["id"],
            "ambiguous": len(candidates) > 1,
            "low_confidence": True,
            "cited_ids": [best["id"]],
        }

    def interpret_narration(self, narration: str) -> dict:
        STATS["t2_calls"] += 1
        return {"extracted_refs": sorted(extract_order_refs(narration)), "quoted_spans": []}

    def orchestrate(self, state: dict) -> dict:
        STATS["t3_calls"] += 1
        order = [
            "FEE_SCHEDULE_MATCH",
            "REFUND_ADJUSTMENT_LOOKUP",
            "SPLIT_COMBINE_TEST",
            "DUPLICATE_SCAN",
            "TIMING_WINDOW",
            "AMBIGUOUS_TWIN_SCAN",
            "EXHAUSTIVE_SEARCH",
        ]
        remaining = [c for c in order if c not in state.get("checks_run", [])]
        if not remaining or state.get("turns", 0) >= 6:
            return {"action": "stop", "reason": "all_checks_exhausted_or_budget"}
        return {"action": "next_check", "check": remaining[0]}


class LiveAssist(NullAssist):
    """Same interface; T1 delegates to an LLM with strict output contract."""

    name = "live_assist"

    def __init__(self):
        from llm.provider import FallbackProvider

        self.provider = FallbackProvider()

    def adjudicate_linkage(self, candidates: list[dict]) -> dict:
        STATS["t1_calls"] += 1
        payload = [
            {
                "id": c["id"],
                "score": round(c.get("score", 0), 2),
                "utr_hit": c.get("utr_hit"),
                "lag_days": c.get("lag_days"),
                "net_paise": c.get("net_paise"),
            }
            for c in candidates
        ]
        messages = [
            {
                "role": "system",
                "content": "You tie-break ambiguous bank-credit to settlement matches in payment "
                "reconciliation. Reply ONLY with JSON: "
                '{"choice":"<id>","rationale":"<="25 chars","cited_ids":["<id>"]} '
                "using ONLY candidate ids shown.",
            },
            {"role": "user", "content": json.dumps(payload)},
        ]
        try:
            resp = self.provider.chat(messages, temperature=0.0)
            data = json.loads(resp.content[resp.content.find("{") : resp.content.rfind("}") + 1])
            valid_ids = {c["id"] for c in candidates}
            if data.get("choice") in valid_ids:
                STATS["t1_used"] += 1
                return {
                    "choice": data["choice"],
                    "ambiguous": False,
                    "low_confidence": False,
                    "cited_ids": [data["choice"]],
                    "rationale": data.get("rationale", ""),
                }
        except Exception:
            pass
        return super().adjudicate_linkage(candidates)


_cached: NullAssist | None = None
_cache_mode: str | None = None


def get_assist() -> NullAssist:
    global _cached, _cache_mode
    mode = assist_mode()
    if _cache_mode != mode or _cached is None:
        _cached = LiveAssist() if mode == "live" else NullAssist()
        _cache_mode = mode
    return _cached


ASSIST = NullAssist()
