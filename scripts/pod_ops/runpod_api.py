"""Thin client for the RunPod GraphQL API - pod create / status / terminate.

The mutation/field names below (`podFindAndDeployOnDemand`, `podTerminate`,
`pod(input: ...)`) were exercised against a live account on 2026-07-03/04:
create, status polling, connect-info and terminate all worked as written.
Providers' APIs still change — if a call starts failing, verify field names at
https://docs.runpod.io/api-reference. `GraphQLError` is raised verbatim with
RunPod's error payload so a schema drift fails loudly instead of silently
doing the wrong thing.

Auth: reads RUNPOD_API_KEY from the environment, optionally loaded from a
repo-root .env file (gitignored) if the shell hasn't already exported it -
never hardcode a key here.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

API_URL = "https://api.runpod.io/graphql"
DEFAULT_TIMEOUT_S = 30.0
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class GraphQLError(RuntimeError):
    """Raised verbatim with RunPod's error payload - never swallowed."""


class MissingApiKeyError(RuntimeError):
    pass


@dataclass(frozen=True)
class PodSpec:
    name: str
    gpu_type_id: str
    cloud_type: str  # "COMMUNITY" | "SECURE"
    image_name: str
    container_disk_gb: int
    volume_gb: int
    volume_mount_path: str
    ports: str  # e.g. "22/tcp"


@dataclass(frozen=True)
class PodInfo:
    pod_id: str
    desired_status: str
    ip: str | None
    ssh_port: int | None
    uptime_s: int | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class PodConnectInfo:
    """SSH-proxy identity + live rate, fetched separately from status.

    pod_host_id is the username for RunPod's ssh.runpod.io proxy
    (`ssh <podHostId>@ssh.runpod.io`) - observed to match the
    `<pod_id>-<8hex>` usernames the console's Connect tab shows.
    """

    pod_host_id: str | None
    cost_per_hr: float | None


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from .env into os.environ, without overriding a
    value the shell already exported. No new dependency (no python-dotenv) -
    this project keeps its dependency footprint minimal.
    """
    if not _ENV_FILE.is_file():
        return
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _api_key() -> str:
    _load_dotenv()
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        raise MissingApiKeyError(
            "RUNPOD_API_KEY is not set. Put it in the repo-root .env file "
            "(RUNPOD_API_KEY=...) or export it in your shell before running "
            "deploy_pod.py / terminate_pod.py - never hardcode it in a script."
        )
    return key


def _graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=DEFAULT_TIMEOUT_S) as http_client:
        response = http_client.post(
            API_URL, json={"query": query, "variables": variables}, headers=headers
        )
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise GraphQLError(str(body["errors"]))
    return body["data"]


_CREATE_MUTATION = """
mutation CreatePod($input: PodFindAndDeployOnDemandInput!) {
  podFindAndDeployOnDemand(input: $input) {
    id
    imageName
    desiredStatus
    machineId
  }
}
"""

_POD_STATUS_QUERY = """
query PodStatus($podId: String!) {
  pod(input: { podId: $podId }) {
    id
    desiredStatus
    runtime {
      uptimeInSeconds
      ports {
        ip
        privatePort
        publicPort
        type
      }
    }
  }
}
"""

_POD_CONNECT_QUERY = """
query PodConnect($podId: String!) {
  pod(input: { podId: $podId }) {
    id
    costPerHr
    machine {
      podHostId
    }
  }
}
"""

_TERMINATE_MUTATION = """
mutation TerminatePod($input: PodTerminateInput!) {
  podTerminate(input: $input)
}
"""


def create_pod(spec: PodSpec) -> str:
    """Create a pod on demand. Returns the new pod ID.

    Spends money the moment this call succeeds - callers must have already
    obtained explicit human confirmation before invoking this function.
    """
    variables = {
        "input": {
            "name": spec.name,
            "imageName": spec.image_name,
            "gpuTypeId": spec.gpu_type_id,
            "cloudType": spec.cloud_type,
            "containerDiskInGb": spec.container_disk_gb,
            "volumeInGb": spec.volume_gb,
            "volumeMountPath": spec.volume_mount_path,
            "ports": spec.ports,
            "gpuCount": 1,
        }
    }
    data = _graphql(_CREATE_MUTATION, variables)
    return data["podFindAndDeployOnDemand"]["id"]


def get_pod(pod_id: str) -> PodInfo:
    data = _graphql(_POD_STATUS_QUERY, {"podId": pod_id})
    pod = data["pod"]
    if pod is None:
        # Terminated/unknown pods come back as `"pod": null`, not as an error.
        raise GraphQLError(f"pod {pod_id} not found (already terminated?)")
    ip = None
    ssh_port = None
    runtime = pod.get("runtime") or {}
    for port in runtime.get("ports") or []:
        if port.get("privatePort") == 22:
            ip = port.get("ip")
            ssh_port = port.get("publicPort")
    uptime = runtime.get("uptimeInSeconds")
    return PodInfo(
        pod_id=pod["id"],
        desired_status=pod["desiredStatus"],
        ip=ip,
        ssh_port=ssh_port,
        uptime_s=int(uptime) if uptime is not None else None,
        raw=pod,
    )


def get_pod_connect(pod_id: str) -> PodConnectInfo:
    """Fetch the ssh.runpod.io proxy username (machine.podHostId) and live rate.

    Kept as a separate query so a schema drift on these fields degrades to the
    manual Connect-tab paste instead of breaking the status polling loop. The
    error is printed, not swallowed silently.
    """
    try:
        data = _graphql(_POD_CONNECT_QUERY, {"podId": pod_id})
    except GraphQLError as exc:
        print(f"warning: PodConnect query failed ({exc}) - falling back to the "
              f"manual Connect-tab paste for POD_SSH_CMD.", file=sys.stderr)
        return PodConnectInfo(pod_host_id=None, cost_per_hr=None)
    pod = data.get("pod") or {}
    machine = pod.get("machine") or {}
    cost = pod.get("costPerHr")
    return PodConnectInfo(
        pod_host_id=machine.get("podHostId"),
        cost_per_hr=float(cost) if cost is not None else None,
    )


def terminate_pod(pod_id: str) -> None:
    """Terminate a pod. Wipes its volume - irreversible.

    Callers must have already obtained explicit human confirmation.
    """
    _graphql(_TERMINATE_MUTATION, {"input": {"podId": pod_id}})
