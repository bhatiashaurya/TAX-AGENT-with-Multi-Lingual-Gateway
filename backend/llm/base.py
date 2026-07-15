"""
LLM provider contract for the Tax Agent chat engine.

The engine only ever talks to an ``LLMProvider`` through this interface, which
keeps the application cloud-agnostic: the offline mock, Anthropic Claude, and
future Bedrock / Azure OpenAI / Vertex adapters are interchangeable at runtime.

Providers emit a stream of event dicts (SSE-friendly):

    {"type": "text",      "text": "..."}                       incremental tokens
    {"type": "tool_use",  "name": "...", "input": {...},
                          "result": "..."}                     simulated/real tool call
    {"type": "citations", "citations": [...],
                          "confidence": 0.87}                  grounding metadata
    {"type": "done",      "stop_reason": "end_turn",
                          "usage": {...}}                      terminal event

A provider must always finish with a ``done`` event unless it raises.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal


@dataclass
class ChatTurn:
    """One prior message in the conversation, oldest first."""

    role: Literal["user", "assistant"]
    content: str


@dataclass
class GroundingChunk:
    """A retrieved knowledge chunk the answer should be grounded in."""

    chunk_id: str
    source: str          # document title, e.g. "GST Refunds"
    path: str            # corpus-relative path, e.g. "gst_refunds.md"
    text: str
    score: float
    section: str = ""

    def as_citation(self, index: int) -> dict[str, Any]:
        return {
            "index": index,
            "source": self.source,
            "path": self.path,
            "section": self.section,
            "score": round(self.score, 3),
            "snippet": (self.text[:220] + "…") if len(self.text) > 220 else self.text,
        }


class LLMError(Exception):
    """Base class for provider failures."""

    recoverable = True

    def __init__(self, message: str, *, provider: str = "") -> None:
        super().__init__(message)
        self.provider = provider


class LLMAuthError(LLMError):
    """Bad or missing credentials — retrying the same provider is pointless."""

    recoverable = False


class LLMRateLimitError(LLMError):
    """Provider throttled us; retry after a backoff."""


class LLMTransientError(LLMError):
    """Overload / 5xx / connection failure; safe to retry."""


class LLMProvider(ABC):
    #: short, stable identifier ("mock" / "anthropic" / "bedrock" / ...)
    name: str = "base"

    @abstractmethod
    def stream_chat(
        self,
        turns: list[ChatTurn],
        system: str,
        grounding: list[GroundingChunk] | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a reply to the conversation as event dicts (see module doc)."""

    def is_configured(self) -> bool:
        """Whether the provider has everything it needs to make real calls."""
        return True

    async def health_check(self) -> dict[str, Any]:
        return {"status": "healthy" if self.is_configured() else "unconfigured"}
