# Known Issues & Deferred Review Findings

Reference document. Source: code review of the one-command lifecycle
(`up.py` / `setup.sh` / pod_ops), 2026-07-04. Findings H1, H2 and M1 from that
review were **fixed the same day** (errexit-safe step-failure handling in
`setup.sh`, `POD_ID` recorded in `.env` at creation time, readiness gates on
`/health` + a 5-min model-load budget). The items below were accepted as known
limitations and deferred — each entry says what it costs and what the fix
would be, so any of them can be picked up in a future session without
re-deriving the analysis.

Severity follows the review scale: **M** = medium (robustness / reproducibility
risk on the normal path), **L** = low (quality / docs; no runtime risk).

---

## H3 — `up.py` stalls after setup completes: EOF does not end the proxy session (FIXED 2026-07-08, validated live)

- **Where:** `up.py` `run_setup()` (feeds `setup.sh` as the ssh process's stdin
  and waits for the session to close)
- **Observed (first live run, 2026-07-04):** all 5 setup steps completed and
  llama-server passed the `/health` gate on the pod, but the ssh session sat
  at the `root@...:/#` prompt instead of closing, so `up.py` never advanced to
  the relay/health/benchmark phases (its only escape is the 120-min
  `SETUP_TIMEOUT_S`). This contradicts the EOF-closes-the-session behavior
  `run_setup.sh` documented on 2026-07-03 — root cause not yet established
  (candidate: stdin-EOF propagation differs between bash's `< file`
  redirection and `subprocess.Popen(stdin=file)` under the forced PTY).
- **Workaround (validated path):** Ctrl+C, then
  `python -m scripts.pod_ops.up --reuse-pod --skip-setup --yes` — setup is
  idempotent and already done, so this resumes at the relay phase.
- **Fix when picked up:** don't rely on EOF at all — end the typed script with
  an explicit `exit` (simplest: append `exit 0` as `setup.sh`'s last line,
  harmless when run standalone; or feed via a pipe and write `exit\n` after
  the script). Then re-test the unattended full run.
- **Status (2026-07-04):** `exit 0` appended to `setup.sh` (approved change).
  Remains OPEN until the unattended full run is re-tested on the next deploy.
- **Status (2026-07-08): FIXED** — validated on the next fresh deploy (third
  pod, $0.22/hr RTX 3090 host): the typed `exit 0` closed the proxy session on
  its own and `up.py` advanced unattended through relay → health check (passed
  on attempt 1) → benchmark → report, zero manual intervention. Full session
  (deploy → report) ~25 min. Results in `results/report-20260708T032850Z.md`
  agree with the two previous sessions within ~3% (LONG gen 169.8 tok/s,
  pp ~5,700 tok/s, TTFT 0.99 s; SHORT gen ~193 tok/s median).

## L5 — setup.sh's final hint suggests `ssh -L`, a dead path on this host

- **Where:** `setup.sh`, the closing `echo` lines ("From your local machine:
  ssh -N -L 8080:...")
- **Impact:** misleading — the RunPod proxy rejects `-L` forwarding here; the
  working access path is `python -m scripts.pod_ops.relay_tunnel`.
- **Confirmed live (2026-07-04):** an external consumer (the EVAL project's
  agent) followed the printed hint and burned several attempts before falling
  back to the relay — the proxy rejected `-L` and the pod's direct TCP
  endpoint (even after refreshing the stale `.env` value via the RunPod API)
  was unreachable, same as documented 2026-07-03. The relay then worked
  end-to-end (llama-server responded through `localhost:8080`). Cost of this
  hint is now demonstrated, not hypothetical.
- **Fix when picked up:** reword the hint to point at the relay (and
  `open_tunnel.sh` only for future pods with working direct TCP).
- **Status (2026-07-04): FIXED** — hint now points at
  `python -m scripts.pod_ops.relay_tunnel`, with `ssh -L` noted as
  direct-TCP-only (approved change).

## M2 — llama.cpp and model revision are unpinned

- **Where:** `setup.sh` (`git clone --depth 1` of llama.cpp master; `MODEL_URL`
  resolves `main`)
- **Impact:** every fresh pod builds whatever llama.cpp master is that day.
  A broken upstream master breaks pod setup, and tok/s numbers are not
  comparable across sessions — which matters for a benchmarking project.
- **Fix when picked up:** clone a release tag (`git clone -b <tag>`) and pin
  the Hugging Face revision in `MODEL_URL`
  (`.../resolve/<commit>/gpt-oss-20b-mxfp4.gguf`). Record both in
  `BENCHMARK.md` alongside the results they produced.

## M3 — no integrity check on the ~12 GB model download

- **Where:** `setup.sh` `step_download_model` / `step_verify_binaries`
- **Impact:** verification only checks the file *exists*. A truncated download
  (killed `wget`, server hiccup during resume) passes and surfaces later as a
  confusing llama-server load failure.
- **Fix when picked up:** assert an expected minimum byte size (cheap), or
  verify sha256 against the value published on the model's Hugging Face page
  (proper). Do this together with M2 — the pinned revision fixes the expected
  hash.

## L1 — `up.py` imports private helpers from sibling modules

- **Where:** `up.py` imports `_RelayHandler`, `_load_ssh_cmd`,
  `_StreamCollector`, `_split_marker`, `_resolve_ssh_binary` from
  `relay_tunnel.py`, and `_print_plan` / `_confirm_interactively` from
  `deploy_pod.py`
- **Impact:** these underscore names are now a shared contract; a future
  refactor of `relay_tunnel.py` can silently break `up.py`.
- **Fix when picked up:** drop the leading underscores (or export public
  aliases) and update imports — pure rename, no behavior change.

## L2 — setup has a 120-min hard cap but no stall detection

- **Where:** `up.py` `SETUP_TIMEOUT_S`
- **Impact:** a wedged Community Cloud host (e.g. the cgroup-throttled build
  observed 2026-07-03) bills for up to 2 h before the flow aborts.
- **Fix when picked up:** watch the streamed setup output and abort early if
  no bytes arrive for N minutes; keep the total cap as backstop.

## L3 — CLAUDE.md governance wording predates API-driven deploy

- **Where:** project `CLAUDE.md`, Safety Category section ("the human always
  drives console.runpod.io actions")
- **Impact:** the agreed mechanism is now API-driven creation/termination
  behind typed `DEPLOY` / `TERMINATE` gates (`up.py` / `down.py`). The typed
  gate honors the spirit, but the letter of CLAUDE.md still describes the
  console-only era, so future reviews will re-flag the tooling as a violation.
- **Fix when picked up:** reword the Critical entry to "never autonomous; a
  typed human confirmation (DEPLOY/TERMINATE) is required, whether via
  console.runpod.io or the gated scripts".
- **Status (2026-07-07): FIXED** — Critical entry reworded exactly as proposed,
  as part of the pre-publication review (approved change).

## L4 — CUDA arch is hardcoded while the GPU type is configurable

- **Where:** `setup.sh` `CUDA_ARCH=86` vs `up.py --gpu-type`
- **Impact:** deploying e.g. an RTX 4090 (arch 89) still builds for 86; it
  works via PTX JIT but the first run recompiles kernels (slower, and timing
  noise in the first benchmark run).
- **Fix when picked up:** derive the arch in-pod from
  `nvidia-smi --query-gpu=compute_cap` and pass it to CMake.

## Housekeeping

- **`__pycache__/*.pyc` files are tracked.** They slipped into the 2026-07-04
  checkpoint commit. The 2026-07-04 cleanup untracked only `pod_ops/`'s caches
  and added `__pycache__/` / `*.pyc` to `.gitignore` — the caches under
  `scripts/` and `scripts/llm_bench/` stayed tracked (compiled files embed the
  author's local absolute paths, which is exactly what a public repo should not
  ship). **RESOLVED 2026-07-07:** every remaining `.pyc` untracked during the
  pre-publication review; `git ls-files` now shows zero.
- **Publication note (2026-07-07).** For the public release on
  github.com/Syntran-Labs, history was squashed to a single curated initial
  commit: earlier commits contained compiled `.pyc` blobs with local paths and
  a (long-terminated) pod identifier. Nothing of substance was lost — this
  file and SESSION-LOG-2026-07-02.md carry the project narrative deliberately,
  so the history rewrite costs no information. The pre-publication history is
  archived locally, unpublished.

## Not issues (checked and cleared, for the record)

- The EOF-closes-the-session mechanism `run_setup()` relies on matches the
  live-validated behavior of `run_setup.sh` (2026-07-03).
- `--reuse-pod` correctly re-reads `.env` after deriving `POD_SSH_CMD`.
- Security posture: llama-server binds `127.0.0.1` only, the relay binds
  loopback only, the pod exposes SSH only — unchanged by the 2026-07-04 fixes.
