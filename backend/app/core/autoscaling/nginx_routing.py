from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True, slots=True)
class RouteOperationResult:
    """Outcome of a proxy route transition.

    confirmed means the intended upstream file was validated and the Nginx
    reload command succeeded, with Nginx's expanded configuration matching
    the intended route set.

    prior_state_restored is true only when a failed transition was rolled
    back and the prior configuration was validated and reloaded.
    """

    confirmed: bool
    prior_state_restored: bool | None
    reason: str
    exit_code: int | None = None


class NginxBackendRoutingAdapter:
    """Withdraw or restore the highest-numbered Compose backend in Nginx.

    The controller runs on the Docker host and owns the bind-mounted upstream
    file. No backend network, container, image, or supporting service is
    mutated by this adapter.
    """

    def __init__(
        self,
        *,
        project_name: str,
        upstream_file: str | Path,
        proxy_container_name: str | None = None,
        timeout_seconds: float = 10.0,
        runner: Callable = subprocess.run,
    ) -> None:
        if (
            not isinstance(project_name, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project_name) is None
        ):
            raise ValueError("Invalid Docker Compose project name")

        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 1 <= float(timeout_seconds) <= 60
        ):
            raise ValueError("Nginx command timeout must be within 1..60 seconds")

        if not callable(runner):
            raise TypeError("runner must be callable")

        expected_proxy = f"{project_name}-proxy-1"
        proxy = proxy_container_name or expected_proxy
        if proxy != expected_proxy:
            raise ValueError("Proxy container must match the configured project")

        path = Path(upstream_file).resolve()
        if path.name != "backend.conf":
            raise ValueError("Upstream file must be named backend.conf")
        if path.is_symlink():
            raise ValueError("Upstream file must not be a symbolic link")
        if not path.is_file():
            raise ValueError("Upstream file must already exist")

        self.project_name = project_name
        self.proxy_container_name = proxy
        self.upstream_file = path
        self.timeout_seconds = float(timeout_seconds)
        self._runner = runner
        self._server_pattern = re.compile(
            r"^\s*server\s+("
            + re.escape(project_name)
            + r"-backend-([1-9][0-9]*))"
            + r":5000 resolve max_fails=2 fail_timeout=10s;\s*$"
        )

    def withdraw(self, container_name: str) -> RouteOperationResult:
        """Withdraw only the highest replica; make repeated calls safe."""
        index = self._backend_index(container_name)
        if index is None:
            return RouteOperationResult(
                False, True, "invalid_backend_name"
            )

        text, names = self._read_server_file()
        if text is None or names is None:
            return RouteOperationResult(
                False, None, "upstream_file_invalid"
            )

        inspected = self._inspect_config_servers()
        if inspected is None or inspected != names:
            return RouteOperationResult(
                False, None, "nginx_config_does_not_match_upstream_file"
            )

        if container_name not in names:
            # On a retry, force a config test and reload even when the file
            # already omits this replica. A file edit alone is not confirmation.
            if index == len(names) + 1:
                return self._publish(
                    names, reason="route_withdrawal_reconfirmed"
                )
            return RouteOperationResult(
                False, True, "route_target_not_present"
            )

        if not names or container_name != names[-1]:
            return RouteOperationResult(
                False, True, "only_highest_replica_can_be_withdrawn"
            )

        if len(names) <= 1:
            return RouteOperationResult(
                False, True, "cannot_withdraw_last_backend_route"
            )

        return self._publish(
            names[:-1], reason="route_withdrawal_confirmed"
        )

    def restore(self, container_name: str) -> RouteOperationResult:
        """Restore routing after the caller has made the backend ready again.

        Callers must undrain and verify the backend before invoking restore,
        so traffic is not deliberately routed to a still-draining replica.
        """
        index = self._backend_index(container_name)
        if index is None:
            return RouteOperationResult(
                False, True, "invalid_backend_name"
            )

        text, names = self._read_server_file()
        if text is None or names is None:
            return RouteOperationResult(
                False, None, "upstream_file_invalid"
            )

        inspected = self._inspect_config_servers()
        if inspected is None or inspected != names:
            return RouteOperationResult(
                False, None, "nginx_config_does_not_match_upstream_file"
            )

        if container_name in names:
            return self._publish(
                names, reason="route_restoration_reconfirmed"
            )

        if index != len(names) + 1:
            return RouteOperationResult(
                False, True, "route_restore_topology_invalid"
            )

        return self._publish(
            names + [container_name],
            reason="route_restoration_confirmed",
        )

    def _backend_index(self, name: str) -> int | None:
        if not isinstance(name, str):
            return None
        match = re.fullmatch(
            re.escape(self.project_name) + r"-backend-([1-9][0-9]*)",
            name,
        )
        return int(match.group(1)) if match else None

    def _parse_server_file(self, text: str) -> list[str] | None:
        names: list[str] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            match = self._server_pattern.fullmatch(line)
            if match is None:
                return None
            names.append(match.group(1))

        if not names or len(names) != len(set(names)):
            return None

        expected = [
            f"{self.project_name}-backend-{index}"
            for index in range(1, len(names) + 1)
        ]
        return names if names == expected else None

    def _parse_expanded_config(self, text: str) -> list[str] | None:
        if (
            "upstream clinic_api_backend {" not in text
            or "/etc/nginx/upstreams/backend.conf" not in text
        ):
            return None

        names: list[str] = []
        for line in text.splitlines():
            match = self._server_pattern.fullmatch(line)
            if match is not None:
                names.append(match.group(1))

        if not names or len(names) != len(set(names)):
            return None

        expected = [
            f"{self.project_name}-backend-{index}"
            for index in range(1, len(names) + 1)
        ]
        return names if names == expected else None

    def _read_server_file(self) -> tuple[str | None, list[str] | None]:
        try:
            text = self.upstream_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None, None
        return text, self._parse_server_file(text)

    def _inspect_config_servers(self) -> list[str] | None:
        result, error = self._invoke(["-T"])
        if (
            error is not None
            or result is None
            or getattr(result, "returncode", None) != 0
        ):
            return None

        output = (result.stdout or "") + "\n" + (result.stderr or "")
        return self._parse_expanded_config(output)

    def _render(self, names: list[str]) -> str:
        return "".join(
            f"    server {name}:5000 resolve "
            "max_fails=2 fail_timeout=10s;\n"
            for name in names
        )

    def _write_atomic(self, text: str) -> bool:
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=".backend.conf.",
                suffix=".tmp",
                dir=str(self.upstream_file.parent),
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(temporary, self.upstream_file)
            return True
        except OSError:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            return False

    def _invoke(
        self, arguments: list[str]
    ) -> tuple[subprocess.CompletedProcess | None, str | None]:
        command = [
            "docker",
            "exec",
            self.proxy_container_name,
            "nginx",
            *arguments,
        ]
        try:
            result = self._runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError, Exception):
            return None, "nginx_command_execution_failed"

        if type(getattr(result, "returncode", None)) is not int:
            return None, "invalid_nginx_command_result"
        return result, None

    @staticmethod
    def _ok(result: subprocess.CompletedProcess | None, error: str | None):
        return (
            error is None
            and result is not None
            and result.returncode == 0
        )

    def _publish(
        self, desired: list[str], *, reason: str
    ) -> RouteOperationResult:
        original_text, original_names = self._read_server_file()
        if original_text is None or original_names is None:
            return RouteOperationResult(
                False, None, "upstream_file_invalid"
            )

        if self._inspect_config_servers() != original_names:
            return RouteOperationResult(
                False, None, "nginx_config_does_not_match_upstream_file"
            )

        expected = [
            f"{self.project_name}-backend-{index}"
            for index in range(1, len(desired) + 1)
        ]
        if desired != expected or not desired:
            return RouteOperationResult(
                False, True, "requested_route_set_invalid"
            )

        if not self._write_atomic(self._render(desired)):
            return RouteOperationResult(
                False, None, "upstream_file_write_failed"
            )

        test_result, test_error = self._invoke(["-t"])
        if not self._ok(test_result, test_error):
            restored = self._write_atomic(original_text)
            verify_result, verify_error = self._invoke(["-t"])
            prior_state_restored = (
                restored
                and self._ok(verify_result, verify_error)
                and self._read_server_file()
                == (original_text, original_names)
            )
            return RouteOperationResult(
                False,
                bool(prior_state_restored),
                "nginx_config_test_failed",
                getattr(test_result, "returncode", None),
            )

        reload_result, reload_error = self._invoke(["-s", "reload"])
        if (
            self._ok(reload_result, reload_error)
            and self._read_server_file()[1] == desired
            and self._inspect_config_servers() == desired
        ):
            return RouteOperationResult(
                True, None, reason, reload_result.returncode
            )

        # A failed or ambiguous reload must not be treated as withdrawal
        # confirmation. Restore the prior file and explicitly reload it.
        restored = self._write_atomic(original_text)
        if not restored:
            return RouteOperationResult(
                False, None, "route_transition_failed_rollback_write_failed",
                getattr(reload_result, "returncode", None),
            )

        rollback_test, rollback_test_error = self._invoke(["-t"])
        if not self._ok(rollback_test, rollback_test_error):
            return RouteOperationResult(
                False, None, "route_transition_failed_rollback_test_failed",
                getattr(rollback_test, "returncode", None),
            )

        rollback_reload, rollback_reload_error = self._invoke(
            ["-s", "reload"]
        )
        prior_state_restored = (
            self._ok(rollback_reload, rollback_reload_error)
            and self._read_server_file()
            == (original_text, original_names)
            and self._inspect_config_servers() == original_names
        )
        return RouteOperationResult(
            False,
            bool(prior_state_restored),
            "route_transition_failed_and_rolled_back"
            if prior_state_restored
            else "route_transition_failed_rollback_uncertain",
            getattr(reload_result, "returncode", None),
        )