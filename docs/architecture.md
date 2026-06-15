# Architecture — ZeroDaemon

A local AI-driven DevSecOps assistant. A **controller runs on your machine**; it
provisions disposable **Kali worker VMs in the cloud** and runs scans (nmap today,
nikto/nuclei next) on them over SSH — so probes originate from a cloud IP, not your
home connection.

Default model: **Claude Fable 5** (`claude-fable-5`).

---

## The Shape of It

```
  ┌────────────── controller (local) ──────────────┐        ┌─── cloud (GCP) ───┐
  │  FastAPI + LangGraph agent                      │  SSH   │  Kali worker VM   │
  │   ├─ scan tools ──────────────────────────────────────► │  docker exec kali │
  │   ├─ manage_infrastructure (builder sub-agent) ─┼─gcloud─►  nmap / nikto ... │
  │   └─ WorkerManager + /workers REST + SQLite     │        │  (spot, TTL-fused)│
  └─────────────────────────────────────────────────┘        └───────────────────┘
```

| Layer | Tech | Lives in |
|-------|------|----------|
| API | FastAPI + Uvicorn, optional Bearer auth | `zerodaemon/api/` |
| Agent | LangGraph ReAct loop | `zerodaemon/agent/graph.py` |
| Builder sub-agent | second, narrowly-scoped LangGraph graph | `zerodaemon/agent/builder.py` |
| Worker provisioning | `gcloud` CLI behind a `WorkerProvider` interface | `zerodaemon/workers/` |
| State | SQLite (source of truth) + FAISS (semantic recall) | `zerodaemon/db/`, `agent/rag.py` |

---

## Three Decisions Worth Defending

### 1. Two agents, not one

The main ReAct agent only knows how to *scan*. Everything that mutates cloud
infrastructure — build a VM, destroy a VM, list VMs — lives in a separate
**builder sub-agent** exposed to the main loop as a single tool,
`manage_infrastructure` (`agent/builder.py`).

**Why:** it keeps the scanning context clean and puts every cloud-mutating action
behind one auditable boundary with its own system prompt ("be frugal, reuse the
existing worker, never request large VMs"). The blast radius of a confused agent is
contained to one tool.

### 2. Guardrails live in code, never in the prompt

`WorkerManager.create_worker` (`workers/manager.py`) enforces, *before* any
`gcloud` call:

| Guardrail | Where | Effect |
|-----------|-------|--------|
| `allowed_machine_types` | `manager.py:177` | reject anything not on the list |
| `max_workers` | `manager.py:185` | refuse to exceed the live-worker cap |
| `ttl_seconds` → `--max-run-duration` | `manager.py:223` | every VM self-deletes |
| SSH `/32` firewall | gcp provider | SSH allowed only from the controller's public IP |
| dedicated ed25519 keypair | `workers/keys.py` | agent never touches `~/.ssh` |

**Why:** a prompt instruction is a suggestion; a `raise WorkerProviderError` is a
wall. However the agent is cajoled, it cannot provision a 64-core box, spin up a
fleet, or leave a VM running past its TTL. **Cost and exposure are bounded by
construction.**

### 3. Time-aware memory

SQLite is the **source of truth** for current state. FAISS adds semantic recall, but
every result is stamped with `age_days` and threat-intel past a threshold is flagged
**stale** (`agent/rag.py`). CVE context ages fast; the agent is told how old each
finding is so a six-month-old "all clear" isn't mistaken for current fact.

---

## Lifecycle of a Scan

1. User (or the **daemon** loop) asks to scan a target.
2. Main agent calls `scan_services`; if no worker is live it invokes
   `manage_infrastructure` → builder builds one (**spot first, on-demand fallback**,
   smallest allowed type).
3. nmap runs *on the worker* via `ssh → docker exec kali` (`workers/remote.py`).
4. Result is summarised, written to SQLite, embedded into FAISS.
5. New scan is **diffed against the historical baseline** — new ports, changed
   services, version bumps surface as drift; anomalies trigger live CVE search.
6. The VM dies on its TTL; on restart `/workers/reconcile` realigns the DB with what
   actually exists in the cloud.

---

## Why a Cloud Worker At All?

| | Scan from laptop | Scan from cloud worker |
|---|---|---|
| Source IP | your home/office | disposable cloud IP |
| Local tooling | nmap/nikto/nuclei required | none — runs on Kali image |
| Footprint | persistent | ephemeral, TTL-fused |
| Cost when idle | n/a | **zero** (no worker running) |

The worker is shared (normally one), spot-priced, and self-destructs. Adding AWS or
Azure is one `WorkerProvider` subclass — no other code changes.

---

## Persistence & Restart Safety

Everything that must survive a restart is in SQLite: `scans`, `threat_intel`,
`llm_usage`, `daemon_targets`, and `workers`. On boot the daemon reloads its monitored
targets and the worker table is reconciled against the cloud — no orphaned VMs, no lost
history.
