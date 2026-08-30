"""Self-improvement harness tests: allowlist, gate, keep-or-revert."""

from __future__ import annotations

import json

import pytest

from llm.provider import ChatResponse, MockProvider
from memory import improve


@pytest.fixture()
def clean_cwd(tmp_path, monkeypatch):
    """Run in an empty cwd so allowlist writes never touch the repo."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "llm" / "prompts").mkdir(parents=True)
    (tmp_path / "memory").mkdir(parents=True)
    return tmp_path


@pytest.fixture(scope="module")
def gated_batch(tmp_path_factory):
    from engine.runner import persist, run_pipeline
    from generator.config import GeneratorConfig
    from generator.generate import generate

    td = tmp_path_factory.mktemp("improve")
    paths = generate(GeneratorConfig(seed=42, size=45), td)
    verdicts, links, _ = run_pipeline(paths["db"])
    persist(paths["db"], verdicts, links)
    return paths


def _mock_provider(*contents: str) -> MockProvider:
    return MockProvider([ChatResponse(content=c) for c in contents])


def test_allowlist_only(clean_cwd):
    assert set(improve.ALLOWLIST) == {"chat_system", "skills"}
    assert all(str(p).endswith(".md") for p in improve.ALLOWLIST.values())


def test_snapshot_restore_roundtrip(clean_cwd):
    p = improve.ALLOWLIST["skills"]
    p.write_text("v1")
    snap = improve.snapshot()
    p.write_text("v2")
    improve.restore(snap)
    assert p.read_text() == "v1"

    # file that did not exist before is removed on restore
    q = improve.ALLOWLIST["chat_system"]
    assert not q.exists()
    snap2 = improve.snapshot()
    q.write_text("new")
    improve.restore(snap2)
    assert not q.exists()


def test_gate_passed_detects_regressions(clean_cwd):
    base = {
        "engine_sha": "abc",
        "metrics": {"match_rate": 1.0, "correct_abstention_rate": 1.0},
        "chat": {"all_ok": True},
    }
    ok, why = improve.gate_passed(base, base)
    assert ok

    regressed = json.loads(json.dumps(base))
    regressed["metrics"]["match_rate"] = 0.95
    ok, why = improve.gate_passed(regressed, base)
    assert not ok and "regression" in why

    det = json.loads(json.dumps(base))
    det["engine_sha"] = "different"
    ok, why = improve.gate_passed(det, base)
    assert not ok and "determinism" in why

    chat = json.loads(json.dumps(base))
    chat["chat"]["all_ok"] = False
    ok, why = improve.gate_passed(chat, base)
    assert not ok and "citation" in why


def test_improve_iteration_rejects_non_allowlist(clean_cwd):
    provider = _mock_provider("anything")
    r = improve.improve_iteration("engine/runner.py", "x.db", "y.json", provider,
                                  clean_cwd, baseline={})
    assert r["kept"] is False and "allowlist" in r["reason"]


def test_improve_iteration_keep_path(clean_cwd, gated_batch, monkeypatch):
    provider = _mock_provider(
        "You are the finance-ops agent. Cite record IDs. Abstain when unsure."
    )
    baseline = {
        "engine_sha": improve.engine_hash(gated_batch["db"]),
        "metrics": {
            "match_rate": 1.0,
            "exception_precision": 1.0,
            "exception_recall": 1.0,
            "correct_abstention_rate": 1.0,
            "false_match_rate": 0.0,
        },
        "chat": {"all_ok": True},
    }
    monkeypatch.setattr(improve, "run_gate", lambda *a, **k: baseline)
    r = improve.improve_iteration(
        "chat_system", gated_batch["db"], gated_batch["gt"], provider, clean_cwd, baseline
    )
    assert r["kept"] is True
    assert improve.ALLOWLIST["chat_system"].read_text().startswith("You are the finance-ops")
    # logged
    lines = improve.LOG.read_text().splitlines()
    assert json.loads(lines[-1])["kept"] is True


def test_improve_iteration_revert_on_regression(clean_cwd, gated_batch, monkeypatch):
    improve.ALLOWLIST["skills"].write_text("original skills")
    provider = _mock_provider("revised skills text")
    baseline = {
        "engine_sha": "x",
        "metrics": {"match_rate": 1.0},
        "chat": {"all_ok": True},
    }
    bad_gate = {
        "engine_sha": "x",
        "metrics": {"match_rate": 0.5},
        "chat": {"all_ok": True},
    }
    monkeypatch.setattr(improve, "run_gate", lambda *a, **k: bad_gate)
    r = improve.improve_iteration(
        "skills", gated_batch["db"], gated_batch["gt"], provider, clean_cwd, baseline
    )
    assert r["kept"] is False and "regression" in r["reason"]
    assert improve.ALLOWLIST["skills"].read_text() == "original skills"  # reverted


def test_chat_probe_with_mock(clean_cwd, gated_batch):
    provider = _mock_provider("No supported answer; evidence insufficient.")
    res = improve.chat_probe(gated_batch["db"], provider)
    assert res["all_ok"] is True  # no IDs cited -> no unsupported citations
    assert all(p["n_tool_calls"] >= 0 for p in res["probes"])
