from __future__ import annotations

from flask import Blueprint, abort, jsonify, request
from flask_jwt_extended import get_jwt_identity

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.core.utils.decorators import get_current_clinic_id, role_required
from app.extensions import db

from app.modules.hie.schemas.hie_schema import (
    HIEClinicalDataQuerySchema,
    HIEIntegrationCreateSchema,
    HIEIntegrationResponseSchema,
    HIEIntegrationUpdateSchema,
    HIEPatientQuerySchema,
    HIESubmissionListResponseSchema,
    HIESubmissionQuerySchema,
    HIESubmissionResponseSchema,
)

from app.modules.hie.providers.registry import is_provider_registered
from app.modules.hie.services.hie_service import (
    hie_partner_release_held,
    create_hie_integration,
    get_hie_integration,
    list_hie_submissions,
    query_clinical_data,
    query_patient,
    update_hie_integration,
)


hie_bp = Blueprint(
    "hie",
    __name__,
    url_prefix="/hie",
)


HIE_ADMIN_ROLES = (
    Role.ADMIN,
)

HIE_VIEW_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.LAB_TECHNICIAN,
    Role.PHARMACIST,
)


def _get_current_user() -> User:
    identity = get_jwt_identity()

    if (
        isinstance(identity, bool)
        or identity is None
    ):
        raise ValidationError(
            "Invalid authentication identity"
        )

    try:
        user_id = int(identity)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(
            "Invalid authentication identity"
        ) from exc

    if user_id <= 0:
        raise ValidationError(
            "Invalid authentication identity"
        )

    user = db.session.get(
        User,
        user_id,
    )

    if user is None:
        raise ValidationError(
            "Authenticated user could not be resolved"
        )

    if not user.is_active:
        raise ValidationError(
            "User account is inactive"
        )

    return user


def _get_current_clinic_id(roles) -> int:
    clinic_id = get_current_clinic_id(required=True)
    if hie_partner_release_held():
        # Existing JWT decorator stays in place; recheck the live account
        # against the SAME role allowlist and server-derived clinic context.
        user = _get_current_user()
        if user.role not in roles or user.clinic_id != clinic_id:
            abort(403, description="Insufficient HIE permissions")
    return clinic_id


def _serialize_integration(integration):
    record = HIEIntegrationResponseSchema.model_validate(
        integration
    ).model_dump(mode="json")
    if hie_partner_release_held():
        record["readiness"] = {
            "provider_registered": is_provider_registered(integration.provider),
            "external_access_allowed": False,
            "reason_code": "HIE_PARTNER_APPROVAL_REQUIRED",
        }
    return record


def _submission_metadata(submission):
    if not hie_partner_release_held():
        return HIESubmissionResponseSchema.model_validate(submission)
    # Do not deserialize raw historical payloads for monitoring. Keep the
    # existing response shape; null marks fields withheld, not deleted.
    withheld = {"request_data", "response_data", "external_reference", "error_message"}
    data = {
        field: None if field in withheld else getattr(submission, field)
        for field in HIESubmissionResponseSchema.model_fields
    }
    return HIESubmissionResponseSchema.model_validate(data)


@hie_bp.post("/integrations")
@role_required(*HIE_ADMIN_ROLES)
def create_integration():
    payload = HIEIntegrationCreateSchema.model_validate(
        request.get_json(silent=True) or {}
    )

    clinic_id = _get_current_clinic_id(HIE_ADMIN_ROLES)

    integration = create_hie_integration(
        clinic_id=clinic_id,
        provider=payload.provider,
        endpoint_url=(
            str(payload.endpoint_url)
            if payload.endpoint_url is not None
            else None
        ),
        organization_id=payload.organization_id,
        facility_id=payload.facility_id,
    )

    return jsonify({
        "success": True,
        "data": _serialize_integration(integration),
    }), 201


@hie_bp.get("/integrations/<int:integration_id>")
@role_required(*HIE_VIEW_ROLES)
def get_integration(
    integration_id: int,
):
    clinic_id = _get_current_clinic_id(HIE_VIEW_ROLES)

    integration = get_hie_integration(
        clinic_id=clinic_id,
        integration_id=integration_id,
    )

    return jsonify({
        "success": True,
        "data": _serialize_integration(integration),
    }), 200


@hie_bp.patch("/integrations/<int:integration_id>")
@role_required(*HIE_ADMIN_ROLES)
def update_integration(
    integration_id: int,
):
    payload = HIEIntegrationUpdateSchema.model_validate(
        request.get_json(silent=True) or {}
    )

    clinic_id = _get_current_clinic_id(HIE_ADMIN_ROLES)

    integration = update_hie_integration(
        clinic_id=clinic_id,
        integration_id=integration_id,
        provider=payload.provider,
        status=payload.status,
        endpoint_url=(
            str(payload.endpoint_url)
            if payload.endpoint_url is not None
            else None
        ),
        organization_id=payload.organization_id,
        facility_id=payload.facility_id,
    )

    return jsonify({
        "success": True,
        "data": _serialize_integration(integration),
    }), 200


@hie_bp.post("/queries/patient")
@role_required(*HIE_VIEW_ROLES)
def patient_query():
    payload = HIEPatientQuerySchema.model_validate(
        request.get_json(silent=True) or {}
    )

    user = _get_current_user()

    clinic_id = _get_current_clinic_id(HIE_VIEW_ROLES)

    result = query_patient(
        clinic_id=clinic_id,
        requesting_user_id=user.id,
        purpose_of_use=payload.purpose_of_use,
        patient_identifier=payload.patient_identifier,
        integration_id=payload.integration_id,
    )

    return jsonify({
        "success": True,
        "data": result,
    }), 200


@hie_bp.post("/queries/clinical-data")
@role_required(*HIE_VIEW_ROLES)
def clinical_data_query():
    payload = HIEClinicalDataQuerySchema.model_validate(
        request.get_json(silent=True) or {}
    )

    user = _get_current_user()

    clinic_id = _get_current_clinic_id(HIE_VIEW_ROLES)

    result = query_clinical_data(
        clinic_id=clinic_id,
        requesting_user_id=user.id,
        purpose_of_use=payload.purpose_of_use,
        patient_identifier=payload.patient_identifier,
        filters=payload.filters,
        integration_id=payload.integration_id,
    )

    return jsonify({
        "success": True,
        "data": result,
    }), 200


@hie_bp.get("/submissions")
@role_required(*HIE_VIEW_ROLES)
def get_submissions():
    payload = HIESubmissionQuerySchema.model_validate_strings(
        request.args.to_dict()
    )

    clinic_id = _get_current_clinic_id(HIE_VIEW_ROLES)

    pagination = list_hie_submissions(
        clinic_id=clinic_id,
        integration_id=payload.integration_id,
        patient_id=payload.patient_id,
        operation=payload.operation,
        status=payload.status,
        page=payload.page,
        per_page=payload.per_page,
    )

    response = HIESubmissionListResponseSchema(
        items=[
            _submission_metadata(submission)
            for submission in pagination.items
        ],
        total=pagination.total,
        page=pagination.page,
        per_page=pagination.per_page,
    )

    return jsonify({
        "success": True,
        "data": response.model_dump(mode="json"),
    }), 200