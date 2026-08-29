"""Layer 4 — MEMORY & CONTEXT evaluation (deterministic except live probes).

A. grounded_memory_rate : share of active memories that pass the grounding
   filter (financial facts must cite verifiable record IDs). Target: 1.0.
B. retrieval_eval       : hit@1 over canned (query, expected_fragment) pairs.
C. long_session_eval    : Arize pattern — load 10 filler turns, ask an 11th
   recall question, require citation discipline (live probes only).
"""

from __future__ import annotations

from pathlib import Path

from memory.ingest import is_grounded
from memory.retrieve import retrieve


def grounded_memory_rate(store) -> dict:
    rows = store.all_active()
    if not rows:
        return {"n": 0, "rate": None}
    ok = 0
    for r in rows:
        import json as _json

        good, _ = is_grounded(
            r["text"], _json.loads(r["source_refs"] or "[]"), universe=None
        )
        ok += int(good)
    return {"n": len(rows), "rate": round(ok / len(rows), 4)}


def retrieval_eval(store, cases: list[tuple[str, str]] | None = None) -> dict:
    """hit@1: does the top retrieved memory contain the expected fragment?"""
    cases = cases or [
        ("unresolved misses", "unresolved"),
        ("match rate run summary", "match_rate"),
        ("tool failure retry", "failed"),
    ]
    hits = 0
    per = []
    for q, frag in cases:
        top = retrieve(store, q, top_k=1)
        hit = bool(top) and frag.lower() in top[0]["text"].lower()
        hits += int(hit)
        per.append({"query": q, "expect": frag, "hit": hit})
    return {
        "cases": len(cases),
        "hit_at_1": round(hits / len(cases), 4) if cases else None,
        "per": per,
    }


def long_session_eval(db_path: str | Path, provider, n_filler: int = 10) -> dict:
    """10 filler turns then a recall question; citation discipline must hold."""
    from eval.trajectory import eval_chat_trajectory
    from llm.chat_agent import run_chat

    filler = [
        {"role": "user", "content": f"Filler question {i}: summarize batch stats."}
        if i % 2 == 0
        else {"role": "assistant", "content": f"Batch stats summary {i}."}
        for i in range(n_filler)
    ]
    events = run_chat(
        db_path,
        "After everything discussed: list any unresolved transactions by ID.",
        provider,
        history=filler,
    )
    traj = eval_chat_trajectory(events)
    return {
        "filler_turns": n_filler,
        "citation_state_ok": traj["citation_state_ok"],
        "unverified_citations": traj["unverified_citations"],
        "n_tool_calls": traj["n_tool_calls"],
    }


def memory_layer(store, live_provider=None, db_path: str | Path | None = None) -> dict:
    out = {
        "grounded": grounded_memory_rate(store),
        "retrieval": retrieval_eval(store),
    }
    if live_provider and db_path:
        try:
            out["long_session"] = long_session_eval(db_path, live_provider)
        except Exception as e:  # noqa: BLE001 — probes must not break the report
            out["long_session"] = {"error": str(e)}
    return out
