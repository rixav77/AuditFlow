#!/usr/bin/env python3
"""Entry point: uv run python -m api.run (serves on :8000)."""

import uvicorn

if __name__ == "__main__":
    print("Starting AI Finance Controller API (M4.1)...")
    print("Docs: http://localhost:8000/docs")
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False, access_log=True)
