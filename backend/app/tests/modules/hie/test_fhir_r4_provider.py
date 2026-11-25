"""Unit tests for FHIR R4 provider (fhir_r4_provider.py).

All tests are deterministic and require no network, database, or Flask app
context.  External I/O is replaced with fakes that satisfy the same contracts
as the real HIEHTTPTransport.

Test coverage:
  * Registration helper (register_fhir_r4_provider)
  * query_patient – success path, empty result, HTTP errors, bad JSON,
    contract violations, transport errors
  * query_clinical_data – resource_type routing, subject/patient param
    selection, extra param passthrough, contract violations
  * submit_* – all three raise HIEFailClosedError
  * _PinnedHTTPSConnection is NOT mocked; transport is replaced at the
    provider level
"""

from __future__ import annotations

import json
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from app.modules.hie.providers import registry
from app.modules.hie.providers.exceptions import (
    HIEFailClosedError,
    HIEProviderHTTPError,
    HIERetryableError,
)
from app.modules.hie.providers.fhir_r4_provider import (
    PROVIDER_NAME,
    FHIRR4Provider,
    register_fhir_r4_provider,
)
from app.modules.hie.providers.transport import HIEHTTPResponse


# ---------------------------------------------------------------------------
# Fakes / builders
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int,
    body: dict | None = None,
) -> HIEHTTPResponse:
    encoded = json.dumps(body or {}).encode()
    return HIEHTTPResponse(
        status_code=status_code,
        headers=(),
        body=encoded,
        connected_ip="203.0.113.1",
    )


def _patient_bundle(patient_id: str = "P001", total: int = 1) -> dict[str, Any]:
    entries = (
        [{"resource": {"resourceType": "Patient", "id": patient_id}}] * total
    )
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": total,
        "entry": entries,
    }


def _empty_bundle() -> dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": 0,
        "entry": [],
    }


def _resource_bundle(resource_type: str, resource_id: str = "R1") -> dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": 1,
        "entry": [{"resource": {"resourceType": resource_type, "id": resource_id}}],
    }


def _make_transport(response: HIEHTTPResponse) -> MagicMock:
    transport = MagicMock()
    transport.request.return_value = response
    return transport


def _make_provider(
    transport: Optional[MagicMock] = None,
    endpoint: str = "https://fhir.example.org/fhir/r4",
) -> FHIRR4Provider:
    return FHIRR4Provider(
        endpoint_url=endpoint,
        transport=transport or _make_transport(_make_response(200, _patient_bundle())),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_fhir_r4_provider_adds_to_registry():
    registry.unregister_provider(PROVIDER_NAME)
    assert not registry.is_provider_registered(PROVIDER_NAME)

    register_fhir_r4_provider()

    try:
        assert registry.is_provider_registered(PROVIDER_NAME)
    finally:
        registry.unregister_provider(PROVIDER_NAME)


def test_register_fhir_r4_provider_is_idempotent():
    register_fhir_r4_provider()
    register_fhir_r4_provider()
    try:
        assert registry.is_provider_registered(PROVIDER_NAME)
    finally:
        registry.unregister_provider(PROVIDER_NAME)


def test_registered_factory_requires_endpoint():
    register_fhir_r4_provider()
    try:
        with pytest.raises(HIEFailClosedError, match="requires an endpoint URL"):
            registry.get_provider(PROVIDER_NAME, endpoint=None)
    finally:
        registry.unregister_provider(PROVIDER_NAME)


def test_registered_factory_returns_provider_instance():
    register_fhir_r4_provider()
    try:
        provider = registry.get_provider(
            PROVIDER_NAME,
            endpoint="https://fhir.example.org/fhir/r4",
        )
        assert isinstance(provider, FHIRR4Provider)
    finally:
        registry.unregister_provider(PROVIDER_NAME)


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_constructor_rejects_empty_endpoint():
    with pytest.raises(HIEFailClosedError, match="requires a non-empty endpoint URL"):
        FHIRR4Provider(endpoint_url="   ")


def test_constructor_rejects_none_endpoint():
    with pytest.raises((HIEFailClosedError, AttributeError, TypeError)):
        FHIRR4Provider(endpoint_url=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# query_patient – success paths
# ---------------------------------------------------------------------------


def test_query_patient_returns_records_on_success():
    transport = _make_transport(_make_response(200, _patient_bundle("P42")))
    provider = _make_provider(transport)

    result = provider.query_patient("ID-123")

    assert result["status_code"] == 200
    assert result["external_reference"] == "P42"
    assert len(result["records"]) == 1
    assert result["records"][0]["resourceType"] == "Patient"


def test_query_patient_uses_identifier_param():
    transport = _make_transport(_make_response(200, _patient_bundle()))
    provider = _make_provider(transport)

    provider.query_patient("  ID-XYZ  ")

    call_kwargs = transport.request.call_args.kwargs
    assert call_kwargs["params"]["identifier"] == "ID-XYZ"


def test_query_patient_path_is_patient():
    transport = _make_transport(_make_response(200, _patient_bundle()))
    provider = _make_provider(transport)

    provider.query_patient("ID-1")

    assert transport.request.call_args.kwargs["path"] == "Patient"


def test_query_patient_empty_bundle_returns_no_records():
    transport = _make_transport(_make_response(200, _empty_bundle()))
    provider = _make_provider(transport)

    result = provider.query_patient("UNKNOWN")

    assert result["records"] == []
    assert result["external_reference"] is None


# ---------------------------------------------------------------------------
# query_patient – error paths
# ---------------------------------------------------------------------------


def test_query_patient_rejects_empty_identifier():
    provider = _make_provider()
    with pytest.raises(HIEFailClosedError, match="non-empty patient identifier"):
        provider.query_patient("  ")


def test_query_patient_raises_retryable_on_500():
    transport = _make_transport(_make_response(500))
    provider = _make_provider(transport)
    with pytest.raises(HIERetryableError):
        provider.query_patient("ID-1")


def test_query_patient_raises_retryable_on_429():
    transport = _make_transport(_make_response(429))
    provider = _make_provider(transport)
    with pytest.raises(HIERetryableError):
        provider.query_patient("ID-1")


def test_query_patient_raises_provider_http_error_on_401():
    transport = _make_transport(_make_response(401))
    provider = _make_provider(transport)
    with pytest.raises(HIEProviderHTTPError) as exc_info:
        provider.query_patient("ID-1")
    assert exc_info.value.status_code == 401


def test_query_patient_raises_fail_closed_on_non_json():
    response = HIEHTTPResponse(
        status_code=200,
        headers=(),
        body=b"not-json",
        connected_ip="203.0.113.1",
    )
    transport = _make_transport(response)
    provider = _make_provider(transport)
    with pytest.raises(HIEFailClosedError, match="non-JSON"):
        provider.query_patient("ID-1")


def test_query_patient_raises_fail_closed_on_wrong_bundle_type():
    bad_bundle = {
        "resourceType": "Bundle",
        "type": "transaction",  # not searchset
        "entry": [],
    }
    transport = _make_transport(_make_response(200, bad_bundle))
    provider = _make_provider(transport)
    with pytest.raises(HIEFailClosedError, match="contract check"):
        provider.query_patient("ID-1")


def test_query_patient_propagates_transport_error_as_retryable():
    from app.modules.hie.providers.transport import HIETransportError

    transport = MagicMock()
    transport.request.side_effect = HIETransportError("connection refused")
    provider = _make_provider(transport)
    with pytest.raises(HIERetryableError):
        provider.query_patient("ID-1")


def test_query_patient_propagates_redirect_as_fail_closed():
    from app.modules.hie.providers.transport import HIERedirectRejectedError

    transport = MagicMock()
    transport.request.side_effect = HIERedirectRejectedError("redirect")
    provider = _make_provider(transport)
    with pytest.raises(HIEFailClosedError, match="redirect"):
        provider.query_patient("ID-1")


# ---------------------------------------------------------------------------
# query_clinical_data – success paths
# ---------------------------------------------------------------------------


def test_query_clinical_data_defaults_to_patient_resource():
    transport = _make_transport(_make_response(200, _patient_bundle()))
    provider = _make_provider(transport)

    provider.query_clinical_data("ID-1", filters={})

    call_kwargs = transport.request.call_args.kwargs
    assert call_kwargs["path"] == "Patient"
    assert "identifier" in call_kwargs["params"]


@pytest.mark.parametrize(
    "resource_type,expected_path,expected_param",
    [
        ("Encounter", "Encounter", "patient"),
        ("Condition", "Condition", "patient"),
        ("AllergyIntolerance", "AllergyIntolerance", "patient"),
        ("MedicationRequest", "MedicationRequest", "patient"),
        ("Observation", "Observation", "subject"),
        ("DiagnosticReport", "DiagnosticReport", "subject"),
    ],
)
def test_query_clinical_data_routes_resource_types(
    resource_type: str, expected_path: str, expected_param: str
):
    transport = _make_transport(
        _make_response(200, _resource_bundle(resource_type))
    )
    provider = _make_provider(transport)

    result = provider.query_clinical_data(
        "ID-1", filters={"resource_type": resource_type}
    )

    call_kwargs = transport.request.call_args.kwargs
    assert call_kwargs["path"] == expected_path
    assert expected_param in call_kwargs["params"]
    assert result["status_code"] == 200


def test_query_clinical_data_unknown_resource_type_falls_back_to_patient():
    transport = _make_transport(_make_response(200, _patient_bundle()))
    provider = _make_provider(transport)

    provider.query_clinical_data("ID-1", filters={"resource_type": "Invoice"})

    call_kwargs = transport.request.call_args.kwargs
    assert call_kwargs["path"] == "Patient"


def test_query_clinical_data_passes_extra_params():
    transport = _make_transport(
        _make_response(200, _resource_bundle("Observation"))
    )
    provider = _make_provider(transport)

    provider.query_clinical_data(
        "ID-1",
        filters={"resource_type": "Observation", "params": {"_count": "10"}},
    )

    call_kwargs = transport.request.call_args.kwargs
    assert call_kwargs["params"].get("_count") == "10"


def test_query_clinical_data_does_not_let_extra_params_override_system_params():
    transport = _make_transport(
        _make_response(200, _resource_bundle("Encounter"))
    )
    provider = _make_provider(transport)

    # Caller tries to inject their own "patient" param – must be ignored.
    provider.query_clinical_data(
        "REAL-ID",
        filters={
            "resource_type": "Encounter",
            "params": {"patient": "MALICIOUS-ID"},
        },
    )

    call_kwargs = transport.request.call_args.kwargs
    assert call_kwargs["params"]["patient"] == "REAL-ID"


def test_query_clinical_data_rejects_empty_identifier():
    provider = _make_provider()
    with pytest.raises(HIEFailClosedError, match="non-empty patient identifier"):
        provider.query_clinical_data("", filters={})


def test_query_clinical_data_accepts_none_filters():
    transport = _make_transport(_make_response(200, _patient_bundle()))
    provider = _make_provider(transport)
    result = provider.query_clinical_data("ID-1", filters=None)
    assert result["status_code"] == 200


# ---------------------------------------------------------------------------
# submit_* – all unsupported
# ---------------------------------------------------------------------------


def test_submit_patient_raises_fail_closed():
    provider = _make_provider()
    with pytest.raises(HIEFailClosedError, match="read-only"):
        provider.submit_patient({"patient": "1"})


def test_submit_clinical_data_raises_fail_closed():
    provider = _make_provider()
    with pytest.raises(HIEFailClosedError, match="read-only"):
        provider.submit_clinical_data({"data": "x"})


def test_submit_clinical_document_raises_fail_closed():
    provider = _make_provider()
    with pytest.raises(HIEFailClosedError, match="read-only"):
        provider.submit_clinical_document({"doc": "x"})
