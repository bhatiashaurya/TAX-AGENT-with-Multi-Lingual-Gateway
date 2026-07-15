"""Pydantic request models -- validation + OpenAPI documentation."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from config.settings import settings

ProviderName = Literal["azure", "aws", "gcp", "mock"]


class TextRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Raw user text (any supported language / Hinglish).")
    provider: Optional[ProviderName] = Field(None, description="Override provider for this request.")
    session_id: Optional[str] = Field(None, description="Attach to an existing multi-turn session.")
    include_confidence: bool = Field(True, description="Include confidence scores in the response.")
    debug_mode: bool = Field(False, description="Include intermediate pipeline steps.")
    return_original_language: bool = Field(True, description="Include detected language block.")

    @field_validator("text")
    @classmethod
    def _within_char_limit(cls, v: str) -> str:
        if len(v) > settings.MAX_TEXT_CHARS:
            raise ValueError(
                f"Input text exceeds maximum character limit "
                f"({len(v)} > {settings.MAX_TEXT_CHARS})"
            )
        if not v.strip():
            raise ValueError("Input text must not be blank.")
        return v


class ProviderSwitchRequest(BaseModel):
    new_provider: ProviderName
    make_default: bool = Field(False, description="If true, change the process-wide default provider.")
    test_connectivity: bool = Field(True, description="Health-check the provider before switching.")
