# ZeroDaemon

A local AI-driven DevSecOps assistant for autonomous infrastructure monitoring, drift detection, and threat intelligence. The controller runs locally; it builds disposable **Kali Linux worker VMs in the cloud** and runs scans (nmap, and later nikto/nuclei) on them over SSH, so probes originate from the cloud rather than your home connection.

Default model: **Claude Fable 5** (`claude-fable-5`) — Anthropic's flagship model, 1M context, always-on extended thinking.

## Architecture

```
  ┌────────────── controller (local) ──────────────┐        ┌─── cloud (GCP) ───┐
  │  FastAPI + LangGraph agent                      │  SSH   │  Kali worker VM   │
  │   ├─ scan tools ──────────────────────────────────────► │  docker exec kali │
  │   ├─ manage_infrastructure (builder sub-agent) ─┼─gcloud─►  nmap / nikto ... │
  │   └─ WorkerManager + /workers REST + SQLite     │        │  (spot, TTL-fused)│
  └─────────────────────────────────────────────────┘        └───────────────────┘
```

The main ReAct agent handles scanning; a separate **builder sub-agent** (exposed as the `manage_infrastructure` tool) owns the cloud-mutating actions and their guardrails. Workers are built via the `gcloud` CLI, default to **spot** with on-demand fallback, and self-destruct on a TTL.

## Features

- **Cloud scan workers** — Agent builds a small Kali VM on demand (GCP first; AWS/Azure pluggable) and scans from there; spot-first with on-demand fallback, TTL auto-delete, size/count guardrails enforced in code
- **Drift detection** — Compares live nmap scans against historical baselines; alerts on new ports, changed services, or version bumps
- **Threat intelligence** — Searches live CVE/exploit databases when anomalies are detected
- **Time-aware memory** — SQLite is the source of truth for current state; FAISS vector store adds semantic recall with per-result age and staleness flags so old findings aren't mistaken for current fact
- **Daemon mode** — Scheduled background scanning of registered targets; auto-provisions a worker if none is running; targets persist across restarts
- **Multi-model support** — Claude Fable 5, GPT-4, Gemini, Ollama, or any OpenAI-compatible endpoint; hot-swap without restart
- **Usage tracking** — Per-invocation token counts, latency, and USD cost logged per model
- **Terminal-style web UI** — Real-time streaming chat, tool timing, and a live cloud-worker panel (build / kill / reconcile)
- **Optional API key auth** — Set `ZERODAEMON_API_KEY` to require `Authorization: Bearer <key>` on all routes
- **Configurable CORS** — Lock down allowed origins for production deployments

## Tech Stack

| Layer | Tools |
|---|---|
| API | FastAPI + Uvicorn |
| Agent | LangGraph + LangChain (main agent + builder sub-agent) |
| Default LLM | Claude Fable 5 (Anthropic) |
| Cloud workers | `gcloud` CLI (GCP), SSH into a Kali container; AWS/Azure pluggable |
| Security | nmap (remote), ipwhois, DuckDuckGo search |
| Storage | SQLite (aiosqlite), FAISS (faiss-cpu) |
| Embeddings | FastEmbed (`BAAI/bge-small-en-v1.5`) |
| LLM Providers | Anthropic, OpenAI, Google Gemini, Ollama, custom OpenAI-compatible |

## Requirements

- Python 3.12+
- `openssh-client` (`ssh`, `ssh-keygen`) — to generate the worker keypair and reach workers
- For cloud workers: the **`gcloud` CLI**, authenticated (`gcloud auth login`) with a project set (`gcloud config set project <id>`)
- Optional local system tools: `nmap`, `whois`, `masscan`, `nikto`, `nuclei` (scans run on the remote worker, so these are no longer required locally)

## Quick Start

### Local

```bash
# 1. Run setup (installs system tools, creates venv, copies .env)
./setup.sh

# 2. Add API keys
vi .env

# 3. Start the server
./run.sh
```

The server starts on **http://localhost:8222**.

### Docker

```bash
cp .env.example .env
vi .env   # fill in ANTHROPIC_API_KEY at minimum
docker compose up -d
```

## Environment Variables (`.env`)

```bash
ANTHROPIC_API_KEY=sk-ant-...   # Required for Claude Fable 5 (default model)
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...
MCP_API_KEY=...                # API key for the MCP server (seip-mcp)

# Optional overrides
ZERODAEMON_DB_PATH=zerodaemon.db
ZERODAEMON_OLLAMA_BASE_URL=http://localhost:11434

# Production security
ZERODAEMON_API_KEY=change-me   # Enables Bearer token auth on all routes
ZERODAEMON_ALLOWED_ORIGINS='["https://your-domain.com"]'  # CORS allowlist
```

## Configuration

### `config/settings.yaml`

```yaml
log_level: INFO
daemon_poll_interval: 86400   # seconds between background scans (1 day)
daemon_paused: false
rag_path: zerodaemon_rag
```

### `config/models.yaml`

```yaml
active: claude-fable-5

models:
  - id: claude-fable-5
    provider: anthropic
    input_mtok: 10.0
    output_mtok: 50.0
    max_tokens: 128000
    note: Claude Fable 5 — Anthropic flagship, 1M context, always-on thinking

  - id: syl-default
    provider: syl
    input_mtok: 0.0
    output_mtok: 0.0
    max_tokens: 8192
    note: Syl local OpenAI-compatible endpoint at http://syl:8001/v1
```

### `config/workers.yaml`

Controls cloud worker provisioning. Hard limits are enforced **in code**, not in the prompt — the agent cannot exceed them however it's asked.

```yaml
active_provider: gcp

limits:
  max_workers: 2                 # refuse to create more than this many live workers
  ttl_seconds: 86400             # every VM self-deletes after this (--max-run-duration)
  allowed_machine_types: [e2-micro, e2-small, e2-medium]   # anything else is rejected

ssh_source_cidr: ""              # empty => auto-detect controller public IP, lock SSH to that /32

providers:
  - name: gcp
    enabled: true
    project: ""                  # empty => `gcloud config get-value project`
    zones: [europe-west1-b, europe-west1-c, europe-west1-d]   # spot stockout falls through
    machine_type: e2-small
    prefer_spot: true            # try SPOT first, fall back to on-demand
```

Adding a cloud means implementing one `WorkerProvider` subclass and registering it in `zerodaemon/workers/providers/__init__.py` — no other code changes.

All settings and the model registry can be updated at runtime via API — no restart needed.

## Agent Tools

| Tool | Description |
|---|---|
| `check_ip_owner` | WHOIS/RDAP lookup — ASN, ISP, org, country |
| `scan_services` | Nmap scan with presets: `top-10`, `top-100`, `top-1000`, `full` |
| `search_threat_intel` | Live CVE/exploit search via DuckDuckGo |
| `query_historical_scans` | Retrieve past scan results for an IP from SQLite |
| `search_knowledge_base` | Semantic search over scan history and threat intel via FAISS (age-annotated) |
| `manage_infrastructure` | Builder sub-agent — build / list / destroy cloud worker VMs (used automatically when a scan needs a worker) |

## API Reference

### Agent

| Method | Path | Description |
|---|---|---|
| `POST` | `/agent/chat` | Synchronous chat |
| `WS` | `/agent/stream?thread_id=xyz` | Streaming WebSocket |
| `GET` | `/agent/status` | Daemon status, active model, targets |
| `POST` | `/agent/targets` | Register IP/hostname for monitoring |
| `DELETE` | `/agent/targets/{ip}` | Remove IP/hostname from monitoring |

**Chat request:**

```json
{
  "message": "Scan 192.168.1.1 and report any drift",
  "thread_id": "default"
}
```

**WebSocket stream events:**

```json
{"event": "start", "model_id": "claude-fable-5"}
{"event": "tool_start", "tool": "scan_services", "input": {...}}
{"event": "token", "data": "Found 3 open ports..."}
{"event": "tool_end", "tool": "scan_services", "elapsed_ms": 4821}
{"event": "done"}
```

### Models

| Method | Path | Description |
|---|---|---|
| `GET` | `/models` | List models |
| `POST` | `/models` | Add model |
| `PATCH` | `/models/{id}` | Update model |
| `DELETE` | `/models/{id}` | Delete model |
| `POST` | `/models/{id}/activate` | Switch active model |
| `GET` | `/models/usage/stats` | Aggregate usage & cost |

### Scans

| Method | Path | Description |
|---|---|---|
| `GET` | `/scans?target=1.2.3.4&limit=50` | List scan history |
| `GET` | `/scans/{scan_id}` | Full scan result with raw JSON |

### Workers

| Method | Path | Description |
|---|---|---|
| `GET` | `/workers` | List worker VMs (add `?include_terminated=true` for history) |
| `POST` | `/workers` | Build a worker (body optional: `machine_type`, `zone`, `spot`, `ttl_seconds`, `label`) |
| `GET` | `/workers/{id}` | Worker detail |
| `DELETE` | `/workers/{id}` | Destroy a worker VM |
| `GET` | `/workers/providers` | List providers and whether each is usable on this host |
| `POST` | `/workers/reconcile` | Reconcile the DB against what actually exists in the cloud |

### Settings

| Method | Path | Description |
|---|---|---|
| `GET` | `/settings` | Current settings (API keys masked) |
| `PATCH` | `/settings` | Update and persist to YAML |

### System

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check (exempt from auth) |
| `GET` | `/docs` | Swagger UI |

## Security

- **API key auth**: set `ZERODAEMON_API_KEY` — all routes except `/health`, `/docs`, `/redoc`, and `/` require `Authorization: Bearer <key>`
- **CORS**: defaults to `["*"]` for local dev; set `ZERODAEMON_ALLOWED_ORIGINS` to a JSON array to restrict in production
- **API key masking**: `GET /settings` returns `***` for any configured key — keys are never exposed via API
- **Dedicated worker keypair**: a separate ed25519 keypair is generated on first use under `<db dir>/ssh/` (gitignored) — the agent never touches your personal `~/.ssh` key. The public key is injected into workers via instance metadata
- **Locked-down SSH**: workers get a firewall rule allowing SSH only from the controller's public IP (`/32`); set `ssh_source_cidr` to override
- **Worker guardrails**: max-worker count, allowed machine types, and a TTL that auto-deletes the VM are enforced in code regardless of what the agent is asked to do

## Project Structure

```
ZeroDaemon/
├── main.py                    # Entry point
├── run.sh / setup.sh          # Run and install scripts
├── Dockerfile                 # Production container image
├── docker-compose.yml         # Full-stack deployment
├── config/
│   ├── models.yaml            # LLM registry (active: claude-fable-5)
│   └── settings.yaml          # Runtime settings
└── zerodaemon/
    ├── api/
    │   ├── app.py             # FastAPI app + lifespan + auth middleware
    │   ├── routes/            # agent, models, scans, settings, workers
    │   └── static/index.html  # Web UI (+ live worker panel)
    ├── agent/
    │   ├── graph.py           # LangGraph orchestration
    │   ├── tools.py           # core scan tools (nmap runs remotely over SSH)
    │   ├── builder.py         # builder sub-agent → manage_infrastructure tool
    │   ├── daemon.py          # Background scan loop (persistent targets)
    │   └── rag.py             # FAISS knowledge base (time-aware)
    ├── workers/
    │   ├── manager.py         # WorkerManager — guardrails, DB, reconcile
    │   ├── base.py            # WorkerProvider interface
    │   ├── keys.py            # dedicated SSH keypair (generated, gitignored)
    │   ├── remote.py          # SSH exec layer (docker exec into Kali)
    │   └── providers/gcp.py   # GCP provider via gcloud CLI
    ├── db/
    │   └── sqlite.py          # Schema + async queries
    └── models/
        ├── registry.py        # Model load/switch/persist
        ├── providers.py       # LLM provider builders
        └── usage.py           # Token & cost tracking
```

## Database Schema

**`scans`** — `id, ts, target, scan_type, raw_json, summary`

**`threat_intel`** — `indicator, indicator_type, fetched_ts, data_json, verdict`

**`llm_usage`** — `ts, model_id, provider, thread_id, input_tokens, output_tokens, cost_usd, duration_ms, status`

**`daemon_targets`** — `ip` (persisted monitoring targets, survives restarts)

**`workers`** — `id, provider, instance_name, zone, region, machine_type, public_ip, provisioning_model, status, created_ts, ttl_seconds, expires_ts, is_active, label, error` (cloud worker VMs, survive restarts and are reconciled against the cloud on startup)
