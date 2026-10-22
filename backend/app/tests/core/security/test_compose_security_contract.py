from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]


def read_source(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def service_block(source: str, name: str, next_name: str) -> str:
    lines = source.splitlines()
    start_marker = f"  {name}:"
    end_marker = "volumes:" if next_name == "volumes" else f"  {next_name}:"
    starts = [index for index, line in enumerate(lines) if line == start_marker]
    ends = [index for index, line in enumerate(lines) if line == end_marker]

    if len(starts) != 1:
        raise AssertionError(f"Expected one top-level Compose service named {name!r}")

    if len(ends) != 1:
        raise AssertionError(f"Expected one top-level boundary named {next_name!r}")

    service_start = starts[0]
    service_end = ends[0]
    if service_end <= service_start:
        raise AssertionError(f"Invalid Compose service boundary for {name!r}")

    return chr(10).join(lines[service_start + 1:service_end])


def test_base_compose_requires_database_and_application_secrets():
    compose = read_source("compose.yaml")
    postgres = service_block(compose, "postgres", "redis")

    assert (
        "POSTGRES_DB: "
        "${PHASE6_POSTGRES_DB:?PHASE6_POSTGRES_DB is required}"
    ) in postgres
    assert (
        "POSTGRES_USER: "
        "${PHASE6_POSTGRES_USER:?PHASE6_POSTGRES_USER is required}"
    ) in postgres
    assert (
        "POSTGRES_PASSWORD: "
        "${PHASE6_POSTGRES_PASSWORD:?PHASE6_POSTGRES_PASSWORD is required}"
    ) in postgres

    database_url = (
        "DATABASE_URL: postgresql://"
        "${PHASE6_POSTGRES_USER:?PHASE6_POSTGRES_USER is required}:"
        "${PHASE6_POSTGRES_PASSWORD:?PHASE6_POSTGRES_PASSWORD is required}"
        "@postgres:5432/"
        "${PHASE6_POSTGRES_DB:?PHASE6_POSTGRES_DB is required}"
    )

    for name, next_name in (
        ("backend", "worker"),
        ("worker", "beat"),
        ("beat", "proxy"),
    ):
        service = service_block(compose, name, next_name)

        assert (
            "SECRET_KEY: "
            "${PHASE6_SECRET_KEY:?PHASE6_SECRET_KEY is required}"
        ) in service
        assert (
            "JWT_SECRET_KEY: "
            "${PHASE6_JWT_SECRET_KEY:?PHASE6_JWT_SECRET_KEY is required}"
        ) in service
        assert database_url in service

    assert "phase6-development-secret" not in compose
    assert "phase6-development-jwt-secret" not in compose
    assert "clinic_phase6" not in compose


def test_base_compose_does_not_publish_backend_host_port():
    compose = read_source("compose.yaml")
    backend = service_block(compose, "backend", "worker")
    proxy = service_block(compose, "proxy", "volumes")

    assert "\n    ports:\n" not in backend
    assert '"5000:5000"' not in backend

    # The proxy remains the configured entry point; its security properties
    # are tracked separately and are not silently changed by Finding 1.
    assert '"8080:80"' in proxy
    assert '"8443:8443"' in proxy
