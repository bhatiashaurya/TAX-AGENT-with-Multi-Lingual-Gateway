"""
Static domain knowledge for the NTH Multilingual Voice Gateway.

Everything here is pure data (no runtime dependencies) so it can be imported by
any layer -- services, providers, utils, tests -- without creating import cycles.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Languages & scripts
# ---------------------------------------------------------------------------
# Metadata for every language the gateway is expected to accept.  ``script`` is
# the dominant Unicode script; ``bcp47`` is the tag cloud STT/TTS APIs expect.
SUPPORTED_LANGUAGES: dict[str, dict] = {
    "hi": {"name": "Hindi", "script": "Devanagari", "native": "हिन्दी", "bcp47": "hi-IN"},
    "mr": {"name": "Marathi", "script": "Devanagari", "native": "मराठी", "bcp47": "mr-IN"},
    "ta": {"name": "Tamil", "script": "Tamil", "native": "தமிழ்", "bcp47": "ta-IN"},
    "te": {"name": "Telugu", "script": "Telugu", "native": "తెలుగు", "bcp47": "te-IN"},
    "kn": {"name": "Kannada", "script": "Kannada", "native": "ಕನ್ನಡ", "bcp47": "kn-IN"},
    "en": {"name": "English", "script": "Latin", "native": "English", "bcp47": "en-IN"},
}

# Contiguous Unicode code-point ranges used to classify a single character to a
# script.  Order does not matter; ranges are disjoint.
SCRIPT_RANGES: dict[str, list[tuple[int, int]]] = {
    "Devanagari": [(0x0900, 0x097F), (0xA8E0, 0xA8FF)],
    "Tamil": [(0x0B80, 0x0BFF)],
    "Telugu": [(0x0C00, 0x0C7F)],
    "Kannada": [(0x0C80, 0x0CFF)],
    "Latin": [(0x0041, 0x005A), (0x0061, 0x007A)],
}

# Which language a script maps to when it is the dominant script in the text.
SCRIPT_TO_LANG: dict[str, str] = {
    "Devanagari": "hi",  # shared with Marathi; disambiguation is out of POC scope
    "Tamil": "ta",
    "Telugu": "te",
    "Kannada": "kn",
    "Latin": "en",
}

# ---------------------------------------------------------------------------
# Tax domain terminology -- these must survive translation verbatim.
# ---------------------------------------------------------------------------
TAX_DOMAIN_TERMS: set[str] = {
    "GST", "GSTIN", "TDS", "TCS", "ITR", "PAN", "TAN", "AADHAAR", "HRA", "ITC",
    "AIS", "EPF", "PPF", "NPS", "DIN", "CIN", "UAN", "KYC", "OTP", "IFSC",
    "26AS", "80C", "80D", "ITAT", "AO", "NIL", "CGST", "SGST", "IGST", "TReDS",
}

# Normalise common spelling variants to their canonical token.
TAX_TERM_ALIASES: dict[str, str] = {
    "AADHAR": "AADHAAR",
    "ADHAAR": "AADHAAR",
    "GSTN": "GST",
    "INCOMETAX": "ITR",
}

# ---------------------------------------------------------------------------
# Hinglish lexicon.
# ---------------------------------------------------------------------------
# Maps a (lower-cased) romanised or Devanagari function word to a grammatical
# category + its English gloss.  Categories drive the reordering rules in the
# normalizer:
#   POSS -> possessive pronoun     Q  -> question word
#   BE   -> copula (is/are/was)    V  -> verb (imperative intent)
#   OF   -> genitive particle      DROP -> grammatical particle to discard
#   N    -> content noun override
HINGLISH_LEXICON: dict[str, tuple[str, str]] = {
    # possessives
    "mera": ("POSS", "my"), "meri": ("POSS", "my"), "mere": ("POSS", "my"),
    "apna": ("POSS", "my"), "apni": ("POSS", "my"),
    "aapka": ("POSS", "your"), "apka": ("POSS", "your"), "tumhara": ("POSS", "your"),
    "मेरा": ("POSS", "my"), "मेरी": ("POSS", "my"), "मेरे": ("POSS", "my"),
    "अपना": ("POSS", "my"), "आपका": ("POSS", "your"), "तुम्हारा": ("POSS", "your"),
    # question words
    "kahan": ("Q", "where"), "kaha": ("Q", "where"), "kidhar": ("Q", "where"),
    "kab": ("Q", "when"), "kya": ("Q", "what"), "kaise": ("Q", "how"),
    "kaisa": ("Q", "how"), "kitna": ("Q", "how much"), "kitni": ("Q", "how much"),
    "कहाँ": ("Q", "where"), "कहां": ("Q", "where"), "किधर": ("Q", "where"),
    "कब": ("Q", "when"), "क्या": ("Q", "what"), "कैसे": ("Q", "how"),
    "कितना": ("Q", "how much"), "कितनी": ("Q", "how much"),
    # copula
    "hai": ("BE", "is"), "hain": ("BE", "are"), "tha": ("BE", "was"),
    "hoga": ("BE", "will be"),
    "है": ("BE", "is"), "हैं": ("BE", "are"), "था": ("BE", "was"),
    # verbs (imperative intent)
    "karo": ("V", "check"), "kardo": ("V", "check"), "kar": ("V", "check"),
    "karna": ("V", "check"), "karana": ("V", "check"),
    "batao": ("V", "tell me"), "bata": ("V", "tell me"), "bataye": ("V", "tell me"),
    "bataiye": ("V", "tell me"), "batauo": ("V", "tell me"),
    "dikhao": ("V", "show"), "dikha": ("V", "show"), "dikhaye": ("V", "show"),
    "bhejo": ("V", "send"), "bhej": ("V", "send"),
    "bharo": ("V", "file"), "bhardo": ("V", "file"), "bharna": ("V", "file"),
    "chahiye": ("V", "need"), "chahie": ("V", "need"),
    "nikalo": ("V", "withdraw"), "nikaalo": ("V", "withdraw"),
    "करो": ("V", "check"), "करना": ("V", "check"), "बताओ": ("V", "tell me"),
    "बताइए": ("V", "tell me"), "दिखाओ": ("V", "show"), "भेजो": ("V", "send"),
    "भरो": ("V", "file"), "चाहिए": ("V", "need"),
    # genitive particles
    "ka": ("OF", "of"), "ki": ("OF", "of"), "ke": ("OF", "of"),
    "का": ("OF", "of"), "की": ("OF", "of"), "के": ("OF", "of"),
    # droppable particles
    "ko": ("DROP", ""), "mein": ("DROP", ""), "me": ("DROP", ""), "par": ("DROP", ""),
    "se": ("DROP", ""), "wala": ("DROP", ""), "wali": ("DROP", ""), "hi": ("DROP", ""),
    "को": ("DROP", ""), "में": ("DROP", ""), "पर": ("DROP", ""), "से": ("DROP", ""),
    # noun overrides (Devanagari content words we want in English)
    "स्थिति": ("N", "status"), "वापसी": ("N", "refund"), "रिटर्न": ("N", "return"),
    "नंबर": ("N", "number"), "रसीद": ("N", "receipt"), "भुगतान": ("N", "payment"),
    "सुधार": ("N", "correction"), "जमा": ("V", "file"),
}
# NOTE: "hi" appears twice above (copula vs particle); the DROP entry wins because
# it is defined later.  In practice romanised "hi" is the English filler/particle,
# so dropping it is the safe choice.

# Content nouns we recognise as valid English -- used purely for confidence
# scoring (a query full of known words scores higher than one full of typos).
KNOWN_EN: set[str] = {
    "refund", "status", "return", "returns", "correction", "request", "verification",
    "verify", "detail", "details", "amount", "date", "payment", "pay", "notice",
    "filing", "file", "credit", "number", "reference", "record", "balance", "challan",
    "deadline", "due", "report", "statement", "account", "form", "registration",
    "link", "update", "download", "receipt", "check", "show", "send", "tell", "need",
    "income", "tax", "refunds", "my", "your", "where", "when", "what", "how", "is",
}

# ---------------------------------------------------------------------------
# Intent taxonomy -- keyword driven for the POC.
# ---------------------------------------------------------------------------
INTENT_STATUS_CHECK = "STATUS_CHECK"
INTENT_FILE_RETURN = "FILE_RETURN"
INTENT_CORRECTION = "CORRECTION"
INTENT_VERIFICATION = "VERIFICATION"
INTENT_PAYMENT = "PAYMENT"
INTENT_GENERAL = "GENERAL_QUERY"

# Checked top-to-bottom; first matching keyword set wins.  Note: "return" is
# deliberately NOT a FILE_RETURN trigger -- it is usually the *object* of a
# status query ("where is my return"), so filing is keyed on explicit verbs.
INTENT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (INTENT_CORRECTION, ("correction", "correct", "amend", "rectify", "revise")),
    (INTENT_VERIFICATION, ("verify", "verification", "validate", "kyc")),
    (INTENT_PAYMENT, ("payment", "challan", "deposit", "pay ")),
    (INTENT_FILE_RETURN, ("file", "filing", "submit")),
    (INTENT_STATUS_CHECK, ("status", "where", "track", "tell", "show", "refund", "when")),
]

# ---------------------------------------------------------------------------
# Regex patterns for tax entity extraction (used by the mock agent to decide
# whether the user has supplied an identifier we can "look up").
# ---------------------------------------------------------------------------
ENTITY_PATTERNS: dict[str, str] = {
    # PAN: 5 letters, 4 digits, 1 letter  (e.g. ABCDE1234F)
    "PAN": r"\b[A-Z]{5}\d{4}[A-Z]\b",
    # GSTIN: 15 alphanumerics starting with 2 digits
    "GSTIN": r"\b\d{2}[A-Z0-9]{13}\b",
    # Reference number: TDS-2024-56789 / ITR-2023-001 style
    "REFERENCE": r"\b[A-Z]{2,4}-?\d{2,4}-?\d{3,7}\b",
}

# ---------------------------------------------------------------------------
# Error codes surfaced to clients.
# ---------------------------------------------------------------------------
class ErrorCode:
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    ALL_PROVIDERS_FAILED = "ALL_PROVIDERS_FAILED"
    TRANSLATION_FAILED = "TRANSLATION_FAILED"
    SPEECH_RECOGNITION_FAILED = "SPEECH_RECOGNITION_FAILED"
    AUDIO_VALIDATION_ERROR = "AUDIO_VALIDATION_ERROR"
    AGENT_API_ERROR = "AGENT_API_ERROR"
    UNKNOWN_PROVIDER = "UNKNOWN_PROVIDER"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# Indicative unit economics per request (USD) used to estimate cost in /metrics.
# Sourced from the provider comparison matrix in the design doc; mock is free.
PROVIDER_UNIT_COST_USD: dict[str, float] = {
    "azure": 0.0042,
    "gcp": 0.0033,
    "aws": 0.0038,
    "mock": 0.0,
}

# Fields that must never appear in logs in the clear.
SENSITIVE_FIELD_HINTS: tuple[str, ...] = (
    "key", "secret", "token", "password", "authorization", "pan", "gstin", "aadhaar",
)
