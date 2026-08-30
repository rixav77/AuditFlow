"""LLM provider verification: list models + tiny live chat ping per configured key.

Standing artifact (see docs/DATA.md §3 philosophy). Run before demos:

    uv run python scripts/verify_providers.py

Reports, per provider: key present?, model from env, model list (top few),
one minimal chat call with latency, and the exact failure if any (401 = bad key,
402/429 = quota/credits, 404 = model name stale).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

PROVIDERS = {
    "openai": {
        "base": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "default_model": "gpt-4.1-mini",
        "models_path": "/models",
        "auth": "bearer",
    },
    "anthropic": {
        "base": "https://api.anthropic.com/v1",
        "key_env": "ANTHROPIC_API_KEY",
        "model_env": "ANTHROPIC_MODEL",
        "default_model": "claude-sonnet-4-5",
        "models_path": "/models",
        "auth": "x-api-key",
    },
    "openrouter": {
        "base": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "model_env": "OPENROUTER_MODEL",
        "default_model": "google/gemini-2.5-flash",
        "models_path": "/models",
        "auth": "bearer",
    },
    "gemini": {
        "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_env": "GEMINI_API_KEY",
        "model_env": "GEMINI_MODEL",
        "default_model": "gemini-2.0-flash",
        "models_path": None,  # listed via native endpoint below
        "auth": "bearer",
    },
}


def _headers(name: str, key: str) -> dict:
    if PROVIDERS[name]["auth"] == "x-api-key":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key}"}


def list_models(name: str, key: str) -> list[str]:
    cfg = PROVIDERS[name]
    out: list[str] = []
    if name == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        r = httpx.get(url, params={"key": key}, timeout=20)
        if r.status_code == 200:
            out = [m["name"].split("/")[-1] for m in r.json().get("models", [])]
        else:
            out = [f"(models list HTTP {r.status_code})"]
    elif cfg["models_path"]:
        r = httpx.get(cfg["base"] + cfg["models_path"], headers=_headers(name, key), timeout=20)
        if r.status_code == 200:
            data = r.json()
            rows = data.get("data", data.get("models", []))
            out = [m.get("id") or m.get("name", "?") for m in rows]
        else:
            out = [f"(models list HTTP {r.status_code})"]
    return out


def chat_ping(name: str, key: str, model: str) -> tuple[bool, int, str]:
    """Returns (ok, latency_ms, detail)."""
    cfg = PROVIDERS[name]
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
        "max_tokens": 32,
    }
    t0 = time.time()
    try:
        r = httpx.post(
            cfg["base"] + "/chat/completions",
            headers=_headers(name, key),
            json=body,
            timeout=45,
        )
    except Exception as e:
        return False, int((time.time() - t0) * 1000), f"connection error: {e}"
    ms = int((time.time() - t0) * 1000)
    if r.status_code == 200:
        msg = r.json()["choices"][0].get("message", {})
        content = (msg.get("content") or "").strip()
        detail = f"replied {content[:24]!r}" if content else "replied (empty content, endpoint OK)"
        return True, ms, detail
    detail = r.text[:160].replace("\n", " ")
    return False, ms, f"HTTP {r.status_code}: {detail}"


def main() -> int:
    header = f"{'provider':<12} {'key':<6} {'model':<26} {'ping':<6} {'ms':>6}  detail"
    print(header)
    print("-" * 100)
    any_ok = False
    for name, cfg in PROVIDERS.items():
        key = os.environ.get(cfg["key_env"], "").strip()
        if not key:
            print(f"{name:<12} {'MISSING':<6} {'-':<28} {'skip':<6} {'-':>6}  no key in .env")
            continue
        model = os.environ.get(cfg["model_env"], "").strip() or cfg["default_model"]
        models = list_models(name, key)
        if models and not models[0].startswith("("):
            hit = "yes" if model in models else "NO"
            preview = ", ".join(models[:3])
            print(f"{'':<12} {'':<6} models listed: {len(models)} | env model listed: {hit}")
            print(f"{'':<12} {'':<6} e.g. {preview}{' …' if len(models) > 3 else ''}")
        ok, ms, detail = chat_ping(name, key, model)
        any_ok = any_ok or ok
        print(
            f"{name:<12} {'SET':<6} {model:<28} {'OK' if ok else 'FAIL':<6} {ms:>6}  {detail}"
        )
        print()
    verdict_msg = (
        "at least one provider usable" if any_ok else "NO usable provider - fix keys/models"
    )
    print("RESULT:", verdict_msg)
    return 0 if any_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
