# learn-runpod-llm-serving — Project Instructions

## Governance

This project is governed by **SyntranAI AIEOS**, Syntran Labs' internal AI-engineering
governance framework (agents, skills, safety categories, review workflows). The framework
itself lives outside this repo; everything an external reader needs from it — the safety
categories and operating rules for THIS project — is written out below, so this file
stands alone.

Standard invocation:
```
Use [architect | python-engineer | security-reviewer | technical-writer].
Apply [develop | refactor | debug | review | document].
Relevant domains: local-inference
Task: [objective]
Constraints: [list]
```

---

## Project Purpose

Self-hosted LLM serving on a GPU cloud marketplace (RunPod). Deploys GPT-OSS-20B (MXFP4) via
llama-server on an on-demand RTX 3090 pod, exposes it through an OpenAI-compatible API over an
SSH tunnel, and benchmarks cost/performance against alternatives. Part of the Syntran Labs LLMOps
portfolio track.

---

## Safety Category: CRITICAL (infra) / SENSITIVE (code)

- **Pod deploy / redeploy / terminate** — Critical. Never autonomous; a typed human confirmation (`DEPLOY` / `TERMINATE`) is required, whether via console.runpod.io or the gated scripts (`up.py` / `down.py`). Claude may draft the exact steps but must not claim to have performed them, and must never bypass or automate the typed gates.
- **Changes to server bind address, SSH/network config, or `.gitignore`** — Sensitive. Approval Required block before editing.
- **`.env` / `SENSITIVE.local.md`** (the latter is the legacy name; live data now lives in `.env`) — never read their contents back into a response, never commit them, never suggest removing them from `.gitignore`.
- **Benchmark scripts, docs, README updates** — Moderate. Declare plan, then proceed.

Before modifying any existing script or doc that affects the security model (SSH tunnel, bind address, RUNBOOK procedure), surface an Approval Required block first (Intent / Files impacted / Reason / Risks / Rollback → Proceed?).

---

## Key Design Rules

- `llama-server` binds `127.0.0.1` only — remote access is SSH tunnel, never a public port.
- Client code stays backend-agnostic: switching between local Ollama / this pod / a serverless API is a `base_url` change, not a code change.
- Pin base images to CUDA ≤12.4 for Community Cloud host compatibility.
- Terminate (not Stop) the pod at session end — Stop still bills for disk.

---

## Agents / Skills to use here

- New scripts, client code, benchmarks → `Use Python Engineer. Apply Develop.`
- Bugs (pod/server/CUDA failures) → `Use Python Engineer. Apply Debug.`
- Before any change touching network exposure or SSH → `Use Security Reviewer. Apply Review.`
- RUNBOOK/BENCHMARK/README updates → `Use Technical Writer. Apply Document.`
- Before publishing this repo publicly → `Use Security Reviewer. Apply Review.` (confirm no live infra data survives `.gitignore`)

---

## Reference docs in this repo

- `RUNBOOK.md` — end-to-end pod operations
- `BENCHMARK.md` — benchmark methodology and results
- `SESSION-LOG-2026-07-02.md` — operational memory: decisions and lessons
- `KNOWN-ISSUES.md` — deferred review findings, each with cost and planned fix
- `.env` — live pod data + API key, gitignored, never commit (`.env.example` is the template)
