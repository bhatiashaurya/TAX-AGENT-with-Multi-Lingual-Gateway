"""
Translation service.

Thin, provider-agnostic wrapper over the router that adds a translation cache.
Callers get back the translated text, the confidence, the provider that served
it, whether it was a cache hit, and the router's fallback metadata.
"""
from __future__ import annotations

from typing import Any, Optional

from cache.in_memory import TTLCache
from config.settings import settings
from providers.provider_router import ProviderRouter


class Translator:
    def __init__(self, router: ProviderRouter, cache: Optional[TTLCache] = None) -> None:
        self.router = router
        self.cache = cache

    async def translate(
        self, text: str, source_lang: str, target_lang: str = "en",
        preferred: Optional[str] = None,
    ) -> dict[str, Any]:
        cache_key = None
        if self.cache and settings.ENABLE_CACHING:
            cache_key = f"tr::{preferred or self.router.default}::{source_lang}->{target_lang}::{text}"
            cached = self.cache.get(cache_key)
            if cached is not None:
                return {**cached, "cache_hit": True}

        result, meta = await self.router.execute(
            "translate_text", text, source_lang, target_lang, preferred=preferred
        )
        payload = {
            "text": result.text,
            "confidence": result.confidence,
            "provider": meta["provider_used"],
            "meta": meta,
            "cache_hit": False,
        }
        if cache_key:
            # Do not cache the volatile ``cache_hit`` flag.
            self.cache.set(cache_key, {k: v for k, v in payload.items() if k != "cache_hit"},
                           ttl=settings.CACHE_TTL_SECONDS)
        return payload
