"""LLM providers: OpenAI-compatible clients with automatic fallback + mock."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

PROVIDER_ORDER = ["openai", "openrouter", "gemini", "anthropic"]

DEFAULTS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "model": "gpt-4.1-mini",
    },
    "anthropic": {
        # Anthropic exposes an OpenAI-compatible endpoint (docs: OpenAI SDK compatibility)
        "base_url": "https://api.anthropic.com/v1",
        "key_env": "ANTHROPIC_API_KEY",
        "model_env": "ANTHROPIC_MODEL",
        "model": "claude-sonnet-4-5",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "model_env": "OPENROUTER_MODEL",
        "model": "google/gemini-2.5-flash",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_env": "GEMINI_API_KEY",
        "model_env": "GEMINI_MODEL",
        "model": "gemini-3.6-flash",
    },
}


@dataclass
class ChatResponse:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    provider: str = ""
    latency_ms: int = 0


class ProviderError(Exception):
    pass


class BaseProvider:
    name = "base"

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.2
    ) -> ChatResponse:
        raise NotImplementedError


class OpenAICompatProvider(BaseProvider):
    def __init__(self, name: str, base_url: str, api_key: str, model: str):
        from openai import OpenAI

        self.name = name
        self.model = model
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=60)

    def chat(self, messages, tools=None, temperature=0.2) -> ChatResponse:
        t0 = time.time()
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "700")),
        )
        if tools:
            kwargs["tools"] = tools
        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        tool_calls = []
        if getattr(choice.message, "tool_calls", None):
            for tc in choice.message.tool_calls:
                tool_calls.append(
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                )
            return ChatResponse(
                content="",
                tool_calls=tool_calls,
                usage={"total_tokens": getattr(resp.usage, "total_tokens", 0)},
                provider=self.name,
                latency_ms=int((time.time() - t0) * 1000),
            )
        return ChatResponse(
            content=choice.message.content or "",
            usage={"total_tokens": getattr(resp.usage, "total_tokens", 0)},
            provider=self.name,
            latency_ms=int((time.time() - t0) * 1000),
        )


class MockProvider(BaseProvider):
    """Scripted responses for tests/CI. Queue of ChatResponse or dicts."""

    name = "mock"

    def __init__(self, script: list[ChatResponse | dict] | None = None):
        self.script = list(script or [])
        self.calls: list[dict] = []

    def chat(self, messages, tools=None, temperature=0.2) -> ChatResponse:
        self.calls.append({"messages": messages, "tools": tools})
        if not self.script:
            return ChatResponse(content="mock: no scripted response", provider="mock")
        item = self.script.pop(0)
        return item if isinstance(item, ChatResponse) else ChatResponse(**item)


def available_providers() -> list[str]:
    out = []
    for name in PROVIDER_ORDER:
        cfg = DEFAULTS[name]
        if os.environ.get(cfg["key_env"]):
            out.append(name)
    return out


def get_provider(preferred: str | None = None) -> BaseProvider:
    preferred = preferred or os.environ.get("LLM_PROVIDER", "")
    order = [preferred] if preferred else []
    order += [p for p in PROVIDER_ORDER if p != preferred]
    last_err: Exception | None = None
    for name in order:
        cfg = DEFAULTS.get(name)
        if not cfg:
            continue
        key = os.environ.get(cfg["key_env"])
        if not key:
            continue
        model = os.environ.get(cfg["model_env"], cfg["model"])
        try:
            return OpenAICompatProvider(name, cfg["base_url"], key, model)
        except Exception as e:
            last_err = e
    raise ProviderError(f"no usable LLM provider configured: {last_err}")


class FallbackProvider(BaseProvider):
    """Primary provider with automatic failover to the next configured one."""

    def __init__(self, preferred: str | None = None):
        self.chain: list[BaseProvider] = []
        preferred = preferred or os.environ.get("LLM_PROVIDER", "")
        order = ([preferred] if preferred else []) + [p for p in PROVIDER_ORDER if p != preferred]
        for name in order:
            cfg = DEFAULTS.get(name)
            if not cfg:
                continue
            key = os.environ.get(cfg["key_env"])
            if not key:
                continue
            model = os.environ.get(cfg["model_env"], cfg["model"])
            self.chain.append(OpenAICompatProvider(name, cfg["base_url"], key, model))
        if not self.chain:
            raise ProviderError("no providers configured; set OPENROUTER_API_KEY/GEMINI_API_KEY")

    def chat(self, messages, tools=None, temperature=0.2) -> ChatResponse:
        last: Exception | None = None
        for prov in self.chain:
            for attempt in range(2):
                try:
                    return prov.chat(messages, tools, temperature)
                except Exception as e:
                    last = e
                    time.sleep(1.5 * (attempt + 1))
        raise ProviderError(f"all providers failed: {last}")
