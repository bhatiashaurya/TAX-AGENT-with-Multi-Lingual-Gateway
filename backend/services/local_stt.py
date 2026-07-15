"""
Optional offline speech-to-text via faster-whisper.

This gives the zero-credential POC real ears: uploaded audio is genuinely
transcribed on the local CPU, so voice responses match the words actually
spoken even with no cloud provider configured.  Everything degrades
gracefully -- if the package is missing or the model fails to load, callers
fall back to the client-supplied transcript or a canned sample.

The model is lazy-loaded once per process (cold load is ~20s for "tiny");
``warm()`` lets the app preload it in a background thread at startup.
"""
from __future__ import annotations

import io
import threading

from config.settings import settings

_lock = threading.Lock()
_model = None
_load_failed: str | None = None


def available() -> bool:
    """Local STT is enabled, importable, and has not previously failed to load."""
    if not settings.ENABLE_LOCAL_STT or _load_failed:
        return False
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _get_model():
    global _model, _load_failed
    with _lock:
        if _model is None and _load_failed is None:
            try:
                from faster_whisper import WhisperModel

                _model = WhisperModel(settings.LOCAL_STT_MODEL, device="cpu", compute_type="int8")
            except Exception as exc:  # noqa: BLE001 -- model download/load can fail many ways
                _load_failed = str(exc)
        if _model is None:
            raise RuntimeError(_load_failed or "local STT unavailable")
        return _model


def warm() -> None:
    """Preload the model, swallowing errors; ideal for a startup thread."""
    try:
        _get_model()
    except Exception:  # noqa: BLE001
        pass


def transcribe(audio_bytes: bytes, language_hint: str | None = None) -> tuple[str, str, float]:
    """Blocking transcription -- call via ``asyncio.to_thread``.

    Returns ``(text, language, confidence)``.  Empty text means the audio was
    silent or unintelligible; language is auto-detected (users may speak any
    supported language regardless of the hint).
    """
    model = _get_model()
    segments, info = model.transcribe(io.BytesIO(audio_bytes))
    parts = [seg.text.strip() for seg in segments]
    text = " ".join(p for p in parts if p).strip()
    confidence = round(min(0.99, max(0.30, info.language_probability or 0.0)), 2)
    return text, info.language or (language_hint or "en"), confidence
