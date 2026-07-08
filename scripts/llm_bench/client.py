"""HTTP boundary: send a chat completion request and return a raw result envelope.

Talks to any OpenAI-compatible /v1/chat/completions endpoint (llama-server,
Ollama, OpenRouter). Deliberately avoids the openai SDK, which parses the
response into a typed model and would drop llama-server's non-standard
`timings` block.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_BASE_URL = "http://localhost:8080/v1"
DEFAULT_API_KEY = "not-needed"
DEFAULT_MODEL = "gpt-oss-20b"
DEFAULT_TIMEOUT_S = 120.0
CHAT_COMPLETIONS_PATH = "/chat/completions"


@dataclass(frozen=True)
class ClientConfig:
    """Connection settings, loaded from env vars only. Never hardcode endpoints."""

    base_url: str
    api_key: str
    model: str
    pod_rate_usd_hr: float | None
    timeout_s: float = DEFAULT_TIMEOUT_S

    @property
    def chat_completions_url(self) -> str:
        return self.base_url.rstrip("/") + CHAT_COMPLETIONS_PATH

    @property
    def host_only(self) -> str:
        """scheme://host[:port] with no path, query, or credentials."""
        parts = urlsplit(self.base_url)
        netloc = parts.hostname or ""
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        return f"{parts.scheme}://{netloc}"

    def snapshot(self) -> dict[str, Any]:
        """Non-secret config fields safe to persist in result records."""
        return {
            "model": self.model,
            "timeout_s": self.timeout_s,
            "pod_rate_usd_hr": self.pod_rate_usd_hr,
        }


def load_config_from_env() -> ClientConfig:
    base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)
    api_key = os.environ.get("LLM_API_KEY", DEFAULT_API_KEY)
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    raw_rate = os.environ.get("POD_RATE_USD_HR")
    pod_rate_usd_hr = float(raw_rate) if raw_rate else None
    return ClientConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        pod_rate_usd_hr=pod_rate_usd_hr,
    )


def _auth_headers(config: ClientConfig) -> dict[str, str]:
    if config.api_key and config.api_key != DEFAULT_API_KEY:
        return {"Authorization": f"Bearer {config.api_key}"}
    return {}


def send(payload: dict[str, Any], config: ClientConfig) -> dict[str, Any]:
    """Send one chat completion request and return a result envelope.

    Always returns a dict, never raises: HTTP/timeout/JSON errors at this
    boundary are captured so one bad run doesn't crash the whole session.

    Returns:
        {"ok": True, "response": <parsed JSON>, "wall_clock_s": float}
        {"ok": False, "error": str, "wall_clock_s": float}
    """
    headers = {"Content-Type": "application/json", **_auth_headers(config)}
    start = time.perf_counter()
    try:
        with httpx.Client(timeout=config.timeout_s) as http_client:
            response = http_client.post(
                config.chat_completions_url, json=payload, headers=headers
            )
        wall_clock_s = time.perf_counter() - start
        response.raise_for_status()
        return {"ok": True, "response": response.json(), "wall_clock_s": wall_clock_s}
    except httpx.TimeoutException as exc:
        return {
            "ok": False,
            "error": f"timeout: {exc}",
            "wall_clock_s": time.perf_counter() - start,
        }
    except httpx.HTTPStatusError as exc:
        return {
            "ok": False,
            "error": f"http {exc.response.status_code}: {exc.response.text[:500]}",
            "wall_clock_s": time.perf_counter() - start,
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "error": f"transport error: {exc}",
            "wall_clock_s": time.perf_counter() - start,
        }
    except ValueError as exc:
        return {
            "ok": False,
            "error": f"malformed JSON response: {exc}",
            "wall_clock_s": time.perf_counter() - start,
        }
