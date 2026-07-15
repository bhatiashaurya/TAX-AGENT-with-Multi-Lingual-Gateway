# Tax Agent — Security

Security is a first-class layer, not an add-on. Every chat request passes through
`security.Guardrails` before the model is invoked, and every model token passes
through output sanitisation on the way out.

## Defensive layers

| Layer | Module | What it does |
|---|---|---|
| Input validation | `guardrails.py` | Rejects empty/oversized input before any processing |
| Rate limiting | `rate_limiter.py` | Per-client token bucket (`RATE_LIMIT_PER_MINUTE`, burst) |
| Prompt-injection detection | `detectors.py` | Flags attempts to override instructions, reveal the system prompt, or manipulate tools |
| Jailbreak detection | `detectors.py` | Flags persona/roleplay/"no rules" bypass attempts |
| Harmful-request detection | `detectors.py` | Flags tax evasion, concealment, forgery, laundering, identity theft, malware, data exfiltration |
| PII protection | `pii.py` | Detects PAN/Aadhaar/GSTIN/cards/emails/phones/keys; masks in output and audit |
| Output sanitisation | `guardrails.sanitize_output` | Masks high-risk identifiers a model might echo or invent |
| Audit logging | `audit.py` | Append-only JSONL with correlation id, decision, scores; PII masked before write |

## Decision model

Detectors score an input in `[0, 1]`. The guardrail takes the maximum across
detectors and decides:

- `score ≥ SECURITY_BLOCK_THRESHOLD` (default **0.80**) → **block**: stream a
  compliant refusal, do **not** call the LLM.
- `score ≥ SECURITY_FLAG_THRESHOLD` (default **0.45**) → **flag**: answer, but
  prepend a caution and log the event.
- otherwise → **allow**.

Refusals are helpful, not preachy: a harmful request is met with a lawful
alternative ("if your goal is to reduce tax legally, I can walk through
deductions…"). The assistant never reveals its system prompt, internal reasoning,
detector internals, or thresholds.

## Avoiding over-blocking

The harmful-request detector keys on intent to *commit or conceal* wrongdoing,
not on topic mention. A set of benign-intent guards damps the score for advisory
framings, so legitimate questions pass:

- "What is the **difference between** tax evasion and avoidance?" → allow
- "What **penalties** apply for hiding income?" → allow
- "How do I **reduce my tax legally** with deductions?" → allow
- "How can I **hide my income to evade tax without getting caught**?" → block

This is verified in `tests/test_security.py::test_legitimate_questions_pass`.

## PII handling

- **Detected:** PAN, Aadhaar, GSTIN, card numbers, emails, Indian phone numbers,
  API keys/secrets, `password:`/`api_key:` fields.
- **Output masking (`redact_for_output`)** masks only high-risk identifiers
  (Aadhaar, card, API key, secret fields) so the assistant never surfaces them,
  while leaving tax IDs the user themselves typed readable in the reply context.
- **Audit masking (`redact`)** masks *everything* sensitive before a log line is
  written. Raw PANs/Aadhaar never touch disk.

## Audit & governance integration

Each request writes one JSON line to `AUDIT_LOG_PATH`:

```json
{"ts":"…","correlation_id":"cid_…","client":"…","decision":"blocked",
 "category":"harmful_request","scores":{"harmful_request":0.7},
 "signals":["conceal-income"],"pii":["pan"],"preview":"…masked…"}
```

`/admin/security` exposes recent security events and activity for an ops console.
The `AuditLog` class is the seam to forward events to a SIEM / enterprise
governance platform (CloudWatch, Azure Monitor, Cloud Logging — see cloud guides).

## Secrets

- Credentials come from environment variables / `.env`, never source.
- The request logger records only method/path/status/timing — never bodies.
- In production, use the platform secret manager (AWS Secrets Manager, Azure Key
  Vault, GCP Secret Manager) and mount secrets as env vars at runtime.

## Threat coverage checklist

- [x] Override system instructions — injection detector
- [x] Reveal hidden prompt — injection detector + never-echo policy
- [x] Ignore policies / change behaviour — injection + jailbreak detectors
- [x] Tax fraud / evasion / concealment — harmful detector
- [x] Document forgery / fraudulent claims — harmful detector
- [x] Identity theft / credential theft — harmful detector + PII masking
- [x] Malware / privilege escalation / data exfiltration — harmful detector
- [x] PII / secrets exposure — PII detection + output/audit masking
- [x] Abuse / flooding — rate limiter
- [x] Request tracing — correlation ids + audit log
