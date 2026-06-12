FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install uv

COPY pyproject.toml .
COPY zerodaemon/ zerodaemon/

RUN uv pip install --system --no-cache .


FROM python:3.12-slim

# nmap is kept for any local use; openssh-client is required to generate the
# worker keypair and reach workers over SSH. Cloud provisioning additionally
# needs the gcloud CLI on the host (not bundled here — run the controller where
# `gcloud auth login` is available, or mount the SDK in).
RUN apt-get update && apt-get install -y --no-install-recommends \
    nmap \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY zerodaemon/ zerodaemon/
COPY config/ config/
COPY main.py .

RUN mkdir -p /app/data

VOLUME ["/app/data"]

ENV ZERODAEMON_DB_PATH=/app/data/zerodaemon.db
ENV ZERODAEMON_RAG_PATH=/app/data/zerodaemon_rag

EXPOSE 8222

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8222/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8222", "--workers", "1"]
