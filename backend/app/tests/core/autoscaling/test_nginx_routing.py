from __future__ import annotations

import subprocess
from pathlib import Path

from app.core.autoscaling.nginx_routing import (
    NginxBackendRoutingAdapter,
    RouteOperationResult,
)


def render(count: int) -> str:
    return "".join(
        f"    server clinic-system-backend-{i}:5000 resolve "
        "max_fails=2 fail_timeout=10s;\n"
        for i in range(1, count + 1)
    )


class FakeNginx:
    def __init__(self, path: Path):
        self.path = path
        self.calls: list[list[str]] = []
        self.fail_tests = 0
        self.fail_reloads = 0

    def __call__(self, command, **kwargs):
        command = list(command)
        self.calls.append(command)

        if command[-1] == "-t":
            if self.fail_tests:
                self.fail_tests -= 1
                return subprocess.CompletedProcess(
                    command, 1, "", "injected nginx test failure"
                )
            return subprocess.CompletedProcess(
                command, 0, "syntax is ok\n", ""
            )

        if command[-2:] == ["-s", "reload"]:
            if self.fail_reloads:
                self.fail_reloads -= 1
                return subprocess.CompletedProcess(
                    command, 1, "", "injected reload failure"
                )
            return subprocess.CompletedProcess(
                command, 0, "signal process started\n", ""
            )

        if command[-1] == "-T":
            content = self.path.read_text(encoding="utf-8")
            output = (
                "# configuration file /etc/nginx/nginx.conf:\n"
                "upstream clinic_api_backend {\n"
                "    zone clinic_api_backend 64k;\n"
                "    include /etc/nginx/upstreams/backend.conf;\n"
                "}\n"
                "# configuration file /etc/nginx/upstreams/backend.conf:\n"
                + content
            )
            return subprocess.CompletedProcess(command, 0, output, "")

        return subprocess.CompletedProcess(
            command, 2, "", "unexpected fake command"
        )


def make_adapter(tmp_path, count=4):
    path = tmp_path / "backend.conf"
    path.write_text(render(count), encoding="utf-8")
    fake = FakeNginx(path)
    adapter = NginxBackendRoutingAdapter(
        project_name="clinic-system",
        upstream_file=path,
        runner=fake,
    )
    return adapter, path, fake


def test_withdraw_highest_replica_and_confirm_reload(tmp_path):
    adapter, path, fake = make_adapter(tmp_path)

    result = adapter.withdraw("clinic-system-backend-4")

    assert result == RouteOperationResult(
        True, None, "route_withdrawal_confirmed", 0
    )
    assert path.read_text(encoding="utf-8") == render(3)
    assert any(command[-2:] == ["-s", "reload"] for command in fake.calls)


def test_restore_highest_replica_after_withdrawal(tmp_path):
    adapter, path, fake = make_adapter(tmp_path, count=3)

    result = adapter.restore("clinic-system-backend-4")

    assert result.confirmed is True
    assert result.reason == "route_restoration_confirmed"
    assert path.read_text(encoding="utf-8") == render(4)
    assert any(command[-2:] == ["-s", "reload"] for command in fake.calls)


def test_withdraw_refuses_non_highest_replica(tmp_path):
    adapter, path, _fake = make_adapter(tmp_path)
    before = path.read_text(encoding="utf-8")

    result = adapter.withdraw("clinic-system-backend-2")

    assert result.confirmed is False
    assert result.prior_state_restored is True
    assert result.reason == "only_highest_replica_can_be_withdrawn"
    assert path.read_text(encoding="utf-8") == before


def test_invalid_container_name_is_refused_without_docker_call(tmp_path):
    adapter, _path, fake = make_adapter(tmp_path)

    result = adapter.withdraw("clinic-system-phase9-backend-4")

    assert result.confirmed is False
    assert result.reason == "invalid_backend_name"
    assert fake.calls == []


def test_nginx_test_failure_restores_original_file_without_reload(tmp_path):
    adapter, path, fake = make_adapter(tmp_path)
    before = path.read_text(encoding="utf-8")
    fake.fail_tests = 1

    result = adapter.withdraw("clinic-system-backend-4")

    assert result.confirmed is False
    assert result.prior_state_restored is True
    assert result.reason == "nginx_config_test_failed"
    assert path.read_text(encoding="utf-8") == before
    assert not any(
        command[-2:] == ["-s", "reload"] for command in fake.calls
    )


def test_reload_failure_restores_original_routes_and_reloads(tmp_path):
    adapter, path, fake = make_adapter(tmp_path)
    before = path.read_text(encoding="utf-8")
    fake.fail_reloads = 1

    result = adapter.withdraw("clinic-system-backend-4")

    assert result.confirmed is False
    assert result.prior_state_restored is True
    assert result.reason == "route_transition_failed_and_rolled_back"
    assert path.read_text(encoding="utf-8") == before
    assert sum(
        command[-2:] == ["-s", "reload"] for command in fake.calls
    ) == 2


def test_repeated_withdrawal_revalidates_and_reloads(tmp_path):
    adapter, path, fake = make_adapter(tmp_path, count=3)

    result = adapter.withdraw("clinic-system-backend-4")

    assert result.confirmed is True
    assert result.reason == "route_withdrawal_reconfirmed"
    assert path.read_text(encoding="utf-8") == render(3)
    assert any(command[-2:] == ["-s", "reload"] for command in fake.calls)


def test_malformed_upstream_file_fails_closed(tmp_path):
    adapter, path, fake = make_adapter(tmp_path)
    path.write_text("include attacker.conf;\n", encoding="utf-8")

    result = adapter.withdraw("clinic-system-backend-4")

    assert result.confirmed is False
    assert result.reason == "upstream_file_invalid"
    assert fake.calls == []