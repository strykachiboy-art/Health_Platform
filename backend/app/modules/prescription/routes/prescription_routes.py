from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.core.utils.decorators import (
    active_staff_required,
    emergency_aware_role_required,
    get_current_clinic_id,
    role_required,
)
from app.core.emergency_access.services.emergency_access_service import (
    assert_emergency_access,
)
from app.extensions import db
from app.modules.prescription.schemas.prescription_schema import (
    DrugInteractionCheckSchema,
    DrugInteractionCreateSchema,
    PrescriptionCancelSchema,
    PrescriptionCreateSchema,
    PrescriptionListQuerySchema,
)
from app.modules.prescription.services.prescription_service import (
    cancel_prescription,
    check_interactions,
    complete_prescription,
    create_drug_interaction,
    create_prescription,
    get_prescription,
    list_prescriptions_for_patient,
)
from app.modules.staff.models.staff_model import Staff


prescription_bp = Blueprint(
    "prescription",
    __name__,
    url_prefix="/prescriptions",
)


PRESCRIPTION_VIEW_ROLES = (
    Role.DOCTOR,
    Role.PHARMACIST,
)

PRESCRIPTION_WRITE_ROLES = (
    Role.DOCTOR,
)

PRESCRIPTION_LIFECYCLE_ROLES = (
    Role.DOCTOR,
    Role.PHARMACIST,
)

DRUG_INTERACTION_MANAGEMENT_ROLES = (
    Role.ADMIN,
)


# ============================================================================
# ROUTE HELPERS
# ============================================================================

def _json_body() -> dict:
    payload = request.get_json(
        silent=True
    )

    if payload is None:
        return {}

    if not isinstance(payload, dict):
        raise ValidationError(
            "JSON body must be an object"
        )

    return payload


def _get_current_user() -> User:
    """
    Return the authenticated active user.
    """
    identity = get_jwt_identity()

    try:
        user_id = int(identity)
    except (TypeError, ValueError):
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


def _get_current_clinic_id() -> int:
    return get_current_clinic_id(required=True)


def _get_current_staff() -> Staff:
    """
    Resolve the Staff record belonging to the authenticated user.

    The authenticated doctor is always used as the prescriber.
    """
    user = _get_current_user()
    clinic_id = _get_current_clinic_id()

    staff = (
        Staff.query
        .filter(
            Staff.user_id == user.id,
            Staff.clinic_id == clinic_id,
        )
        .first()
    )

    if staff is None:
        raise ValidationError(
            "Authenticated user is not associated with a staff record"
        )

    return staff


def _ensure_prescription_clinic(
    prescription,
    clinic_id: int,
) -> None:
    """
    Prevent cross-clinic prescription access.

    Historical reads are allowed, including for inactive clinics,
    but a prescription must still belong to the authenticated clinic.
    """
    if prescription.clinic_id != clinic_id:
        raise ValidationError(
            f"Prescription {prescription.id} does not belong "
            f"to clinic {clinic_id}"
        )


# ============================================================================
# SERIALIZERS
# ============================================================================

def _serialize_medication_numeric(
    value,
):
    if value is None:
        return None

    return float(value)


def _serialize_prescription_item(
    item,
) -> dict:
    start_at = getattr(
        item,
        "start_at",
        None,
    )

    end_at = getattr(
        item,
        "end_at",
        None,
    )

    ordered_at = getattr(
        item,
        "ordered_at",
        None,
    )

    return {
        "id": item.id,
        "prescription_id": item.prescription_id,
        "drug_id": item.drug_id,
        "dosage": item.dosage,
        "frequency": item.frequency,
        "duration": item.duration,
        "quantity": item.quantity,
        "instructions": item.instructions,
        "dose_value": _serialize_medication_numeric(
            getattr(
                item,
                "dose_value",
                None,
            )
        ),
        "dose_unit": getattr(
            item,
            "dose_unit",
            None,
        ),
        "route_code": getattr(
            item,
            "route_code",
            None,
        ),
        "route_display": getattr(
            item,
            "route_display",
            None,
        ),
        "frequency_code": getattr(
            item,
            "frequency_code",
            None,
        ),
        "frequency_interval": getattr(
            item,
            "frequency_interval",
            None,
        ),
        "frequency_interval_unit": getattr(
            item,
            "frequency_interval_unit",
            None,
        ),
        "scheduled_times": list(
            getattr(
                item,
                "scheduled_times",
                None,
            )
            or []
        ),
        "start_at": (
            start_at.isoformat()
            if start_at
            else None
        ),
        "end_at": (
            end_at.isoformat()
            if end_at
            else None
        ),
        "is_prn": bool(
            getattr(
                item,
                "is_prn",
                False,
            )
        ),
        "prn_reason": getattr(
            item,
            "prn_reason",
            None,
        ),
        "max_single_dose": _serialize_medication_numeric(
            getattr(
                item,
                "max_single_dose",
                None,
            )
        ),
        "max_daily_dose": _serialize_medication_numeric(
            getattr(
                item,
                "max_daily_dose",
                None,
            )
        ),
        "administration_instructions": getattr(
            item,
            "administration_instructions",
            None,
        ),
        "order_priority": (
            getattr(
                item,
                "order_priority",
                None,
            )
            or "routine"
        ),
        "order_type": (
            getattr(
                item,
                "order_type",
                None,
            )
            or "outpatient"
        ),
        "ordered_at": (
            ordered_at.isoformat()
            if ordered_at
            else None
        ),
    }

def _serialize_prescription(
    prescription,
) -> dict:
    return {
        "id": prescription.id,
        "clinic_id": prescription.clinic_id,
        "patient_id": prescription.patient_id,
        "consultation_id": prescription.consultation_id,
        "prescribed_by_id": prescription.prescribed_by_id,
        "status": prescription.status.value,
        "notes": prescription.notes,
        "issued_at": (
            prescription.issued_at.isoformat()
            if prescription.issued_at
            else None
        ),
        "expires_at": (
            prescription.expires_at.isoformat()
            if prescription.expires_at
            else None
        ),
        "items": [
            _serialize_prescription_item(
                item
            )
            for item in prescription.items
        ],
    }


def _serialize_drug_interaction(
    interaction,
) -> dict:
    return {
        "id": interaction.id,
        "drug_a_id": interaction.drug_a_id,
        "drug_b_id": interaction.drug_b_id,
        "severity": interaction.severity.value,
        "description": interaction.description,
    }


def _serialize_paginated_prescriptions(
    result: dict,
) -> dict:
    return {
        "items": [
            _serialize_prescription(
                prescription
            )
            for prescription in result["items"]
        ],
        "total": result["total"],
        "page": result["page"],
        "per_page": result["per_page"],
    }


# ============================================================================
# PRESCRIPTION ROUTES
# ============================================================================

@prescription_bp.post("")
@role_required(
    *PRESCRIPTION_WRITE_ROLES
)
@active_staff_required
def create_prescription_route():
    clinic_id = _get_current_clinic_id()
    staff = _get_current_staff()

    payload = PrescriptionCreateSchema.model_validate(
        _json_body()
    )

    idempotency_key = request.headers.get(
        "Idempotency-Key"
    )

    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()

        if not idempotency_key:
            return jsonify(
                {
                    "success": False,
                    "error": "Idempotency-Key cannot be blank",
                }
            ), 400

    prescription_kwargs = {
        "clinic_id": clinic_id,
        "patient_id": payload.patient_id,
        "prescribed_by_id": staff.id,
        "items": [
            item.model_dump(
                exclude_none=True,
                exclude_defaults=True,
            )
            for item in payload.items
        ],
        "consultation_id": payload.consultation_id,
        "expires_at": payload.expires_at,
        "notes": payload.notes,
    }

    if idempotency_key is not None:
        prescription_kwargs.update(
            {
                "idempotency_key": idempotency_key,
                "idempotency_user_id": staff.user_id,
            }
        )

    prescription, warnings = create_prescription(
        **prescription_kwargs
    )
    return jsonify(
        {
            "success": True,
            "message": "Prescription created successfully",
            "data": _serialize_prescription(
                prescription
            ),
            "interaction_warnings": warnings,
        }
    ), 201


@prescription_bp.get(
    "/<int:prescription_id>"
)
@emergency_aware_role_required(
    *PRESCRIPTION_VIEW_ROLES
)
@active_staff_required
def get_prescription_route(
    prescription_id: int,
):
    clinic_id = _get_current_clinic_id()

    prescription = get_prescription(
        prescription_id
    )

    _ensure_prescription_clinic(
        prescription=prescription,
        clinic_id=clinic_id,
    )

    emergency_access_header = request.headers.get(
        "X-Emergency-Access-ID"
    )
    if emergency_access_header is not None:
        try:
            emergency_access_id = int(
                emergency_access_header
            )
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "message": "Emergency access is not authorized",
            }), 403

        if emergency_access_id <= 0:
            return jsonify({
                "success": False,
                "message": "Emergency access is not authorized",
            }), 403

        if not assert_emergency_access(
            actor_id=_get_current_user().id,
            patient_id=prescription.patient_id,
            resource_type="prescription",
            resource_id=prescription.id,
            action="read",
            emergency_access_id=emergency_access_id,
        ):
            return jsonify({
                "success": False,
                "message": "Emergency access is not authorized",
            }), 403

    return jsonify(
        {
            "success": True,
            "data": _serialize_prescription(
                prescription
            ),
        }
    ), 200


@prescription_bp.get(
    "/patients/<int:patient_id>"
)
@emergency_aware_role_required(
    *PRESCRIPTION_VIEW_ROLES
)
@active_staff_required
def list_patient_prescriptions_route(
    patient_id: int,
):
    clinic_id = _get_current_clinic_id()

    query = PrescriptionListQuerySchema.model_validate(
        request.args.to_dict()
    )

    emergency_access_header = request.headers.get(
        "X-Emergency-Access-ID"
    )
    if emergency_access_header is not None:
        try:
            emergency_access_id = int(emergency_access_header)
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "message": "Emergency access is not authorized",
            }), 403

        if emergency_access_id <= 0:
            return jsonify({
                "success": False,
                "message": "Emergency access is not authorized",
            }), 403

        if not assert_emergency_access(
            actor_id=_get_current_user().id,
            patient_id=patient_id,
            resource_type="prescription_list",
            resource_id=patient_id,
            action="read",
            emergency_access_id=emergency_access_id,
        ):
            return jsonify({
                "success": False,
                "message": "Emergency access is not authorized",
            }), 403

    result = list_prescriptions_for_patient(
        patient_id=patient_id,
        clinic_id=clinic_id,
        active_only=query.active_only,
        page=query.page,
        per_page=query.per_page,
    )

    return jsonify(
        {
            "success": True,
            "data": _serialize_paginated_prescriptions(
                result
            ),
        }
    ), 200


@prescription_bp.post(
    "/<int:prescription_id>/cancel"
)
@role_required(
    *PRESCRIPTION_WRITE_ROLES
)
@active_staff_required
def cancel_prescription_route(
    prescription_id: int,
):
    clinic_id = _get_current_clinic_id()

    payload = PrescriptionCancelSchema.model_validate(
        _json_body()
    )

    prescription = cancel_prescription(
        prescription_id=prescription_id,
        clinic_id=clinic_id,
        reason=payload.reason,
    )

    return jsonify(
        {
            "success": True,
            "message": "Prescription cancelled successfully",
            "data": _serialize_prescription(
                prescription
            ),
        }
    ), 200


@prescription_bp.post(
    "/<int:prescription_id>/complete"
)
@role_required(
    *PRESCRIPTION_LIFECYCLE_ROLES
)
@active_staff_required
def complete_prescription_route(
    prescription_id: int,
):
    clinic_id = _get_current_clinic_id()

    prescription = complete_prescription(
        prescription_id=prescription_id,
        clinic_id=clinic_id,
    )

    return jsonify(
        {
            "success": True,
            "message": "Prescription completed successfully",
            "data": _serialize_prescription(
                prescription
            ),
        }
    ), 200


# ============================================================================
# DRUG INTERACTION ROUTES
# ============================================================================

@prescription_bp.post(
    "/interactions/check"
)
@role_required(
    *PRESCRIPTION_VIEW_ROLES
)
@active_staff_required
def check_drug_interactions_route():
    clinic_id = _get_current_clinic_id()

    payload = DrugInteractionCheckSchema.model_validate(
        _json_body()
    )

    interactions = check_interactions(
        drug_ids=payload.drug_ids,
        clinic_id=clinic_id,
    )

    return jsonify(
        {
            "success": True,
            "data": {
                "drug_ids": payload.drug_ids,
                "has_interactions": bool(
                    interactions
                ),
                "interaction_warnings": interactions,
            },
        }
    ), 200


@prescription_bp.post(
    "/interactions"
)
@role_required(
    *DRUG_INTERACTION_MANAGEMENT_ROLES
)
@active_staff_required
def create_drug_interaction_route():
    payload = DrugInteractionCreateSchema.model_validate(
        _json_body()
    )

    interaction = create_drug_interaction(
        drug_a_id=payload.drug_a_id,
        drug_b_id=payload.drug_b_id,
        severity=payload.severity,
        description=payload.description,
    )

    return jsonify(
        {
            "success": True,
            "message": "Drug interaction created successfully",
            "data": _serialize_drug_interaction(
                interaction
            ),
        }
    ), 201