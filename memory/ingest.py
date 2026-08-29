"""Memory ingestion — mem0's pipeline, two paths, one honesty rule.

Path 1 — DETERMINISTIC (no LLM): post-run episodic summaries, trace-failure
observations, eval-miss records.

Path 2 — LLM infer (mem0 infer=true), env MEMORY_INFER=1: bounded extraction
over [last-10 session messages + retrieved relevant memories] returning ops
{ADD|UPDATE|DELETE|NOOP}. GROUNDING FILTER: any memory containing financial
facts must cite record IDs verifiable in the batch DB (Layer-A discipline from
llm/citations.py). Failing memories are DROPPED and logged, never stored.

Every op is logged to memory_log (mem0's history store).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from llm.traces import log_event
from memory.store import MemoryStore, jaccard, tokens

ID_PAT = re.compile(r"\b(?:ORD|PAY|SET|BANK|ADJ)[-_]?\d+\b", re.IGNORECASE)
AMOUNT_PAT = re.compile(r"(?:₹|rs\.?|inr)\s?[\d,]+|(?:\d[\d,]*)\s?paise", re.IGNORECASE)
FIN_TOKENS = {"fee", "refund", "settlement", "delta", "amount", "paise", "discrepancy", "paid"}


def _sig(m: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", m.upper())


def batch_ids(db_path: str | Path) -> set[str]:
    """All record IDs that exist in the batch DB (grounding universe)."""
    import sqlite3

    con = sqlite3.connect(db_path)
    out: set[str] = set()
    for table, col in (
        ("orders", "order_id"),
        ("payments", "payment_id"),
        ("settlements", "settlement_id"),
        ("bank_txns", "bank_txn_id"),
        ("adjustments", "adjustment_id"),
        ("verdicts", "work_key"),
    ):
        try:
            for (v,) in con.execute(f"SELECT {col} FROM {table}"):
                if v:
                    out.add(_sig(str(v)))
        except Exception:
            continue
    con.close()
    return out


def is_grounded(text: str, source_refs: list[str], universe: set[str] | None) -> tuple[bool, str]:
    """Grounding filter (Layer-A discipline for memory).

    Grounded when: (a) non-financial text, OR (b) every record ID mentioned is
    in source_refs AND every source_ref resolves in the batch universe.
    """
    ids_in_text = {_sig(m) for m in ID_PAT.findall(text)}
    has_money = bool(AMOUNT_PAT.search(text))
    refs = {_sig(r) for r in source_refs}
    text_words = {w.lower() for w in tokens(text)}

    if not ids_in_text and not has_money and not (text_words & FIN_TOKENS):
        return True, "non-financial"
    if ids_in_text and not ids_in_text.issubset(refs):
        return False, f"unverifiable IDs in text: {sorted(ids_in_text - refs)}"
    if universe is not None and refs and not refs.issubset(universe):
        return False, f"source_refs not in batch: {sorted(refs - universe)}"
    if (has_money or ids_in_text) and not refs:
        return False, "financial fact without source_refs"
    return True, "ok"


def ingest_deterministic(store: MemoryStore, db_path: str | Path, eval_row: dict) -> list[dict]:
    """Post-run episodic + procedural ingestion. Deterministic; never uses an LLM."""
    out = []
    scope = Path(db_path).name
    uni = batch_ids(db_path)

    misses = eval_row.get("failed_cases") or []
    summary = (
        f"Run {scope}: match_rate={eval_row.get('match_rate')}, "
        f"abstention_precision={eval_row.get('abstention_precision')}, "
        f"{len(misses)} failed cases, "
        f"throughput={eval_row.get('throughput_orders_per_sec')} orders/s"
    )
    mid, op = store.add(summary, kind="episodic", attribution="system", scope=scope, source_refs=[])
    out.append({"id": mid, "op": op, "kind": "episodic", "text": summary})

    for fc in misses:
        wk = fc.get("work_key", "")
        text = (
            f"{scope} miss: {wk} cause={fc.get('cause')} expected "
            f"{fc.get('expected_class')} but predicted {fc.get('predicted_class')}"
        )
        ok, why = is_grounded(text, [wk] if wk else [], uni)
        if not ok:
            store.log("DROP", None, f"grounding failed: {why}")
            out.append({"id": None, "op": "DROP", "why": why})
            continue
        mid, op = store.add(
            text,
            kind="procedural",
            attribution="system",
            scope=scope,
            source_refs=[wk] if wk else [],
            grounded=True,
        )
        out.append({"id": mid, "op": op, "kind": "procedural", "text": text})

    log_event(
        "memory", None, "ingest_deterministic", {"scope": scope, "ops": len(out)}, batch=scope
    )
    return out


def ingest_from_traces(
    store: MemoryStore, trace_file: str | Path, max_events: int = 50
) -> list[dict]:
    """Harvest tool-failure observations from chat traces (procedural)."""
    out: list[dict] = []
    p = Path(trace_file)
    if not p.exists():
        return out
    events = []
    for line in p.read_text().splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    fails = [
        e
        for e in events
        if e.get("event") == "tool_call" and not e.get("payload", {}).get("ok", True)
    ]
    for e in fails[:max_events]:
        name = e.get("payload", {}).get("name", "?")
        text = f"Tool {name} failed during chat on {p.stem}; retry with narrowed arguments."
        mid, op = store.add(text, kind="procedural", attribution="agent", source_refs=[])
        out.append({"id": mid, "op": op, "kind": "procedural", "text": text})
    return out


EXTRACT_SYSTEM = """You are a memory extractor for a finance reconciliation agent.
From the conversation, extract durable memories worth keeping.
Return ONLY a JSON array; each item:
{"op": "ADD"|"UPDATE"|"DELETE"|"NOOP", "text": "...", "kind": "semantic"|"episodic"|"procedural",
 "source_refs": ["ORD-1001", ...]}
Rules:
- financial facts (amounts, verdicts, causes) MUST list the record IDs they come from
- NOOP when nothing worth remembering
- max 5 items, each text <= 40 words"""


def ingest_llm(
    store: MemoryStore,
    db_path: str | Path,
    provider,
    session: str = "default",
    top_k: int = 3,
) -> list[dict]:
    """mem0 infer=true path. Env MEMORY_INFER=1 required. Grounded or dropped."""
    if os.environ.get("MEMORY_INFER", "0") != "1":
        return []
    from memory.retrieve import retrieve

    recent = store.last_messages(session, 10)
    if not recent:
        return []
    flat = " ".join(m["content"] for m in recent)
    relevant = retrieve(store, flat, top_k=top_k, scope=Path(db_path).name)
    messages = [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                {"recent": recent, "relevant_memories": [r["text"] for r in relevant]}
            ),
        },
    ]
    resp = provider.chat(messages, tools=None, temperature=0.0)
    try:
        match = re.search(r"\[.*\]", resp.content, re.DOTALL)
        ops = json.loads(match.group(0)) if match else []
    except (json.JSONDecodeError, AttributeError):
        store.log("NOOP", None, "llm extract: unparseable output")
        return []

    uni = batch_ids(db_path)
    out = []
    for item in ops[:5]:
        if not isinstance(item, dict) or item.get("op") not in {"ADD", "UPDATE", "DELETE", "NOOP"}:
            continue
        text = str(item.get("text", "")).strip()
        if item["op"] == "NOOP" or not text:
            continue
        if item["op"] == "DELETE":
            for h in store.all_active():
                if jaccard(tokens(h["text"]), tokens(text)) >= 0.8:
                    store.delete(h["id"], reason="llm DELETE")
                    out.append({"id": h["id"], "op": "DELETE"})
            continue
        refs = [r for r in item.get("source_refs", []) if isinstance(r, str)]
        ok, why = is_grounded(text, refs, uni)
        if not ok:
            store.log("DROP", None, f"llm memory grounding failed: {why}")
            out.append({"id": None, "op": "DROP", "why": why})
            continue
        mid, op = store.add(
            text,
            kind=item.get("kind", "semantic"),
            attribution="agent",
            scope=Path(db_path).name,
            source_refs=refs,
            grounded=bool(refs),
        )
        out.append({"id": mid, "op": op, "kind": item.get("kind", "semantic"), "text": text})
    log_event("memory", None, "ingest_llm", {"ops": len(out)}, batch=Path(db_path).name)
    return out
