"""Production FHIR R4 HIE provider.

This module registers a concrete adapter named ``fhir-r4`` that satisfies the
:class:`~app.modules.hie.providers.base.HIEProvider` protocol.  It is wired
exclusively on top of the already-audited :class:`.HIEHTTPTransport` and the
:mod:`.fhir_contract` validators, so every security invariant that exists for
outbound HIE requests (allowlisted hosts, pinned TLS, redirect rejection,
response-size cap) is preserved without modification.

Design constraints
------------------
* **Read-only outbound contract** – the transport supports GET/HEAD only.
  ``submit_*`` methods are not supported by this provider; they raise
  :class:`~app.modules.hie.providers.exceptions.HIEFailClosedError` so the
  caller records a ``FAIL_CLOSED`` submission and surfaces a clear error.
* **No state, no DB access** – this module contains zero SQLAlchemy imports.
  All persistence is handled by the service layer, which owns the
  ``HIESubmission`` lifecycle.
* **Provider name** – ``"fhir-r4"`` (lowercase, hyphenated).  Pass this string
  when creating an ``HIEIntegration`` record.
* **Registration** – call :func:`register_fhir_r4_provider` once at app
  startup (see ``app/__init__.py``).  Calling it again is idempotent.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.modules.hie.providers.exceptions import (
    HIEFailClosedError,
    HIEProviderHTTPError,
    HIERetryableError,
)
from app.modules.hie.providers.fhir_contract import (
    FHIR_READ_ONLY_RESOURCE_TYPES,
    FHIRContractError,
    validate_capability_statement,
    validate_fhir_resource,
    validate_search_bundle,
)
from app.modules.hie.providers.transport import (
    HIEHTTPTransport,
    HIERedirectRejectedError,
    HIETransportError,
)
from app.modules.hie.providers.registry import register_provider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROVIDER_NAME = "fhir-r4"

# Resource type → FHIR R4 search path segment.
# Extend this table when the contract in fhir_contract.py is widened.
_RESOURCE_SEARCH_PATHS: dict[str, str] = {
    "Patient": "Patient",
    "Encounter": "Encounter",
    "Condition": "Condition",
    "AllergyIntolerance": "AllergyIntolerance",
    "MedicationRequest": "MedicationRequest",
    "Observation": "Observation",
    "DiagnosticReport": "DiagnosticReport",
}

# Common authorization header key (Bearer token value is caller-supplied).
_AUTH_HEADER = "Authorization"

_MAX_PAGE_COUNT = 10  # guard against runaway pagination on a single query


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_json_body(body: bytes) -> Any:
    """Decode a UTF-8 JSON body; raise HIEFailClosedError on malformed input."""
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HIEFailClosedError(
            "FHIR R4 provider received a non-JSON response body"
        ) from exc


def _bearer_headers(bearer_token: Optional[str]) -> dict[str, str] | None:
    if not bearer_token or not bearer_token.strip():
        return None
    return {_AUTH_HEADER: f"Bearer {bearer_token.strip()}"}


def _transport_error_to_provider(exc: Exception) -> None:
    """Re-raise a transport-layer exception as an appropriate provider error."""
    if isinstance(exc, HIERedirectRejectedError):
        raise HIEFailClosedError(
            "FHIR R4 endpoint returned an HTTP redirect"
        ) from exc
    if isinstance(exc, HIETransportError):
        raise HIERetryableError(
            "FHIR R4 transport error – transient connectivity issue"
        ) from exc
    raise HIEFailClosedError(
        f"Unexpected FHIR R4 transport failure: {type(exc).__name__}"
    ) from exc


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class FHIRR4Provider:
    """Concrete FHIR R4 HIE adapter.

    Parameters
    ----------
    endpoint_url:
        The base FHIR R4 server URL that must already pass the endpoint and
        destination policies (e.g. ``https://fhir.partnerhealth.org/fhir/r4``).
        Supplied by the registry factory from the ``HIEIntegration.endpoint_url``
        column value.
    transport:
        Optional pre-configured transport instance; constructed automatically
        when ``None``.  Useful only in tests.
    bearer_token:
        Optional bearer token appended to every outbound request.  Production
        deployments should supply this via the integration's configuration
        rather than a hard-coded value.
    """

    def __init__(
        self,
        endpoint_url: str,
        *,
        transport: HIEHTTPTransport | None = None,
        bearer_token: Optional[str] = None,
    ) -> None:
        if not isinstance(endpoint_url, str) or not endpoint_url.strip():
            raise HIEFailClosedError(
                "FHIR R4 provider requires a non-empty endpoint URL"
            )
        self._endpoint_url = endpoint_url.strip()
        self._transport = transport
        self._bearer_token = bearer_token

    # ------------------------------------------------------------------
    # Read operations (supported)
    # ------------------------------------------------------------------

    def query_patient(
        self,
        patient_identifier: str,
    ) -> dict[str, Any]:
        """Search for a Patient resource by identifier.

        Uses ``GET {base}/Patient?identifier={patient_identifier}``.

        Returns a normalised provider response dict:

        .. code-block:: python

            {
                "status_code": 200,
                "external_reference": "<FHIR patient id or None>",
                "records": [<validated Patient resource>, ...],
            }
        """
        if not isinstance(patient_identifier, str) or not patient_identifier.strip():
            raise HIEFailClosedError(
                "FHIR R4 query_patient requires a non-empty patient identifier"
            )

        transport = self._get_transport()
        headers = _bearer_headers(self._bearer_token)

        try:
            response = transport.request(
                endpoint_url=self._endpoint_url,
                path="Patient",
                params={"identifier": patient_identifier.strip()},
                headers=headers,
            )
        except Exception as exc:
            _transport_error_to_provider(exc)

        self._raise_for_http_error(response.status_code)

        payload = _parse_json_body(response.body)

        try:
            bundle = validate_search_bundle(payload, "Patient")
        except FHIRContractError as exc:
            raise HIEFailClosedError(
                f"FHIR R4 Patient search response failed contract check: {exc}"
            ) from exc

        entries = bundle.get("entry", [])
        records = [entry["resource"] for entry in entries]

        external_reference: Optional[str] = None
        if records:
            external_reference = str(records[0].get("id", ""))

        return {
            "status_code": response.status_code,
            "external_reference": external_reference or None,
            "records": records,
        }

    def query_clinical_data(
        self,
        patient_identifier: str,
        filters: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Retrieve clinical resources for a patient from the FHIR server.

        ``filters`` may contain:

        * ``resource_type`` (``str``) – one of the in-scope FHIR resource
          types.  Defaults to ``"Patient"`` when absent or unrecognised.
        * ``params`` (``dict[str, str]``) – additional FHIR search parameters
          passed verbatim to the transport query string (after the
          ``patient`` or ``subject`` reference is injected).

        Returns the same normalised structure as :meth:`query_patient`.
        """
        if not isinstance(patient_identifier, str) or not patient_identifier.strip():
            raise HIEFailClosedError(
                "FHIR R4 query_clinical_data requires a non-empty patient identifier"
            )

        filters = filters or {}

        resource_type = str(filters.get("resource_type", "")).strip()
        if resource_type not in FHIR_READ_ONLY_RESOURCE_TYPES:
            resource_type = "Patient"

        path = _RESOURCE_SEARCH_PATHS[resource_type]

        # Build search parameters: patient reference or identifier search.
        query_params: dict[str, str] = {"identifier": patient_identifier.strip()}

        # For non-Patient resources the standard FHIR search parameter is
        # "patient" (or "subject" for Observation/DiagnosticReport).
        if resource_type != "Patient":
            if resource_type in {"Observation", "DiagnosticReport"}:
                query_params = {"subject": patient_identifier.strip()}
            else:
                query_params = {"patient": patient_identifier.strip()}

        # Merge caller-supplied extra params (never override system params).
        extra_params = filters.get("params", {})
        if isinstance(extra_params, dict):
            for key, value in extra_params.items():
                if isinstance(key, str) and key.strip() and key not in query_params:
                    query_params[key] = str(value)

        transport = self._get_transport()
        headers = _bearer_headers(self._bearer_token)

        try:
            response = transport.request(
                endpoint_url=self._endpoint_url,
                path=path,
                params=query_params,
                headers=headers,
            )
        except Exception as exc:
            _transport_error_to_provider(exc)

        self._raise_for_http_error(response.status_code)

        payload = _parse_json_body(response.body)

        try:
            bundle = validate_search_bundle(payload, resource_type)
        except FHIRContractError as exc:
            raise HIEFailClosedError(
                f"FHIR R4 {resource_type} search response failed contract check: {exc}"
            ) from exc

        entries = bundle.get("entry", [])
        records = [entry["resource"] for entry in entries]

        external_reference: Optional[str] = None
        if records:
            external_reference = str(records[0].get("id", ""))

        return {
            "status_code": response.status_code,
            "external_reference": external_reference or None,
            "records": records,
        }

    # ------------------------------------------------------------------
    # Write operations (not supported by this read-only provider)
    # ------------------------------------------------------------------

    def submit_patient(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise HIEFailClosedError(
            "FHIR R4 provider does not support patient submission "
            "(read-only outbound contract)"
        )

    def submit_clinical_data(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise HIEFailClosedError(
            "FHIR R4 provider does not support clinical data submission "
            "(read-only outbound contract)"
        )

    def submit_clinical_document(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise HIEFailClosedError(
            "FHIR R4 provider does not support document submission "
            "(read-only outbound contract)"
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_transport(self) -> HIEHTTPTransport:
        """Return the transport, constructing it lazily on first use."""
        if self._transport is not None:
            return self._transport

        # Extract the allowed host from the validated endpoint URL at
        # construction time so it stays in sync with the endpoint.
        from urllib.parse import urlsplit

        parts = urlsplit(self._endpoint_url)
        hostname = parts.hostname
        if not hostname:
            raise HIEFailClosedError(
                "FHIR R4 provider endpoint URL is missing a hostname"
            )

        self._transport = HIEHTTPTransport(
            allowed_hosts=[hostname],
            connect_timeout=5.0,
            read_timeout=15.0,
        )
        return self._transport

    @staticmethod
    def _raise_for_http_error(status_code: int) -> None:
        """Map HTTP status codes to provider exceptions."""
        if 200 <= status_code <= 299:
            return
        if status_code in {408, 425, 429} or status_code >= 500:
            raise HIERetryableError(
                f"FHIR R4 server returned retryable HTTP {status_code}"
            )
        raise HIEProviderHTTPError(
            status_code,
            f"FHIR R4 server returned HTTP {status_code}",
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def _fhir_r4_factory(endpoint: Optional[str]) -> FHIRR4Provider:
    """Factory function passed to :func:`register_provider`."""
    if not endpoint or not endpoint.strip():
        raise HIEFailClosedError(
            f'HIE provider "{PROVIDER_NAME}" requires an endpoint URL. '
            "Set HIEIntegration.endpoint_url to the FHIR R4 base URL "
            "(e.g. https://fhir.example.org/fhir/r4)."
        )
    return FHIRR4Provider(endpoint_url=endpoint)


def register_fhir_r4_provider() -> None:
    """Register the FHIR R4 provider in the global HIE provider registry.

    Safe to call multiple times – subsequent calls are no-ops because
    :func:`~app.modules.hie.providers.registry.register_provider` overwrites
    the previous factory with an identical one.

    Call this once inside ``create_app()`` after extensions are initialised::

        from app.modules.hie.providers.fhir_r4_provider import (
            register_fhir_r4_provider,
        )
        register_fhir_r4_provider()
    """
    register_provider(PROVIDER_NAME, _fhir_r4_factory)
