# 🧠 SESSION LOG — 2026-07-02 — First RunPod deploy + GPT-OSS-20B

> Operational memory: what was done, what was decided and why, what went wrong, and what was learned. This document is the source of truth for resuming the experiment (from VS Code / SYNTRAN AIEOS or any future session).

---

## 1. Context and platform decision

- **Goal**: self-hosted LLM with an OpenAI-compatible API + reproducible benchmark for the Syntran Labs LLMOps portfolio.
- **Alternatives previously evaluated**:
  - Hetzner CAX31 (ARM 8vCPU/16GB, $24.99/mo) → **out of stock**. Estimated 10-18 tok/s on CPU for GPT-OSS-20B.
  - Hetzner CPX (x86) → prices jumped to absurd levels ($141/mo for 8vCPU/16GB).
  - **Decision**: RunPod on-demand for serving sessions + OpenRouter for day-to-day tokens. Hetzner Server Auction remains a future always-on option.
- A pre-existing VPS from another project is **not part** of this experiment.

## 2. Session timeline

| # | Event | Outcome / decision |
|---|---|---|
| 1 | RunPod signup (GitHub OAuth) + MFA | ✅ |
| 2 | Credit top-up — checkout stuck on "Processing" | ❌→✅ The $0 receipt was a **card verification**, not a charge. Root cause: checkout via **Stripe Link** + browser extensions (ABP). **Fix: incognito window.** $15 loaded. |
| 3 | GitHub ↔ RunPod integration | Minimal scope: **only** `Syntran-Labs/runpod-llm-serving` (least privilege; never "All repositories"). Permission: read-only code/metadata. |
| 4 | Repo created | `runpod-llm-serving`, private for now; public once the material is curated. |
| 5 | Deploy 4090 Community $0.34/hr | ❌ "No instances available" — someone else took the offer (marketplace is FCFS). |
| 6 | Deploy with PyTorch 2.8.0 template (cu12.8) | ❌ Container won't start: `unsatisfied condition: cuda>=12.8`. Host on driver 550 = CUDA 12.4 max. |
| 7 | **Edit Pod** → image `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04` | ✅ Same host, no redeploy. Lesson: the image can be swapped in-place. |
| 8 | `nvidia-smi` post-deploy | ⚠️ Actual GPU is an **RTX 3090**, not a 4090 (no 4090 stock; 3090 accepted — same 24GB VRAM). Host: driver 550.144.03, CUDA 12.4, 251GB RAM, 32 vCPU, 50GB volume. |
| 9 | Setup: apt deps + tmux; GGUF download (12GB) and llama.cpp CUDA build in parallel | ✅ |
| 10 | First server start | ⚠️ Launched **outside tmux** and killed by an accidental `Ctrl+C` (mistyped tmux prefix). Relaunched inside tmux. |
| 11 | Server up + smoke test | ✅ Numbers below. |
| 12 | Formal benchmark | ⏳ **PENDING** — next step. |

## 3. Current stack state (as of the end of this part of the session)

- **Pod**: `SYNTRAN-LLM-POD` — RTX 3090 24GB, Community Cloud, **running and billing**. Connection details in `SENSITIVE.local.md`.
- **Image**: `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`
- **Model**: `/workspace/gpt-oss-20b-mxfp4.gguf` (12GB, `ggml-org/gpt-oss-20b-GGUF`)
- **llama.cpp**: built at `/workspace/llama.cpp/build/bin/llama-server` (build `b1-4fc4ec5`, `-DGGML_CUDA=ON`)
- **Server**: running in tmux (session `serve`), `127.0.0.1:8080`, ctx 8192, q8_0 KV, `--jinja`
- **Access**: SSH tunnel from the local machine → `http://localhost:8080/v1`
- **Note**: `n_slots = 4` (llama-server's default parallelism). With one request at a time it doesn't affect measurements; for strict single-slot: `--parallel 1`.

## 4. Recorded numbers (smoke test — NOT the formal benchmark)

| Metric | Value |
|---|---|
| VRAM used | **11,465 MiB / 24,576 MiB** |
| Prompt processing | **346.6 tok/s** (81 tokens, no cache) |
| Generation | **189.0 tok/s** (168 tokens) |
| Model load | ~4.5 s (NVMe) |
| Model's trained context | 131,072 (we use 8,192) |

Key observation: **~189 tok/s beats the expected range (50-100)** for a consumer GPU — the MoE (3.6B active of 21B) generates at small-model speed. On the 3090 the ceiling is memory bandwidth (936 GB/s), not compute.

## 5. Decisions with rationale

1. **3090 instead of 4090**: no 4090 stock on Community at the time. Same 24GB VRAM; ~70-75% of the bandwidth. The benchmark remains valid and the *tok/s per dollar* angle may favor the 3090. Verify the exact rate in Billing.
2. **Community over Secure Cloud**: same hardware, half the price ($0.34 vs $0.69/hr for a 4090). Secure pays for compliance this experiment doesn't need.
3. **llama.cpp built from source** (not the server Docker image): the template already shipped the CUDA toolchain; building gave flag control and avoided pulling another image. Trade-off: ~8 min build. Future alternative: custom template with a precompiled binary.
4. **`--host 127.0.0.1` + SSH tunnel, no public proxy**: zero exposed surface; auth is the SSH key. For a *secure* RAG portfolio, this is the pattern to showcase.
5. **Repo private now, public later**: capture real friction without pressure; hygiene from day 1 (never commit IPs/keys → `SENSITIVE.local.md` + `.gitignore`).
6. **No network volume**: persistent storage independent of the pod that survives Terminate and bills on its own (~$3.60/mo). For ephemeral sessions, local pod volume.

## 6. LLMOps lessons from the session

1. **The host's CUDA version is an uncontrolled variable on marketplaces** → pin the image to the minimum CUDA needed (12.4 runs almost everywhere; 12.8 locks you out of half the fleet).
2. **ALWAYS verify actual hardware with `nvidia-smi`** — marketplace label ≠ delivered hardware.
3. **Edit Pod > redeploy** when the problem is the image, not the host.
4. **Every long-lived process goes inside tmux** — an SSH session is not a supervisor.
5. **Setup friction is part of the real cost**: payment checkout, CUDA mismatch, and GPU availability consumed more time than the technical stack itself. Documenting it is the honest part of the cost analysis.
6. **Prepaid without auto-recharge as the economic kill switch.**

## 7. Pending / next steps

- [ ] Run `scripts/bench.sh` (2 scenarios × 3 runs, `cache_prompt: false`)
- [ ] Record: total setup time, real session cost (Billing), exact pod rate/hr
- [ ] Fill in `docs/BENCHMARK.md` (table + cost/performance analysis vs. comparables)
- [ ] **Terminate the pod at session close** (verify in Billing!)
- [ ] Evaluate a custom RunPod template (image with precompiled llama-server) to cut setup to ~5 min
- [ ] Repeat the methodology against local Ollama and OpenRouter to close the portability story (`LLM_BASE_URL`)
- [ ] Continue the engineering process from VS Code with SYNTRAN AIEOS using this repo as context
