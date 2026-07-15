# Deployment

The gateway is a single stateless container (`backend/Dockerfile`, built from the
**repo root** so the UI is bundled). It reads config from environment variables
and listens on `$PORT` (default 8080) — which makes it drop-in for Cloud Run,
Azure Container Apps, and AWS App Runner / ECS.

```bash
# Build & run locally
docker build -f backend/Dockerfile -t nth-voice-gateway:latest .
docker run --rm -p 8080:8080 -e DEFAULT_PROVIDER=mock nth-voice-gateway:latest
```

Per-cloud, step-by-step guides:

* **GCP Cloud Run** — [deployment/gcp/deployment_guide.md](deployment/gcp/deployment_guide.md)
* **Azure Container Apps** — [deployment/azure/deployment_guide.md](deployment/azure/deployment_guide.md)
* **AWS App Runner / ECS** — [deployment/aws/deployment_guide.md](deployment/aws/deployment_guide.md)

## Environment variables (production)

| Variable | Example | Purpose |
|----------|---------|---------|
| `ENVIRONMENT` | `production` | toggles logging/behaviour |
| `DEFAULT_PROVIDER` | `gcp` | pick the cheapest/fastest for your region |
| `FALLBACK_PROVIDERS` | `["gcp","azure","mock"]` | ordered chain |
| `USE_MOCK_AGENT` | `false` | point at the real agent |
| `AGENT_API_URL` | `https://agent.internal` | real tax agent |
| provider keys | — | see `backend/.env.example` |

## Notes
* Store cloud keys in the platform secret manager, **not** in the image.
* Bind a least-privilege service account (see the GCP guide for exact roles).
* Health/readiness probe: `GET /health` (200 when at least one provider is healthy).
* Autoscaling is safe — the process is stateless except in-memory session/cache
  (swap to Redis for multi-instance session continuity in Phase 2).
