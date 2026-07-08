#!/bin/bash
# Opens the SSH tunnel to the pod, built from POD_DIRECT_SSH_CMD in .env
# (just appends -N -L).
#
# Uses the "SSH over exposed TCP" command, NOT the ssh.runpod.io proxy used
# by run_setup.sh: the proxy rejects port-forward channel types ("channel 2:
# open failed: unknown channel type"), so -L tunnels need the direct-IP
# connection instead.
#
# Run this yourself, in its own terminal window: it blocks on purpose (that's
# the tunnel staying open) and must keep running while you benchmark.
#
# Usage: bash scripts/pod_ops/open_tunnel.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "error: ${ENV_FILE} not found - deploy a pod first (deploy_pod.py --yes)." >&2
  exit 1
fi

SSH_CMD=$(grep '^POD_DIRECT_SSH_CMD=' "$ENV_FILE" | tail -n1 | cut -d'=' -f2-)
if [ -z "$SSH_CMD" ] || [[ "$SSH_CMD" == "<paste"* ]]; then
  echo "error: POD_DIRECT_SSH_CMD in .env is missing/still a placeholder - fill it in from " \
       "console.runpod.io -> your pod -> Connect tab ('SSH over exposed TCP' command) first." >&2
  exit 1
fi

echo "Opening tunnel - this terminal will hang while it's open, that's expected."
echo "Ctrl+C here closes the tunnel."
exec $SSH_CMD -N -L 8080:127.0.0.1:8080
