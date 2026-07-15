"""
Tax Agent chat HTTP surface: conversations CRUD, SSE streaming, edit/regenerate,
and admin monitoring. Registered onto the FastAPI app by main.create_app().
"""
from __future__ import annotations

import random
import uuid
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from chat.attachments import AttachmentError, extract, fold_into_prompt
from chat.engine import ChatEngine
from chat.conversation_store import ConversationStore
from security.audit import AuditLog

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # disable proxy buffering so tokens flush live
}


def _client_key(request: Request) -> str:
    return request.headers.get("X-Client-Id") or (request.client.host if request.client else "anon")


def _corr() -> str:
    return "cid_" + uuid.uuid4().hex[:12]


def register_chat_routes(
    app: FastAPI,
    engine: ChatEngine,
    store: ConversationStore,
    audit: AuditLog,
) -> None:
    # ---- Conversations CRUD ------------------------------------------ #
    @app.post("/api/chat/conversations")
    async def create_conversation():
        conv = store.create()
        return {"id": conv.id, "title": conv.title, "created_at": conv.created_at}

    @app.get("/api/chat/conversations")
    async def list_conversations():
        return {"conversations": store.list()}

    @app.get("/api/chat/conversations/{conv_id}")
    async def get_conversation(conv_id: str):
        conv = store.get(conv_id)
        if not conv:
            return JSONResponse(status_code=404, content={"error": "conversation not found"})
        return conv.to_dict()

    @app.delete("/api/chat/conversations/{conv_id}")
    async def delete_conversation(conv_id: str):
        return {"deleted": store.delete(conv_id)}

    # ---- Streaming chat ---------------------------------------------- #
    @app.post("/api/chat/stream")
    async def chat_stream(
        request: Request,
        message: str = Form(...),
        conversation_id: str | None = Form(None),
        files: list[UploadFile] = File(default=[]),
    ):
        conv = store.get(conversation_id) if conversation_id else None
        if conv is None:
            conv = store.create()

        # Extract attachments (best-effort; a bad file becomes an error event).
        extracted, meta, att_error = [], [], None
        for up in files or []:
            try:
                data = await up.read()
                if not data:
                    continue
                ex = extract(up.filename or "file", data, up.content_type or "")
                extracted.append(ex)
                meta.append({"filename": ex.filename, "kind": ex.kind, "chars": ex.chars})
            except AttachmentError as e:
                att_error = str(e)

        prompt = fold_into_prompt(message, extracted)

        async def event_source():
            if att_error:
                import json
                yield f"data: {json.dumps({'type': 'notice', 'text': att_error, 'category': 'attachment'})}\n\n"
            yield f"data: {{\"type\": \"conversation\", \"id\": \"{conv.id}\"}}\n\n"
            async for chunk in engine.stream_reply(
                conv.id, message,
                client_key=_client_key(request), correlation_id=_corr(),
                attachments_meta=meta, prompt_text=prompt,
            ):
                yield chunk

        return StreamingResponse(event_source(), media_type="text/event-stream", headers=_SSE_HEADERS)

    @app.post("/api/chat/regenerate")
    async def chat_regenerate(
        request: Request,
        conversation_id: str = Form(...),
        message_id: str = Form(...),
    ):
        conv = store.get(conversation_id)
        if not conv:
            return JSONResponse(status_code=404, content={"error": "conversation not found"})
        # Find the user message preceding the assistant message being regenerated.
        idx = next((i for i, m in enumerate(conv.messages) if m.id == message_id), None)
        if idx is None:
            return JSONResponse(status_code=404, content={"error": "message not found"})
        user_msg = next((conv.messages[i] for i in range(idx - 1, -1, -1) if conv.messages[i].role == "user"), None)
        if user_msg is None:
            return JSONResponse(status_code=400, content={"error": "no user turn to regenerate from"})
        store.truncate_after(conversation_id, message_id, inclusive=True)

        async def event_source():
            async for chunk in engine.stream_reply(
                conversation_id, user_msg.content,
                client_key=_client_key(request), correlation_id=_corr(),
                persist_user=False, regenerate_seed=random.randint(1, 10_000),
            ):
                yield chunk

        return StreamingResponse(event_source(), media_type="text/event-stream", headers=_SSE_HEADERS)

    @app.post("/api/chat/edit")
    async def chat_edit(
        request: Request,
        conversation_id: str = Form(...),
        message_id: str = Form(...),
        message: str = Form(...),
    ):
        conv = store.get(conversation_id)
        if not conv:
            return JSONResponse(status_code=404, content={"error": "conversation not found"})
        # Keep everything before the edited user message, then re-ask.
        store.truncate_after(conversation_id, message_id, inclusive=True)

        async def event_source():
            async for chunk in engine.stream_reply(
                conversation_id, message,
                client_key=_client_key(request), correlation_id=_corr(),
            ):
                yield chunk

        return StreamingResponse(event_source(), media_type="text/event-stream", headers=_SSE_HEADERS)

    # ---- Admin / enterprise monitoring ------------------------------- #
    @app.get("/admin/security")
    async def admin_security():
        return {
            "recent_security_events": audit.security_events(limit=50),
            "recent_activity": audit.recent(limit=30),
        }
