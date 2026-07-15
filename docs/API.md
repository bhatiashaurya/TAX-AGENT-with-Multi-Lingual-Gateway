# API Reference

Base URL (local): `http://localhost:8080` · Interactive docs: `/docs`

## Chat (primary)

### `POST /api/chat/stream`
`multipart/form-data`. Streams **Server-Sent Events** (`text/event-stream`).

| Field | Required | Notes |
|---|---|---|
| `message` | yes | User message text |
| `conversation_id` | no | Omit to start a new conversation |
| `files` | no | One or more attachments (`.txt .md .csv .json .log .pdf`) |

**SSE event types** (one JSON object per `data:` line):

| `type` | Payload |
|---|---|
| `conversation` | `{id}` — the (possibly new) conversation id |
| `start` | `{correlation_id, guardrail}` where guardrail ∈ allow/flag/block |
| `notice` | `{text, category}` — guardrail caution or attachment issue |
| `retrieval` | `{sources: [{source, path, section, score}]}` |
| `tool_use` | `{name, input, result}` — e.g. `tax_calculator` |
| `text` | `{text}` — incremental answer tokens |
| `citations` | `{citations: [{index, source, path, section, score, snippet}], confidence}` |
| `done` | `{stop_reason, message_id}` — terminal event |

```bash
curl -N -X POST http://localhost:8080/api/chat/stream \
  -F "message=What are the GST return filing deadlines?"
```

### `POST /api/chat/regenerate`
`form: conversation_id, message_id` — drops the given assistant message and
re-streams a fresh answer (SSE, same events).

### `POST /api/chat/edit`
`form: conversation_id, message_id, message` — replaces a user message,
truncates everything after it, and re-streams (SSE).

### Conversations
| Method + path | Purpose |
|---|---|
| `POST /api/chat/conversations` | Create; returns `{id, title}` |
| `GET /api/chat/conversations` | List `{conversations: [{id, title, updated_at, message_count, preview}]}` |
| `GET /api/chat/conversations/{id}` | Full conversation with messages |
| `DELETE /api/chat/conversations/{id}` | Delete; `{deleted: bool}` |

## Admin / monitoring

### `GET /admin/security`
`{recent_security_events: [...], recent_activity: [...]}` — audit view for an ops
console. PII is masked in every entry.

## Health & metrics

### `GET /health`
Reports `service`, `llm` (provider, status), `rag` (indexed_chunks), `guardrails`,
`voice_providers`, and rolling `metrics`.

### `GET /metrics` · `GET /metrics/prometheus`
Usage/latency snapshot (JSON) and Prometheus exposition text.

## Voice (retained gateway)

### `POST /api/voice`
`multipart/form-data` with `audio` (WAV/MP3). Server-side STT (faster-whisper
offline, or a cloud provider), then the voice-gateway normalisation pipeline.
The chat UI's primary voice path is **browser dictation** into the composer.

### `POST /api/text`, `POST /api/provider/switch`
Retained voice-gateway text normalisation + provider switching (see the
gateway's original contract).
