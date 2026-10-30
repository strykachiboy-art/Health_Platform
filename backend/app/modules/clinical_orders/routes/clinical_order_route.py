
from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity

from app.extensions import db
from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.core.utils.decorators import (
    get_current_clinic_id,
    role_required,
)

from app.modules.clinical_orders.schemas.imaging_order_schema import (
    ImagingOrderCancelSchema,
    ImagingOrderCompleteSchema,
    ImagingOrderCreateSchema,
    ImagingOrderListResponseSchema,
    ImagingOrderResponseSchema,
)
from app.modules.clinical_orders.schemas.procedure_order_schema import (
    ProcedureOrderCancelSchema,
    ProcedureOrderCompleteSchema,
    ProcedureOrderCreateSchema,
    ProcedureOrderListResponseSchema,
    ProcedureOrderResponseSchema,
)
from app.modules.clinical_orders.services.imaging_order_service import (
    cancel_imaging_order,
    complete_imaging_order,
    create_imaging_order,
    get_imaging_order,
    list_imaging_orders,
)
from app.modules.clinical_orders.services.procedure_order_service import (
    cancel_procedure_order,
    complete_procedure_order,
    create_procedure_order,
    get_procedure_order,
    list_procedure_orders,
    verify_procedure_consent,
)


clinical_order_bp = Blueprint(
    "clinical_orders",
    __name__,
    url_prefix="/clinical-orders",
)

IMAGING_WRITE_ROLES = (
    Role.DOCTOR,
)

IMAGING_VIEW_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
)

PROCEDURE_WRITE_ROLES = (
    Role.DOCTOR,
)

PROCEDURE_VIEW_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
)


def _current_user() -> User:
    identity = get_jwt_identity()

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


def _current_staff_id() -> int:
    user = _current_user()

    if user.staff is None:
        raise ValidationError(
            "Authenticated user is not linked "
            "to a staff record"
        )

    return user.staff.id


def _clinic_id() -> int:
    return get_current_clinic_id(
        required=True
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


def _serialize(
    schema,
    value,
):
    return (
        schema
        .model_validate(value)
        .model_dump(mode="json")
    )


@clinical_order_bp.post("/imaging")
@role_required(*IMAGING_WRITE_ROLES)
def create_imaging_order_route():
    payload = (
        ImagingOrderCreateSchema
        .model_validate(
            _json_body()
        )
    )

    order = create_imaging_order(
        admission_id=payload.admission_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        study_name=payload.study_name,
        modality=payload.modality,
        body_site=payload.body_site,
        indication=payload.indication,
        study_code=payload.study_code,
        consultation_id=payload.consultation_id,
        laterality=payload.laterality,
        contrast_required=(
            payload.contrast_required
        ),
        contrast_type=payload.contrast_type,
        instructions=payload.instructions,
        priority=payload.priority,
        scheduled_at=payload.scheduled_at,
    )

    return jsonify(
        {
            "message": "Imaging order created",
            "order": _serialize(
                ImagingOrderResponseSchema,
                order,
            ),
        }
    ), 201


@clinical_order_bp.get("/imaging/<int:imaging_order_id>")
@role_required(*IMAGING_VIEW_ROLES)
def get_imaging_order_route(
    imaging_order_id: int,
):
    order = get_imaging_order(
        imaging_order_id,
        clinic_id=_clinic_id(),
    )

    return jsonify(
        _serialize(
            ImagingOrderResponseSchema,
            order,
        )
    ), 200


@clinical_order_bp.get("/imaging")
@role_required(*IMAGING_VIEW_ROLES)
def list_imaging_orders_route():
    raw_admission_id = request.args.get(
        "admission_id"
    )

    try:
        admission_id = int(
            raw_admission_id
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(
            "admission_id query parameter "
            "must be a positive integer"
        ) from exc

    if admission_id <= 0:
        raise ValidationError(
            "admission_id query parameter "
            "must be a positive integer"
        )

    items = list_imaging_orders(
        admission_id=admission_id,
        clinic_id=_clinic_id(),
    )

    payload = {
        "items": [
            _serialize(
                ImagingOrderResponseSchema,
                item,
            )
            for item in items
        ],
        "total": len(items),
    }

    return jsonify(
        ImagingOrderListResponseSchema
        .model_validate(payload)
        .model_dump(mode="json")
    ), 200


@clinical_order_bp.post(
    "/imaging/<int:imaging_order_id>/cancel"
)
@role_required(*IMAGING_WRITE_ROLES)
def cancel_imaging_order_route(
    imaging_order_id: int,
):
    payload = (
        ImagingOrderCancelSchema
        .model_validate(
            _json_body()
        )
    )

    order = cancel_imaging_order(
        imaging_order_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        reason=payload.reason,
    )

    return jsonify(
        {
            "message": "Imaging order cancelled",
            "order": _serialize(
                ImagingOrderResponseSchema,
                order,
            ),
        }
    ), 200


@clinical_order_bp.post(
    "/imaging/<int:imaging_order_id>/complete"
)
@role_required(*IMAGING_WRITE_ROLES)
def complete_imaging_order_route(
    imaging_order_id: int,
):
    payload = (
        ImagingOrderCompleteSchema
        .model_validate(
            _json_body()
        )
    )

    order = complete_imaging_order(
        imaging_order_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        report_text=payload.report_text,
        result_summary=(
            payload.result_summary
        ),
        performed_at=payload.performed_at,
    )

    return jsonify(
        {
            "message": "Imaging order completed",
            "order": _serialize(
                ImagingOrderResponseSchema,
                order,
            ),
        }
    ), 200

@clinical_order_bp.post("/procedures")
@role_required(*PROCEDURE_WRITE_ROLES)
def create_procedure_order_route():
    payload = ProcedureOrderCreateSchema.model_validate(_json_body())
    order = create_procedure_order(
        admission_id=payload.admission_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        procedure_name=payload.procedure_name,
        procedure_type=payload.procedure_type,
        indication=payload.indication,
        procedure_code=payload.procedure_code,
        consultation_id=payload.consultation_id,
        body_site=payload.body_site,
        laterality=payload.laterality,
        prep_instructions=payload.prep_instructions,
        anesthesia_type=payload.anesthesia_type,
        consent_required=payload.consent_required,
        priority=payload.priority,
        scheduled_at=payload.scheduled_at,
    )
    return jsonify({
        "message": "Procedure order created",
        "order": _serialize(ProcedureOrderResponseSchema, order),
    }), 201


@clinical_order_bp.get("/procedures/<int:procedure_order_id>")
@role_required(*PROCEDURE_VIEW_ROLES)
def get_procedure_order_route(procedure_order_id: int):
    order = get_procedure_order(
        procedure_order_id,
        clinic_id=_clinic_id(),
    )
    return jsonify(
        _serialize(ProcedureOrderResponseSchema, order)
    ), 200


@clinical_order_bp.get("/procedures")
@role_required(*PROCEDURE_VIEW_ROLES)
def list_procedure_orders_route():
    raw_admission_id = request.args.get("admission_id")
    try:
        admission_id = int(raw_admission_id)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "admission_id query parameter must be a positive integer"
        ) from exc
    if admission_id <= 0:
        raise ValidationError(
            "admission_id query parameter must be a positive integer"
        )

    items = list_procedure_orders(
        admission_id=admission_id,
        clinic_id=_clinic_id(),
    )
    payload = {
        "items": [
            _serialize(ProcedureOrderResponseSchema, item)
            for item in items
        ],
        "total": len(items),
    }
    return jsonify(
        ProcedureOrderListResponseSchema
        .model_validate(payload)
        .model_dump(mode="json")
    ), 200


@clinical_order_bp.post(
    "/procedures/<int:procedure_order_id>/verify-consent"
)
@role_required(*PROCEDURE_WRITE_ROLES)
def verify_procedure_consent_route(procedure_order_id: int):
    order = verify_procedure_consent(
        procedure_order_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
    )
    return jsonify({
        "message": "Procedure consent verified",
        "order": _serialize(ProcedureOrderResponseSchema, order),
    }), 200


@clinical_order_bp.post(
    "/procedures/<int:procedure_order_id>/cancel"
)
@role_required(*PROCEDURE_WRITE_ROLES)
def cancel_procedure_order_route(procedure_order_id: int):
    payload = ProcedureOrderCancelSchema.model_validate(_json_body())
    order = cancel_procedure_order(
        procedure_order_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        reason=payload.reason,
    )
    return jsonify({
        "message": "Procedure order cancelled",
        "order": _serialize(ProcedureOrderResponseSchema, order),
    }), 200


@clinical_order_bp.post(
    "/procedures/<int:procedure_order_id>/complete"
)
@role_required(*PROCEDURE_WRITE_ROLES)
def complete_procedure_order_route(procedure_order_id: int):
    payload = ProcedureOrderCompleteSchema.model_validate(_json_body())
    order = complete_procedure_order(
        procedure_order_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        procedure_note=payload.procedure_note,
        outcome_summary=payload.outcome_summary,
        performed_at=payload.performed_at,
    )
    return jsonify({
        "message": "Procedure order completed",
        "order": _serialize(ProcedureOrderResponseSchema, order),
    }), 200
