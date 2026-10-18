from testing.benchmarks.baseline import PERFORMANCE_PATTERN


def test_performance_pattern_accepts_current_and_legacy_field_order():
    samples = (
        (
            "request_id=req-123 ",
            "phase0-current-format-001",
        ),
        (
            "",
            "phase0-legacy-format-002",
        ),
    )

    for request_field, run_id in samples:
        message = (
            "performance.request "
            + request_field
            + f"load_test_id={run_id} "
            + "method=GET route=/health/live status=200 "
            + "duration_ms=1.234 response_size_bytes=31 "
            + "db_query_count=0 db_time_ms=0.000"
        )

        match = PERFORMANCE_PATTERN.search(message)

        assert match is not None, message
        assert match.group("load_test_id") == run_id
        assert match.group("method") == "GET"
        assert match.group("route") == "/health/live"
        assert match.group("status") == "200"
        assert match.group("duration") == "1.234"
        assert match.group("size") == "31"
        assert match.group("queries") == "0"
        assert match.group("db") == "0.000"
