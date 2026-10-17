from __future__ import annotations

import pytest

from app.modules.hie.providers.fhir_contract import (
    FHIRContractError,
    advertised_read_only_interactions,
    validate_capability_statement,
    validate_fhir_resource,
    validate_search_bundle,
)


def capability_statement(
    *,
    version="4.0.0",
    resource_type="Patient",
    interactions=("read", "search-type", "create", "update", "patch", "delete"),
):
    return {
        "resourceType": "CapabilityStatement",
        "fhirVersion": version,
        "rest": [
            {
                "mode": "server",
                "resource": [
                    {
                        "type": resource_type,
                        "interaction": [{"code": code} for code in interactions],
                    }
                ],
            }
        ],
    }


def patient_resource():
    return {"resourceType": "Patient", "id": "synthetic-patient-1"}


def test_validate_capability_statement_accepts_r4_versions():
    assert validate_capability_statement(capability_statement(version="4.0.0")) == "4.0.0"
    assert validate_capability_statement(capability_statement(version="4.0.1")) == "4.0.1"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"resourceType": "Patient", "fhirVersion": "4.0.0", "rest": []},
        {"resourceType": "CapabilityStatement", "fhirVersion": "4.0.0"},
        {"resourceType": "CapabilityStatement", "fhirVersion": "5.0.0", "rest": []},
        {"resourceType": "CapabilityStatement", "fhirVersion": "4.0.2", "rest": []},
    ],
)
def test_validate_capability_statement_rejects_invalid_or_out_of_baseline_payloads(payload):
    with pytest.raises(FHIRContractError):
        validate_capability_statement(payload)


def test_advertised_read_only_interactions_discards_all_write_operations():
    statement = capability_statement()

    assert advertised_read_only_interactions(statement, "Patient") == frozenset(
        {"read", "search-type"}
    )


def test_advertised_read_only_interactions_can_return_empty_for_read_unsupported_resource():
    statement = capability_statement(interactions=("create", "update", "delete"))

    assert advertised_read_only_interactions(statement, "Patient") == frozenset()


def test_advertised_read_only_interactions_rejects_out_of_scope_resource():
    with pytest.raises(FHIRContractError, match="outside the read-only scope"):
        advertised_read_only_interactions(capability_statement(), "Organization")


def test_advertised_read_only_interactions_rejects_unadvertised_resource():
    with pytest.raises(FHIRContractError, match="not advertised"):
        advertised_read_only_interactions(capability_statement(), "Observation")


def test_validate_fhir_resource_accepts_expected_type():
    resource = patient_resource()

    assert validate_fhir_resource(resource, "Patient") is resource


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"resourceType": "Observation"}],
)
def test_validate_fhir_resource_rejects_malformed_or_wrong_type(payload):
    with pytest.raises(FHIRContractError):
        validate_fhir_resource(payload, "Patient")


def test_validate_search_bundle_accepts_entry_when_total_is_omitted():
    bundle = {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [{"resource": patient_resource()}],
    }

    assert validate_search_bundle(bundle, "Patient") is bundle


def test_validate_search_bundle_accepts_empty_searchset_with_zero_total():
    bundle = {"resourceType": "Bundle", "type": "searchset", "total": 0}

    assert validate_search_bundle(bundle, "Patient") is bundle


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"resourceType": "Patient", "type": "searchset"},
        {"resourceType": "Bundle", "type": "collection"},
        {"resourceType": "Bundle", "type": "searchset", "entry": {}},
        {"resourceType": "Bundle", "type": "searchset", "entry": [None]},
        {"resourceType": "Bundle", "type": "searchset", "entry": [{}]},
        {
            "resourceType": "Bundle",
            "type": "searchset",
            "entry": [{"resource": {"resourceType": "Observation"}}],
        },
        {"resourceType": "Bundle", "type": "searchset", "total": True},
        {"resourceType": "Bundle", "type": "searchset", "total": -1},
        {"resourceType": "Bundle", "type": "searchset", "total": "1"},
    ],
)
def test_validate_search_bundle_rejects_invalid_payloads(payload):
    with pytest.raises(FHIRContractError):
        validate_search_bundle(payload, "Patient")
