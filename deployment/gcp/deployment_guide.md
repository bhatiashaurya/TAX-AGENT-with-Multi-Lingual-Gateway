# Tax Agent on GCP — Production Guide

## Reference architecture

```
Cloud Load Balancing ─► Cloud Run (Tax Agent container, autoscale, scale-to-zero)
                              │
   ┌──────────────────┬───────┼──────────────┬──────────────────┐
   ▼                  ▼       ▼              ▼                  ▼
 Vertex AI        Vertex      Cloud         Firestore /       Secret
 (Gemini 2.x /    Vector      Storage       BigQuery          Manager
  Claude on       Search      (corpus,      (conversations,   (keys)
  Vertex)         (index)     attachments)  analytics)
   │                 ▲            │
   │       Cloud Functions (ingest) + Document AI (PDF OCR)
   ▼
 Cloud Logging + Cloud Monitoring (logs, metrics, alerts, SLOs)
```

**Mapping to the codebase**

| Interface | GCP implementation |
|---|---|
| `llm.base.LLMProvider` | `VertexLLM` → `generate_content(stream=True)` (Gemini, or Claude on Vertex) |
| `rag.store.VectorStore` | `VertexVectorSearchStore` (Vector Search index endpoint) |
| `chat.conversation_store.ConversationStore` | Firestore; analytics mirrored to BigQuery |
| corpus ingestion | Cloud Functions + Document AI (PDF) → Vector Search |
| `security.audit.AuditLog` | Cloud Logging; sink to BigQuery for SIEM queries |

## Authentication

- **App → Google APIs:** the Cloud Run service's **service account** (Workload
  Identity) — no key files. Least privilege: `roles/aiplatform.user` (Vertex),
  `roles/storage.objectAdmin` on the bucket, `roles/datastore.user` (Firestore),
  `roles/secretmanager.secretAccessor`.
- **Secrets** (Anthropic key if used) in **Secret Manager**, mounted as env vars.
- **Users → App:** Identity-Aware Proxy (IAP) in front of the load balancer, or
  `--no-allow-unauthenticated` + IAM for internal-only.

## Deployment

```bash
gcloud services enable run.googleapis.com aiplatform.googleapis.com \
  secretmanager.googleapis.com firestore.googleapis.com documentai.googleapis.com

# Build (repo Dockerfile lives at backend/Dockerfile)
gcloud builds submit backend --tag gcr.io/$PROJECT/tax-agent

gcloud run deploy tax-agent \
  --image gcr.io/$PROJECT/tax-agent --region asia-south1 \
  --memory 1Gi --cpu 1 --timeout 300 --max-instances 20 \
  --service-account tax-agent-sa@$PROJECT.iam.gserviceaccount.com \
  --set-env-vars LLM_PROVIDER=vertex,VECTOR_STORE=vertex_vector,GCP_PROJECT_ID=$PROJECT,ENVIRONMENT=production
```

Create the Vector Search index + endpoint and run the ingestion Cloud Function to
chunk `rag/corpus` (Document AI for PDFs) into it.

> Cloud Run buffers responses by default; the app already sends
> `X-Accel-Buffering: no` on the SSE route so tokens stream live.

## Infrastructure as code

Terraform: Artifact Registry, Cloud Run service + service account, Vertex Vector
Search index/endpoint, GCS bucket (uniform access, CMEK), Firestore database,
Secret Manager secrets, Monitoring alert policies + log sink to BigQuery.

## Monitoring

- **Cloud Monitoring:** request latency, error rate, Vertex token usage; a custom
  `guardrail/block_rate` metric from `AuditLog`. Define SLOs (availability,
  latency) with burn-rate alerts.
- **Cloud Logging:** structured logs; a sink routes audit lines to BigQuery for
  security analytics and long-term retention.
- **Alerts:** p95 latency, 5xx rate, block-rate spike.

## Scalability

Cloud Run autoscales 0→N on concurrency (set `--concurrency` to tune tokens/instance)
and scales to zero for dev. Firestore scales automatically. Vertex Vector Search
scales replicas for QPS. Use Provisioned Throughput on Vertex for steady load.

## CI/CD

Cloud Build (or GitHub Actions + WIF) → `pytest` → build → push Artifact Registry
→ `gcloud run deploy` with revision tags for canary/blue-green traffic splitting.
Gate on tests + Artifact Analysis vulnerability scan.

## Estimated cost (indicative, asia-south1)

| Service | Assumption | Monthly |
|---|---|---|
| Cloud Run | 1 vCPU / 1 GB, scale-to-zero + light traffic | $10–40 |
| Vertex Vector Search | 1 small index endpoint | ~$60–200 |
| Vertex (Gemini) | 5M in / 2M out tokens | ~$30–90 (usage) |
| Firestore | light chat volume | $5–20 |
| GCS + Logging + Secret Manager | modest | $5–15 |

Cloud Run scale-to-zero makes GCP the cheapest for spiky/low traffic; Vector
Search endpoints bill while running — delete idle pilot endpoints.

## Production recommendations

- **Claude on Vertex** (Model Garden) keeps everything under one GCP identity and
  billing if you prefer Claude to Gemini.
- Front with **IAP** for enterprise SSO; keep the service non-public.
- CMEK on GCS + BigQuery; VPC-SC perimeter around Vertex/Storage for exfiltration
  control.
- Log sink → BigQuery gives SQL over the audit trail for governance.
- Keep `ENABLE_GUARDRAILS=true`; optionally add Vertex safety filters as a second
  layer.
