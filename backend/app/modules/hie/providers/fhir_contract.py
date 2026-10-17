from __future__ import annotations

from typing import Any


# The R4 family is accepted for protocol inspection. This does not assert that
# a server implements FHIR 4.0.1 or conforms to any US Core profile.
FHIR_R4_COMPATIBLE_VERSIONS = frozenset({"4.0.0", "4.0.1"})

# Resource types in the proposed first chart-import workflow. Expanding this
# list requires an explicit workflow and profile review.
FHIR_READ_ONLY_RESOURCE_TYPES = frozenset(
    {
        "Patient",
        "Encounter",
        "Condition",
        "AllergyIntolerance",
        "MedicationRequest",
        "Observation",
        "DiagnosticReport",
    }
)

# Even if a CapabilityStatement advertises write operations, this contract
# exposes only search/read interactions for the initial workflow.
FHIR_READ_ONLY_INTERACTIONS = frozenset({"read", "search-type"})


class FHIRContractError(ValueError):
    """Raised when an input does not satisfy the bounded FHIR R4 contract."""


def _require_object(value: Any, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FHIRContractError(f"{description} must be an object")
    return value


def _require_resource_type(resource_type: str) -> str:
    if (
        not isinstance(resource_type, str)
        or resource_type not in FHIR_READ_ONLY_RESOURCE_TYPES
    ):
        raise FHIRContractError("FHIR resource type is outside the read-only scope")
    return resource_type


def validate_capability_statement(payload: Any) -> str:
    """Validate the shape and declared R4-family version of a server statement.

    A valid result is protocol discovery only. It does not prove US Core
    profile conformance, authorization behavior, or production readiness.
    """
    statement = _require_object(payload, "CapabilityStatement")
    if statement.get("resourceType") != "CapabilityStatement":
        raise FHIRContractError("Expected a CapabilityStatement resource")

    version = statement.get("fhirVersion")
    if version not in FHIR_R4_COMPATIBLE_VERSIONS:
        raise FHIRContractError("FHIR server version is not in the accepted R4 baseline")

    rest = statement.get("rest")
    if not isinstance(rest, list):
        raise FHIRContractError("CapabilityStatement.rest must be an array")

    return version


def advertised_read_only_interactions(
    capability_statement: Any,
    resource_type: str,
) -> frozenset[str]:
    """Return only supported read/search interactions for an in-scope resource."""
    _require_resource_type(resource_type)
    statement = _require_object(capability_statement, "CapabilityStatement")
    validate_capability_statement(statement)

    for rest_entry in statement["rest"]:
        if not isinstance(rest_entry, dict):
            raise FHIRContractError("CapabilityStatement.rest entries must be objects")
        if rest_entry.get("mode") != "server":
            continue

        resources = rest_entry.get("resource", [])
        if not isinstance(resources, list):
            raise FHIRContractError("CapabilityStatement resource list must be an array")

        for resource in resources:
            if not isinstance(resource, dict):
                raise FHIRContractError("CapabilityStatement resource entries must be objects")
            if resource.get("type") != resource_type:
                continue

            interactions = resource.get("interaction", [])
            if not isinstance(interactions, list):
                raise FHIRContractError("FHIR resource interactions must be an array")

            advertised: set[str] = set()
            for interaction in interactions:
                if not isinstance(interaction, dict):
                    raise FHIRContractError("FHIR interaction entries must be objects")
                code = interaction.get("code")
                if isinstance(code, str):
                    advertised.add(code)

            return frozenset(advertised & FHIR_READ_ONLY_INTERACTIONS)

    raise FHIRContractError(f"FHIR resource is not advertised: {resource_type}")


def validate_fhir_resource(payload: Any, expected_resource_type: str) -> dict[str, Any]:
    """Validate the top-level type for a FHIR resource in the first read workflow."""
    _require_resource_type(expected_resource_type)
    resource = _require_object(payload, "FHIR resource")
    if resource.get("resourceType") != expected_resource_type:
        raise FHIRContractError(
            f"Expected FHIR resource type {expected_resource_type}"
        )
    return resource


def validate_search_bundle(payload: Any, expected_resource_type: str) -> dict[str, Any]:
    """Validate a resource-only searchset Bundle; an omitted total is permitted."""
    _require_resource_type(expected_resource_type)
    bundle = _require_object(payload, "FHIR search response")
    if bundle.get("resourceType") != "Bundle":
        raise FHIRContractError("Expected a Bundle resource")
    if bundle.get("type") != "searchset":
        raise FHIRContractError("Expected a FHIR searchset Bundle")

    if "total" in bundle:
        total = bundle["total"]
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise FHIRContractError("FHIR Bundle.total must be a non-negative integer")

    entries = bundle.get("entry", [])
    if not isinstance(entries, list):
        raise FHIRContractError("FHIR searchset entry must be an array")

    for entry in entries:
        if not isinstance(entry, dict):
            raise FHIRContractError("FHIR searchset entries must be objects")
        if "resource" not in entry:
            raise FHIRContractError("FHIR searchset entry is missing its resource")
        validate_fhir_resource(entry["resource"], expected_resource_type)

    return bundle
