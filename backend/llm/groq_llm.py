"""
Groq LLM provider.

Groq serves open models (Llama, etc.) over an OpenAI-compatible API with very
fast inference and a generous free tier. Credential-gated: ``is_configured()``
is False until GROQ_API_KEY is set. Streams over HTTP with ``httpx`` (already a
dependency) — no extra SDK required.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from config.settings import settings
from llm.base import (
    ChatTurn,
    GroundingChunk,
    LLMAuthError,
    LLMError,
    LLMProvider,
    LLMRateLimitError,
    LLMTransientError,
)

_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


def _system_with_grounding(system: str, grounding: list[GroundingChunk]) -> str:
    if not grounding:
        return system
    blocks = [
        f'<source index="{i}" title="{c.source}" section="{c.section}">\n{c.text}\n</source>'
        for i, c in enumerate(grounding, start=1)
    ]
    return (
        system
        + "\n\n<knowledge_base>\nRetrieved passages from the organisation's tax "
        "knowledge base. Ground your answer in these where relevant and cite them "
        "inline as [1], [2] matching the source index. Synthesize — never copy "
        "passages verbatim. If they don't cover the question, say so plainly.\n"
        + "\n".join(blocks)
        + "\n</knowledge_base>"
    )


class GroqLLM(LLMProvider):
    name = "groq"

    def is_configured(self) -> bool:
        return bool(settings.GROQ_API_KEY)

    @staticmethod
    def _delta_from_line(line: str) -> str | None:
        """Extract the incremental content from one OpenAI-style SSE line."""
        if not line.startswith("data:"):
            return None
        data = line[len("data:"):].strip()
        if not data or data == "[DONE]":
            return None
        try:
            chunk = json.loads(data)
            return chunk["choices"][0]["delta"].get("content")
        except (json.JSONDecodeError, KeyError, IndexError):
            return None

    async def stream_chat(
        self,
        turns: list[ChatTurn],
        system: str,
        grounding: list[GroundingChunk] | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        import httpx

        grounding = grounding or []
        messages = [{"role": "system", "content": _system_with_grounding(system, grounding)}]
        messages += [{"role": t.role, "content": t.content} for t in turns]
        payload = {
            "model": settings.GROQ_MODEL,
            "messages": messages,
            "max_tokens": max_tokens or settings.GROQ_MAX_TOKENS,
            "temperature": 0.3,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
            "Content-Type": "application/json",
        }

        chars = 0
        try:
            async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SECONDS) as client:
                async with client.stream("POST", _ENDPOINT, json=payload, headers=headers) as resp:
                    if resp.status_code == 401:
                        raise LLMAuthError("Groq authentication failed (check GROQ_API_KEY)", provider=self.name)
                    if resp.status_code == 429:
                        raise LLMRateLimitError("Groq rate limit hit", provider=self.name)
                    if resp.status_code >= 500:
                        raise LLMTransientError(f"Groq server error {resp.status_code}", provider=self.name)
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode("utf-8", "replace")[:200]
                        raise LLMError(f"Groq API error {resp.status_code}: {body}", provider=self.name)
                    async for line in resp.aiter_lines():
                        delta = self._delta_from_line(line)
                        if delta:
                            chars += len(delta)
                            yield {"type": "text", "text": delta}
        except httpx.RequestError as e:
            raise LLMTransientError(f"Network error reaching Groq: {e}", provider=self.name) from e

        if grounding:
            yield {
                "type": "citations",
                "citations": [c.as_citation(i) for i, c in enumerate(grounding, start=1)],
                "confidence": None,
            }
        yield {
            "type": "done",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": sum(len(t.content) for t in turns) // 4, "output_tokens": chars // 4},
        }

    async def health_check(self) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "hint": "set GROQ_API_KEY + LLM_PROVIDER=groq"}
        return {"status": "healthy", "model": settings.GROQ_MODEL}
