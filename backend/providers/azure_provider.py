"""
Azure provider (Cognitive Services: Translator + Speech).

Translation is implemented against the Translator REST API directly (no SDK
needed -- works as soon as a key is present).  Speech uses the Azure Speech SDK,
imported lazily so the package is only required when actually transcribing.

With no credentials configured everything degrades gracefully: ``health_check``
reports ``unhealthy`` and the call methods raise ``ProviderAuthError`` so the
router falls back to the next provider.
"""
from __future__ import annotations

import time
import uuid

import httpx

from config.settings import settings
from providers.base import (
    Provider,
    ProviderAuthError,
    ProviderError,
    ProviderUnavailableError,
    STTResult,
    TranslationResult,
)


class AzureProvider(Provider):
    name = "azure"

    def is_configured(self) -> bool:
        return bool(settings.AZURE_TRANSLATOR_KEY or settings.AZURE_SPEECH_KEY)

    def _require_translator(self) -> None:
        if not settings.AZURE_TRANSLATOR_KEY:
            raise ProviderAuthError(
                "Azure Translator key not configured", provider=self.name
            )

    async def translate_text(
        self, text: str, source_lang: str, target_lang: str = "en"
    ) -> TranslationResult:
        self._require_translator()
        url = f"{settings.AZURE_TRANSLATOR_ENDPOINT}/translate"
        params = {"api-version": "3.0", "from": source_lang, "to": target_lang}
        headers = {
            "Ocp-Apim-Subscription-Key": settings.AZURE_TRANSLATOR_KEY,
            "Ocp-Apim-Subscription-Region": settings.AZURE_TRANSLATOR_REGION,
            "Content-Type": "application/json",
            "X-ClientTraceId": str(uuid.uuid4()),
        }
        try:
            async with httpx.AsyncClient(timeout=settings.provider_timeout("azure")) as client:
                resp = await client.post(url, params=params, headers=headers, json=[{"text": text}])
            if resp.status_code in (401, 403):
                raise ProviderAuthError(f"Azure auth failed: {resp.status_code}", provider=self.name)
            resp.raise_for_status()
            payload = resp.json()
            translation = payload[0]["translations"][0]["text"]
            # Azure does not return a per-string confidence for translation; use a
            # fixed prior informed by the provider comparison matrix.
            return TranslationResult(text=translation, confidence=0.88)
        except (ProviderError,):
            raise
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"Azure Translator error: {exc}", provider=self.name)

    async def speech_to_text(
        self, audio_bytes: bytes, language_hint: str = "hi", mime: str = "audio/wav"
    ) -> STTResult:
        if not settings.AZURE_SPEECH_KEY:
            raise ProviderAuthError("Azure Speech key not configured", provider=self.name)
        try:
            import azure.cognitiveservices.speech as speechsdk  # noqa: F401
        except ImportError as exc:
            raise ProviderUnavailableError(
                "azure-cognitiveservices-speech not installed", provider=self.name
            ) from exc
        # Full push-stream recognition wiring is intentionally left for the
        # credentialed integration; the POC exercises this path via the mock.
        raise ProviderUnavailableError(
            "Azure STT requires a configured Speech resource (not wired in the offline POC)",
            provider=self.name,
        )

    async def text_to_speech(
        self, text: str, language: str = "en-IN", voice_style: str = "neutral"
    ) -> bytes:
        if not settings.AZURE_SPEECH_KEY:
            raise ProviderAuthError("Azure Speech key not configured", provider=self.name)
        raise ProviderUnavailableError("Azure TTS not wired in the offline POC", provider=self.name)

    async def health_check(self) -> dict:
        t0 = time.perf_counter()
        if not self.is_configured():
            return {
                "status": "unhealthy",
                "auth_valid": False,
                "error": "No Azure credentials configured",
                "recommendation": "Set AZURE_TRANSLATOR_KEY / AZURE_SPEECH_KEY in .env",
            }
        try:
            await self.translate_text("ping", "en", "hi")
            return {
                "status": "healthy",
                "auth_valid": True,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        except ProviderError as exc:
            return {"status": "unhealthy", "auth_valid": False, "error": str(exc)}
