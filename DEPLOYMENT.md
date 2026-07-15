# Deployment

Tax Agent ships as a **single stateless container** (`backend/Dockerfile`, built
from the **repo root** so the frontend is bundled and served at `/ui/`). It reads
config from environment variables and listens on `$PORT` (default `8080`).

There are two paths below: the **manual deploy** (what's running now on EC2) and
the **automated CI/CD pipeline** (push to `main` → tests → image → deploy).

---

## Current deployment

- **Host:** AWS EC2 `t3.micro` (Ubuntu), free tier, instance name `tax-agent`
- **URL:** http://54.144.100.99:8080/ui/
- **LLM:** offline mock (no credentials); set `LLM_PROVIDER=anthropic` + a key to use Claude
- **Security group:** inbound `8080` (app) — add inbound `22` (SSH) to enable CI/CD

> The public IP (`54.144.100.99`) **changes if the instance is stopped/started**.
> Allocate an **Elastic IP** (free while attached to a running instance) for a
> stable address, then update the `EC2_HOST` secret and any bookmarks.

---

## A. Manual deploy (Ubuntu EC2)

```bash
# one-time: install Docker and allow the ubuntu user to run it
sudo apt-get update && sudo apt-get install -y docker.io
sudo usermod -aG docker ubuntu    # then reconnect the SSH session

# build from the repo root (context must be '.')
git clone https://github.com/bhatiashaurya/TAX-AGENT-with-Multi-Lingual-Gateway.git
cd TAX-AGENT-with-Multi-Lingual-Gateway
docker build -f backend/Dockerfile -t tax-agent:latest .

# run (named volume persists conversations/audit across restarts and works with
# the image's non-root user; a bind mount would break that on uid mismatch)
docker run -d --name tax-agent --restart unless-stopped \
  -p 8080:8080 \
  -e LLM_PROVIDER=mock \
  -v tax-agent-data:/app/backend/data \
  tax-agent:latest

curl -s http://localhost:8080/health   # expect 73 indexed chunks, guardrails operational
```

t3.micro has 1 GB RAM. The mock-LLM chat path fits comfortably; the optional
server-side Whisper STT (voice uploads) is the memory-heavy part — the chat UI
uses **browser** dictation, so you can drop `faster-whisper` from
`requirements.txt` for a slimmer image if you never call `/api/voice`.

---

## B. Automated CI/CD (GitHub Actions → EC2)

Pipeline: [.github/workflows/deploy.yml](.github/workflows/deploy.yml). On every
push to `main`:

1. **test** — install deps, run the full pytest suite (deploy is gated on green).
2. **build** — build the image, push to GHCR (`ghcr.io/bhatiashaurya/tax-agent`).
3. **deploy** — SSH into EC2, pull the new image, restart the container. Skips
   automatically until the EC2 secrets are set.

### One-time setup

**1. Make the image pullable.** After the first `build`, GHCR creates the
`tax-agent` package (private by default). Make it **public** so EC2 can pull it
without credentials (the image contains no secrets — keys are runtime env):
`github.com/users/bhatiashaurya/packages` → `tax-agent` → Package settings →
Change visibility → Public.

**2. Add repository secrets** (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `EC2_HOST` | `54.144.100.99` (or your Elastic IP) |
| `EC2_USER` | `ubuntu` |
| `EC2_SSH_KEY` | full contents of the instance's private key (`-----BEGIN…END-----`) |

**3. Open port 22** to the runner in the security group, and ensure the `ubuntu`
user is in the `docker` group (step A).

**4. Trigger:** Actions → latest run → **Re-run jobs** (or push any change).

### Using real Claude in production

Add `ANTHROPIC_API_KEY` as a secret, pass it to the container in the deploy step
(`-e LLM_PROVIDER=anthropic -e ANTHROPIC_API_KEY=…`), and `pip install anthropic`
is already in `requirements.txt`. Never bake the key into the image.

---

## Other clouds

Provider-specific reference architectures (managed, autoscaling, less ops):

- **GCP Cloud Run** — [deployment/gcp/deployment_guide.md](deployment/gcp/deployment_guide.md) (scale-to-zero; best free-forever option)
- **Azure Container Apps** — [deployment/azure/deployment_guide.md](deployment/azure/deployment_guide.md)
- **AWS App Runner / ECS / Bedrock** — [deployment/aws/deployment_guide.md](deployment/aws/deployment_guide.md)

## Operational notes

- **HTTPS:** the instance serves plain `http` on 8080. For TLS, put **Caddy** or
  nginx in front (Caddy auto-provisions Let's Encrypt certs); with nginx set
  `proxy_buffering off` so SSE streams live.
- **Health probe:** `GET /health` (200 when the LLM provider is healthy).
- **Data:** conversations + audit log live in the `tax-agent-data` volume. Back it
  up, or move `ConversationStore` to a managed DB for multi-instance scale.
- **Secrets:** keep all keys in GitHub secrets / a secret manager, never in the image or git.
