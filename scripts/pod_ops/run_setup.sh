#!/bin/bash
# Runs setup.sh on the pod by feeding it to the proxy's interactive shell.
#
# Reality check (observed 2026-07-03): RunPod's ssh.runpod.io proxy IGNORES
# ssh exec commands and always opens an interactive login shell. It also
# rejects SCP/SFTP and -L forwarding. So there is no "upload" step at all:
# the script's lines go in via stdin and the interactive shell executes them
# one by one (job-control noise like "[1] Done" in the output is normal).
# EOF at the end of the script closes the shell and the connection.
#
# Run this yourself: bash scripts/pod_ops/run_setup.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "error: ${ENV_FILE} not found - deploy a pod first (deploy_pod.py --yes)." >&2
  exit 1
fi

SSH_CMD=$(grep '^POD_SSH_CMD=' "$ENV_FILE" | tail -n1 | cut -d'=' -f2-)
if [ -z "$SSH_CMD" ] || [[ "$SSH_CMD" == "<paste"* ]]; then
  echo "error: POD_SSH_CMD in .env is missing/still a placeholder - fill it in from " \
       "console.runpod.io -> your pod -> Connect tab (top 'SSH' command) first." >&2
  exit 1
fi

# -tt: the proxy refuses sessions without a PTY.
echo "Feeding setup.sh to the pod's interactive shell (expect ~10-15 min on a fresh pod)..."
$SSH_CMD -tt -o StrictHostKeyChecking=accept-new < "${REPO_ROOT}/setup.sh"
