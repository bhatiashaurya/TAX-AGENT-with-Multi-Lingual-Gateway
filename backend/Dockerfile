# syntax=docker/dockerfile:1
#
# Build context MUST be the repository root so the frontend can be copied in and
# served by the gateway at /ui:
#     docker build -f backend/Dockerfile -t nth-voice-gateway .
#
# ---- Stage 1: build wheels -------------------------------------------------
FROM python:3.12-slim AS builder
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Stage 2: runtime ------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080
WORKDIR /app

# Non-root user for least privilege.
RUN useradd --create-home --uid 10001 appuser

COPY --from=builder /install /usr/local
COPY backend /app/backend
COPY frontend /app/frontend

USER appuser
WORKDIR /app/backend
EXPOSE 8080

# Cloud Run / Container Apps inject $PORT; default to 8080 locally.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
