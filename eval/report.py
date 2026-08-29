"""Eval report orchestrator: Layer 1 (outcome) + Layer 2 (trajectory) + Layer
2.5 (robustness) + optional live probes into one honest report (JSON + markdown).

Usage:
  uv run python -m eval.report --dbs data/synthetic/batch_seed42.db ...
  EVAL_LIVE=1 uv run python -m eval.report   # also runs chat pass^k + error probe
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from .env file (must be before any imports that might use os.getenv)
load_dotenv()

from engine.runner import run_pipeline  # noqa: E402 (needs .env loaded first)
from eval.memory_eval import memory_layer  # noqa: E402
from eval.outcome import evaluate_outcome  # noqa: E402
from eval.robustness import error_recovery_probe, pass_k, perturbation_suite  # noqa: E402
from eval.run import evaluate  # noqa: E402
from eval.trajectory import eval_engine_trajectory  # noqa: E402

LIVE = os.environ.get("EVAL_LIVE", "0") == "1"


def batch_metrics(db: Path, gt: Path, live_chat: bool = False) -> dict:
    verdicts, links, elapsed = run_pipeline(db)
    results = db.parent / f".eval_tmp_{db.stem}.json"
    results.write_text(json.dumps({"verdicts": [v.__dict__ for v in verdicts]}))
    outcome = evaluate_outcome(db, gt, results)
    base = evaluate(db, gt, results)
    engine_traj = eval_engine_trajectory([v.__dict__ for v in verdicts], links)
    perturb = perturbation_suite(db)
    gt_meta = json.loads(gt.read_text())

    row = {
        "batch": Path(db).name,
        "difficulty": gt_meta.get("difficulty", "?"),
        "transactions": base["transactions"],
        "elapsed_ms": round(elapsed * 1000, 1),
        "throughput_orders_per_sec": round(base["transactions"] / max(1e-9, elapsed), 1),
        # Layer 1 outcome
        "match_rate": base["match_rate"],
        "exception_precision": base["exception_precision"],
        "exception_recall": base["exception_recall"],
        "correct_abstention_rate": base["correct_abstention_rate"],
        "false_match_rate": base["false_match_rate"],
        "root_cause_accuracy": outcome["root_cause_accuracy"],
        "abstention_precision": outcome["abstention_precision"],
        "abstention_recall": outcome["abstention_recall"],
        "unsupported_resolution_rate": outcome["unsupported_resolution_rate"],
        "evidence_precision": outcome["evidence_precision"],
        "evidence_recall": outcome["evidence_recall"],
        "decoy_hit_cases": outcome["decoy_hit_cases"],
        "fabricated_link_count": outcome["fabricated_link_count"],
        "trap_breakdown": outcome["trap_breakdown"],
        "cause_confusion": outcome["cause_confusion"],
        # Layer 2 trajectory
        "avg_checks_per_case": engine_traj["avg_checks_per_case"],
        "max_checks": engine_traj["max_checks"],
        "unresolved_without_exhaustive": engine_traj["unresolved_without_exhaustive_gate"],
        # Layer 2.5 robustness
        "perturbation_mean_stability": round(_avg_stability(perturb), 4),
        "perturbation": perturb,
        "failed_cases": _failed_cases(outcome),
    }
    # Layer 4 memory & context (global store; skipped when absent)
    try:
        import os as _os

        from memory.store import DB_PATH, MemoryStore

        _mp = _os.environ.get("MEMORY_DB", str(DB_PATH))
        if Path(_mp).exists():
            _ms = MemoryStore(_mp)
            try:
                livep = _live_provider() if live_chat else None
                row["memory"] = memory_layer(_ms, live_provider=livep, db_path=str(db))
            finally:
                _ms.close()
    except Exception as e:  # noqa: BLE001 — memory eval must not break the report
        row["memory"] = {"error": str(e)}
    if live_chat:
        provider = _live_provider()
        if provider:
            try:
                row["chat_pass_k"] = pass_k(
                    str(db), "List the unresolved cases, citing each.", k=3, provider=provider
                )
            except Exception as e:  # noqa: BLE001
                row["chat_pass_k"] = {"error": str(e)}
            try:
                row["chat_error_recovery"] = error_recovery_probe(
                    str(db), "Are there any refunds for ORD-100001?", "list_adjustments", provider
                )
            except Exception as e:  # noqa: BLE001
                row["chat_error_recovery"] = {"error": str(e)}
        else:
            row["live"] = "no provider configured; run with EVAL_LIVE=1 and keys"
    results.unlink(missing_ok=True)
    return row


def _avg_stability(perturb: dict) -> float:
    vals = [m["stability"] for m in perturb.get("per_mutation", {}).values()]
    return sum(vals) / max(1, len(vals))


def _failed_cases(outcome: dict) -> list[dict]:
    out = []
    for c in outcome["per_case"]:
        if c["expected_class"] != c["predicted_class"] or c["decoy_hit"]:
            out.append(c)
    return out


def _live_provider():
    from llm.provider import FallbackProvider, ProviderError

    try:
        return FallbackProvider()
    except ProviderError:
        return None


def _md(rows, overall, live: bool) -> str:
    L = []
    L.append("# AI Finance Controller — Evaluation Report\n")
    L.append(f"> Auto-generated {datetime.now(UTC).isoformat(timespec='seconds')} "
             f"(live chat probes: {'ON' if live else 'OFF'}). No LLM judge — every number "
             "below is deterministic (outcome on GT, trajectory from engine logs, "
             "robustness by perturbation).\n")
    L.append("## Overall scoreboard\n")
    hdr = ["metric", *[r["batch"] for r in rows]]
    L.append("| " + " | ".join(hdr) + " |")
    L.append("| " + " | ".join(["---"] * len(hdr)) + " |")
    metric_keys = [
        "transactions", "match_rate", "root_cause_accuracy", "exception_precision",
        "exception_recall", "abstention_precision", "abstention_recall",
        "unsupported_resolution_rate", "evidence_precision", "evidence_recall",
        "fabricated_link_count", "decoy_hit_cases", "avg_checks_per_case",
        "perturbation_mean_stability", "throughput_orders_per_sec",
    ]
    for key in metric_keys:
        L.append("| " + key + " | " + " | ".join(str(r.get(key, "")) for r in rows) + " |")
    L.append("")
    L.append("## Failed cases by batch\n")
    for r in rows:
        fc = r.get("failed_cases") or []
        L.append(f"### {r['batch']} — {len(fc)} failed\n")
        if not fc:
            L.append("_none_\n")
        for c in fc:
            tags = []
            if c["expected_class"] != c["predicted_class"]:
                tags.append(f"class {c['expected_class']}->{c['predicted_class']}")
            if c["decoy_hit"]:
                tags.append("decoy hit")
            L.append(f"- `{c['work_key']}` ({c['cause']}): {', '.join(tags)}\n")
    L.append("## Method\n")
    L.append("- **Layer 1 (Outcome, deterministic):** match rate, root-cause accuracy, "
             "abstention precision/recall, unsupported-resolution rate, evidence-ID "
             "precision/recall, decoy avoidance — computed against `ground_truth.json` "
             "(`expected_links`, `decoys`, `abstention_expected`).\n")
    L.append("- **Layer 2 (Trajectory):** avg checks per case, exhaustive-gate on "
             "unresolved; chat citation-state check (cited IDs ⊆ tool-verified IDs).\n")
    L.append("- **Layer 2.5 (Robustness):** narration perturbation suite; live pass^k & "
             "error-recovery probes when EVAL_LIVE=1.\n")
    L.append("- **Layer 3 (Evidence):** llm.citations verifier (hard ID+amount, ALCE "
             "recall/precision) — see drawer `verification` block.\n")
    L.append("- **Layer 4 (Memory & Context):** grounded-memory rate (financial facts "
             "must cite verifiable IDs), retrieval hit@1 on canned queries, long-session "
             "recall probe (10 filler turns + 11th question) when EVAL_LIVE=1.\n")
    for r in rows:
        mem = r.get("memory")
        if not mem or mem.get("error"):
            continue
        g = mem.get("grounded", {})
        rt = mem.get("retrieval", {})
        L.append(f"\n### Memory layer — {r['batch']}\n")
        L.append(f"- active memories: {g.get('n', 0)}, grounded rate: {g.get('rate')}")
        L.append(f"- retrieval hit@1: {rt.get('hit_at_1')} over {rt.get('cases')} cases")
        if mem.get("long_session") and "error" not in mem.get("long_session", {}):
            L.append(f"- long-session citation discipline: "
                     f"{mem['long_session'].get('citation_state_ok')}")

    L.append("\nReferences: AgentBench (2308.03688), ToolSandbox (2408.04682), τ-bench "
             "(2406.12045), FinanceBench (2311.11944), LLM-as-a-Judge (2411.15594).\n")
    return "\n".join(L)


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv()  # .env provides LLM keys for live probes (matches api.run)
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbs", nargs="*", default=None)
    ap.add_argument("--out", default="docs/EVAL_REPORT.md")
    ap.add_argument("--json-out", default="data/synthetic/eval_report.json")
    args = ap.parse_args()

    if args.dbs:
        dbs = [Path(p) for p in args.dbs]
    else:
        dbs = [
            Path("data/synthetic/batch_seed7.db"),
            Path("data/synthetic/batch_seed42.db"),
            Path("data/synthetic/batch_seed1337.db"),
            Path("data/synthetic/batch_seed9001.db"),
            Path("data/synthetic/batch_seed9002.db"),
        ]
    rows = []
    for db in dbs:
        if not db.exists():
            print(f"skip {db.name} (missing)")
            continue
        gt = db.parent / f"ground_truth_{db.stem.removeprefix('batch_')}.json"
        if not gt.exists():
            print(f"skip {db.name} (no GT)")
            continue
        print(f"eval {db.name} ...")
        rows.append(batch_metrics(db, gt, live_chat=LIVE))
    # ReconRiver external
    rr = Path("data/synthetic/reconriver/benchmark_report.json")
    if rr.exists():
        rep = json.loads(rr.read_text())
        rows.append(
            {
                "batch": "ReconRiver(mixed-exceptions)",
                "match_rate": rep.get("agreement_rate", 0),
                "transactions": rep.get("entries_scored", None),
                "failed_cases": [],
            }
        )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "live_probes": LIVE,
        "batches": rows,
    }
    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(json.dumps(payload, indent=2, default=str))
    out_md = _md(rows, LIVE, LIVE)
    Path(args.out).write_text(out_md)
    print(f"wrote {args.out}")
    print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
