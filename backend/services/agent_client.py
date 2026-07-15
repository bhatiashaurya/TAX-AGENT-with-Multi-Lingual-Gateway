"""
Tax agent client.

Defaults to an in-process mock agent (``USE_MOCK_AGENT=true``) that returns
domain-appropriate responses keyed by intent + tax type, and "resolves" queries
when the user supplies an identifier (PAN / GSTIN / reference number).  When a
real agent URL is configured it forwards the enriched context over HTTP instead.
"""
from __future__ import annotations

from typing import Any, Optional

from config.constants import (
    INTENT_CORRECTION,
    INTENT_FILE_RETURN,
    INTENT_PAYMENT,
    INTENT_STATUS_CHECK,
    INTENT_VERIFICATION,
)
from config.settings import settings
from utils.text_utils import extract_entities


class AgentClient:
    def __init__(self) -> None:
        self.use_mock = settings.USE_MOCK_AGENT
        self.base_url = settings.AGENT_API_URL

    async def route(
        self,
        normalized_text: str,
        intent: str,
        tax_type: Optional[str],
        session_id: str,
        original_text: str = "",
        history: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        if not self.use_mock:
            return await self._route_http(normalized_text, intent, tax_type, session_id, history)
        return self._route_mock(normalized_text, intent, tax_type, session_id, original_text, history or [])

    # -- mock agent --------------------------------------------------------
    def _route_mock(
        self, normalized_text: str, intent: str, tax_type: Optional[str],
        session_id: str, original_text: str, history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        tax = tax_type or "tax"
        entities = extract_entities(f"{normalized_text} {original_text}")

        # If the user supplied an identifier, "look it up" -- this is what makes
        # multi-turn conversations resolve on the second turn.
        if entities:
            identifier = next(iter(entities.values()))
            return self._resp(
                f"Found your record for {identifier}. Your {tax} refund was approved "
                f"on 10 January 2024 and credited to your registered bank account.",
                action="status_provided",
                session_id=session_id,
                reference_number=identifier,
            )

        if intent == INTENT_CORRECTION:
            ref = f"{tax}-2024-56789"
            return self._resp(
                f"Your {tax} correction request (Reference: {ref}) is under review. "
                f"Expected resolution by 30 January 2024.",
                action="status_provided",
                session_id=session_id,
                reference_number=ref,
            )

        if intent == INTENT_STATUS_CHECK:
            return self._resp(
                f"To check your {tax} status, please provide your "
                f"{'GSTIN' if tax == 'GST' else tax + ' reference number'} "
                f"or latest ITR reference number.",
                action="query_required",
                session_id=session_id,
                suggested_next_steps=["provide_gstin", "provide_reference_number"],
            )

        if intent == INTENT_VERIFICATION:
            return self._resp(
                f"To verify your {tax}, please share your {tax} and date of birth.",
                action="query_required",
                session_id=session_id,
                suggested_next_steps=["provide_identifier", "provide_dob"],
            )

        if intent == INTENT_FILE_RETURN:
            return self._resp(
                f"I can help you file your {tax} return. Which assessment year "
                f"would you like to file for?",
                action="query_required",
                session_id=session_id,
                suggested_next_steps=["provide_assessment_year"],
            )

        if intent == INTENT_PAYMENT:
            return self._resp(
                f"To generate a {tax} challan, please confirm the amount you wish to pay.",
                action="query_required",
                session_id=session_id,
                suggested_next_steps=["provide_amount"],
            )

        return self._resp(
            f"Could you clarify what you'd like to do regarding {tax}? "
            f"For example: check status, file a return, or request a correction.",
            action="clarification_required",
            session_id=session_id,
            suggested_next_steps=["check_status", "file_return", "request_correction"],
        )

    @staticmethod
    def _resp(message: str, *, action: str, session_id: str, **extra: Any) -> dict[str, Any]:
        return {"message": message, "action": action, "session_id": session_id, **extra}

    # -- real agent --------------------------------------------------------
    async def _route_http(
        self, normalized_text: str, intent: str, tax_type: Optional[str],
        session_id: str, history: Optional[list[dict[str, Any]]],
    ) -> dict[str, Any]:
        import httpx

        payload = {
            "query": normalized_text,
            "intent": intent,
            "tax_type": tax_type,
            "session_id": session_id,
            "history": history or [],
        }
        async with httpx.AsyncClient(timeout=settings.AGENT_API_TIMEOUT_SECONDS) as client:
            resp = await client.post(f"{self.base_url}/query", json=payload)
            resp.raise_for_status()
            data = resp.json()
        data.setdefault("session_id", session_id)
        return data
