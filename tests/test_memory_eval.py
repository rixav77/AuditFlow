"""Eval Layer 4 tests: memory metrics + report wiring."""

from __future__ import annotations

import pytest

from eval.memory_eval import grounded_memory_rate, long_session_eval, memory_layer, retrieval_eval
from memory.store import MemoryStore


@pytest.fixture(scope="module")
def gated_batch(tmp_path_factory):
    from engine.runner import persist, run_pipeline
    from generator.config import GeneratorConfig
    from generator.generate import generate

    td = tmp_path_factory.mktemp("memeval")
    paths = generate(GeneratorConfig(seed=42, size=45), td)
    verdicts, links, _ = run_pipeline(paths["db"])
    persist(paths["db"], verdicts, links)
    return paths


def _store(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    s.add("user prefers concise answers", "semantic")
    s.add("seed9002 miss: ORD-100008 unresolved", "procedural", source_refs=["ORD-100008"])
    s.add("Run batch_seed7.db: match_rate=1.0", "episodic")
    return s


def test_grounded_memory_rate_all_grounded(tmp_path):
    s = _store(tmp_path)
    res = grounded_memory_rate(s)
    assert res["n"] == 3 and res["rate"] == 1.0
    s.close()


def test_grounded_rate_empty_store(tmp_path):
    s = MemoryStore(tmp_path / "empty.db")
    assert grounded_memory_rate(s) == {"n": 0, "rate": None}
    s.close()


def test_retrieval_eval_hit_at_1(tmp_path):
    s = _store(tmp_path)
    res = retrieval_eval(s, [("unresolved miss", "unresolved"), ("match rate", "match_rate")])
    assert res["hit_at_1"] == 1.0
    s.close()


def test_retrieval_eval_miss(tmp_path):
    s = _store(tmp_path)
    res = retrieval_eval(s, [("quantum chromodynamics", "quark")])
    assert res["hit_at_1"] == 0.0
    s.close()


def test_memory_layer_full(tmp_path):
    s = _store(tmp_path)
    res = memory_layer(s)
    assert res["grounded"]["rate"] == 1.0
    assert res["retrieval"]["hit_at_1"] is not None
    s.close()


def test_long_session_eval_mock(gated_batch, tmp_path):
    from llm.provider import MockProvider

    res = long_session_eval(gated_batch["db"], MockProvider(), n_filler=4)
    assert res["citation_state_ok"] is True
