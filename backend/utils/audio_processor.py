"""Audio validation + lightweight metadata extraction."""
from __future__ import annotations

import io
import wave
from typing import Any

from config.constants import ErrorCode
from config.settings import settings


class AudioValidationError(Exception):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = ErrorCode.AUDIO_VALIDATION_ERROR
        self.message = message
        self.details = details or {}


def _guess_format(filename: str, content_type: str, data: bytes) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    if filename and "." in filename:
        return filename.rsplit(".", 1)[-1].lower()
    if content_type and "/" in content_type:
        return content_type.split("/", 1)[-1]
    return "unknown"


def validate_audio(data: bytes, filename: str = "", content_type: str = "") -> dict[str, Any]:
    """Validate an uploaded audio blob and return metadata.

    Raises ``AudioValidationError`` on empty payloads, oversize files, or audio
    longer than the configured maximum duration.
    """
    if not data:
        raise AudioValidationError("Empty audio payload")

    size = len(data)
    if size > settings.max_audio_bytes:
        raise AudioValidationError(
            "Audio file exceeds maximum size",
            {"max_bytes": settings.max_audio_bytes, "provided_bytes": size},
        )

    fmt = _guess_format(filename, content_type, data)
    meta: dict[str, Any] = {"format": fmt, "file_size_bytes": size}

    if fmt == "wav":
        try:
            with wave.open(io.BytesIO(data), "rb") as wav:
                channels = wav.getnchannels()
                rate = wav.getframerate()
                frames = wav.getnframes()
                duration = round(frames / rate, 2) if rate else None
            meta.update(
                {"sample_rate_hz": rate, "channels": channels, "duration_seconds": duration}
            )
            if duration and duration > settings.MAX_AUDIO_DURATION_SECONDS:
                raise AudioValidationError(
                    "Audio exceeds maximum duration",
                    {"max_seconds": settings.MAX_AUDIO_DURATION_SECONDS, "provided_seconds": duration},
                )
        except AudioValidationError:
            raise
        except wave.Error:
            # Corrupt/unsupported WAV -- keep size info, skip duration checks.
            meta["duration_seconds"] = None

    return meta
