"""Exception endpoints: the honest exception list + per-case evidence drawer."""

from __future__ import annotations

from fastapi import APIRouter, Query

from api import service
from api.exceptions import NotFoundException

router = APIRouter(prefix="/api/exceptions", tags=["exceptions"])


@router.get("")
def list_exceptions(
    batch_name: str | None = Query(None, description="Batch file name; defaults to most recent"),
):
    """The honest exception list: every case the controller could not cleanly match.
    These are the cases a human must review — never hidden, never tuned away."""
    db_path = service.batch_path(batch_name) if batch_name else service.default_batch()
    rows = service.load_verdict_rows(db_path)
    exceptions = [
        r for r in rows if not r["internal_status"] and r["cls"] in service.EXCEPTION_CLASSES
    ]
    return {
        "batch_name": db_path.name,
        "count": len(exceptions),
        "exceptions": sorted(exceptions, key=lambda r: r["work_key"]),
    }


@router.get("/{work_key}/drawer")
def exception_drawer(
    work_key: str,
    batch_name: str | None = Query(None),
    llm: bool = Query(
        False, description="Add an LLM narrative (citation-validated, deterministic fallback)"
    ),
):
    """Evidence drawer for one exception: verdict, findings, member records, and
    an explanation. Facts are deterministic; the LLM only words the narrative and
    is citation-validated (it cannot invent record IDs or amounts)."""
    db_path = service.find_batch_with_key(work_key, batch_name)
    provider = None
    if llm:
        from llm.provider import FallbackProvider, ProviderError

        try:
            provider = FallbackProvider()
        except ProviderError:
            provider = None  # honest fallback: deterministic narrative
    from engine.explain import explain_exception

    result = explain_exception(db_path, work_key, provider=provider)
    if result is None:
        raise NotFoundException(f"work_key {work_key} not found in {db_path.name}")
    return result
