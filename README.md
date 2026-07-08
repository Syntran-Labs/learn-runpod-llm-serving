<div align="center">

# 🚀 learn-runpod-llm-serving

**Self-hosted LLM serving on a GPU cloud marketplace — deployment, benchmarking, and cost analysis**

![Track](https://img.shields.io/badge/Syntran%20Labs-Learning%20Track-purple?style=flat-square)
![CI](https://github.com/Syntran-Labs/learn-runpod-llm-serving/actions/workflows/ci.yml/badge.svg)
![Status](https://img.shields.io/badge/status-active_experiment-orange?style=flat-square)
![License](https://img.shields.io/badge/license-Apache_2.0-blue?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python&logoColor=white)
![GPU](https://img.shields.io/badge/GPU-RTX_3090_24GB-76B900?style=flat-square&logo=nvidia&logoColor=white)
![Model](https://img.shields.io/badge/model-GPT--OSS--20B_MXFP4-blue?style=flat-square)
![Serving](https://img.shields.io/badge/serving-llama.cpp_server-lightgrey?style=flat-square)
![Cloud](https://img.shields.io/badge/cloud-RunPod_Community-673AB7?style=flat-square)

*Part of the Syntran Labs [Learning Lab](https://github.com/Syntran-Labs/learning-lab) — LLMOps track*

</div>

---

## 🎯 Goal

Deploy an open-weights LLM (**GPT-OSS-20B**, MoE — 21B total / 3.6B active params, MXFP4 quantization) on an on-demand **RunPod Community Cloud** GPU pod, serve it through an **OpenAI-compatible API** (llama-server), measure real-world performance in two scenarios (short chat vs. long RAG-like context), and compare cost/performance against alternatives (CPU VPS, serverless APIs).

**The LLMOps thesis of this experiment:** the same client code (`base_url` + `/v1`) should point interchangeably at local Ollama, this pod, or a serverless API — switching inference backends should be a configuration change, not a code change.

**Who this is for:** anyone experimenting with AI who wants a real, low-cost LLM endpoint of their own — a 20B-class model at ~$0.27/hr while it runs, $0 when terminated, no GPU purchase. No public endpoint, no third-party managed inference API, and no prompts sent to a hosted model provider — access stays behind your SSH-authenticated relay to the RunPod instance. Every step (including the failures) is documented so you can reproduce it end to end.

> This is a Learning Lab project with Systems Lab-grade operational discipline: the goal is educational reproducibility, not a managed production service.

## 📊 Results (formal benchmark — 3 runs × 2 scenarios × 2 sessions)

| Metric | Value | Conditions |
|---|---|---|
| 🔥 Generation (chat) | **~189-195 tok/s** | 84-tok prompt, 300 tok out |
| 🔥 Generation (RAG-like) | **~174 tok/s** | 5,648-tok prompt, 300 tok out |
| ⚡ Prompt processing | **~5,600-5,730 tok/s** | LONG scenario, no cache |
| ⏱️ TTFT (RAG-like) | **~1.0 s** | full 5.6K-tok prompt pass |
| 💾 VRAM | **11.5 GB / 24 GB** | ctx 8192, q8_0 KV cache |
| 💵 Cost per M generated tokens | **~$0.43** | at ~$0.27/hr, GPU saturated |

> Reproduced within ~2% across two pods, two days, and two access paths.
> Full methodology and per-run data: [BENCHMARK.md](BENCHMARK.md)

## ⚡ Quickstart — one command up, one command down

**Prerequisites (one-time, ~10 min):**

1. **Python 3.10+** and the repo's single runtime dependency:
   ```bash
   pip install -r requirements.txt
   ```
2. A **RunPod account with prepaid credits** ($10-15 is plenty for several sessions; don't enable auto-recharge — running out of credit is your economic kill switch).
3. An **ed25519 SSH key** registered in RunPod (Settings → SSH public keys). See [RUNBOOK.md §0](RUNBOOK.md) for the exact commands.
4. Your **RunPod API key** in `.env`:
   ```bash
   cp .env.example .env    # then set RUNPOD_API_KEY=...
   ```

**Then:**

```bash
# Deploy pod + install stack + benchmark (gated: you must type DEPLOY)
python -m scripts.pod_ops.up --yes

# Terminate the pod and stop all billing (gated: you must type TERMINATE)
python -m scripts.pod_ops.down --yes
```

Run either without `--yes` for a dry run that prints the plan and spends nothing.
`up` automates everything *between* the human confirmation gates: SSH command
derivation from the RunPod API, ssh-agent handling, in-pod setup (`setup.sh`),
the local HTTP relay, and the benchmark report. The manual step-by-step
procedure remains documented in [RUNBOOK.md](RUNBOOK.md).

> ⚠️ **During setup your terminal will look broken — it isn't.** RunPod's SSH proxy
> forces an interactive PTY that echoes every line `up.py` types into the pod, so you
> will see `setup.sh`'s text duplicated and mangled, interleaved with `root@...#`
> prompts. That's cosmetic. The real signal is the step markers:
> `=== [n/5] ... ===` → `done (Xs)` — five of those, then the benchmark report.
> (Full explanation of the proxy's quirks: `scripts/pod_ops/relay_tunnel.py`.)

### 💬 Talk to your model

When `up` finishes, the pod is still running and serving. Start the relay in a
terminal (it stays open — that's your local gateway to the pod):

```bash
python -m scripts.pod_ops.relay_tunnel
```

Then point **any** OpenAI-compatible client at it:

```python
# pip install openai   (or use plain httpx/curl — it's just HTTP)
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8080/v1", api_key="not-needed")
r = client.chat.completions.create(
    model="gpt-oss-20b",
    messages=[{"role": "user", "content": "Explain MoE models in one paragraph."}],
)
print(r.choices[0].message.content)
```

Remember: the pod bills while it runs (~$0.27/hr). When you're done experimenting,
`python -m scripts.pod_ops.down --yes` puts your spend back to exactly $0.

**Want to understand how and why?** Suggested reading order:
[RUNBOOK.md](RUNBOOK.md) (the manual procedure behind the automation) →
[BENCHMARK.md](BENCHMARK.md) (methodology and numbers) →
[SESSION-LOG-2026-07-02.md](SESSION-LOG-2026-07-02.md) (what actually went wrong on
day one — the part tutorials skip) →
[KNOWN-ISSUES.md](KNOWN-ISSUES.md) (what's still imperfect, each with its planned fix).

## 🧩 Stack

| Component | Choice | Status |
|---|---|---|
| Cloud | RunPod Community Cloud (marketplace, per-second billing) | ✅ |
| GPU | RTX 3090 24GB (~$0.25/hr) — no 4090 stock at deploy time | ✅ |
| Base image | `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04` | ✅ |
| Model | [`ggml-org/gpt-oss-20b-GGUF`](https://huggingface.co/ggml-org/gpt-oss-20b-GGUF) (`gpt-oss-20b-mxfp4.gguf`, 12 GB) | ✅ |
| Serving | llama-server (llama.cpp built with `-DGGML_CUDA=ON`) | ✅ |
| Access | SSH (RunPod proxy) — **zero public ports** | ✅ |
| Benchmark | llama-server `timings` block | ✅ |

## 📁 Repo structure

```
learn-runpod-llm-serving/
├── README.md                  ← you are here
├── RUNBOOK.md                 ← end-to-end pod operations (the manual path)
├── BENCHMARK.md               ← methodology and results
├── SESSION-LOG-2026-07-02.md  ← operational memory: decisions & lessons
├── KNOWN-ISSUES.md            ← deferred review findings, tracked with fixes
├── LICENSE                    ← Apache 2.0
├── requirements.txt           ← httpx (runtime) + pytest (tests)
├── .env.example               ← template for your .env (copy it, fill it in)
├── .gitignore                 ← excludes .env, keys, and *.local.md
├── .env                       ← API key + live pod data (NEVER committed)
├── setup.sh                   ← in-pod stack setup (deps → model → build → serve)
├── bench.sh                   ← original in-pod benchmark script (reference)
├── results/                   ← benchmark reports (raw JSONL gitignored)
└── scripts/
    ├── llm_bench/             ← Python benchmark client (runner, analyze, tests)
    └── pod_ops/               ← gated pod lifecycle: deploy, terminate, HTTP relay
```

> **Platform note:** developed and live-tested on Windows (the scripts prefer Git for
> Windows' `ssh.exe` to keep ssh-agent working, falling back to whatever `ssh` is on
> PATH). On Linux/macOS the system OpenSSH is used directly — no changes needed.

## 🔐 Security — access model

- llama-server listens **only on `127.0.0.1`** inside the pod — there is no public endpoint.
- Remote access goes over SSH; the SSH key is the authentication layer. Note: RunPod's
  `ssh.runpod.io` proxy does not support `-L` port-forwarding, so local access uses
  `scripts/pod_ops/relay_tunnel.py` (an HTTP relay over SSH) instead of a classic tunnel.
- Live pod details and the RunPod API key live in `.env`, **excluded via `.gitignore`**.
- No live infrastructure data ships in this repo: pod identifiers in docs/tests are from
  long-terminated pods or synthetic, and the public history starts from a curated
  initial commit.

## 💡 LLMOps lessons (summary)

1. **On GPU marketplaces, the host's CUDA version is a variable you don't control** — pin your image to the *minimum* CUDA you need, not the latest.
2. **Verify the actual hardware with `nvidia-smi`** — what the marketplace labels and what you get can differ.
3. **MoE changes the serving economics**: 21B-class quality at 3.6B-active speed (~189 tok/s on a consumer GPU).
4. **Terminate > Stop** for ephemeral sessions: the model re-downloads in minutes; parked storage bills by the hour.

Full detail in [SESSION-LOG-2026-07-02.md](SESSION-LOG-2026-07-02.md).

## 🔗 Series context

| Experiment | Backend | Status |
|---|---|---|
| Local Ollama | own CPU/GPU | 🔜 |
| **RunPod on-demand pod** | RTX 3090 24GB | ✅ benchmarked |
| Serverless API (OpenRouter/DeepInfra) | managed | 🔜 |
| Hetzner Server Auction (always-on) | dedicated | 💤 future evaluation |

## 📄 License

[Apache 2.0](LICENSE) © 2026 Syntran Labs. Use it, fork it, learn from it — that's what it's here for.

---

<div align="center">
<sub>Syntran Labs · AI Engineering portfolio · secure RAG · LLM agents · LLMOps</sub>
</div>
