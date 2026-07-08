"""Unit tests for ClientConfig helpers and env loading. No network calls."""

from __future__ import annotations

from scripts.llm_bench import client


def test_load_config_from_env_defaults(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("POD_RATE_USD_HR", raising=False)

    config = client.load_config_from_env()

    assert config.base_url == client.DEFAULT_BASE_URL
    assert config.api_key == client.DEFAULT_API_KEY
    assert config.model == client.DEFAULT_MODEL
    assert config.pod_rate_usd_hr is None


def test_load_config_from_env_overrides(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-real-key")
    monkeypatch.setenv("LLM_MODEL", "some-model")
    monkeypatch.setenv("POD_RATE_USD_HR", "0.30")

    config = client.load_config_from_env()

    assert config.base_url == "https://openrouter.ai/api/v1"
    assert config.pod_rate_usd_hr == 0.30


def test_host_only_strips_path_and_credentials():
    config = client.ClientConfig(
        base_url="http://user:pass@localhost:8080/v1",
        api_key="not-needed",
        model="gpt-oss-20b",
        pod_rate_usd_hr=None,
    )

    assert config.host_only == "http://localhost:8080"


def test_auth_headers_omitted_for_default_key():
    config = client.ClientConfig(
        base_url=client.DEFAULT_BASE_URL,
        api_key=client.DEFAULT_API_KEY,
        model=client.DEFAULT_MODEL,
        pod_rate_usd_hr=None,
    )

    assert client._auth_headers(config) == {}


def test_auth_headers_present_for_real_key():
    config = client.ClientConfig(
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-real-key",
        model="some-model",
        pod_rate_usd_hr=None,
    )

    assert client._auth_headers(config) == {"Authorization": "Bearer sk-real-key"}


def test_snapshot_excludes_api_key():
    config = client.ClientConfig(
        base_url=client.DEFAULT_BASE_URL,
        api_key="sk-real-key",
        model=client.DEFAULT_MODEL,
        pod_rate_usd_hr=0.25,
    )

    assert "api_key" not in config.snapshot()
    assert "sk-real-key" not in str(config.snapshot())
