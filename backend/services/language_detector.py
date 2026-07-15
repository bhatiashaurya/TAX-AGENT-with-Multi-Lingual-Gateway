"""
Language / Hinglish detection.

Approach (fast, deterministic, no ML dependency -- ideal for a POC):

1. Classify every character to a Unicode script and build a histogram.
2. If any Indic script is present, the primary language is the dominant Indic
   script; the presence of Latin characters alongside it flags Hinglish.
3. If the text is Latin-only, look for romanised Hindi function words
   ("mera", "karo", "chahiye", ...) to distinguish romanised Hinglish from
   plain English.
"""
from __future__ import annotations

from config.constants import HINGLISH_LEXICON, SCRIPT_TO_LANG, SUPPORTED_LANGUAGES
from utils.text_utils import script_histogram, tokenize

_INDIC_SCRIPTS = {"Devanagari", "Tamil", "Telugu", "Kannada"}


def _romanized_hindi_hits(text: str) -> int:
    """Count tokens that are known romanised Hindi function words."""
    hits = 0
    for tok in tokenize(text):
        low = tok.lower()
        # Only count ASCII (romanised) tokens here; Devanagari is handled by the
        # script histogram path.
        if low.isascii() and low in HINGLISH_LEXICON:
            hits += 1
    return hits


def detect(text: str) -> dict:
    hist = script_histogram(text)
    total = sum(hist.values())

    if total == 0:
        return {
            "code": "en", "name": "English", "script": "Latin",
            "confidence": 0.30, "is_hinglish": False, "scripts": {},
        }

    latin = hist.get("Latin", 0)
    indic_present = {s: c for s, c in hist.items() if s in _INDIC_SCRIPTS}
    roman_hits = _romanized_hindi_hits(text)

    if indic_present:
        primary_script = max(indic_present, key=indic_present.get)
        code = SCRIPT_TO_LANG[primary_script]
        is_hinglish = latin > 0  # Indic + Latin in one utterance == code-switched
        # Both scripts present is an unambiguous Hinglish signal.
        confidence = 0.98 if is_hinglish else min(0.99, 0.85 + 0.15 * (indic_present[primary_script] / total))
    elif roman_hits > 0:
        # Latin-only but contains Hindi function words -> romanised Hinglish.
        primary_script = "Devanagari"
        code = "hi"
        is_hinglish = True
        confidence = min(0.92, 0.78 + 0.03 * roman_hits)
    else:
        primary_script = "Latin"
        code = "en"
        is_hinglish = False
        confidence = min(0.97, 0.70 + 0.30 * (latin / total))

    meta = SUPPORTED_LANGUAGES.get(code, {"name": code, "script": primary_script})
    return {
        "code": code,
        "name": meta["name"],
        "script": meta.get("script", primary_script),
        "confidence": round(confidence, 2),
        "is_hinglish": is_hinglish,
        "scripts": hist,
    }
