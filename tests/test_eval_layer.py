"""Eval-layer tests: outcome (Layer 1), trajectory (Layer 2), robustness (2.5)."""

import json

import pytest

from engine.runner import run_pipeline
from eval.outcome import evaluate_outcome
from eval.robustness import error_recovery_probe, pass_k, perturbation_suite
from eval.trajectory import eval_chat_trajectory, eval_engine_trajectory
from generator.config import GeneratorConfig
from generator.generate import generate


@pytest.fixture(scope="module")
def batch(tmp_path_factory):
    td = tmp_path_factory.mktemp("evaldata")
    paths = generate(GeneratorConfig(seed=42, size=60), td)
    from engine.runner import persist

    verdicts, links, _ = run_pipeline(paths["db"])
    persist(paths["db"], verdicts, links)  # needed for ToolBox-based chat tests
    return {**paths, "verdicts": [v.__dict__ for v in verdicts], "links": links}


def _td_results(db, verdicts):
    p = db.parent / "_t.json"
    p.write_text(json.dumps({"verdicts": verdicts}))
    return p


def test_outcome_metrics_on_clean_batch(batch):
    out = evaluate_outcome(batch["db"], batch["gt"], _td_results(batch["db"], batch["verdicts"]))
    assert out["unsupported_resolution_rate"] == 0.0  # engine never fabricates
    assert out["evidence_precision"] >= 0.9
    assert out["abstention_precision"] == 1.0
    assert out["decoy_hit_cases"] == 0
    assert out["fabricated_link_count"] == 0


def test_engine_trajectory_tracks_checks(batch):
    tr = eval_engine_trajectory(batch["verdicts"], batch["links"])
    assert tr["cases"] > 0
    unresolved = [c for c in tr["per_case"] if c["cls"] == "unresolved"]
    assert all(c["exhaustive_gate_ran"] for c in unresolved)


def test_chat_trajectory_state_check():
    events = [
        {"type": "user", "content": "hi"},
        {"type": "tool_call", "name": "get_unresolved", "args": {}},
        {
            "type": "tool_result",
            "name": "get_unresolved",
            "citations": ["ORD-100040", "ORD-100021"],
            "summary": "8 rows",
        },
        {"type": "answer", "content": "ORD-100040 and ORD-999999 are unresolved."},
    ]
    tr = eval_chat_trajectory(events)
    assert tr["citation_state_ok"] is False
    assert any("999999" in c for c in tr["unverified_citations"])


def test_chat_trajectory_state_ok():
    events = [
        {"type": "tool_call", "name": "get_unresolved", "args": {}},
        {
            "type": "tool_result",
            "name": "get_unresolved",
            "citations": ["ORD-100040"],
            "summary": "1 row",
        },
        {"type": "answer", "content": "There is one unresolved case: ORD-100040."},
    ]
    tr = eval_chat_trajectory(events)
    assert tr["citation_state_ok"] is True


def test_perturbation_suite_high_stability(batch):
    res = perturbation_suite(batch["db"])
    assert res["baseline_cases"] > 0
    stabilities = [m["stability"] for m in res["per_mutation"].values()]
    assert all(s == 1.0 for s in stabilities), res["per_mutation"]


def test_pass_k_deterministic_mock(batch):
    from llm.provider import ChatResponse, MockProvider

    provider = MockProvider(
        [ChatResponse(content="The unresolved case is ORD-100040.") for _ in range(3)]
    )
    res = pass_k(str(batch["db"]), "Which is unresolved?", k=3, provider=provider)
    assert res["citation_set_consistency"] == 1.0
    assert len(res["per_run"]) == 3


def test_pass_k_varied_mock(batch):
    from llm.provider import ChatResponse, MockProvider

    provider = MockProvider(
        [ChatResponse(content=f"The unresolved case is ORD-1000{i}.") for i in range(3)]
    )
    res = pass_k(str(batch["db"]), "Which unresolved?", k=3, provider=provider)
    assert len(res["per_run"]) == 3
    assert 0 <= res["citation_set_consistency"] <= 1.0


def test_error_recovery_probe_runs_with_mock(batch):
    from llm.provider import ChatResponse, MockProvider

    provider = MockProvider(
        [
            ChatResponse(
                content="I could not verify the adjustment table because the lookup failed."
            )
        ]
    )
    res = error_recovery_probe(str(batch["db"]), "any refunds?", "list_adjustments", provider)
    assert res["acknowledges_failure"] is True
    assert len(res["tool_calls"]) >= 0