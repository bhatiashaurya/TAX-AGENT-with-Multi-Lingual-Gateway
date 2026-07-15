"""PII detection and redaction for Indian tax context.

Used in two places with different strictness:
* audit logging — everything sensitive is masked before it touches disk
* output validation — high-risk identifiers (Aadhaar, cards, secrets) are
  masked in model output; tax IDs the user themselves shared stay readable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS: dict[str, re.Pattern[str]] = {
    "aadhaar": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
    "pan": re.compile(r"\b[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z]\b"),
    "gstin": re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z\d]{2}\b"),
    "card": re.compile(r"\b(?:\d[ -]?){15,18}\d\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "phone": re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)"),
    "api_key": re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,})\b"),
    "password_field": re.compile(r"(?i)(password|passwd|secret|api[_ ]?key)\s*[:=]\s*\S+"),
}

# Masked in *audit logs* (everything) vs *model output* (high-risk only).
_OUTPUT_MASK = ("aadhaar", "card", "api_key", "password_field")


@dataclass
class PIIFinding:
    kind: str
    count: int


def detect(text: str) -> list[PIIFinding]:
    findings = []
    for kind, pattern in _PATTERNS.items():
        hits = pattern.findall(text)
        if hits:
            findings.append(PIIFinding(kind=kind, count=len(hits)))
    return findings


def _mask(kind: str, value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if kind in ("aadhaar", "card", "phone") and len(digits) >= 4:
        return f"[{kind.upper()}-…{digits[-4:]}]"
    if kind in ("pan", "gstin") and len(value) >= 4:
        return f"[{kind.upper()}-…{value[-4:]}]"
    return f"[{kind.upper()}-REDACTED]"


def redact(text: str, kinds: tuple[str, ...] | None = None) -> str:
    """Mask PII in ``text``. ``kinds=None`` masks everything (audit mode)."""
    for kind, pattern in _PATTERNS.items():
        if kinds is not None and kind not in kinds:
            continue
        text = pattern.sub(lambda m, k=kind: _mask(k, m.group(0)), text)
    return text


def redact_for_output(text: str) -> str:
    """Mask only high-risk identifiers in assistant output."""
    return redact(text, kinds=_OUTPUT_MASK)
