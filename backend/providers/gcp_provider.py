"""
GCP provider (Cloud Translation + Speech-to-Text).

SDKs are imported lazily.  Without ``GCP_PROJECT_ID`` / application credentials
the provider reports unhealthy and raises so the router falls back.
"""
from __future__ import annotations

import time

from config.settings import settings
from providers.base import (
    Provider,
    ProviderAuthError,
    ProviderError,
    ProviderUnavailableError,
    STTResult,
    TranslationResult,
)


class GCPProvider(Provider):
    name = "gcp"

    def is_configured(self) -> bool:
        return bool(settings.GCP_PROJECT_ID and settings.GOOGLE_APPLICATION_CREDENTIALS)

    async def translate_text(
        self, text: str, source_lang: str, target_lang: str = "en"
    ) -> TranslationResult:
        if not self.is_configured():
            raise ProviderAuthError("GCP credentials not configured", provider=self.name)
        try:
            from google.cloud import translate_v2 as translate
        except ImportError as exc:
            raise ProviderUnavailableError(
                "google-cloud-translate not installed", provider=self.name
            ) from exc
        try:
            client = translate.Client()
            result = client.translate(text, source_language=source_lang, target_language=target_lang)
            return TranslationResult(text=result["translatedText"], confidence=0.92)
        except Exception as exc:  # noqa: BLE001 - normalise SDK errors to ours
            raise ProviderUnavailableError(f"GCP Translate error: {exc}", provider=self.name)

    async def speech_to_text(
        self, audio_bytes: bytes, language_hint: str = "hi", mime: str = "audio/wav"
    ) -> STTResult:
        if not self.is_configured():
            raise ProviderAuthError("GCP credentials not configured", provider=self.name)
        try:
            from google.cloud import speech
        except ImportError as exc:
            raise ProviderUnavailableError(
                "google-cloud-speech not installed", provider=self.name
            ) from exc
        try:
            client = speech.SpeechClient()
            audio = speech.RecognitionAudio(content=audio_bytes)
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                language_code="hi-IN",
                alternative_language_codes=["en-IN", "ta-IN", "te-IN"],
                enable_automatic_punctuation=True,
            )
            response = client.recognize(config=config, audio=audio)
            if not response.results:
                raise ProviderUnavailableError("GCP STT returned no transcript", provider=self.name)
            top = response.results[0].alternatives[0]
            return STTResult(transcript=top.transcript, language=language_hint, confidence=top.confidence or 0.9)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailableError(f"GCP STT error: {exc}", provider=self.name)

    async def text_to_speech(
        self, text: str, language: str = "en-IN", voice_style: str = "neutral"
    ) -> bytes:
        if not self.is_configured():
            raise ProviderAuthError("GCP credentials not configured", provider=self.name)
        raise ProviderUnavailableError("GCP TTS not wired in the offline POC", provider=self.name)

    async def health_check(self) -> dict:
        t0 = time.perf_counter()
        if not self.is_configured():
            return {
                "status": "unhealthy",
                "auth_valid": False,
                "error": "No GCP credentials configured",
                "recommendation": "Set GCP_PROJECT_ID and GOOGLE_APPLICATION_CREDENTIALS",
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
