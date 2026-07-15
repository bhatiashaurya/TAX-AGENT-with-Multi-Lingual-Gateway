"""
Provider router: resilience layer in front of the concrete providers.

Responsibilities
----------------
* Hold the registry of providers and the current default.
* Execute any provider operation with an ordered fallback chain.
* Apply a per-provider circuit breaker so a persistently failing provider is
  skipped quickly (transparent, sub-100ms failover to the next one).
* Report aggregate health.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from config.settings import settings
from providers.base import Provider, ProviderError


class AllProvidersFailedError(ProviderError):
    """Raised when every provider in the chain failed."""

    def __init__(self, chain: list[dict[str, Any]]) -> None:
        super().__init__("All providers failed")
        self.chain = chain


class _Breaker:
    """Minimal circuit breaker: HEALTHY -> (failures) -> OPEN -> HALF-OPEN."""

    def __init__(self, threshold: int, recovery_seconds: int) -> None:
        self.threshold = threshold
        self.recovery_seconds = recovery_seconds
        self.failures: dict[str, int] = {}
        self.opened_at: dict[str, float] = {}

    def is_open(self, name: str) -> bool:
        opened = self.opened_at.get(name)
        if opened is None:
            return False
        if (time.monotonic() - opened) >= self.recovery_seconds:
            # Recovery window elapsed -> allow a single trial (half-open).
            return False
        return self.failures.get(name, 0) >= self.threshold

    def state(self, name: str) -> str:
        if self.opened_at.get(name) and self.failures.get(name, 0) >= self.threshold:
            if (time.monotonic() - self.opened_at[name]) >= self.recovery_seconds:
                return "half_open"
            return "open"
        if self.failures.get(name, 0) > 0:
            return "degraded"
        return "healthy"

    def record_success(self, name: str) -> None:
        self.failures[name] = 0
        self.opened_at.pop(name, None)

    def record_failure(self, name: str) -> None:
        self.failures[name] = self.failures.get(name, 0) + 1
        if self.failures[name] >= self.threshold and name not in self.opened_at:
            self.opened_at[name] = time.monotonic()


class ProviderRouter:
    def __init__(self, providers: dict[str, Provider], default: str, fallback: list[str]) -> None:
        self.providers = providers
        self._default = default if default in providers else next(iter(providers))
        self.fallback = fallback
        self.breaker = _Breaker(
            settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            settings.CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS,
        )

    # -- default management ------------------------------------------------
    @property
    def default(self) -> str:
        return self._default

    def set_default(self, name: str) -> None:
        if name not in self.providers:
            raise KeyError(name)
        self._default = name

    def get(self, name: str) -> Provider:
        return self.providers[name]

    def order(self, preferred: Optional[str]) -> list[str]:
        """Build the attempt order: preferred (or default) first, then the
        configured fallbacks, de-duplicated and filtered to registered
        providers.  ``mock`` (if present) is guaranteed last so the POC always
        has a working ultimate fallback."""
        head = preferred or self._default
        chain: list[str] = []
        for name in [head, *self.fallback]:
            if name in self.providers and name not in chain:
                chain.append(name)
        if "mock" in self.providers and "mock" not in chain:
            chain.append("mock")
        return chain

    # -- execution ---------------------------------------------------------
    async def execute(
        self, op: str, *args, preferred: Optional[str] = None, **kwargs
    ) -> tuple[Any, dict[str, Any]]:
        """Run ``provider.<op>(*args, **kwargs)`` across the fallback chain.

        Returns ``(result, meta)`` where ``meta`` documents which provider
        served the request and the full attempt chain.  Raises
        ``AllProvidersFailedError`` if none succeed.
        """
        attempt_order = self.order(preferred)
        primary = attempt_order[0] if attempt_order else None
        chain: list[dict[str, Any]] = []

        for name in attempt_order:
            if self.breaker.is_open(name):
                chain.append({"provider": name, "status": "skipped", "reason": "circuit_open"})
                continue
            provider = self.providers[name]
            timeout = settings.provider_timeout(name)
            t0 = time.perf_counter()
            try:
                coro = getattr(provider, op)(*args, **kwargs)
                result = await asyncio.wait_for(coro, timeout=timeout)
                latency = round((time.perf_counter() - t0) * 1000, 1)
                self.breaker.record_success(name)
                chain.append({"provider": name, "status": "success", "latency_ms": latency})
                meta = {
                    "provider_used": name,
                    "primary_provider": primary,
                    "primary_provider_failed": name != primary,
                    "fallback_used": name != primary,
                    "fallback_chain": chain,
                }
                return result, meta
            except asyncio.TimeoutError:
                latency = round((time.perf_counter() - t0) * 1000, 1)
                self.breaker.record_failure(name)
                chain.append({"provider": name, "status": "failed", "reason": "timeout",
                              "latency_ms": latency})
            except ProviderError as exc:
                latency = round((time.perf_counter() - t0) * 1000, 1)
                self.breaker.record_failure(name)
                chain.append({"provider": name, "status": "failed", "reason": str(exc),
                              "latency_ms": latency})
            except Exception as exc:  # noqa: BLE001 - never let one provider crash the request
                latency = round((time.perf_counter() - t0) * 1000, 1)
                self.breaker.record_failure(name)
                chain.append({"provider": name, "status": "failed",
                              "reason": f"unexpected: {exc}", "latency_ms": latency})

        raise AllProvidersFailedError(chain)

    # -- health ------------------------------------------------------------
    async def health_all(self) -> dict[str, dict[str, Any]]:
        async def _check(name: str, provider: Provider) -> tuple[str, dict[str, Any]]:
            try:
                status = await asyncio.wait_for(provider.health_check(),
                                                timeout=settings.provider_timeout(name))
            except Exception as exc:  # noqa: BLE001
                status = {"status": "unhealthy", "error": str(exc)}
            status["circuit_state"] = self.breaker.state(name)
            status["failure_count"] = self.breaker.failures.get(name, 0)
            return name, status

        results = await asyncio.gather(*(_check(n, p) for n, p in self.providers.items()))
        return dict(results)
