"""LLM provider factory."""
from __future__ import annotations

from config.settings import settings
from llm.anthropic_llm import AnthropicLLM
from llm.base import LLMProvider
from llm.groq_llm import GroqLLM
from llm.mock_llm import MockLLM


def build_llm() -> LLMProvider:
    """Return the configured provider, falling back to the offline mock.

    The fallback keeps the app fully usable when a cloud provider is selected
    without credentials — a warning surfaces in /health instead of a crash.
    """
    if settings.LLM_PROVIDER == "anthropic":
        provider: LLMProvider = AnthropicLLM()
        if provider.is_configured():
            return provider
    elif settings.LLM_PROVIDER == "groq":
        provider = GroqLLM()
        if provider.is_configured():
            return provider
    return MockLLM()
