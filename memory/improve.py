"""Self-improvement harness (AutoAgent/Karpathy auto-research pattern, fenced).

The meta agent may ONLY edit markdown files on an explicit allowlist:
  - llm/prompts/chat_system.md   (chat system prompt)
  - memory/skills.md             (learned procedural skills)
The deterministic engine, tools, and verifier code are NEVER editable.

Loop per iteration (Karpathy's keep-or-revert):
  1. LLM proposes replacement content for the target file
  2. GATE (all deterministic, no LLM judge):
     a. engine determinism: verdict sha256 on the gate batch unchanged
     b. outcome metrics: no metric worse than baseline
     c. chat discipline probe: run_chat on fixed questions ->
        eval_chat_trajectory.citation_state_ok must hold (tau-bench state check)
  3. keep on pass, revert on fail; every iteration logged to
     data/traces/improvement_log.jsonl (reproducible per principle 4)
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

ALLOWLIST = {
    "chat_system": Path("llm/prompts/chat_system.md"),
    "skills": Path("memory/skills.md"),
}

LOG = Path("data/traces/improvement_log.jsonl")

PROPOSE_SYSTEM = """You are improving the system prompt of a finance reconciliation chat agent.
Rules for the agent: facts come only from tool results, cite record IDs verbatim,
abstain explicitly when evidence is insufficient, never invent amounts or causes.
Return ONLY the full new prompt text (no code fences, no commentary).
Improve clarity or tool-usage guidance; never weaken the honesty rules."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def snapshot() -> dict[str, str]:
    return {k: (p.read_text() if p.exists() else "") for k, p in ALLOWLIST.items()}


def restore(snap: dict[str, str]) -> None:
    for k, text in snap.items():
        p = ALLOWLIST[k]
        if text:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        elif p.exists():
            p.unlink()


def engine_hash(db_path: str | Path) -> str:
    from engine.runner import run_pipeline

    verdicts, _, _ = run_pipeline(db_path)
    blob = json.dumps([v.__dict__ for v in verdicts], sort_keys=True, default=str)
    return _sha(blob)


def outcome_metrics(db_path: str | Path, gt_path: str | Path, results_file: Path) -> dict:
    from engine.runner import run_pipeline
    from eval.run import evaluate

    verdicts, _, _ = run_pipeline(db_path)
    results_file.write_text(json.dumps({"verdicts": [v.__dict__ for v in verdicts]}))
    base = evaluate(db_path, gt_path, results_file)
    return {
        "match_rate": base["match_rate"],
        "exception_precision": base["exception_precision"],
        "exception_recall": base["exception_recall"],
        "correct_abstention_rate": base["correct_abstention_rate"],
        "false_match_rate": base["false_match_rate"],
    }


def chat_probe(db_path: str | Path, provider, questions: list[str] | None = None) -> dict:
    """Run the bounded chat agent on fixed questions; verify citation discipline."""
    from eval.trajectory import eval_chat_trajectory
    from llm.chat_agent import run_chat

    questions = questions or [
        "How many transactions did not reconcile cleanly? Cite them.",
        "What is the class mix of this batch?",
    ]
    out = []
    for q in questions:
        events = run_chat(db_path, q, provider)
        traj = eval_chat_trajectory(events)
        out.append(
            {
                "question": q,
                "citation_state_ok": traj["citation_state_ok"],
                "n_tool_calls": traj["n_tool_calls"],
            }
        )
    return {"probes": out, "all_ok": all(p["citation_state_ok"] for p in out)}


def run_gate(db_path: str | Path, gt_path: str | Path, provider, tmp_dir: Path) -> dict:
    rf = tmp_dir / "gate_results.json"
    return {
        "engine_sha": engine_hash(db_path),
        "metrics": outcome_metrics(db_path, gt_path, rf),
        "chat": chat_probe(db_path, provider),
    }


def gate_passed(gate: dict, baseline: dict) -> tuple[bool, str]:
    if gate["engine_sha"] != baseline["engine_sha"]:
        return False, "engine determinism broken"
    for m, v in baseline["metrics"].items():
        if gate["metrics"].get(m, 0) < v - 1e-9:
            return False, f"metric regression: {m} {gate['metrics'].get(m)} < {v}"
    if not gate["chat"]["all_ok"]:
        return False, "chat citation-state probe failed"
    return True, "ok"


def log_iteration(record: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(LOG, "a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def propose(target: str, provider, hint: str = "") -> str | None:
    """LLM proposes new content for an allowlisted file."""
    p = ALLOWLIST[target]
    current = p.read_text() if p.exists() else "(no file yet — write the first version)"
    messages = [
        {"role": "system", "content": PROPOSE_SYSTEM},
        {
            "role": "user",
            "content": json.dumps({"target": str(p), "current": current, "hint": hint}),
        },
    ]
    resp = provider.chat(messages, tools=None, temperature=0.3)
    text = resp.content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("markdown"):
            text = text[len("markdown") :].strip()
    return text or None


def improve_iteration(
    target: str, db_path, gt_path, provider, tmp_dir: Path, baseline: dict, hint: str = ""
) -> dict:
    """One keep-or-revert cycle on one allowlisted file."""
    if target not in ALLOWLIST:
        return {"kept": False, "reason": f"{target} not in allowlist"}
    snap = snapshot()
    new_text = propose(target, provider, hint)
    if not new_text:
        return {"kept": False, "reason": "empty proposal"}
    p = ALLOWLIST[target]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(new_text)
    try:
        gate = run_gate(db_path, gt_path, provider, tmp_dir)
        ok, why = gate_passed(gate, baseline)
    except Exception as e:  # noqa: BLE001
        ok, why, gate = False, f"gate error: {e}", {}
    if not ok:
        restore(snap)
    record = {
        "target": target,
        "kept": ok,
        "reason": why,
        "sha_new": _sha(new_text),
        "gate": gate if ok else {"why": why},
    }
    log_iteration(record)
    return record


def auto_improve(
    db_path, gt_path, provider, tmp_dir: Path, iterations: int = 3, hint: str = ""
) -> dict:
    """Run N iterations; baseline captured once at the start."""
    baseline = run_gate(db_path, gt_path, provider, tmp_dir)
    results = []
    for i in range(iterations):
        target = "chat_system" if i % 2 == 0 else "skills"
        r = improve_iteration(target, db_path, gt_path, provider, tmp_dir, baseline, hint)
        r["iteration"] = i
        results.append(r)
    kept = sum(1 for r in results if r["kept"])
    return {"baseline_sha": baseline["engine_sha"], "iterations": results, "kept": kept}
