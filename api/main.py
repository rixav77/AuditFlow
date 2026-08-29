"""AI Finance Controller — FastAPI app (M4).

Wires the deterministic reconciliation engine + LLM layer behind a small HTTP
surface. Every metric/record served here comes from engine artifacts (verdicts
tables, results JSON, ground truth) — nothing is fabricated at the API layer.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()  # .env provides LLM keys for the chat/explanation endpoints

import api.routers as routers  # noqa: E402
from api.exceptions import ServiceException, service_exception_handler  # noqa: E402
from api.session import SessionDB  # noqa: E402

STATIC_DIR = Path("web/static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="AI Finance Controller API", version="M4.1", lifespan=lifespan)

# CORS for the Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Run history (audit of API-triggered pipeline runs)
session_db = SessionDB("data/api_sessions.db")

# Optional static hosting of a built dashboard (web/static/dist) if present
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(routers.batches.router)
app.include_router(routers.transactions.router)
app.include_router(routers.exceptions.router)
app.include_router(routers.chat.router)


@app.exception_handler(ServiceException)
async def _service_error(request: Request, exc: ServiceException):
    return await service_exception_handler(request, exc)


@app.get("/api/health")
async def health():
    import time

    return {"status": "ok", "ts": time.time()}


@app.get("/api/system-info")
async def system_info():
    return {
        "name": "AI Finance Controller",
        "version": "M4.1",
        "track": "Razorpay Buildathon 04 — AI Finance Controller",
        "features": [
            "batch_list",
            "deterministic_pipeline_run",
            "honest_metrics",
            "exception_drawer",
            "chat_streaming",
        ],
        "endpoints": [
            "GET  /api/health",
            "GET  /api/system-info",
            "GET  /api/batches",
            "POST /api/batches/{batch_name}/run",
            "GET  /api/batches/{batch_name}/metrics",
            "GET  /api/batches/{batch_name}/manifest",
            "GET  /api/batches/{batch_name}/download",
            "GET  /api/batches/runs/history",
            "GET  /api/transactions",
            "GET  /api/transactions/{work_key}",
            "GET  /api/exceptions",
            "GET  /api/exceptions/{work_key}/drawer",
            "POST /api/chat/{batch_name}",
        ],
    }
