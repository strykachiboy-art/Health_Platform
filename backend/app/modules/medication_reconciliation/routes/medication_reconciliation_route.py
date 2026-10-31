from __future__ import annotations

from flask import (
    Blueprint,
    jsonify,
    request,
)
from flask_jwt_extended import (
    get_jwt_identity,
)

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.core.utils.decorators import (
    emergency_aware_role_required,
    get_current_clinic_id,
    role_required,
)
from app.core.emergency_access.routes.resource_access import (
    authorize_emergency_read_if_requested,
)
from app.extensions import db
from app.modules.clinical_records.schemas.clinical_record_schema import (
    ClinicalRecordAmendmentResponseSchema,
)
from app.modules.medication_reconciliation.schemas.medication_reconciliation_schema import (
    MedicationReconciliationAmendmentSchema,
    MedicationReconciliationCreateSchema,
    MedicationReconciliationFinalizeSchema,
    MedicationReconciliationResponseSchema,
    MedicationReconciliationUpdateSchema,
)
from app.modules.medication_reconciliation.services.medication_reconciliation_service import (
    amend_medication_reconciliation,
    create_medication_reconciliation,
    finalize_medication_reconciliation,
    get_medication_reconciliation,
    get_medication_reconciliation_amendments,
    get_medication_reconciliation_effective_document,
    update_medication_reconciliation_draft,
)


medication_reconciliation_bp = Blueprint(
    "medication_reconciliation",
    __name__,
    url_prefix="/medication-reconciliations",
)


MED_RECON_VIEW_ROLES = (
    Role.DOCTOR,
    Role.NURSE,
    Role.PHARMACIST,
)

MED_RECON_WRITE_ROLES = (
    Role.DOCTOR,
    Role.PHARMACIST,
)


def _json_body() -> dict:
    payload = request.get_json(
        silent=True
    )

    if payload is None:
        return {}

    if not isinstance(
        payload,
        dict,
    ):
        raise ValidationError(
            "Request body must be a JSON object"
        )

    return payload


def _clinic_id() -> int:
    return get_current_clinic_id(
        required=True
    )


def _staff_id() -> int:
    identity = get_jwt_identity()

    try:
        user_id = int(
            identity
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(
            "Invalid authentication identity"
        ) from exc

    user = db.session.get(
        User,
        user_id,
    )

    if (
        user is None
        or not user.is_active
        or user.staff is None
    ):
        raise ValidationError(
            "Authenticated user is not linked "
            "to an active staff identity"
        )

    return user.staff.id


def _serialize(
    record,
) -> dict:
    document = (
        get_medication_reconciliation_effective_document(
            record
        )
    )

    payload = {
        "id": record.id,
        "clinic_id": record.clinic_id,
        "patient_id": record.patient_id,
        "admission_id": record.admission_id,
        "ward_transfer_id": record.ward_transfer_id,
        "event_type": record.event_type,
        "event_identity": record.event_identity,
        "created_by_staff_id": (
            record.created_by_staff_id
        ),
        "no_medications_reported": (
            document["no_medications_reported"]
        ),
        "notes": document["notes"],
        "items": document["items"],
        "record_status": record.record_status,
        "record_version": record.record_version,
        "finalized_by_staff_id": (
            record.finalized_by_staff_id
        ),
        "finalized_at": record.finalized_at,
        "latest_amendment_id": (
            record.latest_amendment_id
        ),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }

    return (
        MedicationReconciliationResponseSchema
        .model_validate(
            payload
        )
        .model_dump(
            mode="json"
        )
    )


def _serialize_amendment(
    amendment,
) -> dict:
    return (
        ClinicalRecordAmendmentResponseSchema
        .model_validate(
            amendment
        )
        .model_dump(
            mode="json"
        )
    )


@medication_reconciliation_bp.post("")
@role_required(
    *MED_RECON_WRITE_ROLES
)
def create_reconciliation_route():
    payload = (
        MedicationReconciliationCreateSchema
        .model_validate(
            _json_body()
        )
    )

    record = create_medication_reconciliation(
        clinic_id=_clinic_id(),
        admission_id=payload.admission_id,
        event_type=payload.event_type,
        ward_transfer_id=(
            payload.ward_transfer_id
        ),
        actor_staff_id=_staff_id(),
        no_medications_reported=(
            payload.no_medications_reported
        ),
        notes=payload.notes,
        items=[
            item.model_dump()
            for item in payload.items
        ],
    )

    return jsonify({
        "message": (
            "Medication reconciliation draft created"
        ),
        "reconciliation": _serialize(
            record
        ),
    }), 201


@medication_reconciliation_bp.patch(
    "/<int:reconciliation_id>"
)
@role_required(
    *MED_RECON_WRITE_ROLES
)
def update_reconciliation_route(
    reconciliation_id: int,
):
    payload = (
        MedicationReconciliationUpdateSchema
        .model_validate(
            _json_body()
        )
    )

    record = (
        update_medication_reconciliation_draft(
            reconciliation_id,
            clinic_id=_clinic_id(),
            actor_staff_id=_staff_id(),
            no_medications_reported=(
                payload.no_medications_reported
            ),
            notes=payload.notes,
            items=[
                item.model_dump()
                for item in payload.items
            ],
        )
    )

    return jsonify({
        "message": (
            "Medication reconciliation draft updated"
        ),
        "reconciliation": _serialize(
            record
        ),
    }), 200


@medication_reconciliation_bp.post(
    "/<int:reconciliation_id>/finalize"
)
@role_required(
    *MED_RECON_WRITE_ROLES
)
def finalize_reconciliation_route(
    reconciliation_id: int,
):
    payload = (
        MedicationReconciliationFinalizeSchema
        .model_validate(
            _json_body()
        )
    )

    record = finalize_medication_reconciliation(
        reconciliation_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_staff_id(),
        expected_version=payload.expected_version,
    )

    return jsonify({
        "message": (
            "Medication reconciliation finalized"
        ),
        "reconciliation": _serialize(
            record
        ),
    }), 200


@medication_reconciliation_bp.post(
    "/<int:reconciliation_id>/amend"
)
@role_required(
    *MED_RECON_WRITE_ROLES
)
def amend_reconciliation_route(
    reconciliation_id: int,
):
    payload = (
        MedicationReconciliationAmendmentSchema
        .model_validate(
            _json_body()
        )
    )

    record, amendment = (
        amend_medication_reconciliation(
            reconciliation_id,
            clinic_id=_clinic_id(),
            actor_staff_id=_staff_id(),
            expected_version=payload.expected_version,
            reason=payload.reason,
            document=payload.document.model_dump(),
        )
    )

    return jsonify({
        "message": (
            "Medication reconciliation amended"
        ),
        "reconciliation": _serialize(
            record
        ),
        "amendment": _serialize_amendment(
            amendment
        ),
    }), 200


@medication_reconciliation_bp.get(
    "/<int:reconciliation_id>"
)
@emergency_aware_role_required(
    *MED_RECON_VIEW_ROLES
)
def get_reconciliation_route(
    reconciliation_id: int,
):
    record = get_medication_reconciliation(
        reconciliation_id,
        clinic_id=_clinic_id(),
    )

    emergency_denial = authorize_emergency_read_if_requested(
        patient_id=record.patient_id,
        resource_type="medication_reconciliation",
        resource_id=record.id,
    )

    if emergency_denial is not None:
        return emergency_denial

    return jsonify({
        "reconciliation": _serialize(
            record
        ),
    }), 200


@medication_reconciliation_bp.get(
    "/<int:reconciliation_id>/amendments"
)
@emergency_aware_role_required(
    *MED_RECON_VIEW_ROLES
)
def get_reconciliation_amendments_route(
    reconciliation_id: int,
):
    clinic_id = _clinic_id()

    record = get_medication_reconciliation(
        reconciliation_id,
        clinic_id=clinic_id,
    )

    emergency_denial = authorize_emergency_read_if_requested(
        patient_id=record.patient_id,
        resource_type="medication_reconciliation",
        resource_id=record.id,
    )

    if emergency_denial is not None:
        return emergency_denial

    rows = (
        get_medication_reconciliation_amendments(
            reconciliation_id,
            clinic_id=clinic_id,
        )
    )

    return jsonify({
        "items": [
            _serialize_amendment(
                row
            )
            for row in rows
        ],
        "total": len(rows),
    }), 200
