from __future__ import annotations

from flask import (
    Blueprint,
    jsonify,
    request,
)
from flask_jwt_extended import (
    get_jwt_identity,
)

from app.core.auth.user.models.user_model import (
    User,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.core.idempotency.services.idempotency_service import (
    normalize_idempotency_key,
)
from app.core.utils.decorators import (
    get_current_clinic_id,
    role_required,
)
from app.extensions import db
from app.modules.clinical_records.schemas.clinical_record_schema import (
    ClinicalRecordAmendmentResponseSchema,
)

from app.modules.emar.schemas.emar_schema import (
    BarcodeMedicationVerificationRequestSchema,
    MedicationAdministrationAdministerSchema,
    MedicationAdministrationAmendmentSchema,
    MedicationAdministrationEventListResponseSchema,
    MedicationAdministrationEventResponseSchema,
    MedicationAdministrationCancelSchema,
    MedicationAdministrationDueSchema,
    MedicationAdministrationHoldSchema,
    MedicationAdministrationListQuerySchema,
    MedicationAdministrationListResponseSchema,
    MedicationAdministrationMissedSchema,
    MedicationAdministrationPrnSchema,
    MedicationAdministrationRefusalSchema,
    MedicationAdministrationResponseSchema,
    MedicationAdministrationScheduleSchema,
)
from app.modules.emar.services.barcode_verification_service import (
    list_barcode_verification_events,
    verify_barcode_medication_administration,
)
from app.modules.emar.services.emar_service import (
    administer_barcode_verified_medication,
    administer_prn_medication,
    amend_medication_administration,
    cancel_medication_administration,
    get_medication_administration,
    get_medication_administration_amendments,
    get_medication_administration_effective_document,
    hold_medication,
    list_medication_administrations_for_admission,
    mark_medication_administration_due,
    mark_medication_missed,
    record_medication_refusal,
    schedule_medication_administration,
)


emar_bp = Blueprint(
    "emar",
    __name__,
    url_prefix="/emar",
)


EMAR_ROLES = (
    Role.DOCTOR,
    Role.NURSE,
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


def _query_params() -> dict:
    return request.args.to_dict(
        flat=True
    )


def _required_idempotency_key() -> str:
    raw_key = request.headers.get(
        "Idempotency-Key"
    )

    if raw_key is None:
        raise ValidationError(
            "Idempotency-Key header is required "
            "for medication administration"
        )

    return normalize_idempotency_key(
        raw_key
    )


def _current_clinic_id() -> int:
    return get_current_clinic_id(
        required=True
    )


def _current_user() -> User:
    identity = get_jwt_identity()

    try:
        user_id = int(
            identity
        )
    except (
        TypeError,
        ValueError,
    ):
        raise ValidationError(
            "Invalid authentication identity"
        )

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


def _current_staff_id() -> int:
    user = _current_user()

    if user.staff is None:
        raise ValidationError(
            "Authenticated user is not linked "
            "to a staff record"
        )

    return user.staff.id


def _serialize(
    record,
) -> dict:
    payload = (
        MedicationAdministrationResponseSchema
        .model_validate(
            record
        )
        .model_dump()
    )

    if hasattr(
        record,
        "record_status",
    ):
        payload.update(
            get_medication_administration_effective_document(
                record
            )
        )

    return (
        MedicationAdministrationResponseSchema
        .model_validate(
            payload
        )
        .model_dump(
            mode="json"
        )
    )


def _serialize_barcode_event(
    event,
) -> dict:
    return (
        MedicationAdministrationEventResponseSchema
        .model_validate(event)
        .model_dump(mode="json")
    )


def _serialize_barcode_event_history(
    events,
) -> dict:
    payload = {
        "items": list(
            events
        ),
        "total": len(
            events
        ),
    }

    return (
        MedicationAdministrationEventListResponseSchema
        .model_validate(
            payload
        )
        .model_dump(
            mode="json"
        )
    )


def _serialize_page(
    result: dict,
) -> dict:
    payload = {
        "items": [
            _serialize(
                item
            )
            for item in result[
                "items"
            ]
        ],
        "total": result[
            "total"
        ],
        "page": result[
            "page"
        ],
        "per_page": result[
            "per_page"
        ],
    }

    return (
        MedicationAdministrationListResponseSchema
        .model_validate(
            payload
        )
        .model_dump(
            mode="json"
        )
    )


@emar_bp.post("/barcode/verify")
@role_required(*EMAR_ROLES)
def verify_barcode_medication_administration_route():
    clinic_id = _current_clinic_id()
    current_user = _current_user()

    if current_user.staff is None:
        raise ValidationError(
            "Authenticated user is not linked to a staff record"
        )

    payload = BarcodeMedicationVerificationRequestSchema.model_validate(
        _json_body()
    )

    event = verify_barcode_medication_administration(
        clinic_id=clinic_id,
        actor_staff_id=current_user.staff.id,
        patient_barcode=payload.patient_barcode,
        medication_barcode=payload.medication_barcode,
        scan_timestamp=payload.scan_timestamp,
        scan_device=payload.scan_device,
        operation_identity=_required_idempotency_key(),
    )

    return jsonify(
        {
            "message": "Barcode medication verification completed",
            "verification": _serialize_barcode_event(event),
        }
    ), 200


@emar_bp.get(
    "/<int:administration_id>/barcode-events"
)
@role_required(
    *EMAR_ROLES
)
def list_barcode_verification_events_route(
    administration_id: int,
):
    events = (
        list_barcode_verification_events(
            administration_id=(
                administration_id
            ),
            clinic_id=(
                _current_clinic_id()
            ),
        )
    )

    return jsonify(
        {
            "history": (
                _serialize_barcode_event_history(
                    events
                )
            ),
        }
    ), 200


@emar_bp.post(
    "/scheduled"
)
@role_required(
    *EMAR_ROLES
)
def schedule_medication_administration_route():
    clinic_id = _current_clinic_id()
    staff_id = _current_staff_id()

    payload = (
        MedicationAdministrationScheduleSchema
        .model_validate(
            _json_body()
        )
    )

    record = (
        schedule_medication_administration(
            clinic_id=clinic_id,
            patient_id=payload.patient_id,
            admission_id=payload.admission_id,
            prescription_item_id=(
                payload.prescription_item_id
            ),
            scheduled_at=payload.scheduled_at,
            actor_staff_id=staff_id,
        )
    )

    return jsonify(
        {
            "message": (
                "Medication administration "
                "scheduled successfully"
            ),
            "administration": _serialize(
                record
            ),
        }
    ), 201


@emar_bp.post(
    "/prn"
)
@role_required(
    *EMAR_ROLES
)
def administer_prn_medication_route():
    clinic_id = _current_clinic_id()
    staff_id = _current_staff_id()

    payload = (
        MedicationAdministrationPrnSchema
        .model_validate(
            _json_body()
        )
    )

    record = administer_prn_medication(
        clinic_id=clinic_id,
        patient_id=payload.patient_id,
        admission_id=payload.admission_id,
        prescription_item_id=(
            payload.prescription_item_id
        ),
        actor_staff_id=staff_id,
        administered_at=(
            payload.administered_at
        ),
        notes=payload.notes,
    )

    return jsonify(
        {
            "message": (
                "PRN medication administered "
                "successfully"
            ),
            "administration": _serialize(
                record
            ),
        }
    ), 201


@emar_bp.post(
    "/<int:administration_id>/due"
)
@role_required(
    *EMAR_ROLES
)
def mark_medication_administration_due_route(
    administration_id: int,
):
    payload = (
        MedicationAdministrationDueSchema
        .model_validate(
            _json_body()
        )
    )

    record = (
        mark_medication_administration_due(
            administration_id=administration_id,
            clinic_id=_current_clinic_id(),
            actor_staff_id=_current_staff_id(),
            due_at=payload.due_at,
        )
    )

    return jsonify(
        {
            "message": (
                "Medication administration marked due"
            ),
            "administration": _serialize(
                record
            ),
        }
    ), 200


@emar_bp.post(
    "/<int:administration_id>/administer"
)
@role_required(
    *EMAR_ROLES
)
def administer_medication_route(
    administration_id: int,
):
    payload = (
        MedicationAdministrationAdministerSchema
        .model_validate(
            _json_body()
        )
    )

    current_user = _current_user()

    if current_user.staff is None:
        raise ValidationError(
            "Authenticated user is not linked "
            "to a staff record"
        )

    record = administer_barcode_verified_medication(
        administration_id=administration_id,
        clinic_id=_current_clinic_id(),
        actor_staff_id=current_user.staff.id,
        barcode_verification_event_id=(
            payload.barcode_verification_event_id
        ),
        administered_at=(
            payload.administered_at
        ),
        notes=payload.notes,
        idempotency_key=(
            _required_idempotency_key()
        ),
        idempotency_user_id=current_user.id,
    )

    return jsonify(
        {
            "message": (
                "Medication administered successfully"
            ),
            "administration": _serialize(
                record
            ),
        }
    ), 200


@emar_bp.post(
    "/<int:administration_id>/hold"
)
@role_required(
    *EMAR_ROLES
)
def hold_medication_route(
    administration_id: int,
):
    payload = (
        MedicationAdministrationHoldSchema
        .model_validate(
            _json_body()
        )
    )

    record = hold_medication(
        administration_id=administration_id,
        clinic_id=_current_clinic_id(),
        actor_staff_id=_current_staff_id(),
        reason=payload.reason,
        held_at=payload.held_at,
    )

    return jsonify(
        {
            "message": "Medication held successfully",
            "administration": _serialize(
                record
            ),
        }
    ), 200


@emar_bp.post(
    "/<int:administration_id>/refuse"
)
@role_required(
    *EMAR_ROLES
)
def record_medication_refusal_route(
    administration_id: int,
):
    payload = (
        MedicationAdministrationRefusalSchema
        .model_validate(
            _json_body()
        )
    )

    record = record_medication_refusal(
        administration_id=administration_id,
        clinic_id=_current_clinic_id(),
        actor_staff_id=_current_staff_id(),
        reason=payload.reason,
        refused_at=payload.refused_at,
    )

    return jsonify(
        {
            "message": (
                "Medication refusal recorded successfully"
            ),
            "administration": _serialize(
                record
            ),
        }
    ), 200


@emar_bp.post(
    "/<int:administration_id>/miss"
)
@role_required(
    *EMAR_ROLES
)
def mark_medication_missed_route(
    administration_id: int,
):
    payload = (
        MedicationAdministrationMissedSchema
        .model_validate(
            _json_body()
        )
    )

    record = mark_medication_missed(
        administration_id=administration_id,
        clinic_id=_current_clinic_id(),
        actor_staff_id=_current_staff_id(),
        reason=payload.reason,
        missed_at=payload.missed_at,
    )

    return jsonify(
        {
            "message": (
                "Medication administration marked missed"
            ),
            "administration": _serialize(
                record
            ),
        }
    ), 200


@emar_bp.post(
    "/<int:administration_id>/cancel"
)
@role_required(
    *EMAR_ROLES
)
def cancel_medication_administration_route(
    administration_id: int,
):
    payload = (
        MedicationAdministrationCancelSchema
        .model_validate(
            _json_body()
        )
    )

    record = (
        cancel_medication_administration(
            administration_id=administration_id,
            clinic_id=_current_clinic_id(),
            actor_staff_id=_current_staff_id(),
            reason=payload.reason,
            cancelled_at=(
                payload.cancelled_at
            ),
        )
    )

    return jsonify(
        {
            "message": (
                "Medication administration cancelled"
            ),
            "administration": _serialize(
                record
            ),
        }
    ), 200


@emar_bp.get(
    "/<int:administration_id>"
)
@role_required(
    *EMAR_ROLES
)
def get_medication_administration_route(
    administration_id: int,
):
    record = get_medication_administration(
        administration_id,
        clinic_id=_current_clinic_id(),
    )

    return jsonify(
        {
            "administration": _serialize(
                record
            ),
        }
    ), 200


@emar_bp.get(
    "/admissions/<int:admission_id>"
)
@role_required(
    *EMAR_ROLES
)
def list_admission_emar_route(
    admission_id: int,
):
    query = (
        MedicationAdministrationListQuerySchema
        .model_validate(
            _query_params()
        )
    )

    result = (
        list_medication_administrations_for_admission(
            admission_id=admission_id,
            clinic_id=_current_clinic_id(),
            status=query.status,
            page=query.page,
            per_page=query.per_page,
        )
    )

    return jsonify(
        _serialize_page(
            result
        )
    ), 200

@emar_bp.post(
    "/<int:administration_id>/amend"
)
@role_required(
    *EMAR_ROLES
)
def amend_medication_administration_route(
    administration_id: int,
):
    payload = (
        MedicationAdministrationAmendmentSchema
        .model_validate(
            _json_body()
        )
    )

    record, amendment = (
        amend_medication_administration(
            administration_id=administration_id,
            clinic_id=_current_clinic_id(),
            actor_staff_id=_current_staff_id(),
            expected_version=payload.expected_version,
            reason=payload.reason,
            document=payload.document,
        )
    )

    return jsonify({
        "message": (
            "Medication administration record amended"
        ),
        "administration": _serialize(
            record
        ),
        "amendment": (
            ClinicalRecordAmendmentResponseSchema
            .model_validate(
                amendment
            )
            .model_dump(
                mode="json"
            )
        ),
    }), 200


@emar_bp.get(
    "/<int:administration_id>/amendments"
)
@role_required(
    *EMAR_ROLES
)
def get_medication_administration_amendments_route(
    administration_id: int,
):
    rows = (
        get_medication_administration_amendments(
            administration_id,
            clinic_id=_current_clinic_id(),
        )
    )

    return jsonify({
        "items": [
            (
                ClinicalRecordAmendmentResponseSchema
                .model_validate(
                    row
                )
                .model_dump(
                    mode="json"
                )
            )
            for row in rows
        ],
        "total": len(rows),
    }), 200
