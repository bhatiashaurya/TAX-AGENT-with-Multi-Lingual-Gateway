"""
Heuristic Hinglish -> canonical English normalizer.

This is the "conversational intelligence" of the gateway's offline path.  Rather
than a black-box translation, it:

* decomposes code-switched input token-by-token,
* preserves tax-domain terminology verbatim (GST stays GST),
* re-orders Hindi verb-final / genitive constructions into natural English,
* and scores its own confidence so low-quality results can be flagged.

The output shape matches the ``processing.normalization`` block in the API spec.
"""
from __future__ import annotations

from typing import Any, Optional

from config.constants import HINGLISH_LEXICON, INTENT_GENERAL, INTENT_KEYWORDS
from utils.text_utils import canonical_term, extract_domain_terms, tokenize

# Nouns that act as the "head" of a status-style query -- used to turn
# "<X> ka status" into "status of <X>" and to build "tell me the status of <X>".
_HEAD_NOUNS = {"status", "detail", "details", "amount", "date", "balance", "record", "refund"}

# English imperative verbs.  When one of these appears in Latin script it is the
# real verb of the sentence (not a content noun); if the utterance has no Hindi
# verb we promote it, and either way we drop it from the noun phrase so it is not
# duplicated (e.g. "... status check karo" -> verb "check", not a trailing noun).
_ENGLISH_ACTION_VERBS = {
    "check": "check", "recheck": "check", "track": "check", "verify": "check",
    "get": "check", "show": "show", "tell": "tell me", "send": "send",
    "file": "file", "need": "need", "withdraw": "withdraw",
}


def _classify_tokens(tokens: list[str]) -> tuple[list[dict[str, str]], list[str], int, int]:
    """Classify each token; return (classified, preserved_terms, known, unknown)."""
    classified: list[dict[str, str]] = []
    preserved: list[str] = []
    known = unknown = 0

    for tok in tokens:
        term = canonical_term(tok)
        if term:
            classified.append({"cat": "DOMAIN", "val": term})
            if term not in preserved:
                preserved.append(term)
            known += 1
            continue

        low = tok.lower()
        if low in HINGLISH_LEXICON:
            cat, val = HINGLISH_LEXICON[low]
            classified.append({"cat": cat, "val": val})
            known += 1
            continue

        if any(ch.isdigit() for ch in tok):
            classified.append({"cat": "ENTITY", "val": tok})
            known += 1
            continue

        from config.constants import KNOWN_EN

        if low in KNOWN_EN:
            classified.append({"cat": "EN", "val": low})
            known += 1
        else:
            # Unknown token (out-of-vocabulary English or a typo) -- kept, but it
            # drags confidence down.
            classified.append({"cat": "EN", "val": low})
            unknown += 1

    return classified, preserved, known, unknown


def _first(classified: list[dict[str, str]], cat: str) -> Optional[str]:
    return next((c["val"] for c in classified if c["cat"] == cat), None)


def _content_words_and_verb(
    classified: list[dict[str, str]], verb: Optional[str]
) -> tuple[list[str], Optional[str]]:
    """Collect noun-phrase words, pulling out any English imperative verb.

    If the sentence has no Hindi verb, the first English action word becomes the
    verb; either way action words are removed from the noun phrase.
    """
    content: list[str] = []
    for c in classified:
        if c["cat"] not in ("DOMAIN", "EN", "ENTITY", "N"):
            continue
        word = c["val"]
        if c["cat"] == "EN" and word in _ENGLISH_ACTION_VERBS:
            if verb is None:
                verb = _ENGLISH_ACTION_VERBS[word]
            continue
        content.append(word)
    return content, verb


def _capitalize(sentence: str) -> str:
    sentence = " ".join(sentence.split()).strip()
    return sentence[:1].upper() + sentence[1:] if sentence else sentence


def _compose(poss: Optional[str], verb: Optional[str], qword: Optional[str],
             content: list[str]) -> tuple[str, bool]:
    """Assemble an English sentence from the extracted parts.

    Returns ``(sentence, ambiguous)``.
    """
    np = ((poss + " ") if poss else "") + " ".join(content)
    np = np.strip()

    if qword:
        if qword == "where":
            return _capitalize(f"Where is {np}?"), False
        if qword == "when":
            return _capitalize(f"When is {np}?"), False
        if qword == "how much":
            return _capitalize(f"How much is {np}?"), False
        if qword == "what":
            return _capitalize(f"What is {np}?"), False
        return _capitalize(f"How is {np}?"), False

    if verb:
        if verb == "tell me":
            head = next((n for n in content if n in _HEAD_NOUNS), None)
            if head:
                rest = [n for n in content if n != head]
                rest_np = ((poss + " ") if poss else "") + " ".join(rest)
                return _capitalize(f"Tell me the {head} of {rest_np.strip()}"), False
            return _capitalize(f"Tell me about {np}"), False
        if verb == "need":
            return _capitalize(f"I need {np}"), False
        if verb in ("file", "send", "show", "withdraw"):
            return _capitalize(f"{verb} {np}"), False
        # default verb == "check"
        return _capitalize(f"Check {np}"), False

    # No verb and no question word: likely an underspecified/ambiguous utterance.
    ambiguous = not any(n in _HEAD_NOUNS for n in content)
    return _capitalize(np if np else "query"), ambiguous


def normalize(text: str) -> dict[str, Any]:
    tokens = tokenize(text)
    classified, preserved, known, unknown = _classify_tokens(tokens)

    steps = ["code_switch_detection", "script_decomposition"]
    if preserved:
        steps.append("terminology_preservation")

    poss = _first(classified, "POSS")
    verb = _first(classified, "V")
    qword = _first(classified, "Q")
    content, verb = _content_words_and_verb(classified, verb)

    sentence, ambiguous = _compose(poss, verb, qword, content)
    if verb or qword:
        steps.append("verb_ordering_fix")

    # Confidence: reward recognised tokens, penalise unknown ones (weighted x2 so
    # a single typo in a short query pushes below the 0.80 quality threshold).
    total_weighted = known + 2 * unknown
    ratio = (known / total_weighted) if total_weighted else 0.5
    confidence = 0.50 + 0.42 * ratio
    if preserved:
        confidence += 0.03
    if (verb or qword) and unknown == 0:
        confidence += 0.03
    confidence = round(max(0.30, min(0.96, confidence)), 2)

    has_hindi = any(c["cat"] in ("POSS", "Q", "BE", "V", "OF", "DROP", "N") for c in classified)
    method = "hinglish_decomposition" if has_hindi else "passthrough_english"

    return {
        "normalized_english": sentence,
        "method": method,
        "confidence": confidence,
        "steps_applied": steps,
        "terminology_preserved": preserved,
        "ambiguous": ambiguous,
    }


def extract_intent(normalized_text: str, original_text: str = "") -> dict[str, Any]:
    blob = f"{normalized_text} {original_text}".lower()
    terms = extract_domain_terms(f"{normalized_text} {original_text}")

    tax_type: Optional[str] = None
    if terms:
        first = terms[0]
        tax_type = "GST" if first in ("GST", "GSTIN", "CGST", "SGST", "IGST", "ITC") else first

    intent = INTENT_GENERAL
    for name, keywords in INTENT_KEYWORDS:
        if any(k in blob for k in keywords):
            intent = name
            break

    return {
        "intent": intent,
        "tax_type": tax_type,
        "entity_confidence": 0.94 if tax_type else 0.60,
    }
