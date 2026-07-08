"""up.py / down.py: gate behavior and pure helpers - no network, no pod."""

from __future__ import annotations

from scripts.pod_ops import deploy_pod, down, up


def test_proxy_ssh_cmd_matches_connect_tab_shape() -> None:
    # Synthetic podHostId with the real shape (<pod_id>-<8hex>) — not a live pod.
    cmd = deploy_pod.proxy_ssh_cmd("examplepod1234-0f0f0f0f")
    assert cmd == "ssh examplepod1234-0f0f0f0f@ssh.runpod.io -i ~/.ssh/id_ed25519"


def test_up_dry_run_does_nothing(monkeypatch, capsys) -> None:
    monkeypatch.setattr(up.envfile, "read_values", lambda: {})
    assert up.main([]) == 0
    out = capsys.readouterr().out
    assert "Dry run only" in out
    assert "typed DEPLOY" in out


def test_up_reuse_pod_requires_pod_id(monkeypatch) -> None:
    monkeypatch.setattr(up.envfile, "read_values", lambda: {})
    assert up.main(["--reuse-pod", "--yes"]) == 2


def test_deploy_records_pod_id_before_polling(monkeypatch) -> None:
    """A pod that never reaches RUNNING bills anyway: POD_ID must land in
    .env at creation time, not only after the RUNNING poll succeeds."""
    upserts: list[dict[str, str]] = []
    monkeypatch.setattr(deploy_pod.envfile, "upsert",
                        lambda updates: upserts.append(dict(updates)))
    monkeypatch.setattr(deploy_pod.runpod_api, "create_pod", lambda spec: "pod-123")
    stuck = deploy_pod.runpod_api.PodInfo(
        pod_id="pod-123", desired_status="PENDING",
        ip=None, ssh_port=None, uptime_s=None, raw={})
    monkeypatch.setattr(deploy_pod, "_wait_until_running", lambda pod_id: stuck)
    monkeypatch.setattr(deploy_pod, "record_pod_env",
                        lambda pod, spec: (_ for _ in ()).throw(
                            AssertionError("must not run for a non-RUNNING pod")))

    spec = deploy_pod.build_spec("NVIDIA GeForce RTX 3090")
    assert deploy_pod.deploy_confirmed(spec) is None
    assert upserts and upserts[0]["POD_ID"] == "pod-123"


def test_down_without_pod_id_errors(monkeypatch) -> None:
    monkeypatch.setattr(down.envfile, "read_values", lambda: {})
    assert down.main([]) == 2


def test_down_dry_run_does_not_terminate(monkeypatch, capsys) -> None:
    monkeypatch.setattr(down.envfile, "read_values",
                        lambda: {"POD_ID": "fake", "POD_NAME": "n", "POD_GPU_TYPE": "g"})
    calls: list[str] = []
    monkeypatch.setattr(down.runpod_api, "get_pod",
                        lambda pod_id: (_ for _ in ()).throw(down.runpod_api.GraphQLError("x")))
    monkeypatch.setattr(down.runpod_api, "terminate_pod",
                        lambda pod_id: calls.append(pod_id))
    assert down.main([]) == 0
    assert calls == []
    assert "Dry run only" in capsys.readouterr().out


def test_fmt_uptime() -> None:
    assert down._fmt_uptime(0) == "0h00m"
    assert down._fmt_uptime(3 * 3600 + 25 * 60 + 59) == "3h25m"
