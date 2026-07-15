"""
Guardrail pipeline — the single choke point every chat request passes through.

Composes input validation -> threat detection -> decision, and provides output
validation for streamed model text. Designed as a list of composable checks so
new policies (content moderation, hallucination heuristics, governance hooks)
slot in without touching the engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from config.settings import settings
from security import detectors, pii
from security.audit import AuditLog
from security.rate_limiter import RateDecision, TokenBucketRateLimiter

Decision = Literal["allow", "flag", "block"]

# Compliant, helpful refusals — never reveal internal rules or reasoning.
_REFUSALS = {
    "prompt_injection": (
        "I can't follow instructions that try to override how I operate or reveal "
        "my configuration. I'm happy to keep helping with your tax questions, though "
        "— what would you like to know?"
    ),
    "jailbreak": (
        "I can't take on a persona that ignores my guidelines. I can still help with "
        "genuine tax, compliance, and filing questions — ask away."
    ),
    "harmful_request": (
        "I can't help with anything designed to evade tax, conceal income, forge "
        "documents, or otherwise break the law. If your goal is to reduce tax "
        "*legally*, I can walk through deductions, exemptions, regime choices, and "
        "compliant planning — just ask."
    ),
    "input_invalid": "I couldn't process that input. Please send a text message under the size limit.",
    "rate_limited": "You're sending requests faster than I can safely handle. Please wait a moment and try again.",
}


@dataclass
class GuardrailResult:
    decision: Decision
    category: str = ""
    message: str = ""              # refusal text (block) or caution (flag)
    scores: dict[str, float] = field(default_factory=dict)
    signals: list[str] = field(default_factory=list)
    pii_found: list[str] = field(default_factory=list)
    retry_after: float = 0.0


class Guardrails:
    def __init__(self, audit: AuditLog | None = None, limiter: TokenBucketRateLimiter | None = None) -> None:
        self.audit = audit or AuditLog()
        self.limiter = limiter or TokenBucketRateLimiter()

    # ------------------------------------------------------------------ #
    # Input path
    # ------------------------------------------------------------------ #
    def check_input(
        self, text: str, *, client_key: str, correlation_id: str
    ) -> GuardrailResult:
        # 1. Rate limit
        rl: RateDecision = self.limiter.check(client_key)
        if not rl.allowed:
            result = GuardrailResult(
                decision="block", category="rate_limited",
                message=_REFUSALS["rate_limited"], retry_after=rl.retry_after_seconds,
            )
            self._audit(correlation_id, client_key, result, text)
            return result

        # 2. Input validation
        if not text or not text.strip():
            result = GuardrailResult("block", "input_invalid", _REFUSALS["input_invalid"])
            self._audit(correlation_id, client_key, result, text)
            return result
        if len(text) > settings.MAX_MESSAGE_CHARS:
            result = GuardrailResult(
                "block", "input_invalid",
                f"Your message is {len(text)} characters; the limit is {settings.MAX_MESSAGE_CHARS}. "
                "Please shorten it or split it up.",
            )
            self._audit(correlation_id, client_key, result, text)
            return result

        pii_found = [f.kind for f in pii.detect(text)]

        if not settings.ENABLE_GUARDRAILS:
            result = GuardrailResult("allow", pii_found=pii_found)
            self._audit(correlation_id, client_key, result, text)
            return result

        # 3. Threat detection
        detections = detectors.run_all(text)
        scores = {d.category: round(d.score, 2) for d in detections}
        signals = [s for d in detections for s in d.signals]
        top = max(detections, key=lambda d: d.score, default=None)

        if top and top.score >= settings.SECURITY_BLOCK_THRESHOLD:
            result = GuardrailResult(
                "block", top.category, _REFUSALS.get(top.category, _REFUSALS["harmful_request"]),
                scores=scores, signals=signals, pii_found=pii_found,
            )
        elif top and top.score >= settings.SECURITY_FLAG_THRESHOLD:
            result = GuardrailResult(
                "flag", top.category,
                "I'll answer, but I'll stay within compliant, lawful guidance.",
                scores=scores, signals=signals, pii_found=pii_found,
            )
        else:
            result = GuardrailResult("allow", scores=scores, signals=signals, pii_found=pii_found)

        self._audit(correlation_id, client_key, result, text)
        return result

    # ------------------------------------------------------------------ #
    # Output path
    # ------------------------------------------------------------------ #
    def sanitize_output(self, text: str) -> str:
        """Mask high-risk identifiers a model might echo or invent."""
        if not settings.ENABLE_GUARDRAILS:
            return text
        return pii.redact_for_output(text)

    # ------------------------------------------------------------------ #
    def _audit(self, correlation_id: str, client_key: str, result: GuardrailResult, text: str) -> None:
        self.audit.record(
            {
                "correlation_id": correlation_id,
                "client": client_key,
                "decision": {"allow": "allowed", "flag": "flagged", "block": "blocked"}[result.decision],
                "category": result.category or None,
                "scores": result.scores or None,
                "signals": result.signals or None,
                "pii": result.pii_found or None,
                "preview": text,
            }
        )
