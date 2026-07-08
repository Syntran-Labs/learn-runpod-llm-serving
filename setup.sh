#!/bin/bash
# ============================================================
# setup.sh — automated in-pod stack setup for GPT-OSS-20B / llama-server
#
# Mirrors RUNBOOK.md sections 2-3 exactly (same packages, same model, same
# llama-server flags — including --host 127.0.0.1, never 0.0.0.0). Safe to
# re-run: each step is skipped if its output already exists.
#
# Requirements: run INSIDE the pod, after you've deployed it yourself via
# console.runpod.io (or scripts/pod_ops/deploy_pod.py --yes) and have an SSH
# session open. This script does NOT create, redeploy, or terminate any pod —
# that stays a human action per this project's governance (CLAUDE.md).
#
# Usage: bash setup.sh   (upload it from this repo's setup.sh)
# ============================================================
set -euo pipefail

# Steps run as background jobs of an interactive shell (the RunPod proxy
# forces one): any read from the TTY there means SIGTTIN -> job Stopped ->
# infinite spinner. So no step may ever prompt.
export DEBIAN_FRONTEND=noninteractive

WORKSPACE=/workspace
MODEL_PATH="${WORKSPACE}/gpt-oss-20b-mxfp4.gguf"
MODEL_URL="https://huggingface.co/ggml-org/gpt-oss-20b-GGUF/resolve/main/gpt-oss-20b-mxfp4.gguf"
LLAMA_CPP_DIR="${WORKSPACE}/llama.cpp"
LLAMA_SERVER_BIN="${LLAMA_CPP_DIR}/build/bin/llama-server"
# Single CUDA arch instead of llama.cpp's ~8-arch default: cuts the build to a
# fraction, which matters on CPU-throttled Community hosts. 86 = RTX 3090 and
# RTX A5000 (use 89 for RTX 4090); plain "86" also embeds PTX, so newer GPUs
# still work via JIT.
CUDA_ARCH=86
TMUX_SESSION=serve
SERVER_HOST=127.0.0.1
SERVER_PORT=8080
# Readiness gate = /health returning 200, which llama-server only does once
# the model is fully loaded — so this budget covers loading ~12GB from the
# volume, not just binding the port.
SERVER_READY_TIMEOUT_S=300

PB_TOTAL=5
PB_CURRENT=0

# ---- progress helpers (spinner + elapsed time; no fabricated %) ----
pb_run_step() {
  local label="$1"; shift
  PB_CURRENT=$((PB_CURRENT + 1))
  local start; start=$(date +%s)
  echo
  echo "=== [${PB_CURRENT}/${PB_TOTAL}] ${label} ==="

  # stdin from /dev/null: a background job reading the TTY gets SIGTTIN and
  # stops silently (observed 2026-07-03: debconf during apt install).
  "$@" < /dev/null &
  local pid=$!
  local spin='|/-\'
  local i=0
  while kill -0 "$pid" 2>/dev/null; do
    i=$(( (i + 1) % 4 ))
    local elapsed=$(( $(date +%s) - start ))
    printf "\r  %s elapsed %ds   " "${spin:$i:1}" "$elapsed"
    sleep 0.3
  done

  # errexit-safe status capture: a bare `wait` would trip `set -e` before
  # $? is read — exiting early when run locally, and (worse) in the proxy's
  # forced INTERACTIVE shell errexit only aborts to the prompt, so the
  # remaining script lines would keep executing after a failed step. The
  # `|| status=$?` form is immune in both contexts, and the explicit
  # `exit "$status"` below is what actually stops the interactive shell.
  local status=0
  wait "$pid" || status=$?
  local elapsed=$(( $(date +%s) - start ))
  if [ "$status" -eq 0 ]; then
    printf "\r  done (%ds)                              \n" "$elapsed"
  else
    printf "\r  FAILED (%ds, exit %d)                    \n" "$elapsed" "$status"
    exit "$status"
  fi
}

pb_overall() {
  local width=30
  local filled=$(( width * PB_CURRENT / PB_TOTAL ))
  local bar; bar=$(printf '%*s' "$filled" '' | tr ' ' '#')
  bar+=$(printf '%*s' "$((width - filled))" '' | tr ' ' '-')
  echo "[$bar] ${PB_CURRENT}/${PB_TOTAL} steps complete"
}

# ---- steps (each idempotent) ----
step_apt_deps() {
  # Recover from a previously interrupted install (e.g. the SIGTTIN incident):
  # finish half-configured packages before asking for anything new.
  dpkg --configure -a >/dev/null 2>&1 || true
  apt-get update -qq
  apt-get install -y -qq cmake build-essential libcurl4-openssl-dev tmux >/dev/null
}

step_download_model() {
  # wget -c resumes/no-ops if the file is already complete.
  wget -c -q "$MODEL_URL" -O "$MODEL_PATH"
}

step_build_llama_cpp() {
  if [ -x "$LLAMA_SERVER_BIN" ]; then
    echo "  llama-server already built at ${LLAMA_SERVER_BIN}, skipping build."
    return 0
  fi
  if [ ! -d "$LLAMA_CPP_DIR" ]; then
    git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_CPP_DIR" -q
  fi
  cmake -B "${LLAMA_CPP_DIR}/build" -S "$LLAMA_CPP_DIR" -DGGML_CUDA=ON -DGGML_NATIVE=ON \
    -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCH" >/dev/null
  cmake --build "${LLAMA_CPP_DIR}/build" --config Release -j "$(nproc)" --target llama-server >/dev/null
}

step_verify_binaries() {
  [ -f "$MODEL_PATH" ] || { echo "  missing $MODEL_PATH" >&2; return 1; }
  [ -x "$LLAMA_SERVER_BIN" ] || { echo "  missing $LLAMA_SERVER_BIN" >&2; return 1; }
  ls -lh "$MODEL_PATH" "$LLAMA_SERVER_BIN"
}

step_start_server() {
  if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "  tmux session '${TMUX_SESSION}' already running, not relaunching."
  else
    tmux new-session -d -s "$TMUX_SESSION" \
      "$LLAMA_SERVER_BIN" \
        -m "$MODEL_PATH" \
        -ngl 99 \
        --ctx-size 8192 \
        --flash-attn on \
        --cache-type-k q8_0 \
        --cache-type-v q8_0 \
        --jinja \
        --host "$SERVER_HOST" \
        --port "$SERVER_PORT"
  fi

  # -f makes curl fail on HTTP errors: without it, the 503 the server
  # returns while still LOADING the model counts as "ready" and the client
  # side then races the model load (observed risk, review 2026-07-04).
  local waited=0
  while ! curl -sf -o /dev/null "http://${SERVER_HOST}:${SERVER_PORT}/health"; do
    sleep 1
    waited=$((waited + 1))
    if [ "$waited" -ge "$SERVER_READY_TIMEOUT_S" ]; then
      echo "  server did not respond within ${SERVER_READY_TIMEOUT_S}s — check: tmux attach -t ${TMUX_SESSION}" >&2
      return 1
    fi
  done
}

echo "== Environment =="
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader
echo

pb_run_step "Installing apt dependencies" step_apt_deps
pb_run_step "Downloading gpt-oss-20b-mxfp4.gguf (~12GB)" step_download_model
pb_run_step "Building llama.cpp with CUDA" step_build_llama_cpp
pb_run_step "Verifying model + binary" step_verify_binaries
pb_run_step "Starting llama-server (127.0.0.1 only) and waiting until the model is loaded" step_start_server

echo
pb_overall
echo
echo "Server listening on http://${SERVER_HOST}:${SERVER_PORT} (loopback only)."
echo "From your local machine: python -m scripts.pod_ops.relay_tunnel   (validated access path)"
echo "  (ssh -N -L only works on pods with a reachable direct TCP endpoint; the RunPod proxy rejects it)"
echo "Then: python -m scripts.llm_bench.runner   (or bash bench.sh on the pod)"

# Explicit exit so the forced-PTY proxy session closes when this script is fed
# as stdin (H3 in KNOWN-ISSUES.md: EOF alone did not end the session and
# up.py stalled until SETUP_TIMEOUT_S). Harmless when run standalone.
exit 0
