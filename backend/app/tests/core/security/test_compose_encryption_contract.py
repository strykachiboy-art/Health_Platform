from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[5]
COMPOSE_FILE = REPO_ROOT / "compose.yaml"

EXPECTED_ENCRYPTION_ENV = {
    "BACKUP_ENCRYPTION_KEY":
        "${PHASE6_BACKUP_ENCRYPTION_KEY:?PHASE6_BACKUP_ENCRYPTION_KEY is required}",
    "BACKUP_ENCRYPTION_KEY_VERSION":
        "${BACKUP_ENCRYPTION_KEY_VERSION:-1}",
    "INTEGRATION_ENCRYPTION_KEY":
        "${PHASE6_INTEGRATION_ENCRYPTION_KEY:?PHASE6_INTEGRATION_ENCRYPTION_KEY is required}",
    "INTEGRATION_ENCRYPTION_KEY_VERSION":
        "${INTEGRATION_ENCRYPTION_KEY_VERSION:-1}",
    "INTEGRATION_ENCRYPTION_LEGACY_KEYS":
        "${INTEGRATION_ENCRYPTION_LEGACY_KEYS:-{}}",
}


def _service_environment(service_name):
    with COMPOSE_FILE.open("r", encoding="utf-8") as stream:
        compose = yaml.safe_load(stream)

    environment = compose["services"][service_name].get("environment", {})
    if isinstance(environment, list):
        parsed = {}
        for entry in environment:
            key, separator, value = entry.partition("=")
            parsed[key] = value if separator else None
        return parsed

    return environment


@pytest.mark.parametrize("service_name", ["backend", "worker", "beat"])
def test_runtime_services_have_explicit_encryption_contract(service_name):
    environment = _service_environment(service_name)

    for key, expected_value in EXPECTED_ENCRYPTION_ENV.items():
        assert environment.get(key) == expected_value, (
            f"{service_name}.{key} must use the expected encryption contract"
        )
