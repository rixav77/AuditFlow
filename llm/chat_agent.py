"""Bounded chat agent: KB + tools + streaming events + citation discipline."""

from __future__ import annotations

import json
from pathlib import Path

from llm.provider import BaseProvider
from llm.tools import TOOL_SCHEMAS, ToolBox, dispatch

KB = (Path(__file__).parent / "domain_knowledge.md").read_text()

PROMPTS_DIR = Path(__file__).parent / "prompts"
SKILLS_PATH = Path("memory/skills.md")

_DEFAULT_SYSTEM = (
    "You are the finance-ops agent of a reconciliation controller. "
    "Answer questions about transactions using ONLY tool results and the payload facts. "
    "Cite record IDs verbatim. If evidence is insufficient after your tools, say so and "
    "name what document would resolve it. Never invent amounts or causes.\n\n" + KB
)


def _load_system() -> str:
    """System prompt is externally editable (self-improvement surface).

    Priority: llm/prompts/chat_system.md -> inline default. memory/skills.md
    (learned procedural memory) is appended when present.
    """
    text = _DEFAULT_SYSTEM
    f = PROMPTS_DIR / "chat_system.md"
    if f.exists():
        text = f.read_text().strip() + "\n\n" + KB
    if SKILLS_PATH.exists():
        text += "\n\nLearned skills (background):\n" + SKILLS_PATH.read_text().strip()
    return text


SYSTEM = _load_system()

MAX_TURNS = 6
CTX_CHAR_BUDGET = 24000  # ~6K tokens for the whole message list (excl. system KB)


def _smart_history(history: list[dict], budget: int = CTX_CHAR_BUDGET) -> list[dict]:
    """Arize-style smart truncation: keep system prompt + head + tail, drop the
    middle. Tool results in the middle are re-fetchable by ID via the read-only
    tools (heavy data never needed to live in context)."""
    if sum(len(m.get("content") or "") for m in history) <= budget:
        return history
    head, tail = history[:2], history[-6:]
    dropped = len(history) - len(head) - len(tail)
    if dropped <= 0:
        # still over budget: hard-trim the largest contents
        out = []
        for m in history:
            c = m.get("content") or ""
            if len(c) > 3000:
                m = {**m, "content": c[:3000] + " …[trimmed, re-fetch with tools if needed]"}
            out.append(m)
        return out
    marker = {
        "role": "system",
        "content": f"[{dropped} earlier messages dropped to fit the context budget — "
        "their facts remain available via the read-only tools.]",
    }
    return head + [marker] + tail


def _memory_block(db_path: str | Path, user_message: str) -> str:
    """Automatic retrieval (mem0's every-turn injection). Never raises."""
    try:
        import os

        from memory.retrieve import memory_context_block
        from memory.store import DB_PATH, MemoryStore

        path = os.environ.get("MEMORY_DB", str(DB_PATH))
        if not Path(path).exists():
            return ""
        store = MemoryStore(path)
        try:
            return memory_context_block(store, user_message, top_k=3, scope=Path(db_path).name)
        finally:
            store.close()
    except Exception:  # noqa: BLE001 — memory must never break the chat
        return ""


def iter_chat(
    db_path: str | Path,
    user_message: str,
    provider: BaseProvider,
    history: list[dict] | None = None,
    session: str | None = None,
):
    """Streaming variant: yields one event dict at a time as the agent works.

    Event kinds: user → (tool_call → tool_result)* → answer.
    """
    box = ToolBox(db_path)
    sys_prompt = _load_system()
    mem = _memory_block(db_path, user_message)
    if mem:
        sys_prompt += "\n\n" + mem
    messages = [{"role": "system", "content": sys_prompt}]
    history = _smart_history(history or [])
    messages += history
    messages.append({"role": "user", "content": user_message})
    yield {"type": "user", "content": user_message}
    if mem:
        yield {"type": "memory_context", "content": mem}

    # persist the turn for future extraction (mem0's recent-messages buffer)
    if session:
        try:
            import os

            from memory.store import DB_PATH, MemoryStore

            store = MemoryStore(os.environ.get("MEMORY_DB", str(DB_PATH)))
            store.push_message(session, "user", user_message)
            store.close()
        except Exception:  # noqa: BLE001
            pass

    for _turn in range(MAX_TURNS):
        resp = provider.chat(messages, tools=TOOL_SCHEMAS)
        if resp.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": resp.content,
                    "tool_calls": [
                        {
                            "id": t["id"],
                            "type": "function",
                            "function": {"name": t["name"], "arguments": t["arguments"]},
                        }
                        for t in resp.tool_calls
                    ],
                }
                if resp.content
                else {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": t["id"],
                            "type": "function",
                            "function": {"name": t["name"], "arguments": t["arguments"]},
                        }
                        for t in resp.tool_calls
                    ],
                }
            )
            for tc in resp.tool_calls:
                try:
                    args = json.loads(tc["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = dispatch(box, tc["name"], args)
                cits = _result_citations(result)
                yield {"type": "tool_call", "name": tc["name"], "args": args}
                yield {
                    "type": "tool_result",
                    "name": tc["name"],
                    "summary": _summarize(result),
                    "citations": cits,
                }
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, default=str)[:4000],
                    }
                )
            continue

        yield {
            "type": "answer",
            "content": resp.content,
            "provider": resp.provider,
            "latency_ms": resp.latency_ms,
        }
        if session:
            try:
                import os

                from memory.ingest import ingest_llm
                from memory.store import DB_PATH, MemoryStore

                mstore = MemoryStore(os.environ.get("MEMORY_DB", str(DB_PATH)))
                try:
                    mstore.push_message(session, "assistant", resp.content)
                    ingest_llm(mstore, db_path, provider, session=session)
                finally:
                    mstore.close()
            except Exception:  # noqa: BLE001 — memory must never break the chat
                pass
        return

    yield {
        "type": "answer",
        "content": "Investigation budget exhausted without a supported answer.",
        "provider": "",
        "latency_ms": 0,
    }


def run_chat(
    db_path: str | Path,
    user_message: str,
    provider: BaseProvider,
    history: list[dict] | None = None,
) -> list[dict]:
    """Returns a list of streaming events; last one is the final answer."""
    return list(iter_chat(db_path, user_message, provider, history))


_ID_PAT = None


def _result_citations(result: dict) -> list[str]:
    """Collect record IDs (ORD-/PAY-/SET-/BANK-/ADJ-) present in a tool result so
    the trajectory layer can verify that an answer only cites tool-verified IDs
    (τ-bench principle: judge the state, not the claim)."""
    import re

    global _ID_PAT
    if _ID_PAT is None:
        _ID_PAT = re.compile(r"\b(?:ORD|PAY|SET|BANK|ADJ)[-_]?\d+\b", re.IGNORECASE)
    found: dict[str, str] = {}
    for val in result.values():
        if isinstance(result.get("citations"), list):
            for c in result["citations"]:
                found.setdefault(re.sub(r"[^A-Z0-9]", "", str(c).upper()), c)
        if isinstance(val, str):
            for m in _ID_PAT.findall(val):
                found.setdefault(re.sub(r"[^A-Z0-9]", "", m.upper()), m)
        elif isinstance(val, (int,)):
            continue
        else:
            try:
                s = json.dumps(val, default=str)
                for m in _ID_PAT.findall(s):
                    found.setdefault(re.sub(r"[^A-Z0-9]", "", m.upper()), m)
            except Exception:
                pass
    return list(found.values())


def _summarize(result: dict) -> str:
    if not result.get("ok"):
        return f"error: {result.get('error')}"
    if "items" in result:
        return f"{result['count']} rows"
    return "ok"
