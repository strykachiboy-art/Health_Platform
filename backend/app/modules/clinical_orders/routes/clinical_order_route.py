
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
from app.modules.clinical_orders.schemas.blood_product_order_schema import (
    BloodProductCancelSchema,
    BloodProductCompatibilitySchema,
    BloodProductFulfillSchema,
    BloodProductOrderCreateSchema,
    BloodProductOrderListResponseSchema,
    BloodProductOrderResponseSchema,
)
from app.modules.clinical_orders.schemas.diet_order_schema import (
    DietOrderCancelSchema,
    DietOrderCompleteSchema,
    DietOrderCreateSchema,
    DietOrderListResponseSchema,
    DietOrderResponseSchema,
)
from app.modules.clinical_orders.schemas.nursing_order_schema import (
    NursingOrderCancelSchema,
    NursingOrderCompleteSchema,
    NursingOrderCreateSchema,
    NursingOrderExecuteSchema,
    NursingOrderExecutionListResponseSchema,
    NursingOrderExecutionResponseSchema,
    NursingOrderListResponseSchema,
    NursingOrderResponseSchema,
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
from app.modules.clinical_orders.services.blood_product_order_service import (
    cancel_blood_product_order,
    create_blood_product_order,
    fulfill_blood_product_order,
    get_blood_product_order,
    list_blood_product_orders,
    verify_blood_product_compatibility,
)
from app.modules.clinical_orders.services.diet_order_service import (
    cancel_diet_order,
    complete_diet_order,
    create_diet_order,
    get_diet_order,
    list_diet_orders,
)
from app.modules.clinical_orders.services.nursing_order_service import (
    cancel_nursing_order,
    complete_nursing_order,
    create_nursing_order,
    get_nursing_order,
    list_nursing_order_executions,
    list_nursing_orders,
    record_nursing_order_execution,
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

BLOOD_PRODUCT_ORDER_ROLES = (
    Role.DOCTOR,
)

BLOOD_PRODUCT_VERIFY_ROLES = (
    Role.LAB_TECHNICIAN,
)

BLOOD_PRODUCT_VIEW_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.LAB_TECHNICIAN,
)

DIET_WRITE_ROLES = (
    Role.DOCTOR,
)

DIET_VIEW_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
)

NURSING_ORDER_WRITE_ROLES = (
    Role.DOCTOR,
)

NURSING_ORDER_EXECUTION_ROLES = (
    Role.NURSE,
)

NURSING_ORDER_VIEW_ROLES = (
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

@clinical_order_bp.post("/blood-products")
@role_required(*BLOOD_PRODUCT_ORDER_ROLES)
def create_blood_product_order_route():
    payload = BloodProductOrderCreateSchema.model_validate(_json_body())
    order = create_blood_product_order(
        admission_id=payload.admission_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        product_type=payload.product_type,
        units_requested=payload.units_requested,
        indication=payload.indication,
        consultation_id=payload.consultation_id,
        special_requirements=payload.special_requirements,
        priority=payload.priority,
        scheduled_at=payload.scheduled_at,
    )
    return jsonify({
        "message": "Blood product order created",
        "order": _serialize(BloodProductOrderResponseSchema, order),
    }), 201


@clinical_order_bp.get("/blood-products/<int:blood_product_order_id>")
@role_required(*BLOOD_PRODUCT_VIEW_ROLES)
def get_blood_product_order_route(blood_product_order_id: int):
    order = get_blood_product_order(
        blood_product_order_id,
        clinic_id=_clinic_id(),
    )
    return jsonify(
        _serialize(BloodProductOrderResponseSchema, order)
    ), 200


@clinical_order_bp.get("/blood-products")
@role_required(*BLOOD_PRODUCT_VIEW_ROLES)
def list_blood_product_orders_route():
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

    items = list_blood_product_orders(
        admission_id=admission_id,
        clinic_id=_clinic_id(),
    )
    payload = {
        "items": [
            _serialize(BloodProductOrderResponseSchema, item)
            for item in items
        ],
        "total": len(items),
    }
    return jsonify(
        BloodProductOrderListResponseSchema
        .model_validate(payload)
        .model_dump(mode="json")
    ), 200


@clinical_order_bp.post(
    "/blood-products/<int:blood_product_order_id>/verify-compatibility"
)
@role_required(*BLOOD_PRODUCT_VERIFY_ROLES)
def verify_blood_product_compatibility_route(
    blood_product_order_id: int,
):
    payload = BloodProductCompatibilitySchema.model_validate(
        _json_body()
    )
    order = verify_blood_product_compatibility(
        blood_product_order_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        emergency_release=payload.emergency_release,
        crossmatch_reference=payload.crossmatch_reference,
        product_blood_type=payload.product_blood_type,
        notes=payload.notes,
        emergency_release_reason=payload.emergency_release_reason,
    )
    return jsonify({
        "message": "Blood product compatibility verified",
        "order": _serialize(BloodProductOrderResponseSchema, order),
    }), 200


@clinical_order_bp.post(
    "/blood-products/<int:blood_product_order_id>/fulfill"
)
@role_required(*BLOOD_PRODUCT_VERIFY_ROLES)
def fulfill_blood_product_order_route(
    blood_product_order_id: int,
):
    payload = BloodProductFulfillSchema.model_validate(_json_body())
    order = fulfill_blood_product_order(
        blood_product_order_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        units_issued=payload.units_issued,
        fulfillment_note=payload.fulfillment_note,
    )
    return jsonify({
        "message": "Blood product order fulfilled",
        "order": _serialize(BloodProductOrderResponseSchema, order),
    }), 200


@clinical_order_bp.post(
    "/blood-products/<int:blood_product_order_id>/cancel"
)
@role_required(*BLOOD_PRODUCT_ORDER_ROLES)
def cancel_blood_product_order_route(
    blood_product_order_id: int,
):
    payload = BloodProductCancelSchema.model_validate(_json_body())
    order = cancel_blood_product_order(
        blood_product_order_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        reason=payload.reason,
    )
    return jsonify({
        "message": "Blood product order cancelled",
        "order": _serialize(BloodProductOrderResponseSchema, order),
    }), 200

@clinical_order_bp.post("/diets")
@role_required(*DIET_WRITE_ROLES)
def create_diet_order_route():
    payload = DietOrderCreateSchema.model_validate(_json_body())
    order = create_diet_order(
        admission_id=payload.admission_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        diet_type=payload.diet_type,
        consultation_id=payload.consultation_id,
        custom_description=payload.custom_description,
        feeding_route=payload.feeding_route,
        texture=payload.texture,
        fluid_restriction_ml_per_day=(
            payload.fluid_restriction_ml_per_day
        ),
        sodium_restriction_mg_per_day=(
            payload.sodium_restriction_mg_per_day
        ),
        calorie_target_kcal_per_day=(
            payload.calorie_target_kcal_per_day
        ),
        instructions=payload.instructions,
        priority=payload.priority,
        start_at=payload.start_at,
    )
    return jsonify({
        "message": "Diet order created",
        "order": _serialize(DietOrderResponseSchema, order),
    }), 201


@clinical_order_bp.get("/diets/<int:diet_order_id>")
@role_required(*DIET_VIEW_ROLES)
def get_diet_order_route(diet_order_id: int):
    order = get_diet_order(
        diet_order_id,
        clinic_id=_clinic_id(),
    )
    return jsonify(
        _serialize(DietOrderResponseSchema, order)
    ), 200


@clinical_order_bp.get("/diets")
@role_required(*DIET_VIEW_ROLES)
def list_diet_orders_route():
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

    items = list_diet_orders(
        admission_id=admission_id,
        clinic_id=_clinic_id(),
    )
    payload = {
        "items": [
            _serialize(DietOrderResponseSchema, item)
            for item in items
        ],
        "total": len(items),
    }
    return jsonify(
        DietOrderListResponseSchema
        .model_validate(payload)
        .model_dump(mode="json")
    ), 200


@clinical_order_bp.post("/diets/<int:diet_order_id>/complete")
@role_required(*DIET_WRITE_ROLES)
def complete_diet_order_route(diet_order_id: int):
    payload = DietOrderCompleteSchema.model_validate(_json_body())
    order = complete_diet_order(
        diet_order_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        completion_note=payload.completion_note,
    )
    return jsonify({
        "message": "Diet order completed",
        "order": _serialize(DietOrderResponseSchema, order),
    }), 200


@clinical_order_bp.post("/diets/<int:diet_order_id>/cancel")
@role_required(*DIET_WRITE_ROLES)
def cancel_diet_order_route(diet_order_id: int):
    payload = DietOrderCancelSchema.model_validate(_json_body())
    order = cancel_diet_order(
        diet_order_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        reason=payload.reason,
    )
    return jsonify({
        "message": "Diet order cancelled",
        "order": _serialize(DietOrderResponseSchema, order),
    }), 200

@clinical_order_bp.post("/nursing-orders")
@role_required(*NURSING_ORDER_WRITE_ROLES)
def create_nursing_order_route():
    payload = NursingOrderCreateSchema.model_validate(
        _json_body()
    )
    order = create_nursing_order(
        admission_id=payload.admission_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        category=payload.category,
        order_text=payload.order_text,
        frequency=payload.frequency,
        consultation_id=payload.consultation_id,
        instructions=payload.instructions,
        custom_frequency=payload.custom_frequency,
        assigned_to_staff_id=payload.assigned_to_staff_id,
        priority=payload.priority,
        scheduled_at=payload.scheduled_at,
    )
    return jsonify({
        "message": "Nursing order created",
        "order": _serialize(
            NursingOrderResponseSchema,
            order,
        ),
    }), 201


@clinical_order_bp.get(
    "/nursing-orders/<int:nursing_order_id>"
)
@role_required(*NURSING_ORDER_VIEW_ROLES)
def get_nursing_order_route(
    nursing_order_id: int,
):
    order = get_nursing_order(
        nursing_order_id,
        clinic_id=_clinic_id(),
    )
    return jsonify(
        _serialize(
            NursingOrderResponseSchema,
            order,
        )
    ), 200


@clinical_order_bp.get("/nursing-orders")
@role_required(*NURSING_ORDER_VIEW_ROLES)
def list_nursing_orders_route():
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

    items = list_nursing_orders(
        admission_id=admission_id,
        clinic_id=_clinic_id(),
    )

    payload = {
        "items": [
            _serialize(
                NursingOrderResponseSchema,
                item,
            )
            for item in items
        ],
        "total": len(items),
    }

    return jsonify(
        NursingOrderListResponseSchema
        .model_validate(payload)
        .model_dump(mode="json")
    ), 200


@clinical_order_bp.get(
    "/nursing-orders/<int:nursing_order_id>/executions"
)
@role_required(*NURSING_ORDER_VIEW_ROLES)
def list_nursing_order_executions_route(
    nursing_order_id: int,
):
    items = list_nursing_order_executions(
        nursing_order_id,
        clinic_id=_clinic_id(),
    )

    payload = {
        "items": [
            _serialize(
                NursingOrderExecutionResponseSchema,
                item,
            )
            for item in items
        ],
        "total": len(items),
    }

    return jsonify(
        NursingOrderExecutionListResponseSchema
        .model_validate(payload)
        .model_dump(mode="json")
    ), 200


@clinical_order_bp.post(
    "/nursing-orders/<int:nursing_order_id>/execute"
)
@role_required(*NURSING_ORDER_EXECUTION_ROLES)
def execute_nursing_order_route(
    nursing_order_id: int,
):
    payload = NursingOrderExecuteSchema.model_validate(
        _json_body()
    )

    execution = record_nursing_order_execution(
        nursing_order_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        result_note=payload.result_note,
        performed_at=payload.performed_at,
    )

    order = get_nursing_order(
        nursing_order_id,
        clinic_id=_clinic_id(),
    )

    return jsonify({
        "message": "Nursing order execution recorded",
        "execution": _serialize(
            NursingOrderExecutionResponseSchema,
            execution,
        ),
        "order": _serialize(
            NursingOrderResponseSchema,
            order,
        ),
    }), 200


@clinical_order_bp.post(
    "/nursing-orders/<int:nursing_order_id>/complete"
)
@role_required(*NURSING_ORDER_WRITE_ROLES)
def complete_nursing_order_route(
    nursing_order_id: int,
):
    payload = NursingOrderCompleteSchema.model_validate(
        _json_body()
    )

    order = complete_nursing_order(
        nursing_order_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        completion_note=payload.completion_note,
    )

    return jsonify({
        "message": "Nursing order completed",
        "order": _serialize(
            NursingOrderResponseSchema,
            order,
        ),
    }), 200


@clinical_order_bp.post(
    "/nursing-orders/<int:nursing_order_id>/cancel"
)
@role_required(*NURSING_ORDER_WRITE_ROLES)
def cancel_nursing_order_route(
    nursing_order_id: int,
):
    payload = NursingOrderCancelSchema.model_validate(
        _json_body()
    )

    order = cancel_nursing_order(
        nursing_order_id,
        clinic_id=_clinic_id(),
        actor_staff_id=_current_staff_id(),
        reason=payload.reason,
    )

    return jsonify({
        "message": "Nursing order cancelled",
        "order": _serialize(
            NursingOrderResponseSchema,
            order,
        ),
    }), 200
