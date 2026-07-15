"""
Request orchestration -- the business flow lives here, isolated from HTTP.

Text flow:   detect -> normalize -> (provider translate) -> pick best -> intent
             -> agent -> assemble.
Voice flow:  validate audio -> STT -> [same language pipeline] -> optional TTS.

Keeping this HTTP-free makes the whole pipeline unit-testable without a server.
"""
from __future__ import annotations

import base64
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from cache.in_memory import SessionStore, TTLCache
from config.settings import settings
from middlewares.metrics import MetricsCollector
from providers.provider_router import AllProvidersFailedError, ProviderRouter
from services.agent_client import AgentClient
from services.hinglish_normalizer import extract_intent, normalize
from services.language_detector import detect
from services.translator import Translator
from utils.audio_processor import validate_audio


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def gen_request_id() -> str:
    return "req_" + uuid.uuid4().hex[:12]


def gen_session_id() -> str:
    return "sess_" + uuid.uuid4().hex[:20]


class Orchestrator:
    def __init__(
        self,
        router: ProviderRouter,
        translator: Translator,
        agent: AgentClient,
        session_store: SessionStore,
        cache: TTLCache,
        metrics: MetricsCollector,
    ) -> None:
        self.router = router
        self.translator = translator
        self.agent = agent
        self.session_store = session_store
        self.cache = cache
        self.metrics = metrics

    # ------------------------------------------------------------------ #
    # Shared language pipeline
    # ------------------------------------------------------------------ #
    async def _analyze(self, text: str, preferred: Optional[str]) -> dict[str, Any]:
        timings: dict[str, float] = {}

        t = time.perf_counter()
        detection = detect(text)
        timings["lang_ms"] = _ms(t)

        t = time.perf_counter()
        norm = normalize(text)
        timings["norm_ms"] = _ms(t)

        need_translation = detection["is_hinglish"] or detection["code"] != "en"
        translation: Optional[dict[str, Any]] = None
        t = time.perf_counter()
        if need_translation:
            translation = await self.translator.translate(
                text, detection["code"], "en", preferred=preferred
            )
            provider_used = translation["provider"]
            provider_meta = translation["meta"]
            cache_hit = translation["cache_hit"]
        else:
            provider_used = preferred or self.router.default
            cache_hit = False
            provider_meta = {
                "provider_used": provider_used,
                "primary_provider": provider_used,
                "primary_provider_failed": False,
                "fallback_used": False,
                "fallback_chain": [
                    {"provider": provider_used, "status": "skipped", "reason": "english_no_translation"}
                ],
            }
        timings["trans_ms"] = _ms(t)

        # Quality selection: pick the higher-confidence of heuristic normalization
        # vs provider translation (ties favour the heuristic, listed first).
        candidates = [(norm["normalized_english"], norm["confidence"], norm["method"])]
        if translation is not None:
            candidates.append(
                (translation["text"], translation["confidence"], f"provider:{provider_used}")
            )
        final_text, final_conf, final_method = max(candidates, key=lambda c: c[1])

        intent = extract_intent(final_text, text)

        return {
            "detection": detection,
            "normalization": norm,
            "final_text": final_text,
            "final_confidence": final_conf,
            "final_method": final_method,
            "provider_used": provider_used,
            "provider_meta": provider_meta,
            "cache_hit": cache_hit,
            "intent": intent,
            "timings": timings,
        }

    def _normalization_block(self, analysis: dict[str, Any], include_confidence: bool) -> dict[str, Any]:
        norm = analysis["normalization"]
        block = {
            "normalized_english": analysis["final_text"],
            "method": analysis["final_method"],
            "steps_applied": norm["steps_applied"],
            "terminology_preserved": norm["terminology_preserved"],
        }
        if include_confidence:
            block["confidence"] = analysis["final_confidence"]
        if norm.get("ambiguous"):
            block["ambiguous"] = True
        return block

    def _intent_block(self, analysis: dict[str, Any], include_confidence: bool) -> dict[str, Any]:
        intent = dict(analysis["intent"])
        if not include_confidence:
            intent.pop("entity_confidence", None)
        return intent

    def _language_block(self, analysis: dict[str, Any], include_confidence: bool) -> dict[str, Any]:
        det = analysis["detection"]
        block = {
            "code": det["code"],
            "name": det["name"],
            "script": det["script"],
            "is_hinglish": det["is_hinglish"],
        }
        if include_confidence:
            block["confidence"] = det["confidence"]
        return block

    async def _run_agent(self, analysis: dict[str, Any], text: str, session_id: str) -> tuple[dict, float]:
        history = self.session_store.history(session_id)
        t = time.perf_counter()
        agent_response = await self.agent.route(
            normalized_text=analysis["final_text"],
            intent=analysis["intent"]["intent"],
            tax_type=analysis["intent"]["tax_type"],
            session_id=session_id,
            original_text=text,
            history=history,
        )
        agent_ms = _ms(t)
        self.session_store.append_turn(
            session_id,
            {
                "user_text": text,
                "normalized": analysis["final_text"],
                "intent": analysis["intent"]["intent"],
                "agent_message": agent_response.get("message", ""),
                "timestamp": _now_iso(),
            },
        )
        return agent_response, agent_ms

    # ------------------------------------------------------------------ #
    # Text
    # ------------------------------------------------------------------ #
    async def process_text(self, req: Any) -> tuple[int, str, str, dict[str, Any]]:
        """Returns ``(http_status, envelope_status, request_id, data)``."""
        request_id = gen_request_id()
        session_id = req.session_id or gen_session_id()
        t_total = time.perf_counter()

        analysis = await self._analyze(req.text, req.provider)
        agent_response, agent_ms = await self._run_agent(analysis, req.text, session_id)

        total_ms = _ms(t_total)
        tm = analysis["timings"]
        formatting_ms = round(max(0.0, total_ms - tm["lang_ms"] - tm["norm_ms"] - tm["trans_ms"] - agent_ms), 1)
        pmeta = analysis["provider_meta"]

        data: dict[str, Any] = {
            "input": {"original_text": req.text},
            "processing": {
                "normalization": self._normalization_block(analysis, req.include_confidence),
                "intent_extraction": self._intent_block(analysis, req.include_confidence),
            },
            "agent_response": agent_response,
            "metadata": {
                "provider_used": analysis["provider_used"],
                "timestamp": _now_iso(),
                "latency": {
                    "total_ms": total_ms,
                    "breakdown": {
                        "language_detection_ms": tm["lang_ms"],
                        "hinglish_normalization_ms": tm["norm_ms"],
                        "translation_ms": tm["trans_ms"],
                        "agent_routing_ms": agent_ms,
                        "response_formatting_ms": formatting_ms,
                    },
                },
                "cache_hit": analysis["cache_hit"],
                "fallback_used": pmeta.get("fallback_used", False),
            },
        }
        if req.return_original_language:
            data["input"]["language_detected"] = self._language_block(analysis, req.include_confidence)
        if pmeta.get("fallback_used"):
            data["metadata"]["fallback"] = {
                "primary_provider": pmeta.get("primary_provider"),
                "primary_provider_failed": pmeta.get("primary_provider_failed"),
                "fallback_chain": pmeta.get("fallback_chain"),
                "warning": "Primary provider unavailable; used fallback",
            }
        if req.debug_mode:
            data["debug"] = {
                "detection": analysis["detection"],
                "heuristic_normalization": analysis["normalization"],
                "provider_fallback_chain": pmeta.get("fallback_chain"),
            }

        envelope_status = "success_with_fallback" if pmeta.get("fallback_used") else "success"
        self.metrics.record_request(
            "/api/text", total_ms, language=analysis["detection"]["code"],
            provider=analysis["provider_used"], success=True,
            fallback_used=pmeta.get("fallback_used", False),
        )
        return 200, envelope_status, request_id, data

    # ------------------------------------------------------------------ #
    # Voice
    # ------------------------------------------------------------------ #
    async def process_voice(
        self,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
        provider: Optional[str],
        session_id: Optional[str],
        return_audio: bool = False,
        mock_transcript: Optional[str] = None,
    ) -> tuple[int, str, str, dict[str, Any]]:
        request_id = gen_request_id()
        session_id = session_id or gen_session_id()
        t_total = time.perf_counter()

        # 1. Validate audio
        t = time.perf_counter()
        audio_info = validate_audio(audio_bytes, filename, content_type)
        audio_ms = _ms(t)

        # 2. Speech-to-text through the resilient router.  The mock provider
        #    genuinely transcribes via local whisper when installed.  The
        #    client-side transcript (browser speech recognition) is honored
        #    only when the server could not actually hear the audio, so the
        #    reply always reflects the words spoken.
        t = time.perf_counter()
        stt_result, stt_meta = await self.router.execute(
            "speech_to_text", audio_bytes, "hi", preferred=provider
        )
        transcript = stt_result.transcript
        stt_language = stt_result.language
        alternatives = stt_result.alternatives
        stt_engine = stt_result.engine or stt_meta.get("provider_used", "")
        if (
            mock_transcript
            and stt_meta.get("provider_used") == "mock"
            and stt_result.engine != "local-whisper"
        ):
            transcript = mock_transcript
            alternatives = []
            stt_engine = "client-stt"
        stt_ms = _ms(t)

        # 3. Language pipeline on the transcript
        analysis = await self._analyze(transcript, provider)
        agent_response, agent_ms = await self._run_agent(analysis, transcript, session_id)

        # 4. Optional TTS of the agent reply
        tts_ms = 0.0
        response_audio = None
        if return_audio:
            t = time.perf_counter()
            try:
                audio_out, _ = await self.router.execute(
                    "text_to_speech", agent_response.get("message", ""), preferred=provider
                )
                response_audio = {
                    "format": "wav",
                    "data": "data:audio/wav;base64," + base64.b64encode(audio_out).decode("ascii"),
                }
            except AllProvidersFailedError:
                response_audio = None
            tts_ms = _ms(t)

        total_ms = _ms(t_total)
        tm = analysis["timings"]
        pmeta = analysis["provider_meta"]

        data: dict[str, Any] = {
            "audio_file_info": audio_info,
            "transcription": {
                "transcript": transcript,
                "stt_engine": stt_engine,
                "language_detected": self._language_block(analysis, True),
                "alternative_transcripts": alternatives,
            },
            "normalization": {
                "normalized_english": analysis["final_text"],
                "confidence": analysis["final_confidence"],
                "terminology_preserved": analysis["normalization"]["terminology_preserved"],
            },
            "intent_extraction": analysis["intent"],
            "agent_response": agent_response,
            "metadata": {
                "provider_used": analysis["provider_used"],
                "stt_provider_used": stt_meta.get("provider_used"),
                "timestamp": _now_iso(),
                "latency": {
                    "total_ms": total_ms,
                    "breakdown": {
                        "audio_validation_ms": audio_ms,
                        "speech_to_text_ms": stt_ms,
                        "language_detection_ms": tm["lang_ms"],
                        "normalization_ms": tm["norm_ms"],
                        "translation_ms": tm["trans_ms"],
                        "agent_routing_ms": agent_ms,
                        "text_to_speech_ms": tts_ms,
                    },
                },
                "fallback_used": pmeta.get("fallback_used", False),
            },
        }
        if response_audio is not None:
            data["response_audio"] = response_audio

        envelope_status = "success_with_fallback" if pmeta.get("fallback_used") else "success"
        self.metrics.record_request(
            "/api/voice", total_ms, language=analysis["detection"]["code"],
            provider=analysis["provider_used"], success=True,
            fallback_used=pmeta.get("fallback_used", False),
        )
        return 200, envelope_status, request_id, data
