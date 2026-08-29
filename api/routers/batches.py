"""Batch endpoints: list, run (deterministic pipeline), metrics, download."""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

from api import service
from api.exceptions import NotFoundException, ServiceException
from api.session import SessionDB

router = APIRouter(prefix="/api/batches", tags=["batches"])


def _session_db() -> SessionDB:
    from api.main import session_db

    return session_db


@router.get("/eval-report")
def eval_report():
    """Standing eval report (docs/EVAL_REPORT.md data source)."""
    import json
    from pathlib import Path

    p = Path("data/synthetic/eval_report.json")
    if not p.exists():
        raise NotFoundException("eval report not generated; run `uv run python -m eval.report`")
    return JSONResponse(json.loads(p.read_text()))


@router.get("")
def list_all_batches():
    batches = service.list_batches()
    return {"batches": batches, "total": len(batches)}


@router.post("/{batch_name}/run")
def run_batch(batch_name: str):
    """Run the deterministic pipeline on a batch (sync — it is fast), persist
    verdicts into the batch db, record the run, and return the class mix."""
    db_path = service.batch_path(batch_name)
    try:
        verdict_dicts, elapsed = service.run_and_persist(db_path)
    except Exception as e:  # noqa: BLE001 — record then re-raise
        _session_db().record_run(db_path.name, service.seed_of(db_path), "failed", error=str(e))
        raise ServiceException(f"pipeline failed: {e}", "PIPELINE_ERROR") from e
    scored = [v for v in verdict_dicts if not v.get("internal_status")]
    mix = dict(Counter(v["cls"] for v in scored if v["cls"]))
    run_id = _session_db().record_run(
        db_path.name,
        service.seed_of(db_path),
        "completed",
        elapsed_ms=round(elapsed * 1000, 1),
        class_mix=mix,
    )
    return {
        "run_id": run_id,
        "batch_name": db_path.name,
        "seed": service.seed_of(db_path),
        "elapsed_ms": round(elapsed * 1000, 1),
        "scored": len(scored),
        "class_mix": mix,
    }


@router.get("/{batch_name}/metrics")
def batch_metrics(batch_name: str):
    """Honest metrics for a batch: throughput, class mix, and — when ground
    truth exists — the full eval report (match rate, exception P/R, abstention)."""
    return service.metrics_for_batch(batch_name)


@router.get("/{batch_name}/download")
def download_batch(batch_name: str):
    db_path = service.batch_path(batch_name)
    return FileResponse(
        db_path,
        media_type="application/x-sqlite3",
        filename=db_path.name,
    )


@router.get("/{batch_name}/manifest")
def batch_manifest(batch_name: str):
    db_path = service.batch_path(batch_name)
    manifest = db_path.parent / f"manifest_{service.seed_of(db_path)}.json"
    if not manifest.exists():
        raise NotFoundException(f"no manifest for {db_path.name}")
    import json

    return json.loads(manifest.read_text())


@router.get("/runs/history")
def run_history(limit: int = Query(20, ge=1, le=200)):
    return {"runs": _session_db().list_runs(limit)}


@router.post("/{batch_name}/citation-audit")
def citation_audit(
    batch_name: str,
    limit: int = Query(10, ge=1, le=50),
    llm: bool = Query(False, description="Generate LLM narratives (verified); else deterministic"),
):
    """Aggregate citation-verifier stats across exception narratives (ALCE-style
    recall/precision). Bounded: at most `limit` cases, deterministic by default."""
    db_path = service.batch_path(batch_name)
    rows = [
        r
        for r in service.load_verdict_rows(db_path)
        if not r["internal_status"] and r["cls"] in service.EXCEPTION_CLASSES
    ]
    cases = sorted(rows, key=lambda r: r["work_key"])[:limit]

    provider = None
    if llm:
        from llm.provider import FallbackProvider, ProviderError

        try:
            provider = FallbackProvider()
        except ProviderError:
            provider = None  # audit proceeds on deterministic narratives

    from engine.explain import explain_exception

    per_case = []
    for r in cases:
        res = explain_exception(db_path, r["work_key"], provider=provider)
        if res is None:
            continue
        v = res["verification"]
        per_case.append(
            {
                "work_key": r["work_key"],
                "verified": v["verified"],
                "fully_supported": v["fully_supported"],
                "citation_recall": v["citation_recall"],
                "citation_precision": v["citation_precision"],
                "source": res["explanation_source"],
            }
        )
    recalls = [c["citation_recall"] for c in per_case if c["citation_recall"] is not None]
    precisions = [c["citation_precision"] for c in per_case if c["citation_precision"] is not None]
    return {
        "batch_name": db_path.name,
        "narrative_source": "llm" if provider else "deterministic",
        "cases": len(per_case),
        "mean_citation_recall": round(sum(recalls) / len(recalls), 4) if recalls else None,
        "mean_citation_precision": round(sum(precisions) / len(precisions), 4)
        if precisions
        else None,
        "hard_error_cases": sum(1 for c in per_case if not c["verified"]),
        "fully_supported_cases": sum(1 for c in per_case if c["fully_supported"]),
        "fallback_cases": sum(1 for c in per_case if c["source"] == "deterministic_fallback"),
        "per_case": per_case,
    }
