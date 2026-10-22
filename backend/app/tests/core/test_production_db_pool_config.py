from pathlib import Path
import os
import re

from app.config import ProductionConfig, TestingConfig


REPO_ROOT = Path(__file__).resolve().parents[4]


def _service_block(compose_text: str, service_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service_name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|^volumes:\n|\Z)",
        compose_text,
    )
    assert match is not None, f"Compose service missing: {service_name}"
    return match.group(0)


def test_production_sqlalchemy_pool_settings_are_explicit_and_bounded():
    options = ProductionConfig.SQLALCHEMY_ENGINE_OPTIONS

    assert options["pool_size"] == int(os.environ.get("SQLALCHEMY_POOL_SIZE", "3"))
    assert options["max_overflow"] == int(os.environ.get("SQLALCHEMY_MAX_OVERFLOW", "2"))
    assert options["pool_timeout"] == float(
        os.environ.get("SQLALCHEMY_POOL_TIMEOUT_SECONDS", "5")
    )
    assert options["pool_recycle"] == int(
        os.environ.get("SQLALCHEMY_POOL_RECYCLE_SECONDS", "1800")
    )
    assert options["pool_pre_ping"] is True


def test_testing_configuration_does_not_inherit_production_pool_budget():
    assert not hasattr(TestingConfig, "SQLALCHEMY_ENGINE_OPTIONS")


def test_compose_passes_pool_budget_to_all_runtime_services():
    compose_text = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")

    expected = (
        'SQLALCHEMY_POOL_SIZE: "${PHASE6_SQLALCHEMY_POOL_SIZE:-3}"',
        'SQLALCHEMY_MAX_OVERFLOW: "${PHASE6_SQLALCHEMY_MAX_OVERFLOW:-2}"',
        'SQLALCHEMY_POOL_TIMEOUT_SECONDS: "${PHASE6_SQLALCHEMY_POOL_TIMEOUT_SECONDS:-5}"',
        'SQLALCHEMY_POOL_RECYCLE_SECONDS: "${PHASE6_SQLALCHEMY_POOL_RECYCLE_SECONDS:-1800}"',
    )

    for service_name in ("backend", "worker", "beat"):
        service = _service_block(compose_text, service_name)
        for setting in expected:
            assert setting in service, f"{service_name} missing pool setting: {setting}"


def test_compose_worker_concurrency_is_explicitly_two():
    compose_text = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    worker = _service_block(compose_text, "worker")

    assert '"--concurrency=2"' in worker
