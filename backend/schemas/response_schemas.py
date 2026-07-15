"""
Response envelope helpers.

The rich success payloads are assembled as plain dicts by the orchestrator (they
are deeply nested and vary by endpoint), but the envelope shape and error format
are standardised here so every endpoint responds consistently.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def success_envelope(request_id: str, data: dict[str, Any], status: str = "success") -> dict[str, Any]:
    return {"status": status, "request_id": request_id, "data": data}


def error_envelope(
    request_id: str,
    code: str,
    message: str,
    *,
    details: Optional[dict[str, Any]] = None,
    category: str = "error",
    recoverable: bool = False,
    suggestions: Optional[list[str]] = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "status": "error",
        "request_id": request_id,
        "error": {
            "code": code,
            "message": message,
            "category": category,
            "recoverable": recoverable,
            "timestamp": _now_iso(),
        },
    }
    if details:
        body["error"]["details"] = details
    if suggestions:
        body["suggestions"] = suggestions
    return body
