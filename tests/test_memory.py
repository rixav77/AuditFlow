"""Memory layer tests: store ops, grounding filter, retrieval, chat integration."""

from __future__ import annotations

import json

import pytest

from memory.ingest import batch_ids, ingest_deterministic, is_grounded
from memory.retrieve import extract_entities, memory_context_block, retrieve
from memory.store import MemoryStore


@pytest.fixture()
def store(tmp_path):
    s = MemoryStore(tmp_path / "mem.db")
    yield s
    s.close()


@pytest.fixture(scope="module")
def batch_db(tmp_path_factory):
    from engine.runner import persist, run_pipeline
    from generator.config import GeneratorConfig
    from generator.generate import generate

    td = tmp_path_factory.mktemp("memdata")
    paths = generate(GeneratorConfig(seed=42, size=45), td)
    verdicts, links, _ = run_pipeline(paths["db"])
    persist(paths["db"], verdicts, links)
    return str(paths["db"])


# -- store ----------------------------------------------------------------


def test_add_and_dedup_exact(store):
    mid, op = store.add("user prefers concise answers", "semantic")
    assert op == "ADD"
    mid2, op2 = store.add("User prefers concise answers", "semantic")
    assert op2 == "UPDATE" and mid2 == mid
    assert store.stats()["active"] == 1


def test_add_near_duplicate_updates(store):
    store.add("seed9002 had one UNEXPLAINED_DELTA miss on abstention", "procedural")
    mid2, op2 = store.add("seed9002 had one UNEXPLAINED_DELTA miss on abstention.", "procedural")
    assert op2 == "UPDATE"
    assert store.stats()["active"] == 1


def test_delete_soft_and_log(store):
    mid, _ = store.add("temporary note", "episodic")
    assert store.delete(mid, "test") is True
    assert store.get(mid)["status"] == "deleted"
    assert store.delete(mid) is False  # already deleted -> NOOP logged
    ops = [r["op"] for r in store.con.execute("SELECT op FROM memory_log")]
    assert "DELETE" in ops and "NOOP" in ops


def test_session_ring_buffer_keeps_last_n(store):
    for i in range(15):
        store.push_message("s1", "user", f"message {i}", keep=10)
    msgs = store.last_messages("s1", 10)
    assert len(msgs) == 10
    assert msgs[0]["content"] == "message 5"
    assert msgs[-1]["content"] == "message 14"


# -- grounding filter -------------------------------------------------------


def test_grounded_financial_fact_with_refs():
    ok, _ = is_grounded(
        "ORD-100040 stayed unresolved after the exhaustive check",
        ["ORD-100040"],
        {"ORD100040"},
    )
    assert ok


def test_ungrounded_fact_missing_refs():
    ok, why = is_grounded("ORD-999999 had a ₹500 fee", [], None)
    assert not ok and "unverifiable" in why


def test_ungrounded_id_not_in_refs():
    ok, why = is_grounded("ORD-100040 and ORD-888888 both unresolved", ["ORD-100040"], None)
    assert not ok and "unverifiable" in why


def test_non_financial_passes_without_refs():
    ok, why = is_grounded("user prefers concise answers", [], None)
    assert ok and why == "non-financial"


def test_ref_outside_batch_universe():
    ok, why = is_grounded("ORD-100040 delta", ["ORD-100040"], {"ORD999999"})
    assert not ok and "not in batch" in why


def test_batch_ids_reads_db(batch_db):
    ids = batch_ids(batch_db)
    assert any(i.startswith("ORD") for i in ids)


# -- retrieval --------------------------------------------------------------


def _seed(store):
    store.add(
        "seed9002 miss: ORD-100008 cause SHORT_SETTLED expected genuine_discrepancy "
        "predicted unresolved",
        "procedural",
        scope="batch_seed9002.db",
        source_refs=["ORD-100008"],
        grounded=True,
    )
    store.add("user prefers concise answers", "semantic")
    store.add("Tool list_adjustments failed during chat; retry narrowed", "procedural")


def test_entity_extraction():
    ents = extract_entities("ORD-100040 hit UNEXPLAINED_DELTA; use get_unresolved")
    assert "ORD-100040" in ents
    assert "UNEXPLAINED_DELTA" in ents
    assert "get_unresolved" in ents


def test_retrieve_ranks_relevant_first(store):
    _seed(store)
    hits = retrieve(store, "unresolved misses in seed9002", top_k=2)
    assert hits and "ORD-100008" in hits[0]["text"]
    assert hits[0]["scores"]["keyword"] > 0


def test_retrieve_entity_boost_fires(store):
    _seed(store)
    hits = retrieve(store, "what happened with ORD-100008?", top_k=1)
    assert hits[0]["scores"]["entity"] > 0


def test_memory_context_block_empty_when_no_store(tmp_path):
    s = MemoryStore(tmp_path / "empty.db")
    assert memory_context_block(s, "anything") == ""
    s.close()


# -- deterministic ingestion --------------------------------------------------


def test_ingest_deterministic_records_misses(tmp_path, batch_db):
    import sqlite3

    con = sqlite3.connect(batch_db)
    real_key = con.execute("SELECT work_key FROM verdicts LIMIT 1").fetchone()[0]
    con.close()
    store = MemoryStore(tmp_path / "mem.db")
    eval_row = {
        "match_rate": 0.97,
        "abstention_precision": 0.9,
        "throughput_orders_per_sec": 3000.0,
        "failed_cases": [
            {
                "work_key": real_key,
                "cause": "SHORT_SETTLED",
                "expected_class": "genuine_discrepancy",
                "predicted_class": "unresolved",
            }
        ],
    }
    ops = ingest_deterministic(store, batch_db, eval_row)
    kinds = {o["kind"] for o in ops if o.get("id")}
    assert "episodic" in kinds and "procedural" in kinds
    hits = retrieve(store, "SHORT_SETTLED miss", top_k=1)
    assert hits and real_key in hits[0]["text"]
    store.close()


# -- chat integration ---------------------------------------------------------


def test_smart_history_trims_middle():
    from llm.chat_agent import _smart_history

    hist = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i} " + "x" * 2000}
        for i in range(20)
    ]
    out = _smart_history(hist, budget=8000)
    assert len(out) < len(hist)
    assert any("dropped" in m["content"] for m in out)
    assert out[0]["content"].startswith("msg 0")
    assert out[-1]["content"].startswith("msg 19")


def test_smart_history_noop_under_budget():
    from llm.chat_agent import _smart_history

    hist = [{"role": "user", "content": "short"}]
    assert _smart_history(hist, budget=8000) == hist


def test_search_memory_tool(tmp_path, store, batch_db):
    store.add("seed42 all metrics 1.0", "episodic", scope="batch_seed42.db")
    import os

    from llm.tools import ToolBox

    os.environ["MEMORY_DB"] = str(store.db_path)
    try:
        box = ToolBox(batch_db)
        res = box.search_memory("seed42 metrics")
        assert res["ok"] and res["count"] >= 1
    finally:
        os.environ.pop("MEMORY_DB", None)


def test_search_memory_tool_empty(tmp_path, batch_db):
    import os

    from llm.tools import ToolBox

    os.environ["MEMORY_DB"] = str(tmp_path / "missing.db")
    try:
        box = ToolBox(batch_db)
        res = box.search_memory("anything")
        assert res["ok"] and res["count"] == 0
    finally:
        os.environ.pop("MEMORY_DB", None)


def test_llm_ingest_grounded_vs_dropped(tmp_path, batch_db):
    """mem0 infer path: grounded memories stored, ungrounded financial facts dropped."""
    import os

    from llm.provider import ChatResponse, MockProvider
    from memory.ingest import ingest_llm

    os.environ["MEMORY_INFER"] = "1"
    store = MemoryStore(tmp_path / "mem.db")
    try:
        store.push_message("s", "user", "remember that ORD-100001 had a fee mismatch")
        script = json.dumps(
            [
                {
                    "op": "ADD",
                    "text": "ORD-100001 had a fee mismatch per the user",
                    "kind": "semantic",
                    "source_refs": ["ORD-100001"],
                },
                {
                    "op": "ADD",
                    "text": "SET-999999 paid ₹500 extra fee",
                    "kind": "semantic",
                    "source_refs": ["SET-999999"],
                },
            ]
        )
        provider = MockProvider([ChatResponse(content=script)])
        ops = ingest_llm(store, batch_db, provider, session="s")
        added = [o for o in ops if o.get("op") in ("ADD", "UPDATE")]
        dropped = [o for o in ops if o.get("op") == "DROP"]
        assert len(added) >= 1 and "ORD-100001" in added[0]["text"]
        assert len(dropped) == 1  # fabricated SET-999999 rejected
    finally:
        os.environ.pop("MEMORY_INFER", None)
        store.close()


def test_llm_ingest_disabled_by_default(tmp_path, batch_db):
    from llm.provider import MockProvider
    from memory.ingest import ingest_llm

    store = MemoryStore(tmp_path / "mem.db")
    try:
        ops = ingest_llm(store, batch_db, MockProvider(), session="s")
        assert ops == []
    finally:
        store.close()
