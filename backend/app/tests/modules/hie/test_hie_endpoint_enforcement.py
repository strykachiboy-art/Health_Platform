from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.enums.hie_enums import HIEIntegrationStatus
from app.core.exceptions import ValidationError
from app.modules.hie.services import hie_service


ALLOWED_HOSTS = (
    "fhir.partnerhealth.org",
    "api.epic.com",
)


def make_integration(
    *,
    endpoint_url,
    status=HIEIntegrationStatus.ACTIVE,
):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=42,
        clinic_id=1,
        provider="test-hie-provider",
        status=status,
        endpoint_url=endpoint_url,
        organization_id="ORG-001",
        facility_id="FAC-001",
        last_sync_at=None,
        created_at=now,
        updated_at=now,
    )


def test_create_rejects_endpoint_when_allowlist_is_missing(
    app,
    monkeypatch,
):
    app.config["HIE_ALLOWED_ENDPOINT_HOSTS"] = ()
    monkeypatch.setattr(hie_service, "_get_clinic", Mock())

    with pytest.raises(
        ValidationError,
        match="configured outbound endpoint policy",
    ):
        hie_service.create_hie_integration(
            clinic_id=1,
            provider="test-hie-provider",
            endpoint_url="https://fhir.partnerhealth.org/fhir/r4",
        )


def test_create_rejects_unapproved_hostname(
    app,
    monkeypatch,
):
    app.config["HIE_ALLOWED_ENDPOINT_HOSTS"] = ALLOWED_HOSTS
    monkeypatch.setattr(hie_service, "_get_clinic", Mock())

    with pytest.raises(
        ValidationError,
        match="configured outbound endpoint policy",
    ):
        hie_service.create_hie_integration(
            clinic_id=1,
            provider="test-hie-provider",
            endpoint_url="https://attacker.net/fhir/r4",
        )


def test_update_rejects_unapproved_endpoint_without_mutation(
    app,
    monkeypatch,
):
    app.config["HIE_ALLOWED_ENDPOINT_HOSTS"] = ALLOWED_HOSTS
    integration = make_integration(
        endpoint_url=None,
        status=HIEIntegrationStatus.PENDING,
    )
    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    with pytest.raises(
        ValidationError,
        match="configured outbound endpoint policy",
    ):
        hie_service.update_hie_integration(
            clinic_id=1,
            integration_id=integration.id,
            endpoint_url="https://attacker.net/fhir/r4",
        )

    assert integration.endpoint_url is None
    assert integration.status is HIEIntegrationStatus.PENDING


def test_activation_rejects_legacy_unapproved_endpoint_without_mutation(
    app,
    monkeypatch,
):
    app.config["HIE_ALLOWED_ENDPOINT_HOSTS"] = ALLOWED_HOSTS
    integration = make_integration(
        endpoint_url="https://attacker.net/fhir/r4",
        status=HIEIntegrationStatus.PENDING,
    )
    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )
    monkeypatch.setattr(
        hie_service,
        "is_provider_registered",
        Mock(return_value=True),
    )

    with pytest.raises(
        ValidationError,
        match="configured outbound endpoint policy",
    ):
        hie_service.update_hie_integration(
            clinic_id=1,
            integration_id=integration.id,
            status=HIEIntegrationStatus.ACTIVE,
        )

    assert integration.status is HIEIntegrationStatus.PENDING
    assert integration.endpoint_url == "https://attacker.net/fhir/r4"


def test_provider_dispatch_rejects_legacy_endpoint_before_registry_lookup(
    app,
    monkeypatch,
):
    app.config["HIE_ALLOWED_ENDPOINT_HOSTS"] = ALLOWED_HOSTS
    integration = make_integration(
        endpoint_url="https://attacker.net/fhir/r4",
    )
    get_provider = Mock()
    monkeypatch.setattr(hie_service, "get_provider", get_provider)

    with pytest.raises(
        ValidationError,
        match="configured outbound endpoint policy",
    ):
        hie_service._get_provider(integration)

    get_provider.assert_not_called()


@pytest.mark.parametrize(
    "configured_hosts",
    [
        (),
        ("localhost",),
        ("bad/hostname",),
    ],
)
def test_provider_dispatch_fails_closed_for_missing_or_malformed_allowlist(
    app,
    monkeypatch,
    configured_hosts,
):
    app.config["HIE_ALLOWED_ENDPOINT_HOSTS"] = configured_hosts
    integration = make_integration(
        endpoint_url="https://fhir.partnerhealth.org/fhir/r4",
    )
    get_provider = Mock()
    monkeypatch.setattr(hie_service, "get_provider", get_provider)

    with pytest.raises(
        ValidationError,
        match="configured outbound endpoint policy",
    ):
        hie_service._get_provider(integration)

    get_provider.assert_not_called()


@pytest.mark.parametrize(
    "endpoint_url",
    [
        " https://fhir.partnerhealth.org/fhir/r4",
        "https://fhir.partnerhealth.org/fhir/r4 ",
    ],
)
def test_create_rejects_endpoint_with_surrounding_whitespace(
    app,
    monkeypatch,
    endpoint_url,
):
    app.config["HIE_ALLOWED_ENDPOINT_HOSTS"] = ALLOWED_HOSTS
    monkeypatch.setattr(hie_service, "_get_clinic", Mock())

    with pytest.raises(
        ValidationError,
        match="configured outbound endpoint policy",
    ):
        hie_service.create_hie_integration(
            clinic_id=1,
            provider="test-hie-provider",
            endpoint_url=endpoint_url,
        )


@pytest.mark.parametrize(
    "endpoint_url",
    [
        " https://fhir.partnerhealth.org/fhir/r4",
        "https://fhir.partnerhealth.org/fhir/r4 ",
    ],
)
def test_update_rejects_endpoint_with_surrounding_whitespace(
    app,
    monkeypatch,
    endpoint_url,
):
    app.config["HIE_ALLOWED_ENDPOINT_HOSTS"] = ALLOWED_HOSTS
    integration = make_integration(
        endpoint_url=None,
        status=HIEIntegrationStatus.PENDING,
    )
    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    with pytest.raises(
        ValidationError,
        match="configured outbound endpoint policy",
    ):
        hie_service.update_hie_integration(
            clinic_id=1,
            integration_id=integration.id,
            endpoint_url=endpoint_url,
        )

    assert integration.endpoint_url is None
    assert integration.status is HIEIntegrationStatus.PENDING
