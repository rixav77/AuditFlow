"""Read-only tool registry exposed to the chat agent. Every call cites records."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from engine.fees_ext import compute_net_with, merged_schedule


class ToolBox:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            self.verdicts = {r["work_key"]: dict(r) for r in con.execute("SELECT * FROM verdicts")}
            self.tabs = {
                t: [dict(r) for r in con.execute(f"SELECT * FROM {t}")]
                for t in ("orders", "payments", "settlements", "bank_txns", "adjustments")
            }
            meta = {}
            try:
                meta = {
                    k: v
                    for k, v in con.execute("SELECT key,value FROM batch_meta")
                    if k.startswith("_")
                }
            except sqlite3.OperationalError:
                pass
            self.extra_schedule = (
                json.loads(meta["_fee_schedule"]) if "_fee_schedule" in meta else None
            )
        finally:
            con.close()

    # -- helpers ---------------------------------------------------------
    def _records_for(self, ids: list[str]) -> list[dict]:
        out = []
        for ident in ids:
            for table, col in (
                ("orders", "order_id"),
                ("payments", "payment_id"),
                ("settlements", "settlement_id"),
                ("bank_txns", "bank_txn_id"),
                ("adjustments", "adjustment_id"),
            ):
                for r in self.tabs[table]:
                    if r.get(col) == ident:
                        out.append({"source": table, **r})
        return out

    # -- tools -----------------------------------------------------------
    def get_verdict(self, work_key: str) -> dict:
        v = self.verdicts.get(work_key)
        if not v:
            return {"ok": False, "error": f"unknown work_key {work_key}"}
        try:
            members = json.loads(v.get("members_json") or "[]")
        except Exception:
            members = []
        member_ids = [m.split(":", 1)[1] for m in members]
        return {"ok": True, "verdict": v, "citations": member_ids}

    def get_records(self, work_key: str) -> dict:
        vr = self.get_verdict(work_key)
        if not vr["ok"]:
            return vr
        recs = self._records_for(vr["citations"])
        return {"ok": True, "records": recs}

    def list_transactions(self, cls: str | None = None, limit: int = 20) -> dict:
        rows = [
            {"work_key": k, "cls": v["cls"], "reason": v["reason_code"]}
            for k, v in self.verdicts.items()
            if v["cls"] and (not cls or v["cls"] == cls)
        ]
        return {"ok": True, "count": len(rows), "items": rows[: max(1, min(limit, 100))]}

    def get_unresolved(self) -> dict:
        items = [
            {"work_key": k, "reason": v["reason_code"]}
            for k, v in self.verdicts.items()
            if v["cls"] == "unresolved"
        ]
        return {"ok": True, "count": len(items), "items": items[:50]}

    def check_fee_schedule(self, method: str, gross_paise: int) -> dict:
        sched = merged_schedule(self.extra_schedule)
        rule = sched.get(method)
        if not rule:
            return {"ok": False, "error": f"unknown method {method}"}
        fee, tax, net = compute_net_with(gross_paise, rule)
        return {
            "ok": True,
            "method": method,
            "gross_paise": gross_paise,
            "fee_paise": fee,
            "tax_paise": tax,
            "net_paise": net,
        }

    def search_narrations(self, pattern: str, limit: int = 15) -> dict:
        hits = [
            {
                "bank_txn_id": b["bank_txn_id"],
                "narration": b["narration"],
                "amount_paise": b["amount_paise"],
            }
            for b in self.tabs["bank_txns"]
            if pattern.lower() in b["narration"].lower()
        ]
        return {"ok": True, "count": len(hits), "items": hits[:limit]}

    def get_settlement_chain(self, payment_id: str) -> dict:
        settles = [s for s in self.tabs["settlements"] if s.get("payment_id") == payment_id]
        return {
            "ok": True,
            "payment_id": payment_id,
            "settlements": settles,
            "citations": [s["settlement_id"] for s in settles],
        }

    def list_adjustments(self, payment_id: str) -> dict:
        adjs = [a for a in self.tabs["adjustments"] if a.get("payment_id") == payment_id]
        return {
            "ok": True,
            "payment_id": payment_id,
            "adjustments": adjs,
            "citations": [a["adjustment_id"] for a in adjs],
        }

    def find_candidate_matches(self, record_id: str) -> dict:
        hits = []
        for t, col in (("orders", "order_id"), ("settlements", "settlement_id")):
            row = next((r for r in self.tabs[t] if r.get(col) == record_id), None)
            if row:
                amt = row.get("amount_paise") or row.get("net_paise")
                for s in self.tabs["settlements"]:
                    if s.get("net_paise") == amt and s["settlement_id"] != record_id:
                        hits.append({"candidate": s["settlement_id"], "match_on": "amount"})
                break
        return {"ok": True, "record": record_id, "candidates": hits[:5]}

    def get_batch_summary(self) -> dict:
        from collections import Counter

        mix = Counter(v["cls"] for v in self.verdicts.values() if v["cls"])
        return {"ok": True, "total_keys": len(self.verdicts), "class_mix": dict(mix)}

    def query_table(self, table: str, where_col: str = "", where_val: str = "", limit: int = 25):
        allowed = {"orders", "payments", "settlements", "bank_txns", "adjustments"}
        if table not in allowed:
            return {"ok": False, "error": "table not permitted"}
        rows = self.tabs[table]
        if where_col:
            rows = [r for r in rows if str(r.get(where_col)) == str(where_val)]
        return {"ok": True, "count": len(rows), "items": rows[: max(1, min(limit, 50))]}

    def search_memory(self, query: str, top_k: int = 5) -> dict:
        """Tool #12: explicit long-term memory search (mem0 retrieval via tool)."""
        import os

        from memory.retrieve import retrieve
        from memory.store import DB_PATH, MemoryStore

        path = os.environ.get("MEMORY_DB", str(DB_PATH))
        if not Path(path).exists():
            return {"ok": True, "count": 0, "items": [], "note": "memory store empty"}
        store = MemoryStore(path)
        try:
            hits = retrieve(store, query, top_k=max(1, min(top_k, 10)))
        finally:
            store.close()
        return {
            "ok": True,
            "count": len(hits),
            "items": hits,
            "citations": [c for h in hits for c in h.get("source_refs", [])],
        }


def dispatch(box: ToolBox, name: str, args: dict) -> dict:
    fn = getattr(box, name, None)
    if fn is None or name.startswith("_"):
        return {"ok": False, "error": f"no such tool {name}"}
    try:
        result = fn(**args)
    except TypeError as e:
        return {"ok": False, "error": f"bad arguments: {e}"}
    # trace every tool call (docs/CONTEXT_MEMORY.md §trace format)
    import hashlib

    from llm.traces import log_event

    log_event(
        "chat",
        args.get("work_key") or args.get("payment_id") or args.get("record_id"),
        "tool_call",
        {
            "name": name,
            "args": args,
            "ok": bool(result.get("ok")),
            "result_sha256": hashlib.sha256(
                json.dumps(result, sort_keys=True, default=str).encode()
            ).hexdigest()[:16],
        },
    )
    return result


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_verdict",
            "description": "Verdict + findings for one transaction",
            "parameters": {
                "type": "object",
                "properties": {"work_key": {"type": "string"}},
                "required": ["work_key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_records",
            "description": "Raw source records of a transaction bundle",
            "parameters": {
                "type": "object",
                "properties": {"work_key": {"type": "string"}},
                "required": ["work_key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_transactions",
            "description": "List transactions by class",
            "parameters": {
                "type": "object",
                "properties": {"cls": {"type": "string"}, "limit": {"type": "integer"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_unresolved",
            "description": "All unresolved cases",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_fee_schedule",
            "description": "Compute expected fee/net for method+gross",
            "parameters": {
                "type": "object",
                "properties": {"method": {"type": "string"}, "gross_paise": {"type": "integer"}},
                "required": ["method", "gross_paise"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_narrations",
            "description": "Search bank narration text",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_settlement_chain",
            "description": "Settlement legs of a payment",
            "parameters": {
                "type": "object",
                "properties": {"payment_id": {"type": "string"}},
                "required": ["payment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_adjustments",
            "description": "Refunds/reversals of a payment",
            "parameters": {
                "type": "object",
                "properties": {"payment_id": {"type": "string"}},
                "required": ["payment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_candidate_matches",
            "description": "Alternate linkage candidates for a record",
            "parameters": {
                "type": "object",
                "properties": {"record_id": {"type": "string"}},
                "required": ["record_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_batch_summary",
            "description": "Batch-level metrics",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_table",
            "description": "Generic guarded query over source tables",
            "parameters": {
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "where_col": {"type": "string"},
                    "where_val": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Search the agent's long-term memory for past runs and heuristics",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
                "required": ["query"],
            },
        },
    },
]
