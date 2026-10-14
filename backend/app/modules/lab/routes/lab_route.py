from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from app.extensions import db
from app.core.exceptions import DomainError, ValidationError
from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.utils.decorators import (
    emergency_aware_role_required,
    get_current_clinic_id,
    role_required,
)
from app.core.emergency_access.services.emergency_access_service import (
    assert_emergency_access,
)

from app.modules.lab.schemas.lab_schema import (
    LabEquipmentLinkSchema,
    LabOrderCancelSchema,
    LabOrderCreateSchema,
    LabOrderListQuerySchema,
    LabProcessingSchema,
    LabResultCreateSchema,
    LabSampleCollectionSchema,
    LabTestCreateSchema,
    LabTestListQuerySchema,
    LabTestUpdateSchema,
    LabVerificationSchema,
)

from app.modules.lab.services.lab_service import (
    cancel_order,
    collect_sample,
    complete_order,
    create_lab_order,
    create_lab_test,
    enter_result,
    get_lab_order,
    get_lab_test,
    link_equipment,
    list_lab_tests,
    list_orders_for_patient,
    process_sample,
    update_lab_test,
    verify_results,
)

from app.modules.staff.models.staff_model import Staff


# ============================================================================
# BLUEPRINT
# ============================================================================

lab_bp = Blueprint(
    "lab",
    __name__,
    url_prefix="/lab",
)


# ============================================================================
# ROLE GROUPS
# ============================================================================

LAB_MANAGEMENT_ROLES = (
    Role.ADMIN,
)

LAB_CLINICAL_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
)

LAB_TECHNICIAN_ROLES = (
    Role.ADMIN,
    Role.LAB_TECHNICIAN,
)

LAB_VIEW_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.RECEPTIONIST,
    Role.LAB_TECHNICIAN,
)


# ============================================================================
# AUTHENTICATION / TENANCY HELPERS
# ============================================================================

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
    Resolve the authenticated user's Staff record.

    Actor IDs are never accepted from the client.
    """
    user = _get_current_user()

    clinic_id = getattr(
        user,
        "clinic_id",
        None,
    )

    if clinic_id is None:
        raise DomainError(
            "Authenticated user is not associated "
            "with a clinic"
        )

    statement = (
        select(Staff)
        .where(
            Staff.user_id == user.id,
            Staff.clinic_id == clinic_id,
        )
    )

    staff = (
        db.session.execute(statement)
        .scalars()
        .first()
    )

    if staff is None:
        raise DomainError(
            "Authenticated user is not associated "
            "with a staff record"
        )

    return staff


def _get_current_staff_id() -> int:
    return _get_current_staff().id


# ============================================================================
# ERROR HANDLING
# ============================================================================

def _handle_route_error(exc):
    """
    Convert application/domain errors into consistent API responses.

    Unexpected exceptions are intentionally not exposed.
    """
    if isinstance(exc, DomainError):
        return jsonify({
            "success": False,
            "error": str(exc),
        }), exc.status_code

    return jsonify({
        "success": False,
        "error": "An unexpected error occurred",
    }), 400


def _validation_error_response(
    exc: PydanticValidationError,
):
    details = []

    for error in exc.errors():
        clean_error = {
            "type": error.get("type"),
            "loc": list(error.get("loc", ())),
            "msg": error.get("msg"),
        }

        if "input" in error:
            clean_error["input"] = error["input"]

        details.append(clean_error)

    return jsonify({
        "success": False,
        "error": "Validation failed",
        "details": details,
    }), 422


# ============================================================================
# SERIALIZERS
# ============================================================================

def _serialize_lab_test(test):
    return {
        "id": test.id,
        "clinic_id": test.clinic_id,
        "loinc_code": test.loinc_code,
        "name": test.name,
        "code": test.code,
        "sample_type": (
            test.sample_type.value
            if test.sample_type
            else None
        ),
        "reference_range": test.reference_range,
        "unit": test.unit,
        "price": (
            str(test.price)
            if test.price is not None
            else None
        ),
        "critical_low": (
            str(test.critical_low)
            if test.critical_low is not None
            else None
        ),
        "critical_high": (
            str(test.critical_high)
            if test.critical_high is not None
            else None
        ),
        "is_active": test.is_active,
        "created_at": (
            test.created_at.isoformat()
            if test.created_at
            else None
        ),
        "updated_at": (
            test.updated_at.isoformat()
            if test.updated_at
            else None
        ),
    }


def _serialize_lab_order_item(item):
    return {
        "id": item.id,
        "order_id": item.order_id,
        "test_id": item.test_id,
        "test": (
            _serialize_lab_test(item.test)
            if item.test
            else None
        ),
        "result_value": item.result_value,
        "flag": (
            item.flag.value
            if item.flag
            else None
        ),
        "result_notes": item.result_notes,
        "result_file_url": item.result_file_url,
        "resulted_at": (
            item.resulted_at.isoformat()
            if item.resulted_at
            else None
        ),
    }


def _serialize_lab_order(order):
    return {
        "id": order.id,
        "clinic_id": order.clinic_id,
        "patient_id": order.patient_id,
        "consultation_id": order.consultation_id,
        "ordered_by_id": order.ordered_by_id,
        "collected_by_id": order.collected_by_id,
        "processed_by_id": order.processed_by_id,
        "verified_by_id": order.verified_by_id,
        "status": (
            order.status.value
            if order.status
            else None
        ),
        "qr_code": order.qr_code,
        "sample_collected_at": (
            order.sample_collected_at.isoformat()
            if order.sample_collected_at
            else None
        ),
        "processed_at": (
            order.processed_at.isoformat()
            if order.processed_at
            else None
        ),
        "verified_at": (
            order.verified_at.isoformat()
            if order.verified_at
            else None
        ),
        "completed_at": (
            order.completed_at.isoformat()
            if order.completed_at
            else None
        ),
        "equipment_reference_id": (
            order.equipment_reference_id
        ),
        "cancellation_reason": (
            order.cancellation_reason
        ),
        "created_at": (
            order.created_at.isoformat()
            if order.created_at
            else None
        ),
        "updated_at": (
            order.updated_at.isoformat()
            if order.updated_at
            else None
        ),
        "items": [
            _serialize_lab_order_item(item)
            for item in order.items
        ],
    }


def _serialize_lab_test_page(result):
    return {
        "items": [
            _serialize_lab_test(test)
            for test in result.items
        ],
        "total": result.total,
        "page": result.page,
        "per_page": result.per_page,
    }


def _serialize_lab_order_page(result):
    return {
        "items": [
            _serialize_lab_order(order)
            for order in result.items
        ],
        "total": result.total,
        "page": result.page,
        "per_page": result.per_page,
    }


# ============================================================================
# LAB TEST CATALOG
# ============================================================================

@lab_bp.post("/tests")
@role_required(*LAB_MANAGEMENT_ROLES)
def create_lab_test_route():
    try:
        data = LabTestCreateSchema.model_validate(
            request.get_json(silent=True) or {}
        )

        clinic_id = _get_current_clinic_id()

        test = create_lab_test(
            clinic_id=clinic_id,
            name=data.name,
            loinc_code=data.loinc_code,
            code=data.code,
            sample_type=data.sample_type,
            reference_range=data.reference_range,
            unit=data.unit,
            price=data.price,
            critical_low=data.critical_low,
            critical_high=data.critical_high,
            is_active=data.is_active,
        )

        return jsonify({
            "success": True,
            "message": "Lab test created successfully",
            "data": _serialize_lab_test(test),
        }), 201

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except Exception as exc:
        return _handle_route_error(exc)


@lab_bp.get("/tests")
@role_required(*LAB_VIEW_ROLES)
def list_lab_tests_route():
    try:
        data = LabTestListQuerySchema.model_validate(
            request.args.to_dict()
        )

        clinic_id = _get_current_clinic_id()

        result = list_lab_tests(
            clinic_id=clinic_id,
            active_only=data.active_only,
            page=data.page,
            per_page=data.per_page,
        )

        return jsonify({
            "success": True,
            "data": _serialize_lab_test_page(result),
        }), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except Exception as exc:
        return _handle_route_error(exc)


@lab_bp.get("/tests/<int:test_id>")
@role_required(*LAB_VIEW_ROLES)
def get_lab_test_route(test_id: int):
    try:
        clinic_id = _get_current_clinic_id()

        test = get_lab_test(
            test_id=test_id,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "data": _serialize_lab_test(test),
        }), 200

    except Exception as exc:
        return _handle_route_error(exc)


@lab_bp.patch("/tests/<int:test_id>")
@role_required(*LAB_MANAGEMENT_ROLES)
def update_lab_test_route(test_id: int):
    try:
        data = LabTestUpdateSchema.model_validate(
            request.get_json(silent=True) or {}
        )

        clinic_id = _get_current_clinic_id()

        fields = data.model_dump(
            exclude_unset=True,
        )

        test = update_lab_test(
            test_id=test_id,
            clinic_id=clinic_id,
            **fields,
        )

        return jsonify({
            "success": True,
            "message": "Lab test updated successfully",
            "data": _serialize_lab_test(test),
        }), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except Exception as exc:
        return _handle_route_error(exc)


# ============================================================================
# LAB ORDERS
# ============================================================================

@lab_bp.post("/orders")
@role_required(*LAB_CLINICAL_ROLES)
def create_lab_order_route():
    try:
        data = LabOrderCreateSchema.model_validate(
            request.get_json(silent=True) or {}
        )

        clinic_id = _get_current_clinic_id()
        ordered_by_id = _get_current_staff_id()

        order = create_lab_order(
            clinic_id=clinic_id,
            patient_id=data.patient_id,
            ordered_by_id=ordered_by_id,
            test_ids=data.test_ids,
            consultation_id=data.consultation_id,
        )

        return jsonify({
            "success": True,
            "message": "Lab order created successfully",
            "data": _serialize_lab_order(order),
        }), 201

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except Exception as exc:
        return _handle_route_error(exc)


@lab_bp.get("/orders/<int:order_id>")
@emergency_aware_role_required(*LAB_VIEW_ROLES)
def get_lab_order_route(order_id: int):
    try:
        clinic_id = _get_current_clinic_id()

        order = get_lab_order(
            order_id=order_id,
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
                patient_id=order.patient_id,
                resource_type="lab_order",
                resource_id=order.id,
                action="read",
                emergency_access_id=emergency_access_id,
            ):
                return jsonify({
                    "success": False,
                    "message": "Emergency access is not authorized",
                }), 403

        return jsonify({
            "success": True,
            "data": _serialize_lab_order(order),
        }), 200

    except Exception as exc:
        return _handle_route_error(exc)


@lab_bp.get("/orders")
@emergency_aware_role_required(*LAB_VIEW_ROLES)
def list_orders_for_patient_route():
    try:
        data = LabOrderListQuerySchema.model_validate(
            request.args.to_dict()
        )

        clinic_id = _get_current_clinic_id()

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
                patient_id=data.patient_id,
                resource_type="lab_order_list",
                resource_id=data.patient_id,
                action="read",
                emergency_access_id=emergency_access_id,
            ):
                return jsonify({
                    "success": False,
                    "message": "Emergency access is not authorized",
                }), 403

        result = list_orders_for_patient(
            patient_id=data.patient_id,
            clinic_id=clinic_id,
            page=data.page,
            per_page=data.per_page,
        )

        return jsonify({
            "success": True,
            "data": _serialize_lab_order_page(result),
        }), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except Exception as exc:
        return _handle_route_error(exc)


# ============================================================================
# SAMPLE COLLECTION
# ============================================================================

@lab_bp.post(
    "/orders/<int:order_id>/collect-sample"
)
@role_required(*LAB_TECHNICIAN_ROLES)
def collect_sample_route(order_id: int):
    try:
        data = LabSampleCollectionSchema.model_validate(
            request.get_json(silent=True) or {}
        )

        clinic_id = _get_current_clinic_id()
        collected_by_id = _get_current_staff_id()

        order = collect_sample(
            order_id=order_id,
            collected_by_id=collected_by_id,
            scanned_qr_code=data.scanned_qr_code,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "message": "Lab sample collected successfully",
            "data": _serialize_lab_order(order),
        }), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except Exception as exc:
        return _handle_route_error(exc)


# ============================================================================
# EQUIPMENT
# ============================================================================

@lab_bp.post(
    "/orders/<int:order_id>/equipment"
)
@role_required(*LAB_TECHNICIAN_ROLES)
def link_equipment_route(order_id: int):
    try:
        data = LabEquipmentLinkSchema.model_validate(
            request.get_json(silent=True) or {}
        )

        clinic_id = _get_current_clinic_id()

        order = link_equipment(
            order_id=order_id,
            equipment_reference_id=(
                data.equipment_reference_id
            ),
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "message": (
                "Lab order linked to equipment successfully"
            ),
            "data": _serialize_lab_order(order),
        }), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except Exception as exc:
        return _handle_route_error(exc)


# ============================================================================
# SAMPLE PROCESSING
# ============================================================================

@lab_bp.post(
    "/orders/<int:order_id>/process"
)
@role_required(*LAB_TECHNICIAN_ROLES)
def process_sample_route(order_id: int):
    try:
        data = LabProcessingSchema.model_validate(
            request.get_json(silent=True) or {}
        )

        clinic_id = _get_current_clinic_id()
        processed_by_id = _get_current_staff_id()

        order = process_sample(
            order_id=order_id,
            processed_by_id=processed_by_id,
            clinic_id=clinic_id,
            equipment_reference_id=(
                data.equipment_reference_id
            ),
        )

        return jsonify({
            "success": True,
            "message": "Lab sample processed successfully",
            "data": _serialize_lab_order(order),
        }), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except Exception as exc:
        return _handle_route_error(exc)


# ============================================================================
# RESULT ENTRY
# ============================================================================

@lab_bp.post(
    "/order-items/<int:order_item_id>/result"
)
@role_required(*LAB_TECHNICIAN_ROLES)
def enter_result_route(order_item_id: int):
    try:
        data = LabResultCreateSchema.model_validate(
            request.get_json(silent=True) or {}
        )

        clinic_id = _get_current_clinic_id()

        item = enter_result(
            order_item_id=order_item_id,
            result_value=data.result_value,
            flag=data.flag,
            result_notes=data.result_notes,
            result_file_url=data.result_file_url,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "message": "Lab result entered successfully",
            "data": _serialize_lab_order_item(item),
        }), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except Exception as exc:
        return _handle_route_error(exc)


# ============================================================================
# RESULT VERIFICATION
# ============================================================================

@lab_bp.post(
    "/orders/<int:order_id>/verify"
)
@role_required(*LAB_TECHNICIAN_ROLES)
def verify_results_route(order_id: int):
    try:
        LabVerificationSchema.model_validate(
            request.get_json(silent=True) or {}
        )

        clinic_id = _get_current_clinic_id()
        verified_by_id = _get_current_staff_id()

        order = verify_results(
            order_id=order_id,
            verified_by_id=verified_by_id,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "message": "Lab results verified successfully",
            "data": _serialize_lab_order(order),
        }), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except Exception as exc:
        return _handle_route_error(exc)


# ============================================================================
# ORDER COMPLETION
# ============================================================================

@lab_bp.post(
    "/orders/<int:order_id>/complete"
)
@role_required(*LAB_TECHNICIAN_ROLES)
def complete_order_route(order_id: int):
    try:
        clinic_id = _get_current_clinic_id()

        order = complete_order(
            order_id=order_id,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "message": "Lab order completed successfully",
            "data": _serialize_lab_order(order),
        }), 200

    except Exception as exc:
        return _handle_route_error(exc)


# ============================================================================
# CANCELLATION
# ============================================================================

@lab_bp.post(
    "/orders/<int:order_id>/cancel"
)
@role_required(*LAB_CLINICAL_ROLES)
def cancel_order_route(order_id: int):
    try:
        data = LabOrderCancelSchema.model_validate(
            request.get_json(silent=True) or {}
        )

        clinic_id = _get_current_clinic_id()
        cancelled_by_id = _get_current_staff_id()

        order = cancel_order(
            order_id=order_id,
            reason=data.reason,
            clinic_id=clinic_id,
            cancelled_by_id=cancelled_by_id,
        )

        return jsonify({
            "success": True,
            "message": "Lab order cancelled successfully",
            "data": _serialize_lab_order(order),
        }), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except Exception as exc:
        return _handle_route_error(exc)