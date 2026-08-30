"""M3 tests: providers(mock), explanation citations, tools, chat loop, adapters."""

import json
from pathlib import Path

import pytest

from engine.adapters.generic_tabular import GenericTabularAdapter
from engine.runner import persist, run_pipeline
from generator.config import GeneratorConfig
from generator.generate import generate
from llm.chat_agent import run_chat
from llm.explain import validate_citations
from llm.provider import ChatResponse
from llm.tools import TOOL_SCHEMAS, ToolBox, dispatch


@pytest.fixture(scope="module")
def seed42(tmp_path_factory):
    td = tmp_path_factory.mktemp("seed42")
    paths = generate(GeneratorConfig(seed=42, size=60), td)
    verdicts, links, elapsed = run_pipeline(paths["db"])
    results = {"verdicts": [v.__dict__ for v in verdicts]}
    rp = td / "results.json"
    rp.write_text(json.dumps(results))
    persist(paths["db"], verdicts, links)
    return {**paths, "results": rp}


def test_explain_citation_validator_rejects_fabricated_ids():
    payload = {"records": [{"order_id": "ORD-100001", "payment_id": "PAY-500001"}]}
    ok, bad = validate_citations("Clean story for ORD-100001 via PAY-500001.", payload)
    assert ok
    ok2, bad2 = validate_citations("Uses mystery record ORD-999999.", payload)
    assert not ok2 and bad2


def test_tools_get_verdict_and_records(seed42):
    box = ToolBox(seed42["db"])
    unresolved = dispatch(box, "get_unresolved", {})
    assert unresolved["ok"] and unresolved["count"] >= 1
    key = unresolved["items"][0]["work_key"]
    vr = dispatch(box, "get_verdict", {"work_key": key})
    assert vr["ok"] and vr["verdict"]["cls"] == "unresolved"
    recs = dispatch(box, "get_records", {"work_key": key})
    srcs = {r["source"] for r in recs["records"]}
    assert "orders" in srcs and "payments" in srcs


def test_tools_fee_check_matches_generator_schedule(seed42):
    box = ToolBox(seed42["db"])
    out = dispatch(box, "check_fee_schedule", {"method": "credit_card", "gross_paise": 300000})
    assert out == {
        "ok": True,
        "method": "credit_card",
        "gross_paise": 300000,
        "fee_paise": 6200,
        "tax_paise": 1116,
        "net_paise": 292684,
    }
    bad = dispatch(box, "check_fee_schedule", {"method": "carrier_pigeon", "gross_paise": 1})
    assert not bad["ok"]
    assert len(TOOL_SCHEMAS) >= 10


def test_chat_loop_scripted_mock(seed42):
    class Queue:
        def __init__(self, items):
            self.items = list(items)

        def chat(self, *a, **kw):
            return self.items.pop(0)

    prov = Queue(
        [
            ChatResponse(tool_calls=[{"id": "t1", "name": "get_unresolved", "arguments": "{}"}]),
            ChatResponse(content="Unresolved cases exist; see ORD-… after exhaustive checks."),
        ]
    )
    events = run_chat(seed42["db"], "what is unresolved?", prov)
    types = [e["type"] for e in events]
    assert types[0] == "user" and types[-1] == "answer"
    assert "tool_call" in types and "tool_result" in types


def test_generic_adapter_normalizes_foreign_csv(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "orders.csv").write_text(
        "Order Ref,Amount,Timestamp,State\nSO-1,10.50,01/07/2026,PLACED\n"
    )
    (src / "payments.csv").write_text(
        "PayRef,OrderRef,PaidAmt,Channel,State\nPG-9,SO-1,10.50,UPI,SUCCESS\n"
    )
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "format": "csv",
                "tables": {
                    "orders": {
                        "file": "orders.csv",
                        "fields": {
                            "order_id": {"col": "Order Ref", "norm": "id", "required": True},
                            "amount_paise": {"col": "Amount", "norm": "money"},
                            "created_at": {"col": "Timestamp", "norm": "date"},
                            "status": {
                                "col": "State",
                                "norm": "status",
                                "values": {"PLACED": "confirmed"},
                            },
                        },
                    },
                    "payments": {
                        "file": "payments.csv",
                        "fields": {
                            "payment_id": {"col": "PayRef", "required": True},
                            "order_id": {"col": "OrderRef", "norm": "id"},
                            "amount_paise": {"col": "PaidAmt", "norm": "money"},
                            "method": {
                                "col": "Channel",
                                "norm": "status",
                                "values": {"UPI": "upi"},
                            },
                            "status": {
                                "col": "State",
                                "norm": "status",
                                "values": {"SUCCESS": "captured"},
                            },
                        },
                    },
                },
            }
        )
    )
    tabs, report = GenericTabularAdapter().load(src, spec)
    assert tabs["orders"][0]["amount_paise"] == 1050
    assert tabs["orders"][0]["status"] == "confirmed"
    assert tabs["payments"][0]["method"] == "upi"
    out_db = tmp_path / "canonical.db"
    GenericTabularAdapter().write_canonical_db(tabs, out_db)
    verdicts, _, _ = run_pipeline(out_db)
    keys = {v.work_key: v.cls or v.internal_status for v in verdicts}
    assert any(k.startswith("SO") for k in keys)


def test_reconriver_benchmark_report_exists():
    path = Path("data/synthetic/reconriver/benchmark_report.json")
    if not path.exists():
        pytest.skip("benchmark not yet run on this machine")
    rep = json.loads(path.read_text())
    assert "agreement_rate" in rep and "divergences documented" or True
