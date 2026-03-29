# ZeroDaemon

A local AI-driven DevSecOps assistant for autonomous infrastructure monitoring, drift detection, and threat intelligence. ZeroDaemon combines LLMs with professional security tools (nmap, WHOIS, CVE search) to continuously watch your IPs, detect configuration changes, and surface threats — with no cloud dependency required.

## Features

- **Drift detection** — Compares live nmap scans against historical baselines; alerts on new ports, changed services, or version bumps
- **Threat intelligence** — Searches live CVE/exploit databases when anomalies are detected
- **Persistent memory** — SQLite + FAISS vector store for semantic search over past scans and threat intel
- **Daemon mode** — Scheduled background scanning of registered targets at configurable intervals
- **Multi-model support** — Claude, GPT-4, Gemini, Ollama, or any OpenAI-compatible endpoint; hot-swap without restart
- **Usage tracking** — Per-invocation token counts, latency, and USD cost logged per model
- **Terminal-style web UI** — Real-time streaming chat with tool execution timing

## Tech Stack

| Layer | Tools |
|---|---|
| API | FastAPI + Uvicorn |
| Agent | LangGraph + LangChain |
| Security | nmap, ipwhois, DuckDuckGo search |
| Storage | SQLite (aiosqlite), FAISS (faiss-cpu) |
| Embeddings | FastEmbed (`BAAI/bge-small-en-v1.5`) |
| LLM Providers | Anthropic, OpenAI, Google Gemini, Ollama, custom OpenAI-compatible |

## Requirements

- Python 3.14+
- System tools: `nmap`, `whois` (others optional: `masscan`, `nikto`, `nuclei`)

## Setup

```bash
# 1. Run setup (installs system tools, creates venv, copies .env)
./setup.sh

# 2. Add API keys
vi .env

# 3. Start the server
./run.sh
```

The server starts on **http://localhost:8222**.

### Environment Variables (`.env`)

```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...

# Optional
ZERODAEMON_DB_PATH=zerodaemon.db
ZERODAEMON_OLLAMA_BASE_URL=http://localhost:11434
ZERODAEMON_AUTO_INSTALL_DEPS=false   # auto-install missing system tools
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
active: syl-default

models:
  - id: gemini-2.5-flash-lite
    provider: google
    input_mtok: 0.1
    output_mtok: 0.4
    max_tokens: 65535

  - id: syl-default
    provider: syl              # local OpenAI-compatible endpoint
    input_mtok: 0.0
    output_mtok: 0.0
    max_tokens: 8192
```

All settings and model registry can be updated at runtime via API — no restart needed.

## Agent Tools

| Tool | Description |
|---|---|
| `check_ip_owner` | WHOIS/RDAP lookup — ASN, ISP, org, country |
| `scan_services` | Nmap scan with presets: `top-10`, `top-100`, `top-1000`, `full` |
| `search_threat_intel` | Live CVE/exploit search via DuckDuckGo |
| `query_historical_scans` | Retrieve past scan results for an IP from SQLite |
| `search_knowledge_base` | Semantic search over scan history and threat intel via FAISS |

## API Reference

### Agent

| Method | Path | Description |
|---|---|---|
| `POST` | `/agent/chat` | Synchronous chat |
| `WS` | `/agent/stream?thread_id=xyz` | Streaming WebSocket |
| `GET` | `/agent/status` | Daemon status, active model, targets |
| `POST` | `/agent/targets` | Register IP for monitoring |
| `DELETE` | `/agent/targets/{ip}` | Remove IP from monitoring |

**Chat request:**

```json
{
  "message": "Scan 192.168.1.1 and report any drift",
  "thread_id": "default"
}
```

**WebSocket stream events:**

```json
{"event": "start", "model_id": "syl-default"}
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

### Settings

| Method | Path | Description |
|---|---|---|
| `GET` | `/settings` | Current settings |
| `PATCH` | `/settings` | Update and persist to YAML |

### System

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Swagger UI |

## Project Structure

```
ZeroDaemon/
├── main.py                    # Entry point
├── run.sh / setup.sh          # Run and install scripts
├── config/
│   ├── models.yaml            # LLM registry
│   └── settings.yaml          # Runtime settings
└── zerodaemon/
    ├── api/
    │   ├── app.py             # FastAPI app + lifespan
    │   ├── routes/            # agent, models, scans, settings
    │   └── static/index.html  # Web UI
    ├── agent/
    │   ├── graph.py           # LangGraph orchestration
    │   ├── tools.py           # 5 core agent tools
    │   ├── daemon.py          # Background scan loop
    │   └── rag.py             # FAISS knowledge base
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
