"""Transaction endpoints: paginated verdict listing + single-verdict lookup."""

from __future__ import annotations

from fastapi import APIRouter, Query

from api import service
from api.exceptions import NotFoundException

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


@router.get("")
def list_transactions(
    batch_name: str | None = Query(None, description="Batch file name; defaults to most recent"),
    cls: str | None = Query(None, description="Filter by verdict class"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    return service.transactions_page(batch_name, cls, page, page_size)


@router.get("/{work_key}")
def get_transaction(work_key: str, batch_name: str | None = Query(None)):
    db_path = service.find_batch_with_key(work_key, batch_name)
    for row in service.load_verdict_rows(db_path):
        if row["work_key"] == work_key:
            return {**row, "batch_name": db_path.name}
    raise NotFoundException(f"work_key {work_key} vanished mid-request")
