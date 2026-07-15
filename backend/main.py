"""
NTH Multilingual Voice Gateway -- FastAPI application.

Run locally:
    uvicorn main:app --reload --port 8080     (from the backend/ directory)

The gateway is fully functional offline using DEFAULT_PROVIDER=mock; configure
real Azure/GCP/AWS credentials in .env to exercise the cloud paths.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from cache.in_memory import SessionStore, TTLCache
from chat.conversation_store import ConversationStore
from chat.engine import ChatEngine
from chat_api import register_chat_routes
from config.constants import ErrorCode
from config.settings import settings
from llm import build_llm
from middlewares.metrics import metrics
from providers import build_router
from providers.provider_router import AllProvidersFailedError
from rag import build_retriever
from schemas.request_schemas import ProviderSwitchRequest, TextRequest
from schemas.response_schemas import error_envelope, success_envelope
from security.audit import AuditLog
from security.guardrails import Guardrails
from services.agent_client import AgentClient
from services.orchestrator import Orchestrator, gen_request_id
from services.translator import Translator

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION)

    # --- composition root ------------------------------------------------
    cache = TTLCache(max_entries=settings.CACHE_MAX_ENTRIES, default_ttl=settings.CACHE_TTL_SECONDS)
    router = build_router()
    translator = Translator(router, cache)
    agent = AgentClient()
    sessions = SessionStore(ttl_minutes=settings.SESSION_TTL_MINUTES)
    orchestrator = Orchestrator(router, translator, agent, sessions, cache, metrics)

    # --- Tax Agent chat stack (LLM + RAG + guardrails) -------------------
    llm = build_llm()
    retriever = build_retriever()
    audit = AuditLog()
    guardrails = Guardrails(audit=audit)
    conversations = ConversationStore()
    chat_engine = ChatEngine(llm, retriever, guardrails, conversations)

    app.state.cache = cache
    app.state.router = router
    app.state.orchestrator = orchestrator
    app.state.agent = agent
    app.state.llm = llm
    app.state.retriever = retriever
    app.state.guardrails = guardrails
    app.state.conversations = conversations
    app.state.chat_engine = chat_engine
    app.state.audit = audit

    @app.on_event("startup")
    async def _warm_local_stt() -> None:
        # Whisper's cold load takes ~20s; warm it off the event loop so the
        # first voice request is fast.  No-op when local STT is unavailable.
        from services import local_stt

        if local_stt.available():
            threading.Thread(target=local_stt.warm, daemon=True).start()

    # --- CORS ------------------------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=settings.CORS_ALLOW_METHODS,
        allow_headers=["*"],
    )

    # --- correlation-id + timing + structured logging --------------------
    @app.middleware("http")
    async def observability_mw(request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID", "cid_" + uuid.uuid4().hex[:12])
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Response-Time-ms"] = str(elapsed)
        if request.url.path.startswith("/ui"):
            # Frontend files change during the POC; force revalidation so the
            # browser never runs a stale app.js against a newer backend.
            response.headers["Cache-Control"] = "no-cache"
        if settings.ENABLE_REQUEST_LOGGING:
            # Only method/path/status/timing are logged -- never bodies -- so no
            # PII (PAN/GSTIN) or secrets leak into logs.
            print(
                f'{{"ts":"{_now_iso()}","cid":"{correlation_id}","method":"{request.method}",'
                f'"path":"{request.url.path}","status":{response.status_code},"ms":{elapsed}}}'
            )
        return response

    # --- error handlers --------------------------------------------------
    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError):
        messages = [e.get("msg", "invalid") for e in exc.errors()]
        details: dict[str, Any] = {"errors": messages}
        # Enrich the common "too long" case to match the API contract.
        for msg in messages:
            if "maximum character limit" in msg:
                details["max_chars"] = settings.MAX_TEXT_CHARS
        return JSONResponse(
            status_code=400,
            content=error_envelope(
                gen_request_id(), ErrorCode.VALIDATION_ERROR,
                messages[0] if messages else "Validation error",
                details=details,
                suggestions=["Check the request body against the API contract"],
            ),
        )

    @app.exception_handler(AllProvidersFailedError)
    async def _all_failed_handler(request: Request, exc: AllProvidersFailedError):
        metrics.record_error(ErrorCode.ALL_PROVIDERS_FAILED)
        return JSONResponse(
            status_code=502,
            content=error_envelope(
                gen_request_id(), ErrorCode.ALL_PROVIDERS_FAILED,
                "Every provider in the fallback chain failed.",
                details={"fallback_chain": exc.chain},
                category="provider_error", recoverable=True,
                suggestions=["Retry", "Check /health for provider status"],
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception):
        rid = gen_request_id()
        metrics.record_error(ErrorCode.INTERNAL_ERROR)
        print(f'{{"ts":"{_now_iso()}","level":"error","request_id":"{rid}","error":"{exc!r}"}}')
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                rid, ErrorCode.INTERNAL_ERROR,
                "An unexpected error occurred.", category="internal",
            ),
        )

    # --- routes ----------------------------------------------------------
    @app.get("/")
    async def root():
        return {
            "service": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "status": "running",
            "llm_provider": llm.name,
            "knowledge_chunks": len(retriever.store),
            "ui": "/ui/",
            "docs": "/docs",
            "endpoints": [
                "/api/chat/stream", "/api/chat/conversations", "/api/chat/regenerate",
                "/api/chat/edit", "/api/voice", "/health", "/metrics", "/admin/security",
            ],
        }

    @app.post("/api/text")
    async def api_text(req: TextRequest):
        http_status, env_status, request_id, data = await orchestrator.process_text(req)
        return JSONResponse(status_code=http_status, content=success_envelope(request_id, data, env_status))

    @app.post("/api/voice")
    async def api_voice(
        audio: UploadFile = File(...),
        provider: str | None = Form(None),
        session_id: str | None = Form(None),
        return_audio: bool = Form(False),
        mock_transcript: str | None = Form(None),
    ):
        audio_bytes = await audio.read()
        http_status, env_status, request_id, data = await orchestrator.process_voice(
            audio_bytes=audio_bytes,
            filename=audio.filename or "",
            content_type=audio.content_type or "",
            provider=provider,
            session_id=session_id,
            return_audio=return_audio,
            mock_transcript=mock_transcript,
        )
        return JSONResponse(status_code=http_status, content=success_envelope(request_id, data, env_status))

    @app.post("/api/provider/switch")
    async def api_switch(req: ProviderSwitchRequest):
        request_id = gen_request_id()
        if req.new_provider not in router.providers:
            return JSONResponse(
                status_code=400,
                content=error_envelope(
                    request_id, ErrorCode.UNKNOWN_PROVIDER,
                    f"Unknown provider '{req.new_provider}'",
                    details={"available": list(router.providers)},
                ),
            )
        previous = router.default
        health = {}
        if req.test_connectivity:
            health[req.new_provider] = await router.get(req.new_provider).health_check()
        if req.make_default:
            router.set_default(req.new_provider)
        message = (
            f"Default provider is now {req.new_provider} (was {previous})."
            if req.make_default
            else f"Selected {req.new_provider} for this session. Default remains {previous}."
        )
        return success_envelope(request_id, {
            "previous_provider": previous,
            "current_provider": req.new_provider,
            "is_default": req.make_default,
            "provider_health": health,
            "message": message,
        })

    @app.get("/health")
    async def health():
        provider_health = await router.health_all()
        llm_health = await llm.health_check()
        overall = "healthy" if llm_health.get("status") == "healthy" else "degraded"
        summary = metrics.health_summary(cache.hit_rate)
        return {
            "status": overall,
            "service": {
                "name": settings.APP_NAME, "version": settings.APP_VERSION,
                "environment": settings.ENVIRONMENT,
            },
            "timestamp": _now_iso(),
            "uptime_seconds": metrics.uptime_seconds,
            "llm": {"provider": llm.name, "configured_provider": settings.LLM_PROVIDER, **llm_health},
            "rag": {
                "status": "operational" if len(retriever.store) else "empty",
                "indexed_chunks": len(retriever.store),
            },
            "guardrails": {
                "status": "operational" if settings.ENABLE_GUARDRAILS else "disabled",
                "rate_limit_per_minute": settings.RATE_LIMIT_PER_MINUTE,
            },
            "voice_providers": provider_health,
            "agent_api": {
                "status": "healthy",
                "type": "mock" if settings.USE_MOCK_AGENT else "http",
            },
            "services": {
                "chat": "operational",
                "retrieval": "operational",
                "speech_recognition": "operational",
                "caching": "operational" if settings.ENABLE_CACHING else "disabled",
            },
            "metrics": summary,
        }

    @app.get("/metrics")
    async def get_metrics():
        return {"timestamp": _now_iso(), **metrics.snapshot(cache.stats())}

    @app.get("/metrics/prometheus")
    async def get_metrics_prom():
        return PlainTextResponse(metrics.prometheus())

    @app.get("/debug")
    async def debug():
        """Non-sensitive runtime config snapshot (secrets never included)."""
        return {
            "environment": settings.ENVIRONMENT,
            "default_provider": router.default,
            "fallback_providers": settings.FALLBACK_PROVIDERS,
            "registered_providers": list(router.providers),
            "provider_configured": {
                name: router.get(name).is_configured() for name in router.providers
            },
            "feature_flags": {
                "caching": settings.ENABLE_CACHING,
                "hinglish_normalization": settings.ENABLE_HINGLISH_NORMALIZATION,
                "text_to_speech": settings.ENABLE_TEXT_TO_SPEECH,
                "metrics": settings.ENABLE_METRICS_COLLECTION,
            },
            "limits": {
                "max_text_chars": settings.MAX_TEXT_CHARS,
                "max_audio_mb": settings.MAX_AUDIO_SIZE_MB,
                "max_audio_seconds": settings.MAX_AUDIO_DURATION_SECONDS,
            },
        }

    # Tax Agent chat routes (conversations, SSE stream, edit/regenerate, admin).
    register_chat_routes(app, chat_engine, conversations, audit)

    # Static UI (mounted last so it never shadows the API routes).
    if FRONTEND_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="ui")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=False)
