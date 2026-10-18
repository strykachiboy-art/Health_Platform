from datetime import datetime, timezone
from typing import Any, Optional

from flask import current_app, has_app_context

from app.config import Config
from app.core.auth.user.models.user_model import User
from app.core.enums.hie_enums import (
    HIEIntegrationStatus,
    HIEOperation,
    HIEPurposeOfUse,
    HIESubmissionStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.hie.models.hie_model import (
    HIEIntegration,
    HIESubmission,
)
from app.modules.hie.providers.base import HIEProvider
from app.modules.hie.providers.exceptions import (
    HIEFailClosedError,
    HIEProviderHTTPError,
    classify_hie_failure,
)
from app.modules.hie.providers.endpoint_policy import (
    HIEEndpointPolicyError,
    validate_hie_endpoint_url,
)
from app.modules.hie.providers.registry import (
    get_provider,
    is_provider_registered,
)
from app.modules.patient.models.patient_model import Patient


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


HIE_QUERY_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.LAB_TECHNICIAN,
    Role.PHARMACIST,
)


def _validate_configured_hie_endpoint_url(
    endpoint_url: Optional[str],
) -> Optional[str]:
    if endpoint_url is None:
        return None

    if has_app_context():
        allowed_hosts = current_app.config.get(
            "HIE_ALLOWED_ENDPOINT_HOSTS"
        )
    else:
        allowed_hosts = Config.HIE_ALLOWED_ENDPOINT_HOSTS

    if isinstance(allowed_hosts, str):
        allowed_hosts = tuple(
            host.strip()
            for host in allowed_hosts.split(",")
        )

    try:
        return validate_hie_endpoint_url(
            endpoint_url,
            allowed_hosts=allowed_hosts,
        )
    except HIEEndpointPolicyError as exc:
        raise ValidationError(
            "HIE endpoint does not satisfy the configured "
            "outbound endpoint policy"
        ) from exc


def _validate_positive_int(
    value: Any,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValidationError(
            f"Invalid {field_name}"
        )

    return value


def _get_hie_requesting_user(
    *,
    clinic_id: int,
    requesting_user_id: int,
) -> User:
    _validate_positive_int(
        clinic_id,
        "clinic ID",
    )

    _validate_positive_int(
        requesting_user_id,
        "requesting user ID",
    )

    user = db.session.get(
        User,
        requesting_user_id,
    )

    if user is None or not user.is_active:
        raise NotFoundError(
            "Requesting user not found"
        )

    if user.clinic_id != clinic_id:
        raise NotFoundError(
            "Requesting user not found"
        )

    if user.role not in HIE_QUERY_ROLES:
        raise ValidationError(
            "User is not authorized for HIE queries"
        )

    staff = user.staff

    if staff is None:
        raise NotFoundError(
            "Requesting user not found"
        )

    if staff.user_id != user.id:
        raise NotFoundError(
            "Requesting user not found"
        )

    if staff.clinic_id != clinic_id:
        raise NotFoundError(
            "Requesting user not found"
        )

    if staff.status != StaffStatus.ACTIVE:
        raise ValidationError(
            "Staff member is not active"
        )

    return user


def _normalize_hie_purpose(
    purpose_of_use: HIEPurposeOfUse | str,
) -> HIEPurposeOfUse:
    if isinstance(
        purpose_of_use,
        HIEPurposeOfUse,
    ):
        return purpose_of_use

    if not isinstance(
        purpose_of_use,
        str,
    ):
        raise ValidationError(
            "Invalid HIE purpose of use"
        )

    purpose = purpose_of_use.strip().lower()

    if not purpose:
        raise ValidationError(
            "Invalid HIE purpose of use"
        )

    try:
        return HIEPurposeOfUse(
            purpose
        )
    except ValueError as exc:
        raise ValidationError(
            "Invalid HIE purpose of use"
        ) from exc


def _authorize_hie_query(
    *,
    clinic_id: int,
    requesting_user_id: int,
    purpose_of_use: HIEPurposeOfUse | str,
) -> HIEPurposeOfUse:
    _get_hie_requesting_user(
        clinic_id=clinic_id,
        requesting_user_id=requesting_user_id,
    )

    return _normalize_hie_purpose(
        purpose_of_use
    )


def _get_clinic(
    clinic_id: int,
) -> Clinic:
    _validate_positive_int(
        clinic_id,
        "clinic ID",
    )

    clinic = db.session.get(
        Clinic,
        clinic_id,
    )

    if clinic is None:
        raise NotFoundError(
            f"Clinic {clinic_id} not found"
        )

    return clinic


def _get_patient(
    clinic_id: int,
    patient_id: Optional[int],
) -> Optional[Patient]:
    if patient_id is None:
        return None

    _validate_positive_int(
        patient_id,
        "patient ID",
    )

    patient = db.session.get(
        Patient,
        patient_id,
    )

    if patient is None:
        raise NotFoundError(
            f"Patient {patient_id} not found"
        )

    if patient.clinic_id != clinic_id:
        raise ValidationError(
            "Patient does not belong to the clinic"
        )

    return patient


def _get_integration(
    clinic_id: int,
    integration_id: Optional[int] = None,
) -> HIEIntegration:
    _validate_positive_int(
        clinic_id,
        "clinic ID",
    )

    if integration_id is not None:
        _validate_positive_int(
            integration_id,
            "HIE integration ID",
        )

        integration = db.session.get(
            HIEIntegration,
            integration_id,
        )

        if integration is None:
            raise NotFoundError(
                f"HIE integration {integration_id} not found"
            )

        if integration.clinic_id != clinic_id:
            raise ValidationError(
                "HIE integration does not belong to the clinic"
            )

        return integration

    statement = (
        db.select(HIEIntegration)
        .where(
            HIEIntegration.clinic_id == clinic_id,
            HIEIntegration.status
            == HIEIntegrationStatus.ACTIVE,
        )
        .order_by(
            HIEIntegration.id.asc()
        )
        .limit(2)
    )

    integrations = (
        db.session.execute(statement)
        .scalars()
        .all()
    )

    if not integrations:
        raise ValidationError(
            "No active HIE integration is configured "
            "for this clinic"
        )

    if len(integrations) > 1:
        raise ValidationError(
            "Multiple active HIE integrations are configured; "
            "integration ID is required"
        )

    return integrations[0]


def _validate_integration(
    integration: HIEIntegration,
) -> None:
    if integration is None:
        raise ValidationError(
            "HIE integration is required"
        )

    if integration.status != HIEIntegrationStatus.ACTIVE:
        raise ValidationError(
            "HIE integration is not active: "
            f"{integration.status.value}"
        )

    provider = (
        integration.provider or ""
    ).strip().lower()

    if not provider:
        raise ValidationError(
            "HIE integration provider is not configured"
        )

    _validate_configured_hie_endpoint_url(
        getattr(integration, "endpoint_url", None)
    )


def _get_provider(
    integration: HIEIntegration,
) -> HIEProvider:
    _validate_integration(
        integration
    )

    provider_name = (
        integration.provider or ""
    ).strip().lower()

    endpoint_url = _validate_configured_hie_endpoint_url(
        getattr(integration, "endpoint_url", None)
    )

    return get_provider(
        provider_name,
        endpoint=endpoint_url,
    )


def _validate_patient_identifier(
    patient_identifier: str,
) -> str:
    if patient_identifier is None:
        raise ValidationError(
            "Patient identifier is required"
        )

    if not isinstance(
        patient_identifier,
        str,
    ):
        raise ValidationError(
            "Patient identifier must be a string"
        )

    patient_identifier = patient_identifier.strip()

    if not patient_identifier:
        raise ValidationError(
            "Patient identifier is required"
        )

    return patient_identifier


def _validate_request_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(
        payload,
        dict,
    ):
        raise ValidationError(
            "HIE request payload must be an object"
        )

    return payload


def _validate_submission_context(
    *,
    integration: HIEIntegration,
    clinic_id: int,
    patient_id: Optional[int],
) -> None:
    if integration is None:
        raise ValidationError(
            "HIE integration is required"
        )

    if integration.clinic_id != clinic_id:
        raise ValidationError(
            "HIE integration does not belong to the clinic"
        )

    if patient_id is not None:
        _get_patient(
            clinic_id,
            patient_id,
        )


def _validate_pagination(
    page: int,
    per_page: int,
) -> tuple[int, int]:
    if (
        isinstance(page, bool)
        or not isinstance(page, int)
    ):
        raise ValidationError(
            "Page must be an integer"
        )

    if (
        isinstance(per_page, bool)
        or not isinstance(per_page, int)
    ):
        raise ValidationError(
            "per_page must be an integer"
        )

    if page < 1:
        raise ValidationError(
            "Page must be greater than or equal to 1"
        )

    if per_page < 1 or per_page > 100:
        raise ValidationError(
            "per_page must be between 1 and 100"
        )

    return page, per_page


@transactional
def _create_submission(
    *,
    integration_id: int,
    clinic_id: int,
    patient_id: Optional[int],
    operation: HIEOperation,
    request_data: Optional[dict[str, Any]],
) -> int:
    _validate_positive_int(
        clinic_id,
        "clinic ID",
    )

    _validate_positive_int(
        integration_id,
        "HIE integration ID",
    )

    integration = db.session.get(
        HIEIntegration,
        integration_id,
    )

    if integration is None:
        raise NotFoundError(
            f"HIE integration {integration_id} not found"
        )

    _validate_submission_context(
        integration=integration,
        clinic_id=clinic_id,
        patient_id=patient_id,
    )

    if not isinstance(
        operation,
        HIEOperation,
    ):
        raise ValidationError(
            "Invalid HIE operation"
        )

    if request_data is not None and not isinstance(
        request_data,
        dict,
    ):
        raise ValidationError(
            "HIE request data must be an object"
        )

    submission = HIESubmission(
        integration_id=integration.id,
        clinic_id=clinic_id,
        patient_id=patient_id,
        operation=operation,
        status=HIESubmissionStatus.PENDING,
        request_data=request_data,
        retry_count=0,
    )

    db.session.add(
        submission
    )
    db.session.flush()

    return submission.id


def _validate_provider_response(
    response: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(
        response,
        dict,
    ):
        raise HIEFailClosedError(
            "HIE provider response must be an object"
        )

    if not response:
        raise HIEFailClosedError(
            "HIE provider response was empty"
        )

    status_code = response.get(
        "status_code"
    )

    if (
        isinstance(status_code, bool)
        or not isinstance(status_code, int)
        or status_code < 100
        or status_code > 599
    ):
        raise HIEFailClosedError(
            "HIE provider response status_code is invalid"
        )

    if not 200 <= status_code <= 299:
        raise HIEProviderHTTPError(
            status_code
        )

    payload_fields = {
        key: value
        for key, value in response.items()
        if key != "status_code"
    }

    if not payload_fields:
        raise HIEFailClosedError(
            "HIE provider response payload is missing"
        )

    external_reference = response.get(
        "external_reference"
    )

    if external_reference is not None and (
        not isinstance(
            external_reference,
            str,
        )
        or not external_reference.strip()
    ):
        raise HIEFailClosedError(
            "HIE provider response external_reference is invalid"
        )

    return response

@transactional
def _mark_submission_success(
    submission_id: int,
    response: dict[str, Any],
) -> None:
    _validate_positive_int(
        submission_id,
        "HIE submission ID",
    )

    submission = db.session.get(
        HIESubmission,
        submission_id,
    )

    if submission is None:
        raise NotFoundError(
            f"HIE submission {submission_id} not found"
        )

    try:
        _validate_provider_response(
            response
        )
    except HIEFailClosedError as exc:
        raise ValidationError(
            str(exc)
        ) from exc

    submission.status = (
        HIESubmissionStatus.SUCCESS
    )
    submission.failure_class = None
    submission.response_data = response
    submission.status_code = response.get(
        "status_code"
    )
    submission.external_reference = response.get(
        "external_reference"
    )
    submission.error_message = None
    submission.submitted_at = _utcnow()


@transactional
def _mark_submission_failure(
    submission_id: int,
    error: Exception,
) -> None:
    _validate_positive_int(
        submission_id,
        "HIE submission ID",
    )

    submission = db.session.get(
        HIESubmission,
        submission_id,
    )

    if submission is None:
        raise NotFoundError(
            f"HIE submission {submission_id} not found"
        )

    submission.status = (
        HIESubmissionStatus.FAILED
    )

    submission.failure_class = classify_hie_failure(
        error
    )
    submission.error_message = (
        "HIE provider operation failed"
    )

    submission.retry_count = (
        submission.retry_count or 0
    ) + 1

    submission.submitted_at = _utcnow()


@transactional
def _update_last_sync(
    integration_id: int,
) -> None:
    _validate_positive_int(
        integration_id,
        "HIE integration ID",
    )

    integration = db.session.get(
        HIEIntegration,
        integration_id,
    )

    if integration is None:
        raise NotFoundError(
            f"HIE integration {integration_id} not found"
        )

    integration.last_sync_at = _utcnow()


def submit_patient(
    *,
    clinic_id: int,
    patient_id: int,
    payload: dict[str, Any],
    integration_id: Optional[int] = None,
) -> dict[str, Any]:
    _get_clinic(
        clinic_id
    )

    _get_patient(
        clinic_id,
        patient_id,
    )

    payload = _validate_request_payload(
        payload
    )

    integration = _get_integration(
        clinic_id,
        integration_id,
    )

    _validate_integration(
        integration
    )

    submission_id = _create_submission(
        integration_id=integration.id,
        clinic_id=clinic_id,
        patient_id=patient_id,
        operation=HIEOperation.PATIENT_SUBMISSION,
        request_data=payload,
    )

    provider = _get_provider(
        integration
    )

    try:
        response = provider.submit_patient(
            payload
        )
        _validate_provider_response(
            response
        )

    except Exception as exc:
        _mark_submission_failure(
            submission_id,
            exc,
        )
        raise

    _mark_submission_success(
        submission_id,
        response,
    )

    _update_last_sync(
        integration.id
    )

    return response


def submit_clinical_data(
    *,
    clinic_id: int,
    patient_id: int,
    payload: dict[str, Any],
    integration_id: Optional[int] = None,
) -> dict[str, Any]:
    _get_clinic(
        clinic_id
    )

    _get_patient(
        clinic_id,
        patient_id,
    )

    payload = _validate_request_payload(
        payload
    )

    integration = _get_integration(
        clinic_id,
        integration_id,
    )

    _validate_integration(
        integration
    )

    submission_id = _create_submission(
        integration_id=integration.id,
        clinic_id=clinic_id,
        patient_id=patient_id,
        operation=HIEOperation.CLINICAL_DATA_SUBMISSION,
        request_data=payload,
    )

    provider = _get_provider(
        integration
    )

    try:
        response = provider.submit_clinical_data(
            payload
        )
        _validate_provider_response(
            response
        )

    except Exception as exc:
        _mark_submission_failure(
            submission_id,
            exc,
        )
        raise

    _mark_submission_success(
        submission_id,
        response,
    )

    _update_last_sync(
        integration.id
    )

    return response


def submit_clinical_document(
    *,
    clinic_id: int,
    patient_id: int,
    payload: dict[str, Any],
    integration_id: Optional[int] = None,
) -> dict[str, Any]:
    _get_clinic(
        clinic_id
    )

    _get_patient(
        clinic_id,
        patient_id,
    )

    payload = _validate_request_payload(
        payload
    )

    integration = _get_integration(
        clinic_id,
        integration_id,
    )

    _validate_integration(
        integration
    )

    submission_id = _create_submission(
        integration_id=integration.id,
        clinic_id=clinic_id,
        patient_id=patient_id,
        operation=HIEOperation.CLINICAL_DOCUMENT_SUBMISSION,
        request_data=payload,
    )

    provider = _get_provider(
        integration
    )

    try:
        response = provider.submit_clinical_document(
            payload
        )
        _validate_provider_response(
            response
        )

    except Exception as exc:
        _mark_submission_failure(
            submission_id,
            exc,
        )
        raise

    _mark_submission_success(
        submission_id,
        response,
    )

    _update_last_sync(
        integration.id
    )

    return response


def query_patient(
    *,
    clinic_id: int,
    requesting_user_id: int,
    purpose_of_use: HIEPurposeOfUse | str,
    patient_identifier: str,
    integration_id: Optional[int] = None,
) -> dict[str, Any]:
    _get_clinic(
        clinic_id
    )

    purpose_of_use = _authorize_hie_query(
        clinic_id=clinic_id,
        requesting_user_id=requesting_user_id,
        purpose_of_use=purpose_of_use,
    )

    patient_identifier = _validate_patient_identifier(
        patient_identifier
    )

    integration = _get_integration(
        clinic_id,
        integration_id,
    )

    _validate_integration(
        integration
    )

    provider = _get_provider(
        integration
    )

    submission_id = _create_submission(
        integration_id=integration.id,
        clinic_id=clinic_id,
        patient_id=None,
        operation=HIEOperation.PATIENT_QUERY,
        request_data={
            "patient_identifier": patient_identifier,
            "purpose_of_use": purpose_of_use.value,
            "requesting_user_id": requesting_user_id,
        },
    )

    try:
        response = provider.query_patient(
            patient_identifier
        )
        _validate_provider_response(
            response
        )

    except Exception as exc:
        _mark_submission_failure(
            submission_id,
            exc,
        )
        raise

    _mark_submission_success(
        submission_id,
        response,
    )

    _update_last_sync(
        integration.id
    )

    return response


def query_clinical_data(
    *,
    clinic_id: int,
    requesting_user_id: int,
    purpose_of_use: HIEPurposeOfUse | str,
    patient_identifier: str,
    filters: Optional[dict[str, Any]] = None,
    integration_id: Optional[int] = None,
) -> dict[str, Any]:
    _get_clinic(
        clinic_id
    )

    purpose_of_use = _authorize_hie_query(
        clinic_id=clinic_id,
        requesting_user_id=requesting_user_id,
        purpose_of_use=purpose_of_use,
    )

    patient_identifier = _validate_patient_identifier(
        patient_identifier
    )

    if filters is not None and not isinstance(
        filters,
        dict,
    ):
        raise ValidationError(
            "Clinical data filters must be an object"
        )

    integration = _get_integration(
        clinic_id,
        integration_id,
    )

    _validate_integration(
        integration
    )

    provider = _get_provider(
        integration
    )

    request_data = {
        "patient_identifier": patient_identifier,
        "filters": filters,
        "purpose_of_use": purpose_of_use.value,
        "requesting_user_id": requesting_user_id,
    }

    submission_id = _create_submission(
        integration_id=integration.id,
        clinic_id=clinic_id,
        patient_id=None,
        operation=HIEOperation.CLINICAL_DATA_QUERY,
        request_data=request_data,
    )

    try:
        response = provider.query_clinical_data(
            patient_identifier,
            filters,
        )
        _validate_provider_response(
            response
        )

    except Exception as exc:
        _mark_submission_failure(
            submission_id,
            exc,
        )
        raise

    _mark_submission_success(
        submission_id,
        response,
    )

    _update_last_sync(
        integration.id
    )

    return response


def get_hie_integration(
    clinic_id: int,
    integration_id: int,
) -> HIEIntegration:
    _get_clinic(
        clinic_id
    )

    return _get_integration(
        clinic_id,
        integration_id,
    )


@transactional
def create_hie_integration(
    *,
    clinic_id: int,
    provider: str,
    endpoint_url: Optional[str] = None,
    organization_id: Optional[str] = None,
    facility_id: Optional[str] = None,
) -> HIEIntegration:
    _get_clinic(
        clinic_id
    )

    if provider is None:
        raise ValidationError(
            "Provider is required"
        )

    if not isinstance(
        provider,
        str,
    ):
        raise ValidationError(
            "Provider must be a string"
        )

    provider = provider.strip().lower()

    if not provider:
        raise ValidationError(
            "Provider is required"
        )

    if endpoint_url is not None:
        if not isinstance(
            endpoint_url,
            str,
        ):
            endpoint_url = str(
                endpoint_url
            )

        if not endpoint_url.strip():
            raise ValidationError(
                "Endpoint URL cannot be empty"
            )

        endpoint_url = _validate_configured_hie_endpoint_url(
            endpoint_url
        )

    if organization_id is not None:
        if not isinstance(
            organization_id,
            str,
        ):
            raise ValidationError(
                "Organization ID must be a string"
            )

        organization_id = organization_id.strip()

        if not organization_id:
            raise ValidationError(
                "Organization ID cannot be empty"
            )

    if facility_id is not None:
        if not isinstance(
            facility_id,
            str,
        ):
            raise ValidationError(
                "Facility ID must be a string"
            )

        facility_id = facility_id.strip()

        if not facility_id:
            raise ValidationError(
                "Facility ID cannot be empty"
            )

    integration = HIEIntegration(
        clinic_id=clinic_id,
        provider=provider,
        status=HIEIntegrationStatus.PENDING,
        endpoint_url=endpoint_url,
        organization_id=organization_id,
        facility_id=facility_id,
    )

    db.session.add(
        integration
    )
    db.session.flush()

    return integration


@transactional
def update_hie_integration(
    *,
    clinic_id: int,
    integration_id: int,
    provider: Optional[str] = None,
    status: Optional[HIEIntegrationStatus] = None,
    endpoint_url: Optional[str] = None,
    organization_id: Optional[str] = None,
    facility_id: Optional[str] = None,
) -> HIEIntegration:
    integration = _get_integration(
        clinic_id,
        integration_id,
    )

    if provider is not None:
        if not isinstance(
            provider,
            str,
        ):
            raise ValidationError(
                "Provider must be a string"
            )

        provider = provider.strip().lower()

        if not provider:
            raise ValidationError(
                "Provider cannot be empty"
            )

    if status is not None:
        if not isinstance(
            status,
            HIEIntegrationStatus,
        ):
            raise ValidationError(
                "Invalid HIE integration status"
            )

    effective_provider = (
        provider
        if provider is not None
        else integration.provider
    )
    effective_status = (
        status
        if status is not None
        else integration.status
    )

    # An integration may remain pending as an extension point, but it cannot
    # be activated (or switched to another provider while active) unless the
    # current process has registered an implementation for that provider.
    if (
        effective_status == HIEIntegrationStatus.ACTIVE
        and (
            status == HIEIntegrationStatus.ACTIVE
            or provider is not None
        )
        and not is_provider_registered(effective_provider)
    ):
        raise ValidationError(
            "HIE provider implementation is not registered; "
            "integration cannot be activated or switched while active"
        )

    validated_endpoint_url: Optional[str] = None
    if endpoint_url is not None:
        if not isinstance(
            endpoint_url,
            str,
        ):
            endpoint_url = str(
                endpoint_url
            )

        if not endpoint_url.strip():
            raise ValidationError(
                "Endpoint URL cannot be empty"
            )

        validated_endpoint_url = _validate_configured_hie_endpoint_url(
            endpoint_url
        )
    elif (
        effective_status == HIEIntegrationStatus.ACTIVE
        and integration.endpoint_url is not None
    ):
        # An activation or update must not preserve a stale unsafe URL.
        _validate_configured_hie_endpoint_url(
            integration.endpoint_url
        )

    if provider is not None:
        integration.provider = provider

    if status is not None:
        integration.status = status

    if endpoint_url is not None:
        integration.endpoint_url = validated_endpoint_url

    if organization_id is not None:
        if not isinstance(
            organization_id,
            str,
        ):
            raise ValidationError(
                "Organization ID must be a string"
            )

        organization_id = organization_id.strip()

        if not organization_id:
            raise ValidationError(
                "Organization ID cannot be empty"
            )

        integration.organization_id = organization_id

    if facility_id is not None:
        if not isinstance(
            facility_id,
            str,
        ):
            raise ValidationError(
                "Facility ID must be a string"
            )

        facility_id = facility_id.strip()

        if not facility_id:
            raise ValidationError(
                "Facility ID cannot be empty"
            )

        integration.facility_id = facility_id

    return integration


def list_hie_submissions(
    *,
    clinic_id: int,
    integration_id: Optional[int] = None,
    patient_id: Optional[int] = None,
    operation: Optional[HIEOperation] = None,
    status: Optional[HIESubmissionStatus] = None,
    page: int = 1,
    per_page: int = 20,
):
    _get_clinic(
        clinic_id
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    if integration_id is not None:
        _validate_positive_int(
            integration_id,
            "HIE integration ID",
        )

        _get_integration(
            clinic_id,
            integration_id,
        )

    if patient_id is not None:
        _validate_positive_int(
            patient_id,
            "patient ID",
        )

        _get_patient(
            clinic_id,
            patient_id,
        )

    if operation is not None and not isinstance(
        operation,
        HIEOperation,
    ):
        raise ValidationError(
            "Invalid HIE operation"
        )

    if status is not None and not isinstance(
        status,
        HIESubmissionStatus,
    ):
        raise ValidationError(
            "Invalid HIE submission status"
        )

    statement = (
        db.select(HIESubmission)
        .where(
            HIESubmission.clinic_id
            == clinic_id
        )
    )

    if integration_id is not None:
        statement = statement.where(
            HIESubmission.integration_id
            == integration_id
        )

    if patient_id is not None:
        statement = statement.where(
            HIESubmission.patient_id
            == patient_id
        )

    if operation is not None:
        statement = statement.where(
            HIESubmission.operation
            == operation
        )

    if status is not None:
        statement = statement.where(
            HIESubmission.status
            == status
        )

    statement = statement.order_by(
        HIESubmission.created_at.desc(),
        HIESubmission.id.desc(),
    )

    return db.paginate(
        statement,
        page=page,
        per_page=per_page,
        error_out=False,
    )