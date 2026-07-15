"""
Provider contract shared by every cloud implementation.

The gateway only ever talks to a ``Provider`` through this interface, which is
what makes the system cloud-agnostic: Azure, AWS, GCP and the offline mock are
interchangeable at runtime.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from config.constants import ErrorCode


# ---------------------------------------------------------------------------
# Typed results
# ---------------------------------------------------------------------------
@dataclass
class STTResult:
    transcript: str
    language: str
    confidence: float
    alternatives: list[str] = field(default_factory=list)
    #: what actually produced the transcript, e.g. "local-whisper" / "sample";
    #: empty means "the provider itself" (cloud STT).
    engine: str = ""


@dataclass
class TranslationResult:
    text: str
    confidence: float


# ---------------------------------------------------------------------------
# Exceptions -- the router inspects these to decide fallback behaviour.
# ---------------------------------------------------------------------------
class ProviderError(Exception):
    """Base class for all provider failures."""

    code: str = ErrorCode.PROVIDER_UNAVAILABLE
    recoverable: bool = True

    def __init__(self, message: str, *, provider: str = "", code: str | None = None,
                 recoverable: bool | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        if code is not None:
            self.code = code
        if recoverable is not None:
            self.recoverable = recoverable


class ProviderTimeoutError(ProviderError):
    code = ErrorCode.PROVIDER_TIMEOUT
    recoverable = True


class ProviderAuthError(ProviderError):
    """Bad/missing credentials -- not worth retrying the same provider."""

    code = ErrorCode.PROVIDER_UNAVAILABLE
    recoverable = False


class ProviderUnavailableError(ProviderError):
    """SDK missing or service unreachable."""

    code = ErrorCode.PROVIDER_UNAVAILABLE
    recoverable = True


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------
class Provider(ABC):
    #: short, stable identifier ("azure" / "gcp" / "aws" / "mock")
    name: str = "base"

    @abstractmethod
    async def speech_to_text(
        self, audio_bytes: bytes, language_hint: str = "hi", mime: str = "audio/wav"
    ) -> STTResult:
        """Transcribe audio to text."""

    @abstractmethod
    async def translate_text(
        self, text: str, source_lang: str, target_lang: str = "en"
    ) -> TranslationResult:
        """Translate ``text`` from ``source_lang`` into ``target_lang``."""

    @abstractmethod
    async def text_to_speech(
        self, text: str, language: str = "en-IN", voice_style: str = "neutral"
    ) -> bytes:
        """Synthesise speech; returns WAV bytes."""

    @abstractmethod
    async def health_check(self) -> dict:
        """Return ``{"status": "healthy"|"unhealthy", "latency_ms": int, ...}``."""

    async def detect_language(self, text: str) -> tuple[str, float]:
        """Optional provider-side language detection.

        The gateway performs detection locally, so this is not required; the
        default signals that the caller should fall back to the local detector.
        """
        raise NotImplementedError

    def is_configured(self) -> bool:
        """Whether the provider has everything it needs to make real calls."""
        return True
