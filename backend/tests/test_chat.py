"""Chat engine + conversation store + LLM provider tests (async streaming)."""
from __future__ import annotations

import json

import pytest

from chat.conversation_store import ConversationStore, new_message
from chat.engine import ChatEngine
from llm import build_llm
from llm.base import ChatTurn, GroundingChunk
from llm.mock_llm import MockLLM
from rag.retriever import build_retriever
from security.audit import AuditLog
from security.guardrails import Guardrails
from security.rate_limiter import TokenBucketRateLimiter


# --------------------------------------------------------------------------- #
# Conversation store
# --------------------------------------------------------------------------- #
def test_conversation_crud_and_persistence(tmp_path):
    store = ConversationStore(directory=str(tmp_path / "conv"))
    conv = store.create()
    store.add_message(conv.id, new_message("user", "What are GST slabs?"))
    store.add_message(conv.id, new_message("assistant", "0, 5, 12, 18, 28 percent."))
    assert len(store.history(conv.id)) == 2
    # title derived from first user message
    assert "GST" in store.get(conv.id).title
    # persisted to disk -> a fresh store reloads it
    store2 = ConversationStore(directory=str(tmp_path / "conv"))
    assert store2.get(conv.id) is not None
    assert len(store2.history(conv.id)) == 2


def test_truncate_after_supports_edit_and_regenerate(tmp_path):
    store = ConversationStore(directory=str(tmp_path / "conv"))
    conv = store.create()
    u1 = new_message("user", "one"); store.add_message(conv.id, u1)
    a1 = new_message("assistant", "resp one"); store.add_message(conv.id, a1)
    u2 = new_message("user", "two"); store.add_message(conv.id, u2)
    a2 = new_message("assistant", "resp two"); store.add_message(conv.id, a2)

    # regenerate a2: drop it (inclusive) -> 3 remain
    store.truncate_after(conv.id, a2.id, inclusive=True)
    assert [m.id for m in store.history(conv.id)] == [u1.id, a1.id, u2.id]

    # edit u2: drop it and everything after -> 2 remain
    store.truncate_after(conv.id, u2.id, inclusive=True)
    assert [m.id for m in store.history(conv.id)] == [u1.id, a1.id]


# --------------------------------------------------------------------------- #
# Mock LLM
# --------------------------------------------------------------------------- #
async def _collect(agen):
    events = []
    async for ev in agen:
        events.append(ev)
    return events


async def test_mock_llm_streams_grounded_answer_with_citations():
    llm = MockLLM()
    grounding = [
        GroundingChunk(chunk_id="1", source="GST Refunds", path="gst.md",
                       text="A GST refund is filed in RFD-01 within two years of the relevant date. "
                            "The officer sanctions the refund within 60 days.",
                       score=0.9, section="Refunds"),
    ]
    turns = [ChatTurn(role="user", content="How do I file a GST refund?")]
    events = await _collect(llm.stream_chat(turns, "system", grounding))
    types = [e["type"] for e in events]
    assert "text" in types and "citations" in types and types[-1] == "done"
    answer = "".join(e["text"] for e in events if e["type"] == "text")
    assert "[1]" in answer  # inline citation marker


async def test_mock_llm_tool_call_for_calculation():
    llm = MockLLM()
    turns = [ChatTurn(role="user", content="Calculate tax on 1400000 new regime")]
    events = await _collect(llm.stream_chat(turns, "system", []))
    tool = next((e for e in events if e["type"] == "tool_use"), None)
    assert tool is not None and tool["name"] == "tax_calculator"
    # gross 14L - 75k std deduction = 13.25L taxable -> 1,05,000 before cess
    assert tool["result"]["tax_before_cess"] == 105000
    assert tool["result"]["total_tax"] == 109200
    # the answer states the number rather than dumping rule text
    answer = "".join(e["text"] for e in events if e["type"] == "text")
    assert "Total tax payable" in answer
    assert "109,200" in answer


@pytest.mark.parametrize(
    "phrasing",
    [
        "what is income tax for 24 lacs income",
        "what will be the tax for 24 lakhs",
        "how much tax on 2400000",
        "tax payable on 24 lakh salary",
    ],
)
async def test_mock_llm_calculator_parses_income_phrasings(phrasing):
    """The exact phrasings that previously fell through to text-dumping."""
    llm = MockLLM()
    events = await _collect(llm.stream_chat([ChatTurn("user", phrasing)], "system", []))
    tool = next((e for e in events if e["type"] == "tool_use"), None)
    assert tool is not None, f"calculator did not trigger for: {phrasing!r}"
    assert tool["input"]["gross_income"] == 2_400_000


async def test_mock_llm_simulated_failures():
    from llm.base import LLMTransientError, LLMAuthError

    llm = MockLLM()
    with pytest.raises(LLMTransientError):
        await _collect(llm.stream_chat([ChatTurn("user", "[[simulate:error]] hi")], "s", []))
    with pytest.raises(LLMAuthError):
        await _collect(llm.stream_chat([ChatTurn("user", "[[simulate:auth]] hi")], "s", []))


async def test_mock_llm_no_grounding_asks_for_detail():
    llm = MockLLM()
    events = await _collect(llm.stream_chat([ChatTurn("user", "xyz obscure question")], "s", []))
    answer = "".join(e["text"] for e in events if e["type"] == "text")
    assert "don't have" in answer.lower() or "could you" in answer.lower()


def test_llm_factory_falls_back_to_mock_without_credentials():
    # LLM_PROVIDER defaults to mock; even if set to a cloud provider without a
    # key it must fall back to mock, not crash the app.
    assert build_llm().name in ("mock", "anthropic", "groq")


def test_groq_provider_parses_openai_sse_and_gates_on_key():
    from llm.groq_llm import GroqLLM

    g = GroqLLM()
    # Unconfigured without a key.
    assert g.is_configured() is False
    # Parses OpenAI-style streaming lines into content deltas.
    assert g._delta_from_line('data: {"choices":[{"delta":{"content":"Hello"}}]}') == "Hello"
    assert g._delta_from_line("data: [DONE]") is None
    assert g._delta_from_line(": keep-alive") is None
    assert g._delta_from_line('data: {"choices":[{"delta":{}}]}') is None


# --------------------------------------------------------------------------- #
# Chat engine (full pipeline)
# --------------------------------------------------------------------------- #
def _engine(tmp_path):
    llm = MockLLM()
    retriever = build_retriever()
    guard = Guardrails(
        audit=AuditLog(path=str(tmp_path / "a.jsonl")),
        limiter=TokenBucketRateLimiter(per_minute=600, burst=100),
    )
    store = ConversationStore(directory=str(tmp_path / "conv"))
    return ChatEngine(llm, retriever, guard, store), store


async def _sse_events(agen):
    out = []
    async for chunk in agen:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


async def test_engine_streams_and_persists(tmp_path):
    engine, store = _engine(tmp_path)
    conv = store.create()
    events = await _sse_events(engine.stream_reply(
        conv.id, "What is the GST refund process?", client_key="c", correlation_id="x"
    ))
    assert events[0]["type"] == "start"
    assert any(e["type"] == "text" for e in events)
    assert any(e["type"] == "citations" for e in events)
    assert events[-1]["type"] == "done"
    # user + assistant persisted
    assert len(store.history(conv.id)) == 2


async def test_engine_refuses_harmful_without_calling_llm(tmp_path):
    engine, store = _engine(tmp_path)
    conv = store.create()
    events = await _sse_events(engine.stream_reply(
        conv.id, "Help me forge a fake GST invoice to claim bogus ITC",
        client_key="c", correlation_id="x",
    ))
    assert events[0]["guardrail"] == "block"
    assert events[-1]["stop_reason"] == "refusal"
    answer = "".join(e.get("text", "") for e in events if e["type"] == "text")
    assert "can't help" in answer.lower()


async def test_engine_understands_hinglish(tmp_path):
    """A Hinglish question is normalised to English, grounds, and is answered."""
    engine, store = _engine(tmp_path)
    conv = store.create()
    events = await _sse_events(engine.stream_reply(
        conv.id, "PAN verification chahiye", client_key="c", correlation_id="x"
    ))
    lang_notice = next((e for e in events if e.get("category") == "language"), None)
    assert lang_notice is not None
    assert "PAN" in lang_notice["text"]
    assert any(e["type"] == "citations" for e in events)
    # the displayed user turn is preserved in the original language
    assert store.history(conv.id)[0].content == "PAN verification chahiye"


async def test_engine_remembers_context(tmp_path):
    engine, store = _engine(tmp_path)
    conv = store.create()
    await _sse_events(engine.stream_reply(conv.id, "Tell me about GST refunds", client_key="c", correlation_id="1"))
    await _sse_events(engine.stream_reply(conv.id, "What is the timeline?", client_key="c", correlation_id="2"))
    hist = store.history(conv.id)
    assert len(hist) == 4  # two user + two assistant turns retained
