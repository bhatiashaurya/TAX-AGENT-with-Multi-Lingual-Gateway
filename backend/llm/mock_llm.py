"""
Offline mock LLM.

This is what makes Tax Agent fully functional with zero credentials.  It is
NOT a canned-response bot: every answer is synthesized at request time from
the retrieved knowledge chunks and the conversation history, so different
questions produce different, grounded answers and follow-ups stay on topic.

It also simulates the operational behaviours of a real provider so the whole
application can be exercised locally:

* token-by-token streaming with realistic latency
* tool calls (an income-tax calculator) with visible inputs/results
* citations + confidence derived from retrieval scores
* transient errors / rate limits / auth failures via ``[[simulate:...]]``
  directives in the message (documented in TESTING.md) or MOCK_FAILURE_RATE
"""
from __future__ import annotations

import asyncio
import hashlib
import random
import re
from typing import Any, AsyncIterator

from config.settings import settings
from llm.base import (
    ChatTurn,
    GroundingChunk,
    LLMAuthError,
    LLMProvider,
    LLMRateLimitError,
    LLMTransientError,
)

_WORD = re.compile(r"[a-zA-Z][a-zA-Z\-']+")
_STOPWORDS = frozenset(
    "the a an is are was were be been being do does did to of in on for with and or "
    "but if then than as at by from up about into over after under again what which "
    "who whom this that these those i you he she it we they my your his her its our "
    "their me him them can could should would may might must will shall how when "
    "where why not no nor only own same so too very just also please tell give need "
    "want know mera meri mere hai hain kya kaise kab kaha karo batao chahiye ka ki ke".split()
)

# FY 2024-25 (AY 2025-26) new-regime slabs for resident individuals.
_NEW_REGIME_SLABS = [
    (300_000, 0.00),
    (700_000, 0.05),
    (1_000_000, 0.10),
    (1_200_000, 0.15),
    (1_500_000, 0.20),
    (float("inf"), 0.30),
]
_STANDARD_DEDUCTION = 50_000
_REBATE_87A_LIMIT = 700_000  # zero tax up to this taxable income (new regime)


def _terms(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text) if w.lower() not in _STOPWORDS]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if len(p.strip()) > 30]


def _compute_new_regime_tax(gross_income: float) -> dict[str, Any]:
    taxable = max(0.0, gross_income - _STANDARD_DEDUCTION)
    if taxable <= _REBATE_87A_LIMIT:
        base_tax = 0.0
    else:
        base_tax, prev = 0.0, 0.0
        for limit, rate in _NEW_REGIME_SLABS:
            span = min(taxable, limit) - prev
            if span <= 0:
                break
            base_tax += span * rate
            prev = limit
    cess = base_tax * 0.04
    return {
        "gross_income": round(gross_income),
        "standard_deduction": _STANDARD_DEDUCTION,
        "taxable_income": round(taxable),
        "tax_before_cess": round(base_tax),
        "health_education_cess_4pct": round(cess),
        "total_tax": round(base_tax + cess),
        "regime": "new (FY 2024-25)",
    }


class MockLLM(LLMProvider):
    name = "mock"

    # ------------------------------------------------------------------ #
    # Simulation directives (developer test hooks)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_directive(text: str) -> tuple[str, str | None]:
        m = re.match(r"^\s*\[\[simulate:(\w+)\]\]\s*", text)
        if m:
            return text[m.end():], m.group(1).lower()
        return text, None

    def _maybe_fail(self, directive: str | None) -> None:
        if directive == "error":
            raise LLMTransientError("Simulated upstream 529 (overloaded)", provider=self.name)
        if directive == "rate_limit":
            raise LLMRateLimitError("Simulated 429 rate limit", provider=self.name)
        if directive == "auth":
            raise LLMAuthError("Simulated 401 invalid credentials", provider=self.name)
        if directive is None and settings.MOCK_FAILURE_RATE > 0:
            if random.random() < settings.MOCK_FAILURE_RATE:
                raise LLMTransientError("Random simulated transient failure", provider=self.name)

    # ------------------------------------------------------------------ #
    # Answer synthesis
    # ------------------------------------------------------------------ #
    @staticmethod
    def _topic(query_terms: list[str]) -> str:
        acronyms = [t.upper() for t in query_terms if t.upper() in
                    {"GST", "TDS", "TCS", "ITR", "PAN", "GSTIN", "HRA", "LTCG", "STCG", "DTAA"}]
        if acronyms:
            return acronyms[0]
        return " ".join(query_terms[:3]) if query_terms else "your question"

    def _score_sentences(
        self, chunks: list[GroundingChunk], query_terms: list[str], history_terms: list[str]
    ) -> list[tuple[float, str, int]]:
        """Return (score, sentence, citation_index) sorted best-first."""
        qset, hset = set(query_terms), set(history_terms)
        scored: list[tuple[float, str, int]] = []
        for ci, chunk in enumerate(chunks, start=1):
            for sent in _sentences(chunk.text):
                sterms = set(_terms(sent))
                if not sterms:
                    continue
                overlap = len(sterms & qset) + 0.35 * len(sterms & hset)
                if overlap == 0:
                    continue
                score = (overlap / (len(sterms) ** 0.5)) * (0.5 + chunk.score)
                scored.append((score, sent, ci))
        scored.sort(key=lambda t: -t[0])
        return scored

    def _compose(
        self,
        query: str,
        turns: list[ChatTurn],
        grounding: list[GroundingChunk],
        seed: int,
    ) -> tuple[str, float]:
        """Build a grounded markdown answer + confidence from retrieval."""
        query_terms = _terms(query)
        history_terms = _terms(" ".join(t.content for t in turns[:-1] if t.role == "user"))
        topic = self._topic(query_terms)

        if not grounding:
            return (
                f"I don't have specific material on **{topic}** in my current knowledge "
                "base, and I'd rather not guess on a tax matter.\n\n"
                "Could you share a bit more detail — for example the tax type (GST, "
                "income tax, TDS…), the financial year, and whether this is for an "
                "individual or a business? I can also help with registration, returns, "
                "refunds, notices, compliance deadlines, and audit preparation.",
                0.30,
            )

        ranked = self._score_sentences(grounding, query_terms, history_terms)
        rng = random.Random(seed)
        # Regeneration nudges selection so a second answer reads differently
        # while staying grounded in the same sources.
        if seed and len(ranked) > 6:
            head = ranked[:8]
            rng.shuffle(head)
            ranked = head + ranked[8:]

        picked: list[tuple[str, int]] = []
        seen: set[str] = set()
        for score, sent, ci in ranked:
            key = sent[:60].lower()
            if key in seen:
                continue
            seen.add(key)
            picked.append((sent, ci))
            if len(picked) >= 6:
                break

        if not picked:
            picked = [(s, 1) for s in _sentences(grounding[0].text)[:3]]

        used_citations = sorted({ci for _, ci in picked})
        coverage = len(set(query_terms) & set(_terms(" ".join(s for s, _ in picked)))) / max(
            1, len(set(query_terms))
        )
        mean_score = sum(c.score for c in grounding) / len(grounding)
        confidence = round(min(0.97, 0.45 + 0.35 * coverage + 0.25 * mean_score), 2)

        lines = [f"Here's what applies to **{topic}**:", ""]
        for sent, ci in picked[:4]:
            lines.append(f"- {sent} [{ci}]")
        if len(picked) > 4:
            lines.append("")
            lines.append("Additional points worth knowing:")
            for sent, ci in picked[4:]:
                lines.append(f"- {sent} [{ci}]")

        lines.append("")
        if confidence < 0.6:
            lines.append(
                "_My sources only partially cover this. If you tell me the financial "
                "year and whether this concerns an individual or a company, I can be "
                "more specific._"
            )
        else:
            follow = rng.choice(
                [
                    "Want me to walk through the filing steps, deadlines, or penalties for this?",
                    "I can break down the deadlines or documentation next — which would help?",
                    "If you share your specific situation, I can apply these rules to it.",
                ]
            )
            lines.append(follow)
        return "\n".join(lines), confidence

    # ------------------------------------------------------------------ #
    # Tool-call simulation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _calculator_intent(query: str) -> float | None:
        if not re.search(r"\b(calculate|compute|how much tax|tax on|tax for)\b", query, re.I):
            return None
        amounts = re.findall(r"(?:₹|rs\.?\s*)?([\d,]{4,})\s*(lakh|lac|crore|l|cr)?", query, re.I)
        for raw, unit in amounts:
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                continue
            unit = (unit or "").lower()
            if unit in ("lakh", "lac", "l"):
                value *= 100_000
            elif unit in ("crore", "cr"):
                value *= 10_000_000
            if value >= 100_000:
                return value
        return None

    # ------------------------------------------------------------------ #
    # Provider interface
    # ------------------------------------------------------------------ #
    async def stream_chat(
        self,
        turns: list[ChatTurn],
        system: str,
        grounding: list[GroundingChunk] | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        grounding = grounding or []
        query = turns[-1].content if turns else ""
        query, directive = self._extract_directive(query)
        self._maybe_fail(directive)

        delay = settings.MOCK_STREAM_DELAY_MS / 1000.0
        if directive == "slow":
            delay *= 12

        # Deterministic per-(conversation, attempt) seed lives in the system
        # string tail the engine appends; fall back to hash of the query.
        seed_match = re.search(r"\[vary:(\d+)\]", system)
        seed = int(seed_match.group(1)) if seed_match else 0

        income = self._calculator_intent(query)
        tool_result: dict[str, Any] | None = None
        if income is not None:
            tool_result = _compute_new_regime_tax(income)
            yield {
                "type": "tool_use",
                "name": "tax_calculator",
                "input": {"gross_income": income, "regime": "new", "fy": "2024-25"},
                "result": tool_result,
            }
            await asyncio.sleep(delay * 6)

        answer, confidence = self._compose(query, turns, grounding, seed)

        if tool_result:
            calc = (
                f"Using the new-regime slabs for FY 2024-25 on a gross income of "
                f"₹{tool_result['gross_income']:,}:\n\n"
                f"| Item | Amount |\n|---|---|\n"
                f"| Standard deduction | ₹{tool_result['standard_deduction']:,} |\n"
                f"| Taxable income | ₹{tool_result['taxable_income']:,} |\n"
                f"| Tax before cess | ₹{tool_result['tax_before_cess']:,} |\n"
                f"| Health & education cess (4%) | ₹{tool_result['health_education_cess_4pct']:,} |\n"
                f"| **Total tax** | **₹{tool_result['total_tax']:,}** |\n\n"
            )
            if tool_result["total_tax"] == 0:
                calc += "No tax is payable — the Section 87A rebate applies below ₹7,00,000.\n\n"
            answer = calc + answer

        # Stream in small chunks so the UI renders progressively.
        buf: list[str] = []
        for token in re.split(r"(\s+)", answer):
            buf.append(token)
            if sum(len(b) for b in buf) >= 14:
                yield {"type": "text", "text": "".join(buf)}
                buf.clear()
                await asyncio.sleep(delay)
        if buf:
            yield {"type": "text", "text": "".join(buf)}

        if grounding:
            yield {
                "type": "citations",
                "citations": [c.as_citation(i) for i, c in enumerate(grounding, start=1)],
                "confidence": confidence,
            }
        yield {
            "type": "done",
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": sum(len(t.content) for t in turns) // 4,
                "output_tokens": len(answer) // 4,
            },
        }

    async def health_check(self) -> dict[str, Any]:
        return {"status": "healthy", "note": "offline mock — no external calls"}


def _stable_seed(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
