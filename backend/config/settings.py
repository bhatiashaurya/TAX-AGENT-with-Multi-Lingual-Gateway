"""
Application configuration.

Uses ``pydantic-settings`` so every value can be overridden by an environment
variable (or a ``.env`` file) of the same name.  Validation runs at import time
so a misconfigured deployment fails fast instead of at first request.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Environment -------------------------------------------------------
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "text"] = "text"

    # -- Service identity --------------------------------------------------
    APP_NAME: str = "Tax Agent"
    APP_VERSION: str = "0.2.0"
    API_PREFIX: str = "/api"
    PORT: int = 8080

    # -- Input constraints -------------------------------------------------
    MAX_AUDIO_SIZE_MB: int = 3
    MAX_AUDIO_DURATION_SECONDS: int = 60
    MAX_TEXT_CHARS: int = 1000

    # -- Provider selection ------------------------------------------------
    # Defaults to ``mock`` so the gateway runs end-to-end with zero credentials.
    DEFAULT_PROVIDER: Literal["azure", "aws", "gcp", "mock"] = "mock"
    # Ordered fallback chain; unregistered / open-circuit providers are skipped.
    FALLBACK_PROVIDERS: list[str] = Field(default_factory=lambda: ["gcp", "aws", "azure", "mock"])
    # mock needs headroom because local whisper STT (when installed) runs on
    # the CPU: cold model load ~20s + transcription ~1s/10s of audio.
    PROVIDER_TIMEOUT_SECONDS: dict[str, float] = Field(
        default_factory=lambda: {"azure": 10, "aws": 15, "gcp": 12, "mock": 45}
    )

    # -- Retry / circuit breaker ------------------------------------------
    MAX_RETRIES: int = 2
    RETRY_BACKOFF_FACTOR: float = 2.0
    RETRY_INITIAL_DELAY_MS: int = 100
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS: int = 300

    # -- Caching -----------------------------------------------------------
    ENABLE_CACHING: bool = True
    CACHE_TTL_SECONDS: int = 3600
    CACHE_MAX_ENTRIES: int = 1000

    # -- Sessions ----------------------------------------------------------
    SESSION_TTL_MINUTES: int = 30
    SESSION_STORE: Literal["memory", "redis", "dynamodb"] = "memory"

    # -- Quality thresholds ------------------------------------------------
    NORMALIZATION_CONFIDENCE_THRESHOLD: float = 0.80

    # -- Agent -------------------------------------------------------------
    AGENT_API_URL: str = "http://localhost:9000"
    AGENT_API_TIMEOUT_SECONDS: int = 30
    USE_MOCK_AGENT: bool = True

    # -- Azure -------------------------------------------------------------
    AZURE_SPEECH_KEY: str = ""
    AZURE_SPEECH_REGION: str = "centralindia"
    AZURE_TRANSLATOR_KEY: str = ""
    AZURE_TRANSLATOR_ENDPOINT: str = "https://api.cognitive.microsofttranslator.com"
    AZURE_TRANSLATOR_REGION: str = "centralindia"

    # -- AWS ---------------------------------------------------------------
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "ap-south-1"
    AWS_TRANSCRIBE_BUCKET: str = ""

    # -- GCP ---------------------------------------------------------------
    GCP_PROJECT_ID: str = ""
    GOOGLE_APPLICATION_CREDENTIALS: str = ""

    # -- Local speech-to-text (offline, zero credentials) -------------------
    # When faster-whisper is installed the mock provider genuinely transcribes
    # uploaded audio on the CPU, so voice replies match the words actually
    # spoken.  Disable to fall back to the client transcript / canned samples.
    ENABLE_LOCAL_STT: bool = True
    LOCAL_STT_MODEL: str = "tiny"  # tiny | base | small  (bigger = better + slower)

    # -- Feature flags -----------------------------------------------------
    ENABLE_LANGUAGE_DETECTION: bool = True
    ENABLE_HINGLISH_NORMALIZATION: bool = True
    ENABLE_TEXT_TO_SPEECH: bool = False
    ENABLE_METRICS_COLLECTION: bool = True
    ENABLE_REQUEST_LOGGING: bool = True
    ENABLE_SENSITIVE_DATA_REDACTION: bool = True

    # -- CORS --------------------------------------------------------------
    # "*" keeps the POC frictionless when the UI is served from file:// or a
    # different port; credentials are disabled so this remains browser-legal.
    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["*"])
    CORS_ALLOW_CREDENTIALS: bool = False
    CORS_ALLOW_METHODS: list[str] = Field(default_factory=lambda: ["GET", "POST", "OPTIONS"])

    # -- LLM (Tax Agent chat) ------------------------------------------------
    # ``mock`` runs fully offline; ``anthropic`` uses Claude when a key is set.
    LLM_PROVIDER: Literal["mock", "anthropic"] = "mock"
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-opus-4-8"
    LLM_MAX_TOKENS: int = 16000
    LLM_TIMEOUT_SECONDS: float = 120.0
    # Mock realism: per-chunk streaming delay and simulated transient failures.
    MOCK_STREAM_DELAY_MS: int = 14
    MOCK_FAILURE_RATE: float = 0.0

    # -- Chat / conversations ------------------------------------------------
    MAX_MESSAGE_CHARS: int = 8000
    MAX_ATTACHMENT_MB: int = 5
    MAX_CONVERSATION_TURNS: int = 200
    CONVERSATION_DIR: str = "data/conversations"

    # -- RAG -----------------------------------------------------------------
    RAG_CORPUS_DIR: str = "rag/corpus"
    RAG_CHUNK_CHARS: int = 900
    RAG_CHUNK_OVERLAP: int = 150
    RAG_TOP_K: int = 8            # first-stage retrieval
    RAG_RERANK_TOP_N: int = 4     # kept after reranking
    RAG_MIN_SCORE: float = 0.05   # below this, treat as "no relevant knowledge"

    # -- Security guardrails ---------------------------------------------------
    ENABLE_GUARDRAILS: bool = True
    SECURITY_BLOCK_THRESHOLD: float = 0.80  # detector score >= this -> refuse
    SECURITY_FLAG_THRESHOLD: float = 0.45   # >= this -> allow but log + caution
    RATE_LIMIT_PER_MINUTE: int = 30
    RATE_LIMIT_BURST: int = 10
    AUDIT_LOG_PATH: str = "data/audit.jsonl"

    @property
    def max_audio_bytes(self) -> int:
        return self.MAX_AUDIO_SIZE_MB * 1024 * 1024

    @property
    def max_attachment_bytes(self) -> int:
        return self.MAX_ATTACHMENT_MB * 1024 * 1024

    def provider_timeout(self, provider: str) -> float:
        return float(self.PROVIDER_TIMEOUT_SECONDS.get(provider, 10))


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so settings are parsed exactly once per process."""
    return Settings()


settings = get_settings()
