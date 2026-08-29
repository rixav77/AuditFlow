"""mem0-style retrieval adapted to our scale: BM25 pool -> 3-score rerank.

Scores (matching mem0's shape, deterministic):
  keyword  0..1   — token overlap of query vs memory text
  entity   0..0.5 — memories linked to query entities, boost ~ inverse link count
  recency  0..0.5 — updated_at age decay (half-life 14 days)
final = (kw + ent + rec) / 2.0; top-K returned.

Entities: our ID grammar (ORD/PAY/SET/BANK/ADJ), cause codes, class labels and
tool names — extracted by regex, no model call.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime

ID_PAT = re.compile(r"\b(?:ORD|PAY|SET|BANK|ADJ)[-_]?\d+\b", re.IGNORECASE)
CAUSE_PAT = re.compile(
    r"\b(?:CLEAN_MATCH|FEE_EXPLAINED|REFUND_FULL|REFUND_PARTIAL|COMBINED_SETTLEMENT|"
    r"SPLIT_SETTLEMENT|LATE_SETTLEMENT|MISSING_SETTLEMENT|DUPLICATE_BANK_CREDIT|"
    r"UNEXPLAINED_DELTA|AMBIGUOUS_CANDIDATES|SHORT_SETTLED|BANK_ONLY_CREDIT|"
    r"MALFORMED_SOURCE_ROW)\b",
    re.IGNORECASE,
)
CLS_PAT = re.compile(
    r"\b(?:matched_after_reasoning|genuine_discrepancy|unresolved|data_quality|matched)\b",
    re.IGNORECASE,
)
TOOL_PAT = re.compile(
    r"\b(?:get_verdict|get_records|list_transactions|get_unresolved|check_fee_schedule|"
    r"search_narrations|get_settlement_chain|list_adjustments|find_candidate_matches|"
    r"get_batch_summary|query_table|search_memory)\b"
)


def extract_entities(text: str) -> list[str]:
    out: list[str] = []
    for pat, norm in (
        (ID_PAT, lambda m: re.sub(r"[-_]", "-", m.upper())),
        (CAUSE_PAT, lambda m: m.upper()),
        (CLS_PAT, lambda m: m.lower()),
        (TOOL_PAT, lambda m: m.lower()),
    ):
        for m in pat.findall(text):
            v = norm(m)
            if v not in out:
                out.append(v)
    return out


def _fts_query(text: str) -> str:
    words = [w for w in re.findall(r"[a-zA-Z0-9]+", text) if len(w) > 2]
    return " OR ".join(f'"{w}"' for w in words[:12])


def _pool(store, query: str, scope: str | None, pool_size: int) -> tuple[list[dict], set[str]]:
    """Candidate pool: FTS5 BM25 when available, else recency-ranked fallback."""
    rows: list = []
    if store._fts:
        fq = _fts_query(query)
        if fq:
            try:
                sql = (
                    "SELECT m.* FROM memories_fts f JOIN memories m ON m.id=f.rowid "
                    "WHERE memories_fts MATCH ? AND m.status='active' "
                    "ORDER BY bm25(memories_fts) LIMIT ?"
                )
                rows = list(store.con.execute(sql, (fq, pool_size)))
            except Exception:
                rows = []
    if not rows:  # fallback (also covers FTS5-missing and no-match)
        args: list = []
        sql = "SELECT * FROM memories WHERE status='active'"
        if scope:
            sql += " AND (scope IS NULL OR scope=?)"
            args.append(scope)
        rows = list(
            store.con.execute(sql + " ORDER BY updated_at DESC LIMIT ?", (*args, pool_size))
        )
    qtok = {w.lower() for w in re.findall(r"[a-z0-9]+", query.lower())}
    return [dict(r) for r in rows], qtok


def _keyword_score(qtok: set[str], text: str) -> float:
    tt = {w.lower() for w in re.findall(r"[a-z0-9]+", text.lower())}
    if not qtok or not tt:
        return 0.0
    return min(1.0, len(qtok & tt) / max(3, len(qtok)))


def _recency_score(updated_at: str) -> float:
    try:
        dt = datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return 0.0
    age_days = max(0.0, (time.time() - dt.timestamp()) / 86400)
    return 0.5 * (0.5 ** (age_days / 14.0))


def retrieve(store, query: str, top_k: int = 5, scope: str | None = None) -> list[dict]:
    pool_size = max(60, top_k * 4)
    pool, qtok = _pool(store, query, scope, pool_size)
    if not pool:
        return []

    q_ents = extract_entities(query)
    ent_links: dict[int, float] = {}
    if q_ents:
        ph = ",".join("?" * len(q_ents))
        for r in store.con.execute(
            f"SELECT memory_id, COUNT(*) OVER (PARTITION BY entity) AS n "
            f"FROM entities WHERE entity IN ({ph})",
            q_ents,
        ):
            # fewer memories per entity -> higher boost (mem0's inverse-link rule)
            ent_links[r["memory_id"]] = max(ent_links.get(r["memory_id"], 0.0), 0.5 / r["n"])

    scored = []
    for d in pool:
        kw = _keyword_score(qtok, d["text"])
        ent = ent_links.get(d["id"], 0.0)
        rec = _recency_score(d["updated_at"])
        scored.append(
            {
                "id": d["id"],
                "text": d["text"],
                "kind": d["kind"],
                "attribution": d["attribution"],
                "scope": d["scope"],
                "source_refs": json.loads(d["source_refs"] or "[]"),
                "grounded": bool(d["grounded"]),
                "updated_at": d["updated_at"],
                "scores": {
                    "keyword": round(kw, 3),
                    "entity": round(ent, 3),
                    "recency": round(rec, 3),
                },
                "final": round((kw + ent + rec) / 2.0, 4),
            }
        )
    scored.sort(key=lambda m: m["final"], reverse=True)
    return scored[:top_k]


def memory_context_block(store, query: str, top_k: int = 3, scope: str | None = None) -> str:
    """Rendered memory block for chat injection. Empty when nothing relevant."""
    hits = retrieve(store, query, top_k=top_k, scope=scope)
    if not hits:
        return ""
    lines = ["Relevant long-term memory (background only — verify against tool results):"]
    for h in hits:
        lines.append(f"- [{h['kind']}/{h['attribution']}] {h['text']}")
    return "\n".join(lines)
