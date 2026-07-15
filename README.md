# Tax Agent — Enterprise AI Tax Assistant

An enterprise-grade, conversational AI assistant specialised for Indian taxation
(GST, income tax, corporate tax, TDS/TCS, transfer pricing, customs, international
tax, compliance, notices, and audit support). It runs **fully offline** with a
built-in mock LLM and knowledge base, and switches to Claude (or a cloud provider)
by setting one environment variable.

> This repository began as a multilingual voice gateway POC and was rebuilt into
> Tax Agent. The gateway's value is **folded into the chat**: ask in Hindi or
> Hinglish and Tax Agent detects the language, normalises it to English for
> retrieval, and answers (the original message is preserved in the transcript).
> The gateway endpoints (`/api/text`, `/api/voice`, `/api/provider/switch`) remain
> available as a JSON API and are covered by tests; there is no separate gateway UI.

---

## Highlights

| Capability | How it's delivered |
|---|---|
| **Conversational, streaming chat** | Server-Sent Events, token-by-token, with stop/edit/regenerate |
| **Grounded answers (RAG)** | Chunking → hybrid BM25+cosine retrieval → rerank → citations → confidence |
| **Offline-first** | `MockLLM` synthesizes grounded answers, tool calls, citations, and simulates latency/errors/auth failures — zero credentials |
| **Real LLM** | Anthropic Claude (`claude-opus-4-8`) via streaming when `ANTHROPIC_API_KEY` is set |
| **Security by default** | Prompt-injection / jailbreak / harmful-request detection, PII redaction, rate limiting, audit log |
| **Premium UI** | Original minimal chat interface, markdown + code highlighting + tables, dark/light, voice dictation, attachments |
| **Multilingual** | Hindi/Hinglish input is detected and normalised to English before retrieval (reuses the original gateway's detector + normaliser); Tamil/Telugu/Kannada detected, full support via a cloud translation provider |
| **Cloud-agnostic** | Provider + vector-store abstractions; production guides for AWS, Azure, GCP |

---

## Quick start (offline, ≤ 2 minutes)

```powershell
# Windows PowerShell
powershell -ExecutionPolicy Bypass -File scripts\run.ps1
# then open http://127.0.0.1:8080/ui/
```

```bash
# macOS / Linux
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
cd backend && uvicorn main:app --port 8080
# open http://127.0.0.1:8080/ui/
```

No API keys required. The app boots with the offline mock LLM and a 13-document
tax knowledge base (73 chunks indexed at startup).

### Enable Claude

```bash
# backend/.env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-4-8
```

`pip install anthropic`, restart, and `/health` will report `llm.provider = anthropic`.

---

## Architecture

```
Browser (frontend/)  ── SSE ──►  FastAPI (backend/main.py)
  chat UI · dictation                     │
                                          ▼
                               ChatEngine (chat/engine.py)
             ┌────────────────────┼─────────────────────┐
             ▼                    ▼                     ▼
     Guardrails (security/)   Retriever (rag/)     LLM (llm/)
     injection · jailbreak    chunk · hybrid       mock ⇆ anthropic
     harmful · PII · rate     search · rerank      (stream, tools,
     limit · audit            · citations           citations)
```

Design docs: [ARCHITECTURE.md](ARCHITECTURE.md) · [SECURITY.md](SECURITY.md) ·
[docs/API.md](docs/API.md) · cloud guides under [deployment/](deployment/).

### Module map (`backend/`)

| Package | Responsibility |
|---|---|
| `llm/` | LLM provider contract, `MockLLM`, `AnthropicLLM`, factory |
| `rag/` | `Document`/`Chunk` model, chunker, `VectorStore` (in-memory), retriever |
| `security/` | detectors, PII, rate limiter, audit log, guardrail pipeline |
| `chat/` | conversation store, attachment extraction, SSE engine |
| `chat_api.py` | chat/admin HTTP routes |
| `config/`, `providers/`, `services/` | settings, voice STT/translation providers, voice orchestrator |

---

## Adding your Tax Risk Assessment dataset

The ingestion contract is `rag.chunker.Document`. Drop `.md`/`.txt` files into
`backend/rag/corpus/` and restart — they are chunked and indexed automatically,
no code changes. For other formats, convert to text and call
`Retriever.index_documents([...])`. To scale beyond in-memory, implement
`rag.store.VectorStore` against OpenSearch / Azure AI Search / Vertex Vector
Search (see the cloud guides) — the rest of the pipeline is untouched.

---

## Testing

```bash
cd backend && python -m pytest       # 78 tests
```

Covers detectors, PII, rate limiting, guardrail decisions, RAG chunk/retrieve/
rerank, conversation store, mock-LLM streaming + tool calls + simulated failures,
and the chat SSE endpoints. See [TESTING.md](TESTING.md).

## Enterprise simulation

```bash
cd backend && python -m simulation.enterprise_demo
```

Runs multi-user workflows (employee enquiries, finance support, GST compliance,
risk identification, notice interpretation, audit prep, summarisation, admin
monitoring) against the engine and writes a report. See
[docs/ENTERPRISE_SIMULATION.md](docs/ENTERPRISE_SIMULATION.md).

---

## Configuration

All settings are environment variables (see `backend/.env.example`). Key ones:

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `mock` or `anthropic` |
| `ANTHROPIC_API_KEY` | – | Claude credential |
| `ENABLE_GUARDRAILS` | `true` | Master switch for security checks |
| `SECURITY_BLOCK_THRESHOLD` | `0.80` | Detector score at/above which a request is refused |
| `RATE_LIMIT_PER_MINUTE` | `30` | Per-client request budget |
| `RAG_TOP_K` / `RAG_RERANK_TOP_N` | `8` / `4` | Retrieval and rerank widths |

Secrets are never logged; PII is masked before it reaches the audit log.

