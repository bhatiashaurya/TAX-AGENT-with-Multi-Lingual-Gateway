"""
Anthropic Claude provider.

Credential-gated: instantiate freely, but ``is_configured()`` is False until
ANTHROPIC_API_KEY is set (or an `ant auth login` profile is active, which the
SDK resolves automatically).  Uses the official ``anthropic`` SDK with
streaming and adaptive thinking, per current API guidance for Claude Opus 4.8.
"""
from __future__ import annotations

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


def _render_grounding(grounding: list[GroundingChunk]) -> str:
    if not grounding:
        return ""
    blocks = []
    for i, chunk in enumerate(grounding, start=1):
        blocks.append(
            f'<source index="{i}" title="{chunk.source}" section="{chunk.section}">\n'
            f"{chunk.text}\n</source>"
        )
    return (
        "\n\n<knowledge_base>\n"
        "Retrieved passages from the organisation's tax knowledge base. Ground "
        "your answer in these where relevant and cite them inline as [1], [2] "
        "matching the source index. Synthesize — never copy passages verbatim. "
        "If the passages don't cover the question, say so plainly.\n"
        + "\n".join(blocks)
        + "\n</knowledge_base>"
    )


class AnthropicLLM(LLMProvider):
    name = "anthropic"

    def __init__(self) -> None:
        self._client = None  # lazy so the app boots without the SDK installed

    def is_configured(self) -> bool:
        if not settings.ANTHROPIC_API_KEY:
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic(
                api_key=settings.ANTHROPIC_API_KEY or None,
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )
        return self._client

    async def stream_chat(
        self,
        turns: list[ChatTurn],
        system: str,
        grounding: list[GroundingChunk] | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        import anthropic

        grounding = grounding or []
        client = self._get_client()
        messages = [{"role": t.role, "content": t.content} for t in turns]

        try:
            async with client.messages.stream(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=max_tokens or settings.LLM_MAX_TOKENS,
                thinking={"type": "adaptive"},
                system=system + _render_grounding(grounding),
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield {"type": "text", "text": text}
                final = await stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise LLMAuthError(f"Anthropic authentication failed: {e.message}", provider=self.name) from e
        except anthropic.RateLimitError as e:
            raise LLMRateLimitError("Anthropic rate limit hit", provider=self.name) from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise LLMTransientError(f"Anthropic server error {e.status_code}", provider=self.name) from e
            raise LLMError(f"Anthropic API error {e.status_code}: {e.message}", provider=self.name) from e
        except anthropic.APIConnectionError as e:
            raise LLMTransientError("Network error reaching Anthropic", provider=self.name) from e

        if final.stop_reason == "refusal":
            yield {
                "type": "text",
                "text": "\n\nI can't help with that request.",
            }

        if grounding:
            yield {
                "type": "citations",
                "citations": [c.as_citation(i) for i, c in enumerate(grounding, start=1)],
                "confidence": None,  # model-grounded; retrieval score not comparable
            }
        yield {
            "type": "done",
            "stop_reason": final.stop_reason,
            "usage": {
                "input_tokens": final.usage.input_tokens,
                "output_tokens": final.usage.output_tokens,
            },
        }

    async def health_check(self) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "hint": "set ANTHROPIC_API_KEY + LLM_PROVIDER=anthropic"}
        return {"status": "healthy", "model": settings.ANTHROPIC_MODEL}
