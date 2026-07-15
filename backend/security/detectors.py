"""
Threat detectors for the guardrail pipeline.

Each detector scores an input 0..1 and returns matched signals. Scores feed the
guardrail's block/flag decision (thresholds in settings). The detectors are
heuristic and transparent by design — a real deployment would layer an LLM
classifier on top, but these catch the documented attack classes deterministically
and give the security-review and enterprise-simulation something concrete to show.

Detectors are ONLY meant to catch abuse of *this* assistant. Legitimate tax
questions — including "how do I lower my tax", "what penalties apply", "explain
tax evasion vs avoidance" — must pass. The harmful-request detector therefore
keys on intent to *commit/conceal* wrongdoing, not on topic mention.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class DetectionResult:
    category: str
    score: float
    signals: list[str] = field(default_factory=list)

    @property
    def triggered(self) -> bool:
        return self.score > 0.0


def _scan(text: str, patterns: list[tuple[str, float, str]]) -> DetectionResult:
    lowered = text.lower()
    score = 0.0
    signals: list[str] = []
    for pattern, weight, label in patterns:
        if re.search(pattern, lowered):
            score += weight
            signals.append(label)
    return DetectionResult(category="", score=min(1.0, score), signals=signals)


# --------------------------------------------------------------------------- #
# Prompt injection
# --------------------------------------------------------------------------- #
_INJECTION = [
    (r"ignore (all|any|the|your|previous|prior|above)?\s*(previous|prior|earlier|above|preceding)?\s*(instructions?|prompts?|rules?|context)", 0.85, "ignore-instructions"),
    (r"disregard (the|your|all|any|previous|prior)?\s*(system|previous|prior|above)?\s*(prompt|instructions?|rules?|guidelines?)", 0.85, "disregard-instructions"),
    (r"(reveal|show|print|repeat|display|output|tell me) (me )?(your |the )?(system|initial|original|hidden|secret) ?(prompt|instructions?|rules?|configuration)", 0.85, "extract-system-prompt"),
    (r"what (are|were) your (system |initial |original )?(instructions?|prompt|rules?)", 0.5, "probe-instructions"),
    (r"repeat (everything|the text|all text) (above|before)", 0.5, "repeat-above"),
    (r"you are now|from now on,? you|new (instructions?|rules?|system prompt)\s*[:\-]", 0.45, "role-override"),
    (r"</?(system|instructions?|admin)>|\[/?(system|inst)\]", 0.4, "delimiter-injection"),
    (r"(developer|admin|root|debug|god) mode", 0.4, "mode-override"),
    (r"print (everything |all )?(above|preceding|previous)", 0.4, "print-context"),
]

# --------------------------------------------------------------------------- #
# Jailbreak
# --------------------------------------------------------------------------- #
_JAILBREAK = [
    (r"\b(dan|do anything now|stan|dude|aim)\b.{0,40}(mode|jailbreak|prompt)", 0.85, "named-jailbreak"),
    (r"pretend (you are|to be|that you).{0,40}(no|without) (restrictions?|rules?|filters?|guidelines?)", 0.85, "pretend-unrestricted"),
    (r"(act|roleplay|role-play) as (an? )?(unfiltered|unrestricted|amoral|evil|uncensored)", 0.85, "roleplay-unrestricted"),
    (r"you have no (restrictions?|limits?|rules?|guidelines?|filters?)", 0.55, "assert-no-limits"),
    (r"(bypass|circumvent|get around|disable|turn off) (your |the )?(safety|guardrails?|filters?|restrictions?|rules?)", 0.85, "bypass-safety"),
    (r"hypothetically,? (if )?you (could|were able to) (ignore|bypass)", 0.45, "hypothetical-bypass"),
    (r"for (educational|research) purposes only,? (ignore|bypass|explain how to)", 0.4, "edu-pretext"),
    (r"answer (only |just )?(with )?(yes or no|the raw)|no (disclaimers?|warnings?|caveats?)", 0.3, "suppress-safety-text"),
]

# --------------------------------------------------------------------------- #
# Harmful / illegal — intent to commit or conceal, not topic mention
# --------------------------------------------------------------------------- #
_HARMFUL = [
    (r"how (can|do|to) i? ?(evade|dodge|escape) (paying )?(tax|gst|tds|duty)", 0.7, "tax-evasion-howto"),
    (r"(hide|conceal|hiding|concealing|not report|underreport|under-report|avoid declaring) (my |the |taxable |cash )?(income|revenue|turnover|earnings|cash|sales)", 0.7, "conceal-income"),
    (r"(fake|forge|forged|fabricate|fabricated|create false|generate fake) (a )?(gst )?(invoice|bill|receipt|document|itr|return|form 16|challan)", 0.8, "document-forgery"),
    (r"(claim|generate) (fake|bogus|fraudulent) (itc|input tax credit|refund|deduction|expense)", 0.8, "fraudulent-claim"),
    (r"(shell|dummy|fake|benami) (company|companies|firm|account) (to|for) (hide|launder|evade|avoid)", 0.8, "shell-company"),
    (r"launder(ing)? (money|cash|funds|black money)", 0.8, "money-laundering"),
    (r"(round.trip|round trip|hawala|layering) (transactions?|funds?|money)", 0.6, "laundering-technique"),
    (r"(someone else'?s?|stolen|fake) (pan|aadhaar|gstin|identity|credentials)", 0.7, "identity-theft"),
    (r"(without|avoid) (getting caught|detection|the department (finding|knowing))", 0.6, "evade-detection"),
    (r"two sets of (books|accounts)|parallel (books|accounting)|off.the.books", 0.7, "dual-books"),
    (r"(write|generate|create) (malware|ransomware|a virus|a keylogger|an exploit)", 0.85, "malware"),
    (r"(sql injection|privilege escalation|exfiltrat|ddos|denial of service) ", 0.6, "cyber-attack"),
    (r"steal (credentials?|passwords?|data|pii|customer data)", 0.7, "data-theft"),
]

# Legitimate-intent guards that pull the harmful score back down. These fire on
# framings that are clearly advisory/compliance, so "difference between tax
# evasion and avoidance" or "penalties for hiding income" aren't refused.
_BENIGN_GUARDS = [
    r"\b(difference between|versus|vs\.?|compared to)\b",
    r"\b(penalty|penalties|punishment|consequences?|legal|is it legal|allowed|permitted)\b.{0,30}(for|of|to)",
    r"\bhow (do i |to )?(avoid|prevent|stay compliant|comply|report|disclose)",
    r"\b(legitimately|legally|lawfully|within the law|compliant|compliance)\b",
    r"\bwhat happens if\b",
    r"\b(reduce|lower|save|minimi[sz]e) (my |the )?tax (legally|liability|burden|through deductions?)",
]


def detect_injection(text: str) -> DetectionResult:
    r = _scan(text, _INJECTION)
    r.category = "prompt_injection"
    return r


def detect_jailbreak(text: str) -> DetectionResult:
    r = _scan(text, _JAILBREAK)
    r.category = "jailbreak"
    return r


def detect_harmful(text: str) -> DetectionResult:
    r = _scan(text, _HARMFUL)
    r.category = "harmful_request"
    if r.triggered:
        lowered = text.lower()
        benign_hits = sum(1 for g in _BENIGN_GUARDS if re.search(g, lowered))
        if benign_hits:
            # Advisory framing: damp the score so genuine compliance questions pass.
            r.score = max(0.0, r.score - 0.4 * benign_hits)
            if r.score < 0.45:
                r.signals.append("benign-framing-detected")
    return r


def run_all(text: str) -> list[DetectionResult]:
    return [
        d for d in (detect_injection(text), detect_jailbreak(text), detect_harmful(text))
        if d.triggered
    ]
