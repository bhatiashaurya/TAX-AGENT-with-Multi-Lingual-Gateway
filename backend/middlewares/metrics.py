"""
In-process metrics collector.

Tracks request counts, per-endpoint and per-provider latencies, error tallies,
language distribution and estimated cost.  Exposes both a rich JSON snapshot
(for ``GET /metrics``) and a Prometheus text exposition.
"""
from __future__ import annotations

import threading
import time
from collections import Counter, defaultdict
from typing import Any, Optional

from config.constants import PROVIDER_UNIT_COST_USD


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return round(ordered[k], 1)


class MetricsCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start = time.time()
        self.requests_total = 0
        self.by_endpoint: Counter = Counter()
        self.by_language: Counter = Counter()
        self.errors_by_code: Counter = Counter()
        self.endpoint_latencies: dict[str, list[float]] = defaultdict(list)
        self.provider_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"requests": 0, "success": 0, "latencies": [], "cost": 0.0}
        )

    def record_request(
        self,
        endpoint: str,
        latency_ms: float,
        *,
        language: Optional[str] = None,
        provider: Optional[str] = None,
        success: bool = True,
        error_code: Optional[str] = None,
        fallback_used: bool = False,
    ) -> None:
        with self._lock:
            self.requests_total += 1
            self.by_endpoint[endpoint] += 1
            self.endpoint_latencies[endpoint].append(latency_ms)
            if language:
                self.by_language[language] += 1
            if error_code:
                self.errors_by_code[error_code] += 1
            if provider and provider in PROVIDER_UNIT_COST_USD:
                stats = self.provider_stats[provider]
                stats["requests"] += 1
                stats["latencies"].append(latency_ms)
                stats["cost"] += PROVIDER_UNIT_COST_USD[provider]
                if success:
                    stats["success"] += 1

    def record_error(self, error_code: str) -> None:
        with self._lock:
            self.errors_by_code[error_code] += 1

    @property
    def uptime_seconds(self) -> int:
        return int(time.time() - self._start)

    def _avg_latency(self) -> float:
        allv = [v for lst in self.endpoint_latencies.values() for v in lst]
        return round(sum(allv) / len(allv), 1) if allv else 0.0

    def health_summary(self, cache_hit_rate: float = 0.0) -> dict[str, Any]:
        total_errors = sum(self.errors_by_code.values())
        error_rate = round(100.0 * total_errors / self.requests_total, 1) if self.requests_total else 0.0
        return {
            "total_requests": self.requests_total,
            "error_rate_percent": error_rate,
            "avg_latency_ms": self._avg_latency(),
            "cache_hit_rate_percent": cache_hit_rate,
        }

    def snapshot(self, cache_stats: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        with self._lock:
            providers: dict[str, Any] = {}
            best: Optional[tuple[str, float]] = None
            for name, stats in self.provider_stats.items():
                lat = stats["latencies"]
                success_rate = round(100.0 * stats["success"] / stats["requests"], 1) if stats["requests"] else 0.0
                avg = round(sum(lat) / len(lat), 1) if lat else 0.0
                providers[name] = {
                    "requests": stats["requests"],
                    "success_rate": success_rate,
                    "avg_latency_ms": avg,
                    "p95_latency_ms": _percentile(lat, 95),
                    "p99_latency_ms": _percentile(lat, 99),
                    "cost_estimate_usd": round(stats["cost"], 4),
                }
                if stats["requests"] and (best is None or avg < best[1]):
                    best = (name, avg)

            total_errors = sum(self.errors_by_code.values())
            recommendation = (
                f"{best[0]} currently fastest (avg {best[1]}ms). Consider making it default."
                if best else "Not enough traffic yet for a recommendation."
            )
            snap = {
                "window": "since_startup",
                "uptime_seconds": self.uptime_seconds,
                "requests": {
                    "total": self.requests_total,
                    "by_endpoint": dict(self.by_endpoint),
                    "by_language": dict(self.by_language),
                },
                "providers": providers,
                "errors": {"total": total_errors, "by_type": dict(self.errors_by_code)},
                "recommendation": recommendation,
            }
            if cache_stats is not None:
                snap["cache"] = cache_stats
            return snap

    def prometheus(self) -> str:
        lines: list[str] = []
        lines.append("# HELP nth_requests_total Total requests handled")
        lines.append("# TYPE nth_requests_total counter")
        for endpoint, count in self.by_endpoint.items():
            lines.append(f'nth_requests_total{{endpoint="{endpoint}"}} {count}')
        for lang, count in self.by_language.items():
            lines.append(f'nth_requests_by_language_total{{language="{lang}"}} {count}')
        for code, count in self.errors_by_code.items():
            lines.append(f'nth_errors_total{{error_code="{code}"}} {count}')
        for name, stats in self.provider_stats.items():
            lines.append(f'nth_provider_requests_total{{provider="{name}"}} {stats["requests"]}')
        return "\n".join(lines) + "\n"


# Process-wide singleton.
metrics = MetricsCollector()
