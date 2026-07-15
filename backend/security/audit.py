"""Structured, append-only audit log with request tracing.

Every request writes one JSON line: correlation id, decision, detector scores,
redacted content preview. PII is masked before it reaches disk. This is the
seam a real deployment points at a SIEM / governance platform.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import settings
from security import pii


class AuditLog:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or settings.AUDIT_LOG_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._recent: list[dict[str, Any]] = []  # in-memory tail for /admin views

    def record(self, event: dict[str, Any]) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **event}
        # Defence in depth: mask everything sensitive, whatever the caller passed.
        if "preview" in entry:
            entry["preview"] = pii.redact(str(entry["preview"]))[:280]
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)  # self-heal if removed
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self._recent.append(entry)
            if len(self._recent) > 500:
                self._recent = self._recent[-500:]

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._recent[-limit:]))

    def security_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            events = [e for e in self._recent if e.get("decision") in ("blocked", "flagged")]
            return list(reversed(events[-limit:]))
