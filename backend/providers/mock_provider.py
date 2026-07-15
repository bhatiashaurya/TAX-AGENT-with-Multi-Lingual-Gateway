"""
Offline mock provider.

This is what makes the POC runnable with zero credentials.  It produces
realistic, deterministic outputs:

* ``speech_to_text`` returns a canned Hinglish transcript (real transcription is
  impossible offline; the sample is chosen deterministically from the audio
  length so repeated calls are stable).
* ``translate_text`` reuses the heuristic Hinglish normalizer, so the mock
  actually "translates" Hinglish/Hindi into sensible English.
* ``text_to_speech`` returns a valid (silent) WAV so the audio round-trip works.
"""
from __future__ import annotations

import asyncio
import io
import struct
import time
import wave

from providers.base import Provider, STTResult, TranslationResult

# Canned transcripts the mock "hears".  These intentionally match the demo
# scenarios in the design doc so the voice flow is meaningful offline.
_SAMPLE_TRANSCRIPTS = [
    "मेरा TDS correction request का status batao",
    "Mera GST refund status check karo",
    "मेरा income tax return kahan hai",
    "PAN verification chahiye",
]


def _silent_wav(seconds: float = 0.4, sample_rate: int = 16000) -> bytes:
    """Generate a valid mono 16-bit PCM WAV containing silence."""
    n_frames = int(seconds * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack("<" + "h" * n_frames, *([0] * n_frames)))
    return buf.getvalue()


class MockProvider(Provider):
    name = "mock"

    async def speech_to_text(
        self, audio_bytes: bytes, language_hint: str = "hi", mime: str = "audio/wav"
    ) -> STTResult:
        # Real transcription first: local whisper (if installed) actually hears
        # the audio, so the reply matches the words spoken -- still offline,
        # still zero credentials.
        from services import local_stt

        if local_stt.available():
            try:
                text, lang, conf = await asyncio.to_thread(
                    local_stt.transcribe, audio_bytes, language_hint
                )
                if text:
                    return STTResult(
                        transcript=text, language=lang, confidence=conf,
                        engine="local-whisper",
                    )
            except Exception:  # noqa: BLE001 -- fall through to the canned sample
                pass

        await asyncio.sleep(0.05)  # simulate a little processing latency
        transcript = _SAMPLE_TRANSCRIPTS[len(audio_bytes) % len(_SAMPLE_TRANSCRIPTS)]
        return STTResult(
            transcript=transcript,
            language=language_hint,
            confidence=0.95,
            alternatives=[transcript.replace("TDS", "tax")] if "TDS" in transcript else [],
            engine="sample",
        )

    async def translate_text(
        self, text: str, source_lang: str, target_lang: str = "en"
    ) -> TranslationResult:
        await asyncio.sleep(0.02)
        # Imported lazily to avoid a provider -> service import at module load.
        from services.hinglish_normalizer import normalize

        result = normalize(text)
        return TranslationResult(text=result["normalized_english"], confidence=result["confidence"])

    async def text_to_speech(
        self, text: str, language: str = "en-IN", voice_style: str = "neutral"
    ) -> bytes:
        await asyncio.sleep(0.01)
        # Duration loosely proportional to text length, clamped for the POC.
        return _silent_wav(seconds=min(3.0, 0.3 + len(text) * 0.03))

    async def health_check(self) -> dict:
        t0 = time.perf_counter()
        await asyncio.sleep(0.001)
        return {
            "status": "healthy",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "auth_valid": True,
        }

    async def detect_language(self, text: str) -> tuple[str, float]:
        from services.language_detector import detect

        det = detect(text)
        return det["code"], det["confidence"]
