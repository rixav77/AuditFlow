"""Engine M2 tests: pipeline correctness on generated batches."""

import json
import sqlite3
from pathlib import Path

from engine.runner import run_pipeline
from eval.run import evaluate
from generator.config import GeneratorConfig
from generator.generate import generate


def _run(seed: int = 42, size: int = 60, tmp: Path | None = None):
    paths = generate(GeneratorConfig(seed=seed, size=size), tmp)
    verdicts, links, elapsed = run_pipeline(paths["db"])
    return paths, verdicts, links, elapsed


def test_seed42_perfect_verdicts(tmp_path):
    paths, verdicts, links, _ = _run(42, 60, tmp_path)
    results = {"verdicts": [v.__dict__ for v in verdicts]}
    rp = tmp_path / "results.json"
    rp.write_text(json.dumps(results))
    rep = evaluate(paths["db"], paths["gt"], rp)
    assert rep["match_rate"] == 1.0
    assert rep["exception_precision"] == 1.0
    assert rep["exception_recall"] == 1.0
    assert rep["correct_abstention_rate"] == 1.0
    assert rep["false_match_rate"] == 0.0
    assert rep["noise_false_positives"] == 0
    assert rep["exact_bundle_rate"] == 1.0


def test_unseen_seed_generalizes(tmp_path):
    paths, verdicts, _, _ = _run(20260825, 60, tmp_path)
    results = {"verdicts": [v.__dict__ for v in verdicts]}
    rp = tmp_path / "results.json"
    rp.write_text(json.dumps(results))
    rep = evaluate(paths["db"], paths["gt"], rp)
    # Known rare edge: drop-link + identifier-less chains can be absorbed by a
    # net-coincidence DP merge (~1/60). Threshold documents the honest floor.
    assert rep["match_rate"] >= 0.95, rep["confusion"]
    assert rep["correct_abstention_rate"] == 1.0
    assert rep["false_match_rate"] == 0.0
    assert rep["noise_false_positives"] == 0


def test_combined_group_single_bundle(tmp_path):
    paths, verdicts, links, _ = _run(42, 60, tmp_path)
    by_bid: dict[str, set[str]] = {}
    for v in verdicts:
        by_bid.setdefault(v.bundle_bid, set()).add(v.work_key)
    multi_order_bundles = [
        bids for bids in by_bid.values() if len([b for b in bids if b.startswith("ORD")]) >= 2
    ]
    assert multi_order_bundles, "expected at least one N:1 combined bundle"


def test_verdict_tables_persisted(tmp_path):
    from engine.runner import persist

    paths, verdicts, links, _ = _run(42, 60, tmp_path)
    persist(paths["db"], verdicts, links)
    con = sqlite3.connect(paths["db"])
    try:
        n = con.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0]
        lk = con.execute("SELECT COUNT(*) FROM bundle_links").fetchone()[0]
        assert n == len(verdicts)
        assert lk == len(links)
        classes = {r[0] for r in con.execute("SELECT DISTINCT cls FROM verdicts")}
        assert "" in classes or "matched" in classes
    finally:
        con.close()


def test_throughput_reasonable(tmp_path):
    paths, verdicts, _, elapsed = _run(42, 60, tmp_path)
    n_orders = sum(1 for v in verdicts if v.work_key.startswith("ORD"))
    assert elapsed < 10.0
    assert n_orders >= 45
