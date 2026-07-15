# Tax Agent on AWS — Production Guide

## Reference architecture

```
Route 53 ─► CloudFront ─► ALB ─► ECS Fargate / App Runner (Tax Agent container)
                                         │
   ┌─────────────────┬──────────────────┼────────────────┬─────────────────┐
   ▼                 ▼                  ▼                ▼                 ▼
 Bedrock         OpenSearch        S3 (corpus,       DynamoDB          Secrets
 (Claude on      Serverless        attachments,      (conversations,   Manager
  Bedrock)       vector index      audit archive)    sessions)         (keys)
   │                 ▲                  │
   │            Textract / Comprehend (ingest: OCR + entity/PII on uploaded docs)
   ▼
 CloudWatch (logs, metrics, alarms)  ·  Lambda (async corpus ingestion)  ·  API Gateway (optional public API)
```

**Mapping to the codebase**

| Interface | AWS implementation |
|---|---|
| `llm.base.LLMProvider` | `BedrockLLM` → `bedrock-runtime:InvokeModelWithResponseStream` (Claude on Bedrock) |
| `rag.store.VectorStore` | `OpenSearchVectorStore` (k-NN index, OpenSearch Serverless) |
| `chat.conversation_store.ConversationStore` | DynamoDB-backed store |
| corpus ingestion | Lambda + Textract (PDF OCR) + Comprehend (entity/PII) → chunks into OpenSearch |
| `security.audit.AuditLog` | CloudWatch Logs; archived to S3 |

## Authentication

- **App → AWS services:** IAM task/instance role — no static keys. Least privilege:
  `bedrock:InvokeModelWithResponseStream` on the model ARN; `aoss:APIAccessAll`
  scoped to the collection; `s3:GetObject/PutObject` on the prefix;
  `dynamodb:GetItem/PutItem/Query` on the tables.
- **Secrets** (Anthropic key if not using Bedrock) in Secrets Manager, injected as
  env vars by the task definition.
- **Users → App:** Cognito or corporate SSO/OIDC at the ALB or CloudFront.

## Deployment

```bash
# 1. Build & push (repo already has backend/Dockerfile)
aws ecr create-repository --repository-name tax-agent
docker build -t tax-agent backend/
docker tag tax-agent:latest $ACCT.dkr.ecr.$REGION.amazonaws.com/tax-agent:latest
aws ecr get-login-password --region $REGION | docker login --username AWS \
  --password-stdin $ACCT.dkr.ecr.$REGION.amazonaws.com
docker push $ACCT.dkr.ecr.$REGION.amazonaws.com/tax-agent:latest

# 2. App Runner (simplest) — env selects Bedrock + OpenSearch
aws apprunner create-service --service-name tax-agent \
  --source-configuration '{"ImageRepository":{"ImageIdentifier":"'$ACCT'.dkr.ecr.'$REGION'.amazonaws.com/tax-agent:latest","ImageRepositoryType":"ECR","ImageConfiguration":{"Port":"8080","RuntimeEnvironmentVariables":{"LLM_PROVIDER":"bedrock","VECTOR_STORE":"opensearch","ENVIRONMENT":"production"}}}}' \
  --health-check-configuration '{"Protocol":"HTTP","Path":"/health"}'
```

3. Create the **OpenSearch Serverless** vector collection and run the ingestion
   Lambda to chunk `rag/corpus` (Textract for PDFs) into the k-NN index.
4. Attach the task role; wire CloudWatch log group. ECS Fargate + ALB is the
   alternative when you need VPC-only networking.

## Infrastructure as code

CDK or Terraform: ECR repo, App Runner/ECS service + task role, OpenSearch
Serverless collection + data-access policy, S3 (versioned, SSE-KMS), DynamoDB
(on-demand), Secrets Manager secret, CloudWatch log group + alarms.

## Monitoring

- **Metrics:** latency, 5xx rate, Bedrock token usage, and a custom
  `GuardrailBlockRate` metric emitted from `AuditLog`.
- **Alarms:** p95 latency > 5s; error rate > 2%; block-rate spike (abuse signal).
- **Logs:** structured JSON to CloudWatch; audit lines to a dedicated log group,
  archived to S3 with Object Lock (WORM) for retention.

## Scalability

App Runner autoscales on concurrency; ECS Fargate uses target-tracking on
CPU/ALB request count. Stateless containers + DynamoDB session store scale
horizontally. OpenSearch Serverless scales OCUs automatically. Use Bedrock
provisioned throughput for steady high load.

## CI/CD

GitHub Actions → `pytest` + image scan (ECR enhanced scanning / Trivy) → push ECR
→ `aws apprunner start-deployment` (or ECS blue/green via CodeDeploy). Gate on
green tests.

## Estimated cost (indicative, ap-south-1)

| Service | Assumption | Monthly |
|---|---|---|
| App Runner | 1 vCPU / 2 GB, ~1 instance | $40–70 |
| OpenSearch Serverless | 2 OCU minimum | ~$350 |
| Bedrock (Claude) | 5M in / 2M out tokens | ~$60–120 (usage) |
| DynamoDB on-demand | light chat volume | $5–15 |
| S3 + CloudWatch + Secrets | modest | $10–20 |

At low volume OpenSearch Serverless dominates; for a pilot use a `t3.small.search`
managed domain (~$25/mo) or keep the in-memory store.

## Production recommendations

- Prefer **Bedrock** over Anthropic-direct on AWS: in-VPC, IAM-authenticated, no
  external key to rotate.
- Layer **Bedrock Guardrails** behind the app's own guardrails (defence in depth).
- OpenSearch encryption + fine-grained access; VPC-only endpoints.
- Audit logs to S3 with Object Lock for compliance.
- Keep `ENABLE_GUARDRAILS=true`; export the block-rate metric to CloudWatch.
