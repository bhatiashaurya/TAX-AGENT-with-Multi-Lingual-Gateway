"""
In-memory cache and session store for the POC.

Both are intentionally simple (a dict + monotonic timestamps).  The public
surface is deliberately close to what a Redis-backed implementation would expose
so Phase 2 can swap the backend without touching callers.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional


class TTLCache:
    """Thread-safe cache with per-entry expiry and simple size-bounded eviction."""

    def __init__(self, max_entries: int = 1000, default_ttl: int = 3600) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._max_entries = max_entries
        self._default_ttl = default_ttl
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                self.misses += 1
                return None
            expires_at, value = item
            if expires_at < time.monotonic():
                self._store.pop(key, None)
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        with self._lock:
            if len(self._store) >= self._max_entries and key not in self._store:
                # Evict the entry closest to expiry (cheap approximation of LRU).
                oldest = min(self._store.items(), key=lambda kv: kv[1][0], default=None)
                if oldest is not None:
                    self._store.pop(oldest[0], None)
            expires_at = time.monotonic() + (ttl if ttl is not None else self._default_ttl)
            self._store[key] = (expires_at, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return round(100.0 * self.hits / total, 1) if total else 0.0

    def stats(self) -> dict[str, Any]:
        return {
            "entries": len(self._store),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_percent": self.hit_rate,
        }


class SessionStore:
    """Stores per-session conversation turns with a sliding TTL."""

    def __init__(self, ttl_minutes: int = 30) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._ttl_seconds = ttl_minutes * 60
        self._lock = threading.Lock()

    def _expired(self, entry: dict[str, Any]) -> bool:
        return (time.time() - entry["updated_at"]) > self._ttl_seconds

    def append_turn(self, session_id: str, turn: dict[str, Any]) -> list[dict[str, Any]]:
        """Add a turn to a session and return the (post-append) history."""
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None or self._expired(entry):
                entry = {"created_at": time.time(), "turns": []}
            entry["turns"].append(turn)
            entry["updated_at"] = time.time()
            self._sessions[session_id] = entry
            return list(entry["turns"])

    def history(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None or self._expired(entry):
                self._sessions.pop(session_id, None)
                return []
            return list(entry["turns"])

    def active_sessions(self) -> int:
        with self._lock:
            return sum(1 for e in self._sessions.values() if not self._expired(e))
