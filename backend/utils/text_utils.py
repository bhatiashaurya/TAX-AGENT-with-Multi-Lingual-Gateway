"""
Script-aware text helpers.

These are the low-level primitives the language detector and Hinglish normalizer
build on: classifying characters to a Unicode script, splitting a string at
script boundaries, tokenising, and pulling out tax-domain entities.
"""
from __future__ import annotations

import re
from typing import Optional

from config.constants import (
    ENTITY_PATTERNS,
    SCRIPT_RANGES,
    TAX_DOMAIN_TERMS,
    TAX_TERM_ALIASES,
)

# Word tokeniser that keeps Latin, Devanagari, Tamil, Telugu, Kannada letters and
# digits together (so "26AS" or "GSTIN" stay intact) and treats everything else
# as a separator.
_TOKEN_RE = re.compile(r"[0-9A-Za-zऀ-ॿ஀-௿ఀ-౿ಀ-೿]+")

_COMPILED_ENTITIES = {name: re.compile(pattern) for name, pattern in ENTITY_PATTERNS.items()}


def classify_char(ch: str) -> Optional[str]:
    """Return the script name for a character, or ``None`` if it is not a letter
    in one of the scripts we track (punctuation, whitespace, emoji, ...)."""
    cp = ord(ch)
    for script, ranges in SCRIPT_RANGES.items():
        for start, end in ranges:
            if start <= cp <= end:
                return script
    return None


def script_histogram(text: str) -> dict[str, int]:
    """Count characters per script (ignoring unclassified characters)."""
    counts: dict[str, int] = {}
    for ch in text:
        script = classify_char(ch)
        if script:
            counts[script] = counts.get(script, 0) + 1
    return counts


def split_by_script(text: str) -> list[tuple[str, str]]:
    """Split text into maximal runs that share a script.

    Example: ``"मेरा GST"`` -> ``[("Devanagari", "मेरा"), ("Latin", "GST")]``
    (whitespace is attached to the preceding run and does not start a new one).
    """
    runs: list[tuple[str, str]] = []
    current_script: Optional[str] = None
    buffer = ""
    for ch in text:
        script = classify_char(ch)
        if script is None:
            buffer += ch  # separators stay attached to the current run
            continue
        if current_script is None or script == current_script:
            current_script = script
            buffer += ch
        else:
            # Script boundary: flush the run built so far, start a fresh one.
            if buffer.strip():
                runs.append((current_script, buffer))
            current_script = script
            buffer = ch
    if buffer.strip() and current_script is not None:
        runs.append((current_script, buffer))
    return [(s, seg.strip()) for s, seg in runs if seg.strip()]


def tokenize(text: str) -> list[str]:
    """Split into word tokens (letters/digits), dropping punctuation."""
    return _TOKEN_RE.findall(text)


def detect_code_switches(text: str) -> int:
    """Count how many times the script changes across consecutive tokens.

    A value > 0 is a strong signal of code-switching (e.g. Hinglish).
    """
    scripts: list[str] = []
    for tok in tokenize(text):
        hist = script_histogram(tok)
        if hist:
            scripts.append(max(hist, key=hist.get))
    return sum(1 for a, b in zip(scripts, scripts[1:]) if a != b)


def canonical_term(token: str) -> Optional[str]:
    """Return the canonical tax term for a token, or ``None`` if it is not one."""
    upper = token.upper()
    upper = TAX_TERM_ALIASES.get(upper, upper)
    return upper if upper in TAX_DOMAIN_TERMS else None


def extract_domain_terms(text: str) -> list[str]:
    """Ordered, de-duplicated tax terms present in the text."""
    seen: list[str] = []
    for tok in tokenize(text):
        term = canonical_term(tok)
        if term and term not in seen:
            seen.append(term)
    return seen


def extract_entities(text: str) -> dict[str, str]:
    """Find tax identifiers (PAN, GSTIN, reference numbers) in the text."""
    found: dict[str, str] = {}
    upper = text.upper()
    for name, pattern in _COMPILED_ENTITIES.items():
        match = pattern.search(upper)
        if match:
            found[name] = match.group(0)
    return found
