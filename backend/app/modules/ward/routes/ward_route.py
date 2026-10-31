from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import db
from app.modules.clinical_records.schemas.clinical_record_schema import (
    ClinicalRecordAmendmentResponseSchema,
)
from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.core.utils.decorators import (
    active_staff_required,
    get_current_clinic_id,
    role_required,
)

from app.modules.ward.schemas.admission_schema import (
    AdmissionCreateSchema,
    AdmissionDischargeSchema,
    AdmissionFromReservationSchema,
    AdmissionListQuerySchema,
    AdmissionListResponseSchema,
    AdmissionResponseSchema,
    AdmissionTransferSchema,
)
from app.modules.ward.schemas.reservation_schema import (
    BedReservationCancelSchema,
    BedReservationCreateSchema,
    BedReservationListQuerySchema,
    BedReservationListResponseSchema,
    BedReservationResponseSchema,
)
from app.modules.ward.schemas.bed_schema import (
    BedCreateSchema,
    BedListQuerySchema,
    BedListResponseSchema,
    BedMaintenanceSchema,
    BedResponseSchema,
)
from app.modules.ward.schemas.ward_schema import (
    WardCreateSchema,
    WardListQuerySchema,
    WardListResponseSchema,
    WardOccupancyResponseSchema,
    WardResponseSchema,
    WardUpdateSchema,
)

from app.modules.ward.schemas.discharge_summary_schema import (
    DischargeSummaryAmendmentSchema,
    DischargeSummaryResponseSchema,
)

from app.modules.ward.services.ward_service import (
    add_bed,
    admit_patient,
    admit_patient_from_reservation,
    cancel_bed_reservation,
    create_ward,
    discharge_patient,
    get_active_admission_for_patient,
    get_active_bed_reservation_for_bed,
    get_active_bed_reservation_for_patient,
    get_admission,
    get_bed,
    get_bed_reservation,
    get_ward,
    get_ward_occupancy,
    list_admissions_for_patient,
    list_bed_reservations,
    list_beds,
    list_wards,
    reserve_bed,
    set_bed_maintenance,
    transfer_bed,
    update_ward,
)

from app.modules.ward.services.discharge_service import (
    amend_discharge_summary,
    get_discharge_summary_amendments,
    get_discharge_summary_effective_document,
    get_discharge_summary_for_admission,
)


ward_bp = Blueprint(
    "ward",
    __name__,
    url_prefix="/wards",
)


MANAGEMENT_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
)

CLINICAL_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
)

DISCHARGE_ROLES = (
    Role.DOCTOR,
)

VIEW_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.RECEPTIONIST,
)


def _current_user() -> User:
    identity = get_jwt_identity()

    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        raise ValidationError(
            "Invalid authentication identity"
        )

    if user_id <= 0:
        raise ValidationError(
            "Invalid authentication identity"
        )

    user = db.session.get(User, user_id)

    if user is None:
        raise ValidationError(
            "Authenticated user could not be resolved"
        )

    if not user.is_active:
        raise ValidationError(
            "User account is inactive"
        )

    return user


def _current_clinic_id() -> int:
    return get_current_clinic_id(required=True)


def _current_staff_id() -> int:
    user = _current_user()

    if user.staff is None:
        raise ValidationError(
            "Authenticated user is not linked to a staff record"
        )

    return user.staff.id


def _serialize_model(schema, value):
    if value is None:
        return None

    return schema.model_validate(
        value
    ).model_dump(
        mode="json"
    )


def _serialize_page(
    response_schema,
    item_schema,
    result,
):
    items = [
        _serialize_model(
            item_schema,
            item,
        )
        for item in result["items"]
    ]

    payload = {
        "items": items,
        "total": result["total"],
        "page": result["page"],
        "per_page": result["per_page"],
    }

    return response_schema.model_validate(
        payload
    ).model_dump(
        mode="json"
    )


def _request_json() -> dict:
    payload = request.get_json(
        silent=True
    )

    if payload is None:
        return {}

    if not isinstance(payload, dict):
        raise ValidationError(
            "Request body must be a JSON object"
        )

    return payload


def _query_params() -> dict:
    return request.args.to_dict(
        flat=True
    )


# Wards


@ward_bp.route("", methods=["POST"])
@role_required(*MANAGEMENT_ROLES)
@active_staff_required
def create_ward_route():
    user = _current_user()
    clinic_id = _current_clinic_id()

    payload = WardCreateSchema.model_validate(
        _request_json()
    )

    ward = create_ward(
        clinic_id=clinic_id,
        name=payload.name,
        ward_type=payload.ward_type,
        capacity=payload.capacity,
        actor_user_id=user.id,
    )

    return jsonify(
        {
            "message": "Ward created successfully",
            "ward": _serialize_model(
                WardResponseSchema,
                ward,
            ),
        }
    ), 201


@ward_bp.route(
    "/<int:ward_id>",
    methods=["PATCH"],
)
@role_required(*MANAGEMENT_ROLES)
@active_staff_required
def update_ward_route(ward_id: int):
    user = _current_user()
    clinic_id = _current_clinic_id()

    payload = WardUpdateSchema.model_validate(
        _request_json()
    )

    ward = update_ward(
        ward_id=ward_id,
        clinic_id=clinic_id,
        actor_user_id=user.id,
        name=payload.name,
        ward_type=payload.ward_type,
        capacity=payload.capacity,
    )

    return jsonify(
        {
            "message": "Ward updated successfully",
            "ward": _serialize_model(
                WardResponseSchema,
                ward,
            ),
        }
    ), 200


@ward_bp.route("", methods=["GET"])
@role_required(*VIEW_ROLES)
@active_staff_required
def list_wards_route():
    clinic_id = _current_clinic_id()

    payload = WardListQuerySchema.model_validate(
        _query_params()
    )

    result = list_wards(
        clinic_id=clinic_id,
        ward_type=payload.ward_type,
        page=payload.page,
        per_page=payload.per_page,
    )

    return jsonify(
        _serialize_page(
            WardListResponseSchema,
            WardResponseSchema,
            result,
        )
    ), 200


@ward_bp.route(
    "/<int:ward_id>",
    methods=["GET"],
)
@role_required(*VIEW_ROLES)
@active_staff_required
def get_ward_route(ward_id: int):
    clinic_id = _current_clinic_id()

    ward = get_ward(
        ward_id,
        clinic_id=clinic_id,
    )

    return jsonify(
        _serialize_model(
            WardResponseSchema,
            ward,
        )
    ), 200


@ward_bp.route(
    "/<int:ward_id>/occupancy",
    methods=["GET"],
)
@role_required(*VIEW_ROLES)
@active_staff_required
def get_ward_occupancy_route(ward_id: int):
    clinic_id = _current_clinic_id()

    occupancy = get_ward_occupancy(
        ward_id=ward_id,
        clinic_id=clinic_id,
    )

    return jsonify(
        _serialize_model(
            WardOccupancyResponseSchema,
            occupancy,
        )
    ), 200


# Beds


@ward_bp.route(
    "/<int:ward_id>/beds",
    methods=["POST"],
)
@role_required(*MANAGEMENT_ROLES)
@active_staff_required
def add_bed_route(ward_id: int):
    user = _current_user()
    clinic_id = _current_clinic_id()

    payload = BedCreateSchema.model_validate(
        _request_json()
    )

    bed = add_bed(
        ward_id=ward_id,
        bed_number=payload.bed_number,
        clinic_id=clinic_id,
        actor_user_id=user.id,
    )

    return jsonify(
        {
            "message": "Bed added successfully",
            "bed": _serialize_model(
                BedResponseSchema,
                bed,
            ),
        }
    ), 201


@ward_bp.route(
    "/<int:ward_id>/beds",
    methods=["GET"],
)
@role_required(*VIEW_ROLES)
@active_staff_required
def list_beds_route(ward_id: int):
    clinic_id = _current_clinic_id()

    payload = BedListQuerySchema.model_validate(
        _query_params()
    )

    result = list_beds(
        ward_id=ward_id,
        clinic_id=clinic_id,
        status=payload.status,
        page=payload.page,
        per_page=payload.per_page,
    )

    return jsonify(
        _serialize_page(
            BedListResponseSchema,
            BedResponseSchema,
            result,
        )
    ), 200


@ward_bp.route(
    "/beds/<int:bed_id>",
    methods=["GET"],
)
@role_required(*VIEW_ROLES)
@active_staff_required
def get_bed_route(bed_id: int):
    clinic_id = _current_clinic_id()

    bed = get_bed(
        bed_id,
        clinic_id=clinic_id,
    )

    return jsonify(
        _serialize_model(
            BedResponseSchema,
            bed,
        )
    ), 200


@ward_bp.route(
    "/beds/<int:bed_id>/maintenance",
    methods=["PATCH"],
)
@role_required(*MANAGEMENT_ROLES)
@active_staff_required
def set_bed_maintenance_route(bed_id: int):
    user = _current_user()
    clinic_id = _current_clinic_id()

    payload = BedMaintenanceSchema.model_validate(
        _request_json()
    )

    bed = set_bed_maintenance(
        bed_id=bed_id,
        under_maintenance=payload.under_maintenance,
        clinic_id=clinic_id,
        actor_user_id=user.id,
    )

    return jsonify(
        {
            "message": "Bed maintenance status updated",
            "bed": _serialize_model(
                BedResponseSchema,
                bed,
            ),
        }
    ), 200


# Reservations


@ward_bp.route(
    "/reservations",
    methods=["POST"],
)
@role_required(*CLINICAL_ROLES)
@active_staff_required
def reserve_bed_route():
    user = _current_user()
    clinic_id = _current_clinic_id()
    staff_id = _current_staff_id()

    payload = BedReservationCreateSchema.model_validate(
        _request_json()
    )

    reservation = reserve_bed(
        patient_id=payload.patient_id,
        bed_id=payload.bed_id,
        reserved_by_id=staff_id,
        clinic_id=clinic_id,
        reason=payload.reason,
        expires_at=payload.expires_at,
        actor_user_id=user.id,
    )

    return jsonify(
        {
            "message": "Bed reserved successfully",
            "reservation": _serialize_model(
                BedReservationResponseSchema,
                reservation,
            ),
        }
    ), 201


@ward_bp.route(
    "/reservations",
    methods=["GET"],
)
@role_required(*VIEW_ROLES)
@active_staff_required
def list_bed_reservations_route():
    clinic_id = _current_clinic_id()

    payload = BedReservationListQuerySchema.model_validate(
        _query_params()
    )

    result = list_bed_reservations(
        clinic_id=clinic_id,
        status=payload.status,
        patient_id=payload.patient_id,
        bed_id=payload.bed_id,
        page=payload.page,
        per_page=payload.per_page,
    )

    return jsonify(
        _serialize_page(
            BedReservationListResponseSchema,
            BedReservationResponseSchema,
            result,
        )
    ), 200


@ward_bp.route(
    "/reservations/<int:reservation_id>",
    methods=["GET"],
)
@jwt_required()
@role_required(*VIEW_ROLES)
@active_staff_required
def get_bed_reservation_route(
    reservation_id: int,
):
    clinic_id = _current_clinic_id()

    reservation = get_bed_reservation(
        reservation_id,
        clinic_id=clinic_id,
    )

    return jsonify(
        _serialize_model(
            BedReservationResponseSchema,
            reservation,
        )
    ), 200


@ward_bp.route(
    "/patients/<int:patient_id>/reservation",
    methods=["GET"],
)
@jwt_required()
@role_required(*VIEW_ROLES)
@active_staff_required
def get_patient_active_reservation_route(
    patient_id: int,
):
    clinic_id = _current_clinic_id()

    if patient_id <= 0:
        raise ValidationError(
            "patient_id must be greater than zero"
        )

    reservation = get_active_bed_reservation_for_patient(
        patient_id=patient_id,
        clinic_id=clinic_id,
    )

    if reservation is None:
        return jsonify(
            {
                "message": "No active reservation found",
            }
        ), 404

    return jsonify(
        _serialize_model(
            BedReservationResponseSchema,
            reservation,
        )
    ), 200


@ward_bp.route(
    "/beds/<int:bed_id>/reservation",
    methods=["GET"],
)
@role_required(*VIEW_ROLES)
@active_staff_required
def get_bed_active_reservation_route(
    bed_id: int,
):
    clinic_id = _current_clinic_id()

    if bed_id <= 0:
        raise ValidationError(
            "bed_id must be greater than zero"
        )

    reservation = get_active_bed_reservation_for_bed(
        bed_id=bed_id,
        clinic_id=clinic_id,
    )

    if reservation is None:
        return jsonify(
            {
                "message": "No active reservation found",
            }
        ), 404

    return jsonify(
        _serialize_model(
            BedReservationResponseSchema,
            reservation,
        )
    ), 200


@ward_bp.route(
    "/reservations/<int:reservation_id>/cancel",
    methods=["POST"],
)
@role_required(*CLINICAL_ROLES)
@active_staff_required
def cancel_bed_reservation_route(
    reservation_id: int,
):
    user = _current_user()
    clinic_id = _current_clinic_id()

    payload = BedReservationCancelSchema.model_validate(
        _request_json()
    )

    reservation = cancel_bed_reservation(
        reservation_id=reservation_id,
        clinic_id=clinic_id,
        reason=payload.reason,
        actor_user_id=user.id,
    )

    return jsonify(
        {
            "message": "Bed reservation cancelled successfully",
            "reservation": _serialize_model(
                BedReservationResponseSchema,
                reservation,
            ),
        }
    ), 200


# Admissions


@ward_bp.route(
    "/reservations/<int:reservation_id>/admit",
    methods=["POST"],
)
@role_required(*CLINICAL_ROLES)
@active_staff_required
def admit_patient_from_reservation_route(
    reservation_id: int,
):
    user = _current_user()
    clinic_id = _current_clinic_id()
    staff_id = _current_staff_id()

    payload = AdmissionFromReservationSchema.model_validate(
        _request_json()
    )

    admission = admit_patient_from_reservation(
        reservation_id=reservation_id,
        admitted_by_id=staff_id,
        clinic_id=clinic_id,
        reason=payload.reason,
        actor_user_id=user.id,
    )

    return jsonify(
        {
            "message": "Patient admitted from reservation",
            "admission": _serialize_model(
                AdmissionResponseSchema,
                admission,
            ),
        }
    ), 201


@ward_bp.route(
    "/admissions",
    methods=["POST"],
)
@role_required(*CLINICAL_ROLES)
@active_staff_required
def admit_patient_route():
    user = _current_user()
    clinic_id = _current_clinic_id()
    staff_id = _current_staff_id()

    payload = AdmissionCreateSchema.model_validate(
        _request_json()
    )

    admission = admit_patient(
        patient_id=payload.patient_id,
        bed_id=payload.bed_id,
        admitted_by_id=staff_id,
        clinic_id=clinic_id,
        reason=payload.reason,
        actor_user_id=user.id,
    )

    return jsonify(
        {
            "message": "Patient admitted successfully",
            "admission": _serialize_model(
                AdmissionResponseSchema,
                admission,
            ),
        }
    ), 201


@ward_bp.route(
    "/admissions/<int:admission_id>",
    methods=["GET"],
)
@role_required(*VIEW_ROLES)
@active_staff_required
def get_admission_route(admission_id: int):
    clinic_id = _current_clinic_id()

    admission = get_admission(
        admission_id,
        clinic_id=clinic_id,
    )

    return jsonify(
        _serialize_model(
            AdmissionResponseSchema,
            admission,
        )
    ), 200


@ward_bp.route(
    "/patients/<int:patient_id>/admissions",
    methods=["GET"],
)
@role_required(*VIEW_ROLES)
@active_staff_required
def list_patient_admissions_route(
    patient_id: int,
):
    clinic_id = _current_clinic_id()

    if patient_id <= 0:
        raise ValidationError(
            "patient_id must be greater than zero"
        )

    payload = AdmissionListQuerySchema.model_validate(
        _query_params()
    )

    result = list_admissions_for_patient(
        patient_id=patient_id,
        clinic_id=clinic_id,
        page=payload.page,
        per_page=payload.per_page,
    )

    return jsonify(
        _serialize_page(
            AdmissionListResponseSchema,
            AdmissionResponseSchema,
            result,
        )
    ), 200


@ward_bp.route(
    "/patients/<int:patient_id>/current-admission",
    methods=["GET"],
)
@role_required(*VIEW_ROLES)
@active_staff_required
def get_current_patient_admission_route(
    patient_id: int,
):
    clinic_id = _current_clinic_id()

    if patient_id <= 0:
        raise ValidationError(
            "patient_id must be greater than zero"
        )

    admission = get_active_admission_for_patient(
        patient_id=patient_id,
        clinic_id=clinic_id,
    )

    if admission is None:
        return jsonify(
            {
                "message": "No active admission found",
            }
        ), 404

    return jsonify(
        _serialize_model(
            AdmissionResponseSchema,
            admission,
        )
    ), 200


@ward_bp.route(
    "/admissions/<int:admission_id>/transfer",
    methods=["POST"],
)
@role_required(*CLINICAL_ROLES)
@active_staff_required
def transfer_bed_route(admission_id: int):
    user = _current_user()
    clinic_id = _current_clinic_id()

    payload = AdmissionTransferSchema.model_validate(
        _request_json()
    )

    transfer = transfer_bed(
        admission_id=admission_id,
        to_bed_id=payload.to_bed_id,
        clinic_id=clinic_id,
        reason=payload.reason,
        actor_user_id=user.id,
    )

    return jsonify(
        {
            "message": "Patient transferred successfully",
            "transfer": {
                "id": transfer.id,
                "admission_id": transfer.admission_id,
                "from_bed_id": transfer.from_bed_id,
                "to_bed_id": transfer.to_bed_id,
                "reason": transfer.reason,
                "transferred_at": (
                    transfer.transferred_at.isoformat()
                    if transfer.transferred_at
                    else None
                ),
            },
        }
    ), 201

@ward_bp.route(
    "/admissions/<int:admission_id>/discharge",
    methods=["POST"],
)
@role_required(*DISCHARGE_ROLES)
@active_staff_required
def discharge_patient_route(admission_id: int):
    user = _current_user()
    clinic_id = _current_clinic_id()
    staff_id = _current_staff_id()

    payload = AdmissionDischargeSchema.model_validate(
        _request_json()
    )

    admission = discharge_patient(
        admission_id=admission_id,
        clinic_id=clinic_id,
        reason=payload.reason,
        actor_user_id=user.id,
        actor_staff_id=staff_id,
        primary_diagnosis=payload.primary_diagnosis,
        secondary_diagnoses=payload.secondary_diagnoses,
        condition_at_discharge=payload.condition_at_discharge,
        instructions=payload.instructions,
        follow_up_plan=payload.follow_up_plan,
        destination=payload.destination,
    )

    summary = get_discharge_summary_for_admission(
        admission.id,
        clinic_id=clinic_id,
    )

    return jsonify(
        {
            "message": "Patient discharged successfully",
            "admission": _serialize_model(
                AdmissionResponseSchema,
                admission,
            ),
            "discharge_summary": _serialize_discharge_summary(
                summary
            ),
        }
    ), 200


def _serialize_discharge_summary(summary):
    document = get_discharge_summary_effective_document(
        summary
    )

    payload = {
        "id": summary.id,
        "clinic_id": summary.clinic_id,
        "patient_id": summary.patient_id,
        "admission_id": summary.admission_id,
        "medication_reconciliation_id": (
            summary.medication_reconciliation_id
        ),
        "discharged_by_staff_id": summary.discharged_by_staff_id,
        "primary_diagnosis": document["primary_diagnosis"],
        "secondary_diagnoses": document["secondary_diagnoses"],
        "condition_at_discharge": document["condition_at_discharge"],
        "instructions": document["instructions"],
        "follow_up_plan": document["follow_up_plan"],
        "final_medication_list": document["final_medication_list"],
        "destination": document["destination"],
        "discharged_at": summary.discharged_at,
        "record_status": summary.record_status,
        "record_version": summary.record_version,
        "finalized_by_staff_id": summary.finalized_by_staff_id,
        "finalized_at": summary.finalized_at,
        "latest_amendment_id": summary.latest_amendment_id,
        "created_at": summary.created_at,
        "updated_at": summary.updated_at,
    }

    return (
        DischargeSummaryResponseSchema
        .model_validate(payload)
        .model_dump(mode="json")
    )


@ward_bp.route(
    "/admissions/<int:admission_id>/discharge-summary",
    methods=["GET"],
)
@role_required(*CLINICAL_ROLES)
@active_staff_required
def get_discharge_summary_route(admission_id: int):
    summary = get_discharge_summary_for_admission(
        admission_id,
        clinic_id=_current_clinic_id(),
    )

    return jsonify(
        {
            "discharge_summary": _serialize_discharge_summary(
                summary
            )
        }
    ), 200


@ward_bp.route(
    "/admissions/<int:admission_id>/discharge-summary/amend",
    methods=["POST"],
)
@role_required(*DISCHARGE_ROLES)
@active_staff_required
def amend_discharge_summary_route(admission_id: int):
    user = _current_user()
    clinic_id = _current_clinic_id()
    staff_id = _current_staff_id()

    summary = get_discharge_summary_for_admission(
        admission_id,
        clinic_id=clinic_id,
    )

    payload = DischargeSummaryAmendmentSchema.model_validate(
        _request_json()
    )

    summary, amendment = amend_discharge_summary(
        summary.id,
        clinic_id=clinic_id,
        actor_staff_id=staff_id,
        actor_user_id=user.id,
        expected_version=payload.expected_version,
        reason=payload.reason,
        document=payload.document.model_dump(),
    )

    return jsonify(
        {
            "message": "Discharge summary amended",
            "discharge_summary": _serialize_discharge_summary(
                summary
            ),
            "amendment": (
                ClinicalRecordAmendmentResponseSchema
                .model_validate(amendment)
                .model_dump(mode="json")
            ),
        }
    ), 200


@ward_bp.route(
    "/admissions/<int:admission_id>/discharge-summary/amendments",
    methods=["GET"],
)
@role_required(*CLINICAL_ROLES)
@active_staff_required
def discharge_summary_amendments_route(admission_id: int):
    clinic_id = _current_clinic_id()

    summary = get_discharge_summary_for_admission(
        admission_id,
        clinic_id=clinic_id,
    )

    rows = get_discharge_summary_amendments(
        summary.id,
        clinic_id=clinic_id,
    )

    return jsonify(
        {
            "items": [
                (
                    ClinicalRecordAmendmentResponseSchema
                    .model_validate(row)
                    .model_dump(mode="json")
                )
                for row in rows
            ],
            "total": len(rows),
        }
    ), 200
