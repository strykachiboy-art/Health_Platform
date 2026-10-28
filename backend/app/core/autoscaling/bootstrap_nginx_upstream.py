"""Create the initial Phase 9 Nginx backend upstream file before proxy startup.

This module owns only initial file creation. Runtime route reconciliation remains
the responsibility of :mod:`app.core.autoscaling.nginx_routing`.
"""
from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path


_PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


def render_initial_upstream(project_name: str, replica_count: int) -> str:
    """Render a deterministic initial backend list for a Compose project."""
    if not isinstance(project_name, str) or _PROJECT_RE.fullmatch(project_name) is None:
        raise ValueError("Invalid Docker Compose project name")
    if type(replica_count) is not int or replica_count < 1:
        raise ValueError("replica_count must be a positive integer")

    return "".join(
        f"    server {project_name}-backend-{index}:5000 resolve "
        "max_fails=1 fail_timeout=10s;\n"
        for index in range(1, replica_count + 1)
    )


def bootstrap_initial_upstream(
    project_root: str | Path,
    project_name: str,
    replica_count: int,
) -> Path:
    """Create backend.conf atomically and refuse to overwrite an existing file."""
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ValueError("project_root must be an existing directory")

    target_dir = root / "runtime" / "phase9" / "nginx-upstream"
    target = target_dir / "backend.conf"
    target_dir.mkdir(parents=True, exist_ok=True)

    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing upstream file: {target}")

    text = render_initial_upstream(project_name, replica_count)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=".backend.conf.",
            suffix=".tmp",
            dir=str(target_dir),
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

        # Refuse a race with another bootstrap creator.
        if target.exists():
            raise FileExistsError(f"upstream file appeared during bootstrap: {target}")

        os.replace(temporary, target)
        temporary = None
        return target
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create the initial Phase 9 Nginx backend.conf before proxy startup."
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--replicas", required=True, type=int)
    return parser


def main() -> int:
    args = _parser().parse_args()
    path = bootstrap_initial_upstream(
        args.project_root,
        args.project_name,
        args.replicas,
    )
    print(f"UPSTREAM_BOOTSTRAP_FILE={path}")
    print(f"UPSTREAM_BOOTSTRAP_REPLICAS={args.replicas}")
    print("UPSTREAM_BOOTSTRAP_MAX_FAILS=1")
    print("UPSTREAM_BOOTSTRAP=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())