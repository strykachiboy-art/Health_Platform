from flask import (
    Blueprint,
    jsonify,
    request,
)

from flask_jwt_extended import get_jwt_identity

from pydantic import ValidationError as PydanticValidationError

from app.extensions import db

from app.core.auth.user.models.user_model import User

from app.core.enums.ambulance_enums import VehicleStatus
from app.core.enums.role_enums import Role

from app.core.exceptions import (
    DomainError,
    ValidationError,
)

from app.core.utils.decorators import (
    active_staff_required,
    get_current_clinic_id,
    role_required,
)

from app.modules.ambulance.schemas.ambulance_vehicle_schema import (
    AmbulanceVehicleCreateSchema,
    AmbulanceVehicleStatusSchema,
)

from app.modules.ambulance.services.ambulance_service import (
    create_vehicle,
    get_vehicle,
    list_vehicles,
    set_vehicle_status,
)


vehicle_bp = Blueprint(
    "ambulance_vehicles",
    __name__,
    url_prefix="/ambulance/vehicles",
)


VEHICLE_MANAGEMENT_ROLES = (
    Role.ADMIN,
    Role.AMBULANCE_COORDINATOR,
)


VEHICLE_VIEW_ROLES = (
    Role.ADMIN,
    Role.AMBULANCE_COORDINATOR,
    Role.AMBULANCE_DISPATCHER,
    Role.DRIVER,
    Role.PARAMEDIC,
    Role.EMT,
)


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500


def _payload(schema):
    # Validate request body.
    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Request body must be a JSON object",
                }
            ),
            422,
        )

    try:
        return schema.model_validate(payload)

    except PydanticValidationError as exc:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid request payload",
                    "details": exc.errors(),
                }
            ),
            422,
        )


def _current_user():
    # Resolve authenticated user.
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


def _get_int_query_param(
    name: str,
    *,
    default: int,
) -> int:
    raw_value = request.args.get(name)

    if raw_value is None:
        return default

    raw_value = raw_value.strip()

    if not raw_value:
        raise ValidationError(
            f"{name} must be a positive integer"
        )

    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise ValidationError(
            f"{name} must be a positive integer"
        )

    if value <= 0:
        raise ValidationError(
            f"{name} must be a positive integer"
        )

    return value


def _pagination_params():
    page = _get_int_query_param(
        "page",
        default=DEFAULT_PAGE,
    )

    per_page = _get_int_query_param(
        "per_page",
        default=DEFAULT_PER_PAGE,
    )

    if per_page > MAX_PER_PAGE:
        raise ValidationError(
            f"per_page cannot exceed {MAX_PER_PAGE}"
        )

    return page, per_page


def _vehicle_data(vehicle):
    # Serialize vehicle.
    return {
        "id": vehicle.id,
        "clinic_id": vehicle.clinic_id,
        "plate_number": vehicle.plate_number,
        "equipment_level": (
            vehicle.equipment_level.value
            if vehicle.equipment_level is not None
            else None
        ),
        "capacity": vehicle.capacity,
        "status": (
            vehicle.status.value
            if vehicle.status is not None
            else None
        ),
        "last_service_date": (
            vehicle.last_service_date.isoformat()
            if vehicle.last_service_date is not None
            else None
        ),
        "created_at": (
            vehicle.created_at.isoformat()
            if vehicle.created_at is not None
            else None
        ),
        "updated_at": (
            vehicle.updated_at.isoformat()
            if vehicle.updated_at is not None
            else None
        ),
    }


@vehicle_bp.post("")
@role_required(*VEHICLE_MANAGEMENT_ROLES)
@active_staff_required
def create_ambulance_vehicle():
    payload = _payload(
        AmbulanceVehicleCreateSchema,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()

        data = payload.model_dump()

        # Never accept clinic from client.
        data.pop("clinic_id", None)

        vehicle = create_vehicle(
            clinic_id=clinic_id,
            **data,
        )

        return jsonify(
            {
                "success": True,
                "data": _vehicle_data(vehicle),
            }
        ), 201

    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code


@vehicle_bp.get("")
@role_required(*VEHICLE_VIEW_ROLES)
@active_staff_required
def get_ambulance_vehicles():
    try:
        clinic_id = _current_clinic_id()

        status_value = request.args.get("status")
        status = None

        if status_value is not None:
            status_value = status_value.strip()

            if not status_value:
                raise ValidationError(
                    "Vehicle status cannot be empty"
                )

            try:
                status = VehicleStatus(status_value)
            except ValueError:
                raise ValidationError(
                    f"Invalid vehicle status: "
                    f"{status_value}"
                )

        page, per_page = _pagination_params()

        pagination = list_vehicles(
            clinic_id=clinic_id,
            status=status,
            page=page,
            per_page=per_page,
        )

        return jsonify(
            {
                "success": True,
                "data": {
                    "items": [
                        _vehicle_data(vehicle)
                        for vehicle in pagination.items
                    ],
                    "total": pagination.total,
                    "page": pagination.page,
                    "per_page": pagination.per_page,
                    "pages": pagination.pages,
                    "has_next": pagination.has_next,
                    "has_prev": pagination.has_prev,
                },
            }
        ), 200

    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code


@vehicle_bp.get("/<int:vehicle_id>")
@role_required(*VEHICLE_VIEW_ROLES)
@active_staff_required
def get_ambulance_vehicle(
    vehicle_id: int,
):
    try:
        vehicle = get_vehicle(
            vehicle_id,
        )

        return jsonify(
            {
                "success": True,
                "data": _vehicle_data(vehicle),
            }
        ), 200

    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code


@vehicle_bp.patch("/<int:vehicle_id>/status")
@role_required(*VEHICLE_MANAGEMENT_ROLES)
@active_staff_required
def update_ambulance_vehicle_status(
    vehicle_id: int,
):
    payload = _payload(
        AmbulanceVehicleStatusSchema,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        _current_clinic_id()

        vehicle = set_vehicle_status(
            vehicle_id=vehicle_id,
            new_status=payload.status,
        )

        return jsonify(
            {
                "success": True,
                "data": _vehicle_data(vehicle),
            }
        ), 200

    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code