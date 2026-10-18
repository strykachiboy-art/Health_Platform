from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_HOST = "http://127.0.0.1:5000"
DEFAULT_USERS = 10
DEFAULT_SPAWN_RATE = 2
DEFAULT_RUNTIME = "30s"
DEFAULT_RESULTS_DIR = Path("testing") / "results"

PERFORMANCE_PATTERN = re.compile(
    r"performance\.request\s+"
    r"(?:request_id=\S+\s+)?"
    r"load_test_id=(?P<load_test_id>\S+)\s+"
    r"method=(?P<method>\S+)\s+"
    r"route=(?P<route>\S+)\s+"
    r"status=(?P<status>\d+)\s+"
    r"duration_ms=(?P<duration>[0-9]+(?:\.[0-9]+)?)\s+"
    r"response_size_bytes=(?P<size>\d+)\s+"
    r"db_query_count=(?P<queries>\d+)\s+"
    r"db_time_ms=(?P<db>[0-9]+(?:\.[0-9]+)?)"
)


@dataclass(frozen=True)
class PerformanceRecord:
    method: str
    route: str
    status: int
    duration_ms: float
    response_size_bytes: int
    db_query_count: int
    db_time_ms: float


def _percentile(
    values: list[float],
    percentile: float,
) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)

    if len(ordered) == 1:
        return float(ordered[0])

    position = (len(ordered) - 1) * percentile

    lower = int(position)
    upper = lower + 1

    if upper >= len(ordered):
        return float(ordered[-1])

    weight = position - lower

    return float(
        ordered[lower]
        + (
            ordered[upper]
            - ordered[lower]
        )
        * weight
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp_for_run_id() -> str:
    return _utc_now().strftime("%Y%m%dT%H%M%SZ")


def _normalise_duration(
    value: str,
) -> str:
    value = value.strip()

    if not value:
        raise ValueError(
            "Runtime cannot be empty"
        )

    return f"{value}s" if value.isdigit() else value


def _scenario_path(
    value: str,
) -> Path:
    path = Path(value)

    if path.suffix.lower() == ".py":
        resolved = path
    else:
        resolved = (
            Path("testing")
            / "scenarios"
            / f"{value}.py"
        )

    resolved = resolved.resolve()

    if not resolved.is_file():
        raise FileNotFoundError(
            "Locust scenario file not found: "
            f"{resolved}"
        )

    return resolved


def _read_log_text(
    log_path: Path,
) -> str:
    data = log_path.read_bytes()

    if data.startswith(b"\xff\xfe"):
        return data.decode("utf-16")

    if data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")

    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")

    return data.decode("utf-8")


def _load_records(
    log_path: Path,
    run_id: str,
) -> list[PerformanceRecord]:
    if not log_path.is_file():
        raise FileNotFoundError(
            f"Server log file not found: "
            f"{log_path}"
        )

    raw_text = _read_log_text(log_path)

    normalized_text = re.sub(
        r"\s+",
        " ",
        raw_text,
    )

    records: list[PerformanceRecord] = []

    for match in PERFORMANCE_PATTERN.finditer(
        normalized_text
    ):
        if match.group("load_test_id") != run_id:
            continue

        records.append(
            PerformanceRecord(
                method=match.group("method"),
                route=match.group("route"),
                status=int(
                    match.group("status")
                ),
                duration_ms=float(
                    match.group("duration")
                ),
                response_size_bytes=int(
                    match.group("size")
                ),
                db_query_count=int(
                    match.group("queries")
                ),
                db_time_ms=float(
                    match.group("db")
                ),
            )
        )

    return records


def _count_run_id_records(
    log_path: Path,
    run_id: str,
) -> int:
    if not log_path.is_file():
        return 0

    return len(
        _load_records(
            log_path,
            run_id,
        )
    )


def _filter_records(
    records: Iterable[PerformanceRecord],
    *,
    scenario_name: str,
    include_login: bool,
    include_routes: tuple[str, ...],
    exclude_routes: tuple[str, ...],
) -> tuple[list[PerformanceRecord], int]:
    default_excluded: tuple[str, ...] = ()

    if (
        scenario_name.lower() != "authentication"
        and not include_login
    ):
        default_excluded = (
            "/api/v1/auth/login",
        )

    excluded = tuple(
        dict.fromkeys(
            (
                *default_excluded,
                *exclude_routes,
            )
        )
    )

    filtered: list[PerformanceRecord] = []

    for record in records:
        if (
            include_routes
            and record.route not in include_routes
        ):
            continue

        if record.route in excluded:
            continue

        filtered.append(record)

    return (
        filtered,
        len(records) - len(filtered),
    )


def _aggregate(
    records: list[PerformanceRecord],
) -> dict[str, object]:
    durations = [
        record.duration_ms
        for record in records
    ]

    response_sizes = [
        record.response_size_bytes
        for record in records
    ]

    query_counts = [
        record.db_query_count
        for record in records
    ]

    db_times = [
        record.db_time_ms
        for record in records
    ]

    total = len(records)

    failures = sum(
        record.status >= 400
        for record in records
    )

    statuses: dict[str, int] = {}

    for record in records:
        key = str(record.status)

        statuses[key] = (
            statuses.get(key, 0) + 1
        )

    return {
        "requests": total,
        "failures": failures,
        "failure_rate": (
            failures / total
            if total
            else 0.0
        ),
        "status_distribution": dict(
            sorted(
                statuses.items(),
                key=lambda item: int(
                    item[0]
                ),
            )
        ),
        "min_duration_ms": (
            min(durations)
            if durations
            else 0.0
        ),
        "average_duration_ms": (
            sum(durations) / total
            if total
            else 0.0
        ),
        "p50_duration_ms": _percentile(
            durations,
            0.50,
        ),
        "p95_duration_ms": _percentile(
            durations,
            0.95,
        ),
        "p99_duration_ms": _percentile(
            durations,
            0.99,
        ),
        "max_duration_ms": (
            max(durations)
            if durations
            else 0.0
        ),
        "average_response_size_bytes": (
            sum(response_sizes) / total
            if total
            else 0.0
        ),
        "average_db_time_ms": (
            sum(db_times) / total
            if total
            else 0.0
        ),
        "max_db_time_ms": (
            max(db_times)
            if db_times
            else 0.0
        ),
        "average_db_queries": (
            sum(query_counts) / total
            if total
            else 0.0
        ),
        "total_db_queries": sum(
            query_counts
        ),
        "total_db_time_ms": sum(
            db_times
        ),
    }


def _breakdown(
    records: list[PerformanceRecord],
) -> list[dict[str, object]]:
    groups: dict[
        tuple[str, str],
        list[PerformanceRecord],
    ] = {}

    for record in records:
        groups.setdefault(
            (
                record.method,
                record.route,
            ),
            [],
        ).append(record)

    output: list[dict[str, object]] = []

    for (
        method,
        route,
    ), grouped in groups.items():
        metrics = _aggregate(grouped)

        output.append(
            {
                "method": method,
                "route": route,
                **metrics,
            }
        )

    return sorted(
        output,
        key=lambda item: (
            float(
                item["p95_duration_ms"]
            ),
            float(
                item["average_duration_ms"]
            ),
            str(item["route"]),
        ),
        reverse=True,
    )


def _csv_value(
    row: dict[str, str],
    *names: str,
) -> str:
    for name in names:
        value = row.get(name)

        if value not in (None, ""):
            return value

    return ""


def _parse_float(
    value: str,
) -> float | None:
    try:
        return float(value)
    except (
        TypeError,
        ValueError,
    ):
        return None


def _parse_int(
    value: str,
) -> int | None:
    try:
        return int(float(value))
    except (
        TypeError,
        ValueError,
    ):
        return None


def _parse_locust_stats(
    stats_path: Path,
) -> dict[str, object]:
    if not stats_path.is_file():
        return {
            "available": False,
            "error": (
                "Locust stats CSV not found: "
                f"{stats_path}"
            ),
        }

    with stats_path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        rows = list(
            csv.DictReader(handle)
        )

    aggregate = next(
        (
            row
            for row in rows
            if (
                row.get("Name") or ""
            ).strip().lower()
            == "aggregated"
        ),
        rows[-1] if rows else None,
    )

    if aggregate is None:
        return {
            "available": False,
            "error": (
                "Locust stats CSV "
                "contained no rows"
            ),
        }

    return {
        "available": True,
        "requests": _parse_int(
            _csv_value(
                aggregate,
                "Request Count",
            )
        ),
        "failures": _parse_int(
            _csv_value(
                aggregate,
                "Failure Count",
            )
        ),
        "median_response_time_ms": (
            _parse_float(
                _csv_value(
                    aggregate,
                    "Median Response Time",
                    "50%",
                )
            )
        ),
        "average_response_time_ms": (
            _parse_float(
                _csv_value(
                    aggregate,
                    "Average Response Time",
                )
            )
        ),
        "min_response_time_ms": (
            _parse_float(
                _csv_value(
                    aggregate,
                    "Min Response Time",
                )
            )
        ),
        "max_response_time_ms": (
            _parse_float(
                _csv_value(
                    aggregate,
                    "Max Response Time",
                )
            )
        ),
        "average_content_size_bytes": (
            _parse_float(
                _csv_value(
                    aggregate,
                    "Average Content Size",
                )
            )
        ),
        "requests_per_second": (
            _parse_float(
                _csv_value(
                    aggregate,
                    "Requests/s",
                )
            )
        ),
        "failures_per_second": (
            _parse_float(
                _csv_value(
                    aggregate,
                    "Failures/s",
                )
            )
        ),
        "source": str(stats_path),
    }


def _build_command(
    scenario_file: Path,
    *,
    users: int,
    spawn_rate: float,
    runtime: str,
    host: str,
    csv_prefix: Path,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "locust",
        "-f",
        str(scenario_file),
        "--headless",
        "-u",
        str(users),
        "-r",
        str(spawn_rate),
        "-t",
        runtime,
        "--host",
        host,
        "--csv",
        str(csv_prefix),
    ]


def _write_json(
    output_path: Path,
    payload: dict[str, object],
) -> None:
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _load_json(
    input_path: Path,
) -> dict[str, object]:
    if not input_path.is_file():
        raise FileNotFoundError(
            f"Result JSON not found: "
            f"{input_path}"
        )

    with input_path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise ValueError(
            "Result JSON root must "
            "be an object"
        )

    return payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a Clinic System Pro "
            "load-test scenario and "
            "build a server-side "
            "performance baseline."
        )
    )

    parser.add_argument(
        "--scenario",
        required=True,
        help=(
            "Scenario name under "
            "testing/scenarios "
            "or a .py path."
        ),
    )

    parser.add_argument(
        "--run-id",
        default=None,
        help=(
            "Unique LOCUST_RUN_ID. "
            "Defaults to "
            "<scenario>-baseline-"
            "<UTC timestamp>."
        ),
    )

    parser.add_argument(
        "--host",
        default=os.getenv(
            "LOCUST_HOST",
            DEFAULT_HOST,
        ),
    )

    parser.add_argument(
        "--users",
        type=int,
        default=DEFAULT_USERS,
    )

    parser.add_argument(
        "--spawn-rate",
        type=float,
        default=DEFAULT_SPAWN_RATE,
    )

    parser.add_argument(
        "--time",
        default=DEFAULT_RUNTIME,
        help=(
            "Locust runtime, "
            "for example 30s."
        ),
    )

    parser.add_argument(
        "--log-file",
        default=None,
        help=(
            "Server log. Defaults to "
            "logs/<scenario>_server.log."
        ),
    )

    parser.add_argument(
        "--results-dir",
        default=str(
            DEFAULT_RESULTS_DIR
        ),
    )

    parser.add_argument(
        "--include-login",
        action="store_true",
        help=(
            "Include POST "
            "/api/v1/auth/login "
            "in server metrics."
        ),
    )

    parser.add_argument(
        "--route",
        action="append",
        dest="routes",
        default=[],
        help=(
            "Exact route to include. "
            "Repeat for multiple routes."
        ),
    )

    parser.add_argument(
        "--exclude-route",
        action="append",
        dest="exclude_routes",
        default=[],
        help=(
            "Exact route to exclude. "
            "Repeat as needed."
        ),
    )

    parser.add_argument(
        "--allow-existing-run-id",
        action="store_true",
        help=(
            "Allow a run ID already "
            "present in the server log."
        ),
    )

    parser.add_argument(
        "--repair-existing",
        action="store_true",
        help=(
            "Repair an existing result "
            "JSON from the existing "
            "server log and Locust CSV "
            "without running Locust."
        ),
    )

    return parser.parse_args()


def _repair_existing_result(
    args: argparse.Namespace,
) -> int:
    if not args.run_id:
        raise SystemExit(
            "--run-id is required with "
            "--repair-existing"
        )

    scenario_file = _scenario_path(
        args.scenario
    )

    scenario_name = scenario_file.stem

    log_file = Path(
        args.log_file
        or (
            Path("logs")
            / f"{scenario_name}_server.log"
        )
    ).resolve()

    results_dir = Path(
        args.results_dir
    ).resolve()

    output_path = (
        results_dir
        / f"{args.run_id}.json"
    )

    existing_payload = _load_json(
        output_path
    )

    all_records = _load_records(
        log_file,
        args.run_id,
    )

    records, excluded_count = (
        _filter_records(
            all_records,
            scenario_name=scenario_name,
            include_login=args.include_login,
            include_routes=tuple(
                args.routes
            ),
            exclude_routes=tuple(
                args.exclude_routes
            ),
        )
    )

    csv_prefix = (
        results_dir
        / f"{args.run_id}_locust"
    )

    stats_path = Path(
        f"{csv_prefix}_stats.csv"
    )

    locust_metrics = (
        _parse_locust_stats(
            stats_path
        )
    )

    server_metrics = _aggregate(
        records
    )

    existing_locust = (
        existing_payload.get(
            "locust",
            {}
        )
    )

    if not isinstance(
        existing_locust,
        dict,
    ):
        existing_locust = {}

    locust_exit_code = (
        existing_locust.get(
            "exit_code",
            0,
        )
    )

    if not isinstance(
        locust_exit_code,
        int,
    ):
        locust_exit_code = (
            _parse_int(
                str(locust_exit_code)
            )
            or 0
        )

    validity_checks = {
        "locust_exit_code_zero": (
            locust_exit_code == 0
        ),
        "server_log_exists": (
            log_file.is_file()
        ),
        "matching_server_records_present": (
            bool(records)
        ),
        "run_id_unique_before_run": True,
        "server_failures_zero": (
            int(
                server_metrics["failures"]
            )
            == 0
        ),
    }

    valid = all(
        validity_checks.values()
    )

    configuration = (
        existing_payload.get(
            "configuration",
            {},
        )
    )

    if not isinstance(
        configuration,
        dict,
    ):
        configuration = {}

    configuration.update(
        {
            "server_log": str(
                log_file
            ),
            "include_login": bool(
                args.include_login
            ),
            "included_routes": (
                args.routes
            ),
            "excluded_routes": (
                args.exclude_routes
            ),
        }
    )

    locust_section = {
        **existing_locust,
        **locust_metrics,
        "exit_code": locust_exit_code,
        "csv_stats_file": str(
            stats_path
        ),
    }

    existing_payload[
        "scenario"
    ] = scenario_name

    existing_payload[
        "scenario_file"
    ] = str(scenario_file)

    existing_payload[
        "run_id"
    ] = args.run_id

    existing_payload[
        "configuration"
    ] = configuration

    existing_payload[
        "locust"
    ] = locust_section

    existing_payload[
        "server"
    ] = {
        **server_metrics,
        "matched_log_records_before_filter": (
            len(all_records)
        ),
        "excluded_record_count": (
            excluded_count
        ),
        "route_breakdown": _breakdown(
            records
        ),
    }

    existing_payload[
        "validity"
    ] = {
        "valid": valid,
        "checks": validity_checks,
    }

    _write_json(
        output_path,
        existing_payload,
    )

    print(
        "=== Repair Existing Baseline ==="
    )
    print(
        f"Scenario:      {scenario_name}"
    )
    print(
        f"Run ID:        {args.run_id}"
    )
    print(
        f"Server log:    {log_file}"
    )
    print(
        f"Result JSON:   {output_path}"
    )
    print("")
    print(
        f"Matched records: "
        f"{len(all_records)}"
    )
    print(
        f"Filtered records: "
        f"{len(records)}"
    )
    print(
        f"Excluded records: "
        f"{excluded_count}"
    )
    print(
        f"Server failures: "
        f"{server_metrics['failures']}"
    )
    print(
        "Server average:  "
        f"{float(server_metrics['average_duration_ms']):.3f} ms"
    )
    print(
        "Server p50:      "
        f"{float(server_metrics['p50_duration_ms']):.3f} ms"
    )
    print(
        "Server p95:      "
        f"{float(server_metrics['p95_duration_ms']):.3f} ms"
    )
    print(
        "Server p99:      "
        f"{float(server_metrics['p99_duration_ms']):.3f} ms"
    )
    print(
        "Server max:      "
        f"{float(server_metrics['max_duration_ms']):.3f} ms"
    )
    print(
        "Average DB:      "
        f"{float(server_metrics['average_db_time_ms']):.3f} ms"
    )
    print(
        "Max DB:          "
        f"{float(server_metrics['max_db_time_ms']):.3f} ms"
    )
    print(
        "DB queries/req:  "
        f"{float(server_metrics['average_db_queries']):.3f}"
    )

    if locust_metrics.get("available"):
        print(
            "Locust requests: "
            f"{locust_metrics.get('requests')}"
        )
        print(
            "Locust failures: "
            f"{locust_metrics.get('failures')}"
        )
        print(
            "Locust RPS:      "
            f"{locust_metrics.get('requests_per_second')}"
        )

    print(
        "Validity:        "
        f"{'VALID' if valid else 'INVALID'}"
    )
    print(
        f"Saved:           {output_path}"
    )

    return 0 if valid else 2


def main() -> int:
    args = _parse_args()

    if args.repair_existing:
        return _repair_existing_result(
            args
        )

    if args.users <= 0:
        raise SystemExit(
            "--users must be greater "
            "than zero"
        )

    if args.spawn_rate <= 0:
        raise SystemExit(
            "--spawn-rate must be "
            "greater than zero"
        )

    runtime = _normalise_duration(
        args.time
    )

    scenario_file = _scenario_path(
        args.scenario
    )

    scenario_name = scenario_file.stem

    run_id = (
        args.run_id
        or (
            f"{scenario_name}-baseline-"
            f"{_timestamp_for_run_id()}"
        )
    ).strip()

    if not run_id:
        raise SystemExit(
            "--run-id cannot be empty"
        )

    log_file = Path(
        args.log_file
        or (
            Path("logs")
            / f"{scenario_name}_server.log"
        )
    ).resolve()

    results_dir = Path(
        args.results_dir
    ).resolve()

    results_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    existing_count = (
        _count_run_id_records(
            log_file,
            run_id,
        )
    )

    if (
        existing_count
        and not args.allow_existing_run_id
    ):
        raise SystemExit(
            f"Run ID already exists "
            f"in {log_file}: "
            f"{run_id} "
            f"({existing_count} records). "
            "Use a new --run-id."
        )

    csv_prefix = (
        results_dir
        / f"{run_id}_locust"
    )

    output_path = (
        results_dir
        / f"{run_id}.json"
    )

    command = _build_command(
        scenario_file,
        users=args.users,
        spawn_rate=args.spawn_rate,
        runtime=runtime,
        host=args.host,
        csv_prefix=csv_prefix,
    )

    environment = os.environ.copy()
    environment["LOCUST_RUN_ID"] = run_id

    started_at = _utc_now()

    print(
        "=== Clinic System Pro "
        "Performance Baseline ==="
    )
    print(
        f"Scenario:      {scenario_name}"
    )
    print(
        f"Run ID:        {run_id}"
    )
    print(
        f"Host:          {args.host}"
    )
    print(
        f"Users:         {args.users}"
    )
    print(
        f"Spawn rate:    {args.spawn_rate}"
    )
    print(
        f"Runtime:       {runtime}"
    )
    print(
        f"Server log:    {log_file}"
    )
    print(
        f"Result JSON:   {output_path}"
    )
    print("")
    print("Starting Locust...")
    print("")

    process = subprocess.run(
        command,
        env=environment,
        check=False,
    )

    finished_at = _utc_now()

    all_records = _load_records(
        log_file,
        run_id,
    )

    records, excluded_count = (
        _filter_records(
            all_records,
            scenario_name=scenario_name,
            include_login=args.include_login,
            include_routes=tuple(
                args.routes
            ),
            exclude_routes=tuple(
                args.exclude_routes
            ),
        )
    )

    stats_path = Path(
        f"{csv_prefix}_stats.csv"
    )

    locust_metrics = (
        _parse_locust_stats(
            stats_path
        )
    )

    server_metrics = _aggregate(
        records
    )

    validity_checks = {
        "locust_exit_code_zero": (
            process.returncode == 0
        ),
        "server_log_exists": (
            log_file.is_file()
        ),
        "matching_server_records_present": (
            bool(records)
        ),
        "run_id_unique_before_run": (
            existing_count == 0
        ),
        "server_failures_zero": (
            int(
                server_metrics["failures"]
            )
            == 0
        ),
    }

    valid = all(
        validity_checks.values()
    )

    payload: dict[str, object] = {
        "schema_version": 1,
        "generated_at": (
            finished_at.isoformat()
        ),
        "started_at": (
            started_at.isoformat()
        ),
        "finished_at": (
            finished_at.isoformat()
        ),
        "scenario": scenario_name,
        "scenario_file": str(
            scenario_file
        ),
        "run_id": run_id,
        "configuration": {
            "host": args.host,
            "users": args.users,
            "spawn_rate": args.spawn_rate,
            "runtime": runtime,
            "server_log": str(
                log_file
            ),
            "include_login": bool(
                args.include_login
            ),
            "included_routes": (
                args.routes
            ),
            "excluded_routes": (
                args.exclude_routes
            ),
        },
        "locust": {
            **locust_metrics,
            "exit_code": (
                process.returncode
            ),
            "csv_stats_file": str(
                stats_path
            ),
        },
        "server": {
            **server_metrics,
            "matched_log_records_before_filter": (
                len(all_records)
            ),
            "excluded_record_count": (
                excluded_count
            ),
            "route_breakdown": _breakdown(
                records
            ),
        },
        "validity": {
            "valid": valid,
            "checks": validity_checks,
        },
        "command": command,
    }

    _write_json(
        output_path,
        payload,
    )

    print("")
    print("=== Baseline Result ===")

    print(
        f"Server requests: "
        f"{server_metrics['requests']}"
    )

    print(
        f"Server failures: "
        f"{server_metrics['failures']}"
    )

    print(
        "Server average:  "
        f"{float(server_metrics['average_duration_ms']):.3f} ms"
    )

    print(
        "Server p50:      "
        f"{float(server_metrics['p50_duration_ms']):.3f} ms"
    )

    print(
        "Server p95:      "
        f"{float(server_metrics['p95_duration_ms']):.3f} ms"
    )

    print(
        "Server p99:      "
        f"{float(server_metrics['p99_duration_ms']):.3f} ms"
    )

    print(
        "Server max:      "
        f"{float(server_metrics['max_duration_ms']):.3f} ms"
    )

    print(
        "Average DB:      "
        f"{float(server_metrics['average_db_time_ms']):.3f} ms"
    )

    print(
        "Max DB:          "
        f"{float(server_metrics['max_db_time_ms']):.3f} ms"
    )

    print(
        "DB queries/req:  "
        f"{float(server_metrics['average_db_queries']):.3f}"
    )

    print(
        "Response size:   "
        f"{float(server_metrics['average_response_size_bytes']):.1f} B"
    )

    if locust_metrics.get("available"):
        print(
            "Locust RPS:      "
            f"{locust_metrics.get('requests_per_second')}"
        )

        print(
            "Locust failures: "
            f"{locust_metrics.get('failures')}"
        )

    print(
        "Validity:        "
        f"{'VALID' if valid else 'INVALID'}"
    )

    print(
        f"Saved:           {output_path}"
    )

    return 0 if valid else 2


if __name__ == "__main__":
    raise SystemExit(main())