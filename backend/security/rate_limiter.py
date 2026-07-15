"""Token-bucket rate limiter (per client key, in-memory).

Production swaps this for Redis/API-Gateway throttling; the interface is the
same: ``check(key)`` returns whether the request is allowed plus retry hints.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from config.settings import settings


@dataclass
class RateDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: float


class TokenBucketRateLimiter:
    def __init__(self, per_minute: int | None = None, burst: int | None = None) -> None:
        self.rate = (per_minute or settings.RATE_LIMIT_PER_MINUTE) / 60.0  # tokens/sec
        self.capacity = burst or settings.RATE_LIMIT_BURST
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)
        self._lock = threading.Lock()

    def check(self, key: str, cost: float = 1.0) -> RateDecision:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens >= cost:
                tokens -= cost
                self._buckets[key] = (tokens, now)
                return RateDecision(True, int(tokens), 0.0)
            self._buckets[key] = (tokens, now)
            needed = cost - tokens
            return RateDecision(False, int(tokens), round(needed / self.rate, 2))
