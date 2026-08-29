"""Chat endpoints: bounded, tool-using, citation-disciplined agent over a batch.

POST /api/chat/{batch_name}  body {message, history?, format?="json"|"sse"}
SSE emits one `data: <json>\n\n` frame per agent event (user/tool_call/tool_result/
answer/done) so the dashboard can stream tool calls as they happen.
"""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api import service
from llm.chat_agent import iter_chat

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[dict] = Field(default_factory=list, max_length=40)
    session: str | None = Field(None, max_length=64)
    format: str = Field("json", pattern="^(json|sse)$")


def get_chat_provider():
    """Indirection point so tests can inject a MockProvider."""
    from llm.provider import FallbackProvider

    return FallbackProvider()


@router.post("/{batch_name}")
def chat(batch_name: str, req: ChatRequest):
    db_path = service.batch_path(batch_name)
    provider = get_chat_provider()
    events = iter_chat(str(db_path), req.message, provider, req.history, session=req.session)

    if req.format == "sse":

        def gen():
            for ev in events:
                yield f"data: {json.dumps(ev, default=str)}\n\n"
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(gen(), media_type="text/event-stream")

    collected = list(events)
    return {"batch_name": db_path.name, "events": collected}
