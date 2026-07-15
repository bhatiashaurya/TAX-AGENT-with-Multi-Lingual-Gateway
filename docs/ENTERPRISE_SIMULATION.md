# Enterprise Simulation

`backend/simulation/enterprise_demo.py` drives the **real** ChatEngine (offline
mock LLM + RAG + guardrails) as six personas across an internal-tool workflow. It
is not scripted output — every answer is generated live through the same pipeline
the UI uses.

```bash
cd backend && python -m simulation.enterprise_demo
```

## Personas & workflows exercised

| Persona | Department | Workflow |
|---|---|---|
| Aarti Rao (Engineer) | Engineering | Employee tax enquiry (HRA); regime choice with **retained context** |
| Vikram Shah (Finance Manager) | Finance | GST compliance deadlines; **tax risk identification** (ITC vs GSTR-2B) |
| Priya Nair (Tax Analyst) | Finance | **Notice interpretation** (143(1)); **audit preparation** (Section 65) |
| Rahul Mehta (Procurement) | Operations | **Policy lookup** (TDS rates/thresholds) |
| Sara Khan (Controller) | Finance | **Document summarisation** (attachment folded into the prompt) |
| External (Unknown) | — | **Abuse attempts** — prompt injection + harmful request |

This covers every workflow in the spec: employee enquiries, finance support, GST
compliance, tax risk, notice interpretation, audit prep, policy lookup, document
summarisation, multi-user sessions (each persona is its own conversation and
rate-limit key), and administrative monitoring (audit log + `/admin/security`).

## What a run demonstrates

- **Grounded answers with citations** for every legitimate turn (8/10 grounded;
  the two ungrounded are the blocked abuse attempts).
- **Multi-user isolation** — separate conversations, separate rate-limit buckets.
- **Conversational memory** — Aarti's regime question is answered in the context
  of her prior HRA turn.
- **Security enforced live** — the injection attempt is neutralised (blocked) and
  the fraud request is refused with a compliant alternative; both are audited.
- **Latency profile** — average shown per run (mock streams with realistic delay).

## Outputs

- Console table (per-persona turns + a run summary).
- `data/enterprise_report.json` — full structured report (per turn: citations,
  guardrail decision, latency, answer preview).
- `data/enterprise_audit.jsonl` — the audit trail, with PII masked.

## Live administrative monitoring

With the server running, `GET /admin/security` returns recent security events and
activity — the ops view an administrator would watch during a real deployment.
The `AuditLog` class is the integration seam for a SIEM / governance platform.
