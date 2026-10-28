from __future__ import annotations

import subprocess
import sys

import pytest

from app.core.autoscaling.bootstrap_nginx_upstream import (
    bootstrap_initial_upstream,
    render_initial_upstream,
)


def test_render_initial_upstream_uses_failover_threshold_one():
    text = render_initial_upstream("candidate", 2)

    assert text == (
        "    server candidate-backend-1:5000 resolve "
        "max_fails=1 fail_timeout=10s;\n"
        "    server candidate-backend-2:5000 resolve "
        "max_fails=1 fail_timeout=10s;\n"
    )
    assert "max_fails=2" not in text


def test_bootstrap_creates_missing_directories_and_file(tmp_path):
    path = bootstrap_initial_upstream(tmp_path, "candidate", 2)

    assert path == tmp_path / "runtime" / "phase9" / "nginx-upstream" / "backend.conf"
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == render_initial_upstream("candidate", 2)


def test_bootstrap_refuses_overwrite(tmp_path):
    first = bootstrap_initial_upstream(tmp_path, "candidate", 2)

    with pytest.raises(FileExistsError):
        bootstrap_initial_upstream(tmp_path, "candidate", 3)

    assert first.read_text(encoding="utf-8") == render_initial_upstream("candidate", 2)


@pytest.mark.parametrize(
    ("project_name", "replicas"),
    [
        ("Candidate", 2),
        ("../candidate", 2),
        ("candidate", 0),
        ("candidate", -1),
        ("candidate", True),
    ],
)
def test_bootstrap_rejects_invalid_identity_or_replica_count(
    tmp_path, project_name, replicas
):
    with pytest.raises(ValueError):
        bootstrap_initial_upstream(tmp_path, project_name, replicas)


def test_module_cli_bootstraps_empty_project_root(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.core.autoscaling.bootstrap_nginx_upstream",
            "--project-root",
            str(tmp_path),
            "--project-name",
            "freshproof",
            "--replicas",
            "2",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "UPSTREAM_BOOTSTRAP=PASS" in result.stdout

    target = tmp_path / "runtime" / "phase9" / "nginx-upstream" / "backend.conf"
    assert target.read_text(encoding="utf-8") == render_initial_upstream(
        "freshproof", 2
    )