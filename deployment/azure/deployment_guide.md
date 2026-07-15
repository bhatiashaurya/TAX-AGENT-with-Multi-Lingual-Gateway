# Tax Agent on Azure — Production Guide

## Reference architecture

```
Front Door ─► Container Apps (Tax Agent container, autoscale)
                     │
   ┌─────────────────┬───────────────┼───────────────┬──────────────────┐
   ▼                 ▼               ▼               ▼                  ▼
 Azure OpenAI    Azure AI Search  Blob Storage   Cosmos DB          Key Vault
 (GPT-4o /       (vector index)   (corpus,       (conversations,    (secrets)
  Claude via                      attachments,   sessions)
  model catalog)                  audit archive)
   │                 ▲
   │        Azure Functions (ingest) + Document Intelligence (PDF OCR)
   ▼
 Azure Monitor / Log Analytics (logs, metrics, alerts)
```

**Mapping to the codebase**

| Interface | Azure implementation |
|---|---|
| `llm.base.LLMProvider` | `AzureOpenAILLM` → `chat.completions` with `stream=True` |
| `rag.store.VectorStore` | `AzureAISearchVectorStore` (vector + semantic hybrid) |
| `chat.conversation_store.ConversationStore` | Cosmos DB (SQL API) |
| corpus ingestion | Azure Functions + Document Intelligence (PDF) → AI Search index |
| `security.audit.AuditLog` | Log Analytics via the Monitor ingestion API |

## Authentication

- **App → Azure services:** system-assigned **Managed Identity** on the Container
  App — no keys. RBAC: *Cognitive Services OpenAI User* on the OpenAI resource,
  *Search Index Data Contributor* on AI Search, *Storage Blob Data Contributor* on
  the container, *Cosmos DB Built-in Data Contributor*.
- **Secrets** (any non-MI credential) in **Key Vault**, referenced by Container
  Apps secrets (`secretref:`).
- **Users → App:** Entra ID (Azure AD) via Container Apps built-in auth (Easy Auth).

## Deployment

```bash
az group create -n tax-agent-rg -l centralindia
az acr create -g tax-agent-rg -n taxagentacr --sku Basic --admin-enabled true
az acr build --registry taxagentacr -f backend/Dockerfile -t tax-agent:latest .

az containerapp env create -n tax-env -g tax-agent-rg -l centralindia
az containerapp create -n tax-agent -g tax-agent-rg --environment tax-env \
  --image taxagentacr.azurecr.io/tax-agent:latest --registry-server taxagentacr.azurecr.io \
  --target-port 8080 --ingress external --min-replicas 1 --max-replicas 10 \
  --system-assigned \
  --env-vars LLM_PROVIDER=azure_openai VECTOR_STORE=ai_search ENVIRONMENT=production \
             AZURE_OPENAI_ENDPOINT=https://<res>.openai.azure.com \
             AZURE_SEARCH_ENDPOINT=https://<svc>.search.windows.net
```

Grant the Container App's managed identity the RBAC roles above; deploy an Azure
Function to chunk `rag/corpus` (Document Intelligence for PDFs) into AI Search.

## Infrastructure as code

Bicep or Terraform: ACR, Container Apps environment + app + managed identity,
Azure OpenAI deployment, AI Search service, Blob container, Cosmos DB, Key Vault,
Log Analytics workspace + alert rules.

## Monitoring

- **Azure Monitor / Application Insights:** request latency, failure rate, OpenAI
  token metrics; a custom `guardrail_block` metric from `AuditLog`.
- **Alerts:** p95 latency, 5xx rate, block-rate spike.
- **Logs:** container stdout + audit stream to Log Analytics; KQL dashboards for
  security events; export to a Storage account with immutability policy.

## Scalability

Container Apps scales on HTTP concurrency (KEDA) 1→N and to zero for dev. Cosmos
DB autoscale RU/s. AI Search: raise replicas (query throughput) and partitions
(index size). Azure OpenAI: use Provisioned Throughput Units (PTU) for predictable
latency at scale.

## CI/CD

GitHub Actions / Azure DevOps → `pytest` → `az acr build` → `az containerapp
update`. Use revisions for blue/green and traffic splitting; gate on tests +
container scan (Microsoft Defender for Containers).

## Estimated cost (indicative, Central India)

| Service | Assumption | Monthly |
|---|---|---|
| Container Apps | 1 vCPU / 2 GB, ~1 replica | $35–60 |
| Azure AI Search | Basic tier | ~$75 |
| Azure OpenAI | 5M in / 2M out tokens | ~$50–110 (usage) |
| Cosmos DB | autoscale, light | $25–40 |
| Blob + Monitor + Key Vault | modest | $10–25 |

## Production recommendations

- Managed Identity everywhere; secrets only in Key Vault.
- Enable **Azure AI Content Safety** as a second moderation layer behind the app
  guardrails.
- AI Search **semantic ranker** improves rerank quality over the built-in reranker.
- Private Endpoints for OpenAI, Search, Storage, Cosmos; restrict egress.
- Immutable audit storage for compliance; keep `ENABLE_GUARDRAILS=true`.
