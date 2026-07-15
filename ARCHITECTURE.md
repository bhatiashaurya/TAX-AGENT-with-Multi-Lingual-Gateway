# Tax Agent — Architecture

## Overview

Tax Agent is a layered, cloud-agnostic conversational AI system. Every external
dependency (the LLM, the vector store, speech services) sits behind an interface,
so the offline build and any cloud build share the same core code.

```
┌──────────────────────────────────────────────────────────────────┐
│ Frontend (frontend/)  — vanilla JS, no build step                 │
│   chat UI · SSE client · markdown/code/table render · dictation   │
└───────────────────────────────┬──────────────────────────────────┘
                                 │ HTTP + Server-Sent Events
┌───────────────────────────────▼──────────────────────────────────┐
│ API (backend/main.py, chat_api.py) — FastAPI                      │
│   correlation-id middleware · error envelopes · static UI mount   │
└───────────────────────────────┬──────────────────────────────────┘
                                 │
┌───────────────────────────────▼──────────────────────────────────┐
│ ChatEngine (chat/engine.py) — orchestration                       │
│   1 guardrails.check_input → block short-circuits                 │
│   2 retriever.retrieve      → grounding chunks                    │
│   3 llm.stream_chat         → tokens/tools/citations              │
│   4 guardrails.sanitize_output → mask high-risk PII               │
│   5 conversation_store.add_message → persist                     │
└─────┬───────────────────┬───────────────────────┬────────────────┘
      ▼                   ▼                       ▼
 Security (security/)  RAG (rag/)             LLM (llm/)
 detectors             chunker                base.LLMProvider
 pii                   store.VectorStore      MockLLM
 rate_limiter          retriever              AnthropicLLM
 audit                 corpus/*.md            build_llm()
 guardrails
```

## Design principles

- **Dependency inversion.** `ChatEngine` depends on the `LLMProvider`,
  `VectorStore`, and `Guardrails` abstractions, never on concretions. Providers
  are constructed in the composition root (`main.create_app`) and injected.
- **Offline-first.** `MockLLM` is a first-class provider, not a stub: it does
  real retrieval-grounded synthesis, tool calls, and failure simulation. The
  whole app is validated with no network.
- **Single choke point for safety.** All chat traffic passes `Guardrails`, which
  composes detectors, PII, rate limiting, and audit. New policies are added as
  checks without touching the engine.
- **Streaming everywhere.** Providers yield event dicts; the engine relays them
  as SSE. Backpressure and stop-generation fall out of the async generator model.
- **Strong typing.** Dataclasses (`ChatTurn`, `GroundingChunk`, `Message`,
  `Chunk`, `DetectionResult`) form the contracts between layers.

## Request lifecycle (chat)

1. Browser POSTs `multipart/form-data` (message + optional files) to
   `/api/chat/stream` and reads the SSE response body incrementally.
2. Attachments are extracted to text (`chat/attachments.py`) and folded into the
   prompt; the user turn is persisted.
3. `Guardrails.check_input` runs rate-limit → validation → threat detection and
   writes an audit line. A `block` streams a compliant refusal and **never calls
   the LLM**.
4. `Retriever.retrieve` does hybrid BM25+cosine search over the corpus, reranks,
   and returns grounding chunks with scores.
5. `LLMProvider.stream_chat` streams text/tool/citation events. Each text chunk
   is passed through output PII masking before being sent to the client.
6. The assistant turn (with citations + confidence) is persisted; a `done` event
   carries the message id used by edit/regenerate.

## Extension points

| To change… | Implement / edit | No change needed in |
|---|---|---|
| LLM backend (Bedrock, Azure OpenAI, Vertex) | `llm/base.LLMProvider` + factory | engine, RAG, security, UI |
| Vector DB (OpenSearch, AI Search, Vertex) | `rag/store.VectorStore` | retriever API, engine |
| Knowledge base | add files to `rag/corpus/` | any code |
| Security policy | add a detector or a check in `guardrails.py` | engine |
| Session storage (Redis, DynamoDB) | `chat/conversation_store.ConversationStore` | engine, API |

## Voice subsystem (retained)

The original voice-gateway (`providers/`, `services/`, `/api/voice`) remains for
server-side speech-to-text (faster-whisper offline, or Azure/GCP/AWS). The chat
UI's primary voice path is **browser dictation** (Web Speech API) streaming into
the composer; `/api/voice` is available for audio-file transcription.

## Performance & scalability

- Stateless request handling; conversation state is externalised (swap the store
  for Redis to scale horizontally).
- Retrieval is in-process and O(candidate postings); for large corpora, move to a
  managed vector DB via the `VectorStore` seam.
- SSE keeps memory flat regardless of answer length.
- Cloud deployment targets autoscaling serverless containers (Cloud Run / Azure
  Container Apps / App Runner) — see `deployment/`.
