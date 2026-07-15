"""
AWS provider (Translate + Transcribe).

``boto3`` is imported lazily.  Transcribe is asynchronous/S3-based in reality;
for the POC we implement Translate (synchronous) and leave Transcribe wiring for
the credentialed integration.  Without credentials the provider reports
unhealthy and raises so the router falls back.
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


class AWSProvider(Provider):
    name = "aws"

    def is_configured(self) -> bool:
        return bool(settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY)

    def _client(self, service: str):
        try:
            import boto3
        except ImportError as exc:
            raise ProviderUnavailableError("boto3 not installed", provider=self.name) from exc
        return boto3.client(
            service,
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )

    async def translate_text(
        self, text: str, source_lang: str, target_lang: str = "en"
    ) -> TranslationResult:
        if not self.is_configured():
            raise ProviderAuthError("AWS credentials not configured", provider=self.name)
        try:
            client = self._client("translate")
            # boto3 is synchronous; run it off the event loop.
            import asyncio

            resp = await asyncio.to_thread(
                client.translate_text,
                Text=text,
                SourceLanguageCode=source_lang,
                TargetLanguageCode=target_lang,
            )
            return TranslationResult(text=resp["TranslatedText"], confidence=0.85)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailableError(f"AWS Translate error: {exc}", provider=self.name)

    async def speech_to_text(
        self, audio_bytes: bytes, language_hint: str = "hi", mime: str = "audio/wav"
    ) -> STTResult:
        if not self.is_configured():
            raise ProviderAuthError("AWS credentials not configured", provider=self.name)
        raise ProviderUnavailableError(
            "AWS Transcribe is S3/async based and not wired in the offline POC",
            provider=self.name,
        )

    async def text_to_speech(
        self, text: str, language: str = "en-IN", voice_style: str = "neutral"
    ) -> bytes:
        if not self.is_configured():
            raise ProviderAuthError("AWS credentials not configured", provider=self.name)
        raise ProviderUnavailableError("AWS Polly not wired in the offline POC", provider=self.name)

    async def health_check(self) -> dict:
        t0 = time.perf_counter()
        if not self.is_configured():
            return {
                "status": "unhealthy",
                "auth_valid": False,
                "error": "No AWS credentials configured",
                "recommendation": "Set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY",
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
