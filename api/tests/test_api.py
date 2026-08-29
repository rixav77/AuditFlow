"""API tests — run against an isolated tmp batch (copies of seed42 + its GT)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import service
from api.main import app
from api.routers import chat as chat_router
from llm.provider import ChatResponse, MockProvider

client = TestClient(app)


@pytest.fixture()
def batch_dir(tmp_path: Path, monkeypatch) -> Path:
    """Isolated BATCH_DIR with a seed42 copy (no pre-baked results file) + GT."""
    shutil.copy(Path("data/synthetic/batch_seed42.db"), tmp_path / "batch_test.db")
    shutil.copy(
        Path("data/synthetic/ground_truth_seed42.json"), tmp_path / "ground_truth_test.json"
    )
    monkeypatch.setattr(service, "BATCH_DIR", tmp_path)
    return tmp_path


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_system_info():
    r = client.get("/api/system-info")
    assert r.status_code == 200
    assert r.json()["name"] == "AI Finance Controller"


def test_list_batches(batch_dir):
    r = client.get("/api/batches")
    assert r.status_code == 200
    body = r.json()
    names = [b["batch_name"] for b in body["batches"]]
    assert "batch_test.db" in names
    entry = next(b for b in body["batches"] if b["batch_name"] == "batch_test.db")
    assert entry["row_counts"]["orders"] > 0
    assert entry["has_ground_truth"] is True
    assert entry["has_results"] is False  # nothing pre-baked in the tmp dir


def test_run_then_metrics(batch_dir):
    r = client.post("/api/batches/batch_test.db/run")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] if "status" in body else True
    assert body["scored"] > 0
    assert body["elapsed_ms"] < 10_000

    m = client.get("/api/batches/batch_test.db/metrics").json()
    assert m["transactions"] > 0
    assert m["throughput_orders_per_sec"] > 0
    # seed42 is the in-distribution batch: honest metrics are perfect
    assert m["eval"]["match_rate"] == 1.0
    assert m["eval"]["false_match_rate"] == 0.0
    assert m["eval"]["correct_abstention_rate"] == 1.0
    assert len(m["honest_exception_list"]) > 0


def test_transactions_pagination_and_filter(batch_dir):
    client.post("/api/batches/batch_test.db/run")
    r = client.get("/api/transactions?batch_name=batch_test.db&page_size=10")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 10
    assert body["pagination"]["total"] > 10

    r2 = client.get("/api/transactions?batch_name=batch_test.db&cls=unresolved")
    items = r2.json()["items"]
    assert items and all(i["cls"] == "unresolved" for i in items)


def test_exceptions_list_and_drawer(batch_dir):
    client.post("/api/batches/batch_test.db/run")
    ex = client.get("/api/exceptions?batch_name=batch_test.db").json()
    assert ex["count"] > 0
    wk = ex["exceptions"][0]["work_key"]

    d = client.get(f"/api/exceptions/{wk}/drawer?batch_name=batch_test.db").json()
    assert d["work_key"] == wk
    assert d["verdict"]["cls"] in {"genuine_discrepancy", "unresolved", "data_quality"}
    assert d["explanation_source"] == "deterministic"
    assert isinstance(d["records"], list)


def test_drawer_llm_path_uses_fallback_without_keys(batch_dir, monkeypatch):
    client.post("/api/batches/batch_test.db/run")
    ex = client.get("/api/exceptions?batch_name=batch_test.db").json()
    wk = ex["exceptions"][0]["work_key"]

    for key in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    d = client.get(f"/api/exceptions/{wk}/drawer?batch_name=batch_test.db&llm=true").json()
    assert d["explanation_source"] in {"deterministic", "deterministic_fallback", "llm"}
    assert wk in d["explanation"]
    assert d["verification"]["verified"] is True


def test_drawer_verification_block_present(batch_dir):
    client.post("/api/batches/batch_test.db/run")
    ex = client.get("/api/exceptions?batch_name=batch_test.db").json()
    wk = ex["exceptions"][0]["work_key"]
    d = client.get(f"/api/exceptions/{wk}/drawer?batch_name=batch_test.db").json()
    v = d["verification"]
    assert v["verifier"] == "llm.citations.v1"
    assert v["verified"] is True
    assert v["source"] == "deterministic"
    assert v["citation_recall"] == 1.0


def test_citation_audit_deterministic(batch_dir):
    client.post("/api/batches/batch_test.db/run")
    r = client.post("/api/batches/batch_test.db/citation-audit?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert body["cases"] == 5
    assert body["narrative_source"] == "deterministic"
    assert body["hard_error_cases"] == 0
    assert body["mean_citation_recall"] == 1.0


def test_citation_audit_llm_path_aggregates(batch_dir, monkeypatch):
    client.post("/api/batches/batch_test.db/run")

    # no keys -> endpoint must degrade to deterministic narratives
    for key in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    r = client.post("/api/batches/batch_test.db/citation-audit?limit=3&llm=true")
    assert r.status_code == 200
    body = r.json()
    assert body["cases"] == 3
    assert body["narrative_source"] == "deterministic"  # degraded honestly
    assert "mean_citation_recall" in body


def test_citation_audit_llm_with_mock_provider(batch_dir, monkeypatch):
    client.post("/api/batches/batch_test.db/run")
    from llm import provider as prov_mod

    # a mock provider that always answers hard-clean (no IDs/amounts to break)
    class AlwaysClean:
        name = "mock"

        def chat(self, messages, tools=None, temperature=0.2):
            return ChatResponse(content="Verified summary: all checks ran, evidence insufficient.")

    monkeypatch.setattr(prov_mod, "FallbackProvider", AlwaysClean)
    r = client.post("/api/batches/batch_test.db/citation-audit?limit=3&llm=true")
    body = r.json()
    assert body["narrative_source"] == "llm"
    assert body["hard_error_cases"] == 0
    assert body["cases"] == 3


def test_chat_json_with_mock_provider(batch_dir, monkeypatch):
    client.post("/api/batches/batch_test.db/run")
    script = [
        ChatResponse(
            tool_calls=[{"id": "t1", "name": "get_batch_summary", "arguments": "{}"}]
        ),
        ChatResponse(content="The batch has 68 scored transactions; 52 reconciled."),
    ]
    mock = MockProvider(script)
    monkeypatch.setattr(chat_router, "get_chat_provider", lambda: mock)
    r = client.post(
        "/api/chat/batch_test.db",
        json={"message": "Summarize the batch"},
    )
    assert r.status_code == 200
    events = r.json()["events"]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "user"
    assert "tool_call" in kinds and "tool_result" in kinds
    assert kinds[-1] == "answer"
    assert "68" in events[-1]["content"]


def test_chat_sse_stream(batch_dir, monkeypatch):
    client.post("/api/batches/batch_test.db/run")
    script = [ChatResponse(content="All matched orders reconcile to the paise.")]
    monkeypatch.setattr(chat_router, "get_chat_provider", lambda: MockProvider(script))
    with client.stream(
        "POST", "/api/chat/batch_test.db", json={"message": "hi", "format": "sse"}
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = b"".join(resp.iter_bytes()).decode()
    assert '"type": "user"' in body or '"type":"user"' in body
    assert '"type": "done"' in body or '"type":"done"' in body


def test_batch_not_found(batch_dir):
    r = client.get("/api/batches/nope.db/metrics")
    assert r.status_code == 404
    assert r.json()["type"] == "NOT_FOUND"


def test_chat_with_unknown_batch(batch_dir, monkeypatch):
    monkeypatch.setattr(chat_router, "get_chat_provider", lambda: MockProvider([]))
    r = client.post("/api/chat/ghost.db", json={"message": "hello"})
    assert r.status_code == 404
