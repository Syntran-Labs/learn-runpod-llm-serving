# 📈 BENCHMARK — Methodology and results

## Goal

Measure the performance of **GPT-OSS-20B (MXFP4)** served with llama-server on an on-demand cloud GPU, across two representative scenarios, and translate it into a **cost per token** figure comparable against CPU-VPS and serverless APIs.

## Methodology

- **Metrics source**: llama-server's `timings` block in each response (`prompt_per_second`, `predicted_per_second`, `prompt_ms`).
- **Scenarios**:
  - **SHORT (chat)**: ~80-token prompt, 300 output tokens. Simulates conversational use.
  - **LONG (RAG-like)**: ~5-6K-token prompt (dense repeated text, simulates retrieved chunks), 300 output tokens. The critical metric here is **TTFT** (≈ prompt processing time).
- **3 runs per scenario** to observe variance (run 1 may include warmup).
- **`cache_prompt: false`** in every run — without it, reruns reuse the KV cache and prompt processing looks artificially instant.
- **One request at a time** (the server's `n_slots=4` stay idle; no contention).
- Also record: VRAM (`nvidia-smi`), total setup time, session cost (Billing).

Tooling: [`scripts/llm_bench`](scripts/llm_bench) (Python runner + report generator, used for
the formal runs) and [`bench.sh`](bench.sh) (original in-pod curl script, kept as reference).

## Environment

| Item | Value |
|---|---|
| GPU | RTX 3090 24GB (Community Cloud) — driver 550.144.03, CUDA 12.4 |
| Pod | 32 vCPU, 251GB RAM, 50GB NVMe volume |
| Model | gpt-oss-20b-mxfp4.gguf (12GB; MoE 21B total / 3.6B active) |
| Server | llama-server build b1-4fc4ec5, `-ngl 99 --ctx-size 8192 --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 --jinja` |
| Pod cost | $0.27/hr (RTX 3090, Community Cloud) |

## Results

### Smoke test (informal reference)

| Scenario | prompt_n | pp (tok/s) | gen (tok/s) | TTFT (s) |
|---|---|---|---|---|
| short chat | 81 | 346.6 | 189.0 | 0.23 |

### Formal benchmark

| Scenario | Run | prompt_n | pp (tok/s) | gen (tok/s) | TTFT (s) |
|---|---|---|---|---|---|
| SHORT | 1 | 84 | 985.3 | 189.3 | 0.09 |
| SHORT | 2 | 84 | 1109.1 | 182.0 | 0.08 |
| SHORT | 3 | 84 | 1267.4 | 190.2 | 0.07 |
| LONG | 1 | 5648 | 5708.3 | 174.9 | 0.99 |
| LONG | 2 | 5648 | 5715.5 | 175.7 | 0.99 |
| LONG | 3 | 5648 | 5768.2 | 171.1 | 0.98 |

Aggregates — SHORT: pp mean 1120.6 / median 1109.1 / stdev 141.4 tok/s, gen mean 187.2 / median 189.3 / stdev 4.5 tok/s, TTFT mean 0.08s.
Aggregates — LONG: pp mean 5730.7 / median 5715.5 / stdev 32.7 tok/s, gen mean 173.9 / median 174.9 / stdev 2.4 tok/s, TTFT mean 0.99s.

Produced by `scripts/llm_bench` (`results/report-20260702T060606Z.md`); raw per-run data in `results/raw/20260702T060606Z.jsonl` (gitignored).

### Confirmation run — 2026-07-03 (fresh pod, HTTP relay access path)

Same methodology on a freshly deployed pod (a second RTX 3090 Community Cloud host), accessed through
`scripts/pod_ops/relay_tunnel.py` instead of a native SSH tunnel (this host's direct TCP was
unreachable and the ssh.runpod.io proxy rejects `-L`; see the module docstring). Since all
metrics come from llama-server's server-side `timings` block, the access path does not affect
the numbers — and the two sessions agree within ~2%:

| Scenario | Run | prompt_n | pp (tok/s) | gen (tok/s) | TTFT (s) |
|---|---|---|---|---|---|
| SHORT | 1 | 84 | 344.2 | 194.7 | 0.24 |
| SHORT | 2 | 84 | 1139.6 | 195.0 | 0.07 |
| SHORT | 3 | 84 | 1144.7 | 195.2 | 0.07 |
| LONG | 1 | 5648 | 5644.6 | 175.5 | 1.00 |
| LONG | 2 | 5648 | 5625.6 | 173.1 | 1.00 |
| LONG | 3 | 5648 | 5589.2 | 174.8 | 1.01 |

Aggregates — SHORT: gen median 195.0 tok/s, TTFT median 0.07s (run 1 pp shows the cold-start
penalty on a fresh server — median pp 1139.6 is the representative figure).
Aggregates — LONG: pp median 5625.6 tok/s, gen median 174.8 tok/s, TTFT ~1.00s.

Report: `results/report-20260703T185731Z.md`; raw: `results/raw/20260703T185731Z.jsonl` (gitignored).

**Cross-session takeaway:** gen throughput is stable at **~174-175 tok/s (LONG)** and
**~189-195 tok/s (SHORT)** across two different pods, two days, and two access paths.

**Session record:**

| Item | Value |
|---|---|
| VRAM while serving | 11,465 MiB |
| Pod rate | $0.27/hr (RTX 3090, Community Cloud) |
| Session 1 (2026-07-02) total cost | **$0.789** ($0.763 GPU + $0.026 storage — Billing explorer) |
| Session 1 implied pod time | ~2.8 h ($0.763 ÷ $0.27/hr) |
| Session 2 (2026-07-03) total cost | not copied back into this log before publication (Billing posts day-close aggregates; the pod ran well under an hour at $0.27/hr) |
| Setup time (signup → server up) | not recorded (session 1); ~15 min pod-deploy → server-up (session 2, scripted) |

## Comparables (for the final analysis)

| Backend | Cost | Expected/measured performance | Note |
|---|---|---|---|
| **RunPod 3090 (this experiment)** | ~$0.25-0.35/hr | **189 tok/s gen** (smoke) | on-demand, per-second billing |
| Hetzner CAX31 (ARM CPU) | $24.99/mo | ~10-18 tok/s (estimated) | out of stock — the option we couldn't buy |
| DeepInfra/OpenRouter serverless | ~$0.27/$1.10 per M tok (DeepSeek-V3); small models for cents | n/a (managed) | zero ops, pay per token |
| Local Ollama | $0 marginal | to be measured | same API, local `base_url` |

### Napkin math

- At a sustained 174 tok/s (LONG, the conservative figure), one pod-hour produces ~627K
  generated tokens. At the $0.27/hr rate (confirmed against Billing: $0.763 GPU for ~2.8
  pod-hours on 2026-07-02) that is **~$0.43/M generated tokens** *if the GPU is saturated*. An honest comparison against
  serverless must include the utilization factor: serverless bills per token consumed; the
  pod bills per hour powered on.
- TTFT in the RAG scenario (5-6K prompt, prompt_n=5648): ~1.0s — the number an end user of
  a RAG system actually perceives.

## Conclusions

1. **A ~$0.27/hr consumer GPU serves a 20B-class model at interactive speed.** ~189-195
   tok/s generation on short prompts, ~174 tok/s with a 5.6K-token context, TTFT ~1.0 s
   on the RAG-like scenario. For experimentation, that is a real endpoint — not a toy —
   at the price of a coffee per session, and $0 the moment you terminate.
2. **MoE changes the serving economics.** GPT-OSS-20B activates only 3.6B of its 21B
   parameters per token, so it generates at small-model speed while the quality tier is
   set by the full parameter count. On the RTX 3090 the ceiling is memory bandwidth
   (936 GB/s), not compute — which is exactly why a last-gen consumer card holds up.
3. **Saturated cost is ~$0.43/M generated tokens — but utilization is the whole
   comparison.** The pod bills per hour powered on; serverless bills per token consumed.
   At 100% utilization the pod beats most serverless prices for this quality tier; at
   low utilization serverless wins. The honest framing for experimenters: pay ~$0.27/hr
   *while you're actually testing*, terminate when done.
4. **The numbers are reproducible because they're measured server-side.** Two pods, two
   days, and two different access paths (native tunnel vs. HTTP relay) agree within ~2%,
   since all metrics come from llama-server's own `timings` block — transport overhead
   never contaminates the measurement.
5. **Setup friction is part of the real cost.** Payment checkout quirks, CUDA/driver
   mismatches, and GPU availability consumed more time than the technical stack itself
   on day one; the scripted path (`up.py`) then cut pod-deploy → server-up to ~15 min.
   Documenting that friction is deliberate — it is the part no pricing page tells you.
