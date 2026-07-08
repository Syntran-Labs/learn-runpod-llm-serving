# 📕 RUNBOOK — Operating the LLM pod on RunPod

> Reproducible end-to-end procedure. With this document (plus your gitignored `.env` for live pod data), any future session can rebuild the full stack from scratch in ~20-30 minutes.

> **Automated path (2026-07-03):** the whole session is now two commands —
> `python -m scripts.pod_ops.up --yes` (deploy → setup → benchmark, gated by typed `DEPLOY`)
> and `python -m scripts.pod_ops.down --yes` (terminate, gated by typed `TERMINATE`).
> This document remains the manual procedure behind those commands, and the
> fallback when the automation misbehaves. Live pod data now lives in `.env`
> (gitignored), not `SENSITIVE.local.md`.

---

## 0. Prerequisites (one-time)

- Local tooling: Python 3.10+ and `pip install -r requirements.txt` (httpx — the repo's only runtime dependency).
- RunPod account with prepaid credits (Billing → Add Credits). **Do not enable auto-recharge**: running out of credit is the economic kill switch.
- ed25519 SSH key pair on the local machine:
  ```cmd
  ssh-keygen -t ed25519 -C "you@runpod-llm"
  type %USERPROFILE%\.ssh\id_ed25519.pub     :: Linux/macOS: cat ~/.ssh/id_ed25519.pub
  ```
  Paste the **public** key in RunPod → Settings/Deploy → SSH public key → Save.
- ⚠️ If the Stripe checkout hangs on "Processing": use an **incognito window** (bypasses adblockers and the Stripe Link session). See session log 2026-07-02.

## 1. Pod deployment (UI)

1. `console.runpod.io` → **Pods → Deploy**.
2. **Compute → GPU**: pick a 24GB VRAM card on **Community Cloud** (not Secure — twice the price).
   - Preference: RTX 4090 (~$0.34/hr) > RTX A5000 (~$0.27/hr) > RTX 3090 (~$0.22-0.30/hr).
   - Criteria: Medium/High availability first, price second. A cheap offer with Low availability can vanish mid-session.
3. **Template/Image**: use CUDA ≤ 12.4 for maximum host compatibility:
   ```
   runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04
   ```
   ⚠️ **Do NOT use cu12.8+ images**: many Community hosts run driver 550 (CUDA 12.4 max) and the container won't start (`unsatisfied condition: cuda>=12.8`).
4. **Pod name**: `syntran-llm-<gpu>`.
5. **Jupyter notebook: OFF** (unnecessary surface). **SSH terminal access: ON**.
6. **Disk**: Container ~15GB / Volume ~30-50GB (the GGUF is 12GB). **No network volume** (persistent storage independent of the pod that keeps billing after the pod dies).
7. **Deploy Pod** → note the time (billing starts) → wait for *Running* state.
8. Copy the top "SSH" command from the **Connect** tab → record it in `.env` as `POD_SSH_CMD=...` (the automated path derives this from the API for you).

### Post-deploy verification (ALWAYS)

```bash
nvidia-smi        # Is the GPU what you paid for? Host driver/CUDA version?
free -h && df -h /workspace
```

> On a marketplace, verifying the actual hardware is part of the procedure, not paranoia.

## 2. Stack setup (inside the pod, via SSH)

```bash
apt update && apt install -y cmake build-essential libcurl4-openssl-dev tmux
cd /workspace
tmux new -s setup
```

**Window 1 — model download (~12GB, 3-6 min):**
```bash
wget -c "https://huggingface.co/ggml-org/gpt-oss-20b-GGUF/resolve/main/gpt-oss-20b-mxfp4.gguf" \
  -O /workspace/gpt-oss-20b-mxfp4.gguf
```

**Window 2 (`Ctrl+B`, release, `c`) — build llama.cpp with CUDA (~5-8 min):**
```bash
cd /workspace
git clone --depth 1 https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build -DGGML_CUDA=ON -DGGML_NATIVE=ON
cmake --build build --config Release -j $(nproc) --target llama-server
```

**Verify both:**
```bash
ls -lh /workspace/gpt-oss-20b-mxfp4.gguf                    # ~12G
ls -lh /workspace/llama.cpp/build/bin/llama-server          # exists and is executable
```

## 3. Start the server (inside tmux — never in the bare SSH session)

```bash
tmux new -s serve    # or: tmux attach -t serve
/workspace/llama.cpp/build/bin/llama-server \
  -m /workspace/gpt-oss-20b-mxfp4.gguf \
  -ngl 99 \
  --ctx-size 8192 \
  --flash-attn on \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --jinja \
  --host 127.0.0.1 \
  --port 8080
```

Wait for `listening on http://127.0.0.1:8080` (~5 s on NVMe).

| Flag | Why |
|---|---|
| `-ngl 99` | all layers on GPU |
| `--ctx-size 8192` | moderate context; VRAM has headroom (11.5GB of 24 used) — can be raised |
| `--cache-type-k/v q8_0` | quantized KV cache, saves VRAM with negligible quality loss |
| `--jinja` | uses the GGUF's chat template (critical for GPT-OSS and its special tokens) |
| `--host 127.0.0.1` | **loopback only — never 0.0.0.0** (see Security) |

### ⌨️ tmux — survival minimum

- `Ctrl+B`, release, `c` → new window (it's *prefix → key*, not a simultaneous chord; an accidental `Ctrl+C` **kills the server**)
- `Ctrl+B`, `n` → next window
- `Ctrl+B`, `d` → detach (everything keeps running) · `tmux attach` to return

## 4. Access from the local machine (HTTP relay — the validated path)

The server binds `127.0.0.1` inside the pod, so you need a local forwarding hop. **The
validated path on RunPod Community Cloud is the repo's HTTP relay**, not a classic SSH
tunnel: the `ssh.runpod.io` proxy rejects `-L` port-forwarding, and on every host observed
so far the pod's direct-TCP SSH endpoint was unreachable (connection refused). This was
confirmed live twice — 2026-07-03 and again 2026-07-04, when an external consumer burned
several attempts on `-L` before falling back to the relay (see KNOWN-ISSUES.md L5 and
`scripts/pod_ops/relay_tunnel.py`'s docstring for the full story).

In a separate local terminal (it stays running — that's the relay):
```cmd
python -m scripts.pod_ops.relay_tunnel
```
It needs an unlocked ssh-agent in that shell (`eval $(ssh-agent -s) && ssh-add
~/.ssh/id_ed25519`) so each request doesn't prompt for the key passphrase, and reads
`POD_SSH_CMD` from `.env`. Each request costs one SSH session (~2-5 s of overhead) —
fine for benchmarks and experiments; the server-side `timings` numbers are unaffected.

With the relay up, on the local machine:
- `http://localhost:8080/v1` → **OpenAI-compatible base_url**

> **Fallback — classic tunnel, only if your pod has a reachable direct-TCP endpoint**
> (the Connect tab shows "SSH over exposed TCP" and it actually connects):
> ```cmd
> ssh -N -L 8080:127.0.0.1:8080 root@<POD_IP> -p <POD_PORT> -i ~/.ssh/id_ed25519
> ```
> (`scripts/pod_ops/open_tunnel.sh` wraps this.) If it works for you it has no
> per-request overhead and also serves the web UI at `http://localhost:8080` — try it
> first, but expect the relay to be the one that works on Community Cloud.

Python client:
```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8080/v1", api_key="not-needed")
r = client.chat.completions.create(
    model="gpt-oss-20b",
    messages=[{"role": "user", "content": "ping"}],
    max_tokens=50,
)
print(r.choices[0].message.content)
```

> The same code points at local Ollama or OpenRouter by changing `base_url` (+ a real `api_key` for serverless). That portability is the point of the experiment.

## 5. Health checks

```bash
# Inside the pod
curl -s http://127.0.0.1:8080/v1/models | python3 -m json.tool | head
nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv
tmux ls
```

Healthy values: ~11.5GB VRAM with the server loaded; `models` returns the GGUF.

## 6. Benchmark

Full methodology in [BENCHMARK.md](BENCHMARK.md). The formal runs use the Python client
through the relay (`python -m scripts.llm_bench.runner` on the local machine); the
original in-pod script is kept as reference:
```bash
bash /workspace/bench.sh    # (upload it from this repo's bench.sh)
```

## 7. 🔴 Shutdown — CRITICAL

**At session end: Terminate, not Stop.**

1. Save/copy every result that matters (benchmark outputs, logs) — **Terminate wipes the volume**.
2. Console → pod → `⋮` → Stop Pod → then **Terminate** (Terminate appears once the pod is stopped).
3. Verify in **Billing** that the pod is no longer accruing charges and that **no orphaned network volume** remains under Storage.

| State | Bills | When to use |
|---|---|---|
| Running | GPU + disk (~$0.25-0.35/hr) | active session |
| Stopped | disk only (~$0.014/hr ≈ $0.34/day) | pause of a few hours, same day |
| Terminated | $0 | session end — **default** |

Rationale: rebuilding the full stack takes ~20-30 min and the model re-downloads in minutes. Parked storage isn't worth paying for.

## 8. Known troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `unsatisfied condition: cuda>=12.8` at container start | host driver older than the image's CUDA | **Edit Pod** → switch image to cu12.4 (no redeploy needed, host is kept) |
| "No instances available" on deploy | someone else took the offer (marketplace is FCFS) | back to Compute, re-select GPU; consider 3090/A5000; try a different time of day |
| Credits checkout stuck on "Processing" | Stripe Link + browser extensions | incognito window, direct card without Link |
| Server died on its own | accidental `Ctrl+C` (mistyped tmux prefix) or process outside tmux + dropped SSH | relaunch inside tmux; check `tmux ls` |
| GPU differs from what was selected | marketplace reassignment/mislabel | `nvidia-smi` ALWAYS post-deploy; decide whether the price justifies it or redeploy |
| curl to `/v1/models` returns nothing | server is not running | `tmux attach -t serve`, check logs, relaunch |
