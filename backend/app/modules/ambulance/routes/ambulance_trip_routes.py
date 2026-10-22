from flask import (
    Blueprint,
    jsonify,
    request,
)

from flask_jwt_extended import get_jwt_identity

from pydantic import ValidationError as PydanticValidationError

from app.extensions import db

from app.core.auth.user.models.user_model import User

from app.core.enums.ambulance_enums import TripStatus
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

from app.modules.ambulance.schemas.ambulance_trip_schema import (
    AmbulanceTripCancelSchema,
    AmbulanceTripDispatchSchema,
    AmbulanceTripInvoiceSchema,
    AmbulanceTripPatientSchema,
    AmbulanceTripRequestSchema,
    AmbulanceTripStatusSchema,
)

from app.modules.ambulance.services.ambulance_service import (
    cancel_trip,
    complete_trip,
    dispatch_trip,
    get_trip,
    link_invoice,
    link_patient,
    list_trips,
    request_trip,
    update_trip_status,
)


trip_bp = Blueprint(
    "ambulance_trips",
    __name__,
    url_prefix="/ambulance/trips",
)


TRIP_MANAGEMENT_ROLES = (
    Role.ADMIN,
    Role.AMBULANCE_COORDINATOR,
    Role.AMBULANCE_DISPATCHER,
)


TRIP_VIEW_ROLES = (
    Role.ADMIN,
    Role.AMBULANCE_COORDINATOR,
    Role.AMBULANCE_DISPATCHER,
    Role.DRIVER,
    Role.PARAMEDIC,
    Role.EMT,
)


TRIP_CREW_ROLES = (
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


def _trip_data(trip):
    # Serialize trip.
    return {
        "id": trip.id,
        "clinic_id": trip.clinic_id,
        "vehicle_id": trip.vehicle_id,
        "patient_id": trip.patient_id,
        "driver_id": trip.driver_id,
        "paramedic_id": trip.paramedic_id,
        "admission_id": trip.admission_id,
        "trip_type": (
            trip.trip_type.value
            if trip.trip_type is not None
            else None
        ),
        "status": (
            trip.status.value
            if trip.status is not None
            else None
        ),
        "pickup_address": trip.pickup_address,
        "pickup_lat": (
            float(trip.pickup_lat)
            if trip.pickup_lat is not None
            else None
        ),
        "pickup_lng": (
            float(trip.pickup_lng)
            if trip.pickup_lng is not None
            else None
        ),
        "destination_address": trip.destination_address,
        "destination_lat": (
            float(trip.destination_lat)
            if trip.destination_lat is not None
            else None
        ),
        "destination_lng": (
            float(trip.destination_lng)
            if trip.destination_lng is not None
            else None
        ),
        "requested_at": (
            trip.requested_at.isoformat()
            if trip.requested_at is not None
            else None
        ),
        "dispatched_at": (
            trip.dispatched_at.isoformat()
            if trip.dispatched_at is not None
            else None
        ),
        "pickup_at": (
            trip.pickup_at.isoformat()
            if trip.pickup_at is not None
            else None
        ),
        "completed_at": (
            trip.completed_at.isoformat()
            if trip.completed_at is not None
            else None
        ),
        "cancelled_at": (
            trip.cancelled_at.isoformat()
            if trip.cancelled_at is not None
            else None
        ),
        "cancellation_reason": trip.cancellation_reason,
        "notes": trip.notes,
        "invoice_id": trip.invoice_id,
    }


@trip_bp.post("")
@role_required(*TRIP_MANAGEMENT_ROLES)
@active_staff_required
def create_ambulance_trip():
    payload = _payload(
        AmbulanceTripRequestSchema,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()

        data = payload.model_dump()

        # Never accept clinic from client.
        data.pop("clinic_id", None)

        trip = request_trip(
            clinic_id=clinic_id,
            **data,
        )

        return jsonify(
            {
                "success": True,
                "data": _trip_data(trip),
            }
        ), 201

    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code


@trip_bp.get("")
@role_required(*TRIP_VIEW_ROLES)
@active_staff_required
def get_ambulance_trips():
    try:
        clinic_id = _current_clinic_id()

        status_value = request.args.get("status")
        status = None

        if status_value is not None:
            status_value = status_value.strip()

            if not status_value:
                raise ValidationError(
                    "Trip status cannot be empty"
                )

            try:
                status = TripStatus(status_value)
            except ValueError:
                raise ValidationError(
                    f"Invalid trip status: "
                    f"{status_value}"
                )

        page, per_page = _pagination_params()

        pagination = list_trips(
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
                        _trip_data(trip)
                        for trip in pagination.items
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


@trip_bp.get("/<int:trip_id>")
@role_required(*TRIP_VIEW_ROLES)
@active_staff_required
def get_ambulance_trip(
    trip_id: int,
):
    try:
        trip = get_trip(
            trip_id,
        )

        return jsonify(
            {
                "success": True,
                "data": _trip_data(trip),
            }
        ), 200

    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code


@trip_bp.post("/<int:trip_id>/dispatch")
@role_required(*TRIP_MANAGEMENT_ROLES)
@active_staff_required
def dispatch_ambulance_trip(
    trip_id: int,
):
    payload = _payload(
        AmbulanceTripDispatchSchema,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        trip = dispatch_trip(
            trip_id=trip_id,
            **payload.model_dump(),
        )

        return jsonify(
            {
                "success": True,
                "data": _trip_data(trip),
            }
        ), 200

    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code


@trip_bp.patch("/<int:trip_id>/status")
@role_required(*TRIP_CREW_ROLES)
@active_staff_required
def update_ambulance_trip_status(
    trip_id: int,
):
    payload = _payload(
        AmbulanceTripStatusSchema,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        trip = update_trip_status(
            trip_id=trip_id,
            new_status=payload.status,
        )

        return jsonify(
            {
                "success": True,
                "data": _trip_data(trip),
            }
        ), 200

    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code


@trip_bp.post("/<int:trip_id>/patient")
@role_required(*TRIP_MANAGEMENT_ROLES)
@active_staff_required
def link_ambulance_patient(
    trip_id: int,
):
    payload = _payload(
        AmbulanceTripPatientSchema,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        trip = link_patient(
            trip_id=trip_id,
            patient_id=payload.patient_id,
        )

        return jsonify(
            {
                "success": True,
                "data": _trip_data(trip),
            }
        ), 200

    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code


@trip_bp.post("/<int:trip_id>/complete")
@role_required(*TRIP_CREW_ROLES)
@active_staff_required
def complete_ambulance_trip(
    trip_id: int,
):
    try:
        trip = complete_trip(
            trip_id,
        )

        return jsonify(
            {
                "success": True,
                "data": _trip_data(trip),
            }
        ), 200

    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code


@trip_bp.post("/<int:trip_id>/invoice")
@role_required(*TRIP_MANAGEMENT_ROLES)
@active_staff_required
def link_ambulance_invoice(
    trip_id: int,
):
    payload = _payload(
        AmbulanceTripInvoiceSchema,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        trip = link_invoice(
            trip_id=trip_id,
            invoice_id=payload.invoice_id,
        )

        return jsonify(
            {
                "success": True,
                "data": _trip_data(trip),
            }
        ), 200

    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code


@trip_bp.post("/<int:trip_id>/cancel")
@role_required(*TRIP_MANAGEMENT_ROLES)
@active_staff_required
def cancel_ambulance_trip(
    trip_id: int,
):
    payload = _payload(
        AmbulanceTripCancelSchema,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        trip = cancel_trip(
            trip_id=trip_id,
            reason=payload.reason,
        )

        return jsonify(
            {
                "success": True,
                "data": _trip_data(trip),
            }
        ), 200

    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code