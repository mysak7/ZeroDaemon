# ZeroDaemon — 3-Minute Interview Pitch

A spoken script (~450 words, ~3 min at a calm pace). Section timings in brackets.
Below the script: the one diagram to draw and the three follow-up questions to be
ready for.

---

## The Script

**[0:00 — Hook]**

ZeroDaemon is a local, AI-driven DevSecOps assistant. The idea is simple: instead of
me logging into a box and running nmap by hand, an autonomous agent monitors my
targets, detects drift, and pulls threat intelligence when something looks off — and
it does the actual scanning from a disposable cloud VM, not from my laptop.

**[0:25 — The problem it solves]**

Two problems. First, if you scan from home, every probe comes from your own IP — bad
for footprint and for getting blocked. Second, security context goes stale fast; a
clean scan from six months ago is not a fact about today. ZeroDaemon addresses both.

**[0:50 — What it is, architecturally]**

The controller runs locally — FastAPI plus a LangGraph agent. When it needs to scan,
it provisions a small **Kali Linux worker VM in the cloud**, on a spot instance, and
runs nmap on it over SSH. So the probe originates from a throwaway cloud IP, and when
the scan's done the VM self-destructs on a TTL. When nothing's running, it costs
nothing.

**[1:25 — The part I'm proud of: two agents]**

The design decision I'd highlight: there are actually *two* agents. The main one only
knows how to scan. Everything that touches cloud infrastructure — build a VM, kill a
VM — lives in a separate **builder sub-agent**, exposed to the main loop as one tool.
That keeps the scanning context clean and puts every cloud-mutating action behind a
single auditable boundary.

**[1:55 — Guardrails in code, not in the prompt]**

And critically — the guardrails are enforced in *code*, not in the prompt. Max number
of workers, allowed machine types, a mandatory TTL, SSH locked to my IP only. A prompt
instruction is a suggestion; a raised exception is a wall. However the agent gets
talked into something, it physically cannot spin up a fleet or a 64-core box. Cost and
exposure are bounded by construction.

**[2:30 — Memory]**

For memory, SQLite is the source of truth for current state, and FAISS adds semantic
recall — but every recalled item is stamped with its age, and stale threat-intel is
flagged. The agent always knows how old a finding is.

**[2:50 — Close]**

It's model-agnostic — defaults to Claude Fable 5 but hot-swaps to GPT, Gemini, or a
local model — runs as a persistent daemon, and adding AWS or Azure as a worker backend
is a single class. That's ZeroDaemon.

---

## The One Diagram (draw this)

```
  controller (local)              cloud (GCP)
  ┌─────────────────┐    SSH    ┌──────────────┐
  │ FastAPI + agent │──────────►│ Kali VM      │
  │  + builder      │  gcloud   │ nmap (spot,  │
  │  + SQLite/FAISS │──────────►│  TTL-fused)  │
  └─────────────────┘           └──────────────┘
```

---

## Three Follow-ups to Be Ready For

**"Why two agents instead of one prompt?"**
Separation of concerns and blast radius. The scanner never reasons about billing or
machine types; the builder never reasons about ports. One tool = one auditable cloud
boundary, with its own frugal system prompt.

**"What stops the agent from running up a huge cloud bill?"**
Hard limits in `WorkerManager.create_worker` — checked before any `gcloud` call:
machine-type allowlist, max-worker cap, and a TTL that becomes `--max-run-duration`,
so the VM deletes itself even if the controller dies. Spot-priced, normally one shared
worker, zero cost when idle.

**"How do you avoid acting on stale intel?"**
SQLite is authoritative for live state; FAISS recall is age-annotated and stale
threat-intel is explicitly flagged, so old data is weighed, not trusted blindly.
