"""Shared pytest fixtures.

Adds the backend package root to ``sys.path`` so ``import config`` / ``import
services`` work regardless of the directory pytest is invoked from.
"""
from __future__ import annotations

import io
import os
import struct
import sys
import wave
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Keep the suite deterministic and fast: never load the whisper model in tests
# (the canned/client-transcript fallbacks are what the assertions exercise).
os.environ.setdefault("ENABLE_LOCAL_STT", "false")


@pytest.fixture(scope="session")
def sample_wav_bytes() -> bytes:
    """A valid 1-second mono 16 kHz silent WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(struct.pack("<" + "h" * 16000, *([0] * 16000)))
    return buf.getvalue()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app)


@pytest.fixture()
def sample_queries() -> list[dict]:
    return [
        {"text": "मेरा GST refund status check karo", "lang": "hi", "hinglish": True,
         "intent": "STATUS_CHECK", "tax": "GST", "preserve": "GST"},
        {"text": "Mera income tax return kahan hai?", "lang": "hi", "hinglish": True,
         "intent": "STATUS_CHECK", "tax": None, "preserve": None},
        {"text": "PAN verification chahiye", "lang": "hi", "hinglish": True,
         "intent": "VERIFICATION", "tax": "PAN", "preserve": "PAN"},
        {"text": "मेरा TDS correction request का status batao", "lang": "hi", "hinglish": True,
         "intent": "CORRECTION", "tax": "TDS", "preserve": "TDS"},
    ]
