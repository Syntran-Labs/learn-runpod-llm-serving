#!/bin/bash
# ============================================================
# bench.sh — GPT-OSS-20B benchmark via llama-server /v1
# 2 scenarios (SHORT chat / LONG ~5-6K tok RAG-like) x 3 runs
# Metrics: llama-server `timings` block
# Requirements: server running on 127.0.0.1:8080, python3, curl
# Usage: bash bench.sh   (run INSIDE the pod)
# ============================================================
set -euo pipefail

URL="http://127.0.0.1:8080/v1/chat/completions"

echo "== Environment =="
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader
echo

# Long prompt (~5-6K tokens): dense repeated sentence, simulates RAG chunks
export LONG_CONTEXT=$(python3 -c "print(('An elementary cellular automaton is a discrete dynamical system defined over a one-dimensional tape of binary cells that evolve according to a deterministic local rule applied synchronously. ' * 300))")

run_test() {
  local label=$1; local payload=$2
  for i in 1 2 3; do
    curl -s "$URL" -H "Content-Type: application/json" -d "$payload" \
    | python3 -c "
import json,sys
r=json.load(sys.stdin)
t=r['timings']
print(f'$label run $i | prompt_n={t[\"prompt_n\"]} | pp={t[\"prompt_per_second\"]:.1f} tok/s | gen={t[\"predicted_per_second\"]:.1f} tok/s | ttft~={t[\"prompt_ms\"]/1000:.2f}s | gen_n={t[\"predicted_n\"]}')
"
  done
}

SHORT=$(python3 -c "
import json
print(json.dumps({'model':'gpt-oss-20b','messages':[{'role':'user','content':'Briefly explain the difference between an elementary cellular automaton and a two-dimensional one.'}],'max_tokens':300,'cache_prompt':False}))
")

LONG=$(python3 -c "
import json,os
ctx=os.environ['LONG_CONTEXT']
print(json.dumps({'model':'gpt-oss-20b','messages':[{'role':'user','content':ctx+' Summarize the text above in 3 bullet points.'}],'max_tokens':300,'cache_prompt':False}))
")

echo "== SHORT scenario (chat) =="
run_test "SHORT" "$SHORT"
echo
echo "== LONG scenario (RAG-like) =="
run_test "LONG" "$LONG"
echo
echo "== VRAM post-benchmark =="
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
