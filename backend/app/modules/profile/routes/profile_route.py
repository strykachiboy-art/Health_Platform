from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity
from pydantic import ValidationError as PydanticValidationError

from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.core.storage.storage_service import (
    delete_file,
    save_profile_image,
)
from app.core.utils.decorators import role_required

from app.modules.clinic.services.clinic_operating_modes import operating_status
from app.modules.profile.schemas.profile_schema import (
    ProfileUpdateSchema,
)
from app.modules.profile.services.profile_service import (
    get_profile,
    get_public_profile,
    remove_profile_image,
    update_profile,
    upload_profile_image,
)


profile_bp = Blueprint(
    "profile",
    __name__,
    url_prefix="/profile",
)


PROFILE_ROLES = (
    Role.DOCTOR,
    Role.NURSE,
    Role.PATIENT,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
    Role.RECEPTIONIST,
    Role.ADMIN,
    Role.ACCOUNTANT,
    Role.PARAMEDIC,
    Role.OTHER,
    Role.DRIVER,
    Role.EMT,
    Role.AMBULANCE_DISPATCHER,
    Role.AMBULANCE_COORDINATOR,
)


def _current_user_id() -> int:
    identity = get_jwt_identity()

    if isinstance(identity, bool):
        raise ValidationError(
            "Invalid authentication identity"
        )

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

    return user_id


def _payload() -> dict:
    payload = request.get_json(silent=True)

    if payload is None:
        raise ValidationError(
            "Request body must contain valid JSON"
        )

    if not isinstance(payload, dict):
        raise ValidationError(
            "Request body must be a JSON object"
        )

    return payload


def _private_profile_payload(profile):
    record = profile.model_dump(mode="json")
    if (current_app.config.get("CLINIC_EXTENDED_OPERATING_HOURS_ENABLED", False) is True
            and profile.clinic is not None):
        clinic = profile.clinic
        record["clinic"].update(operating_status(
            clinic.opening_time, clinic.closing_time, clinic.timezone,
        ))
    return record


@profile_bp.get("/me")
@role_required(*PROFILE_ROLES)
def get_my_profile():
    user_id = _current_user_id()

    profile = get_profile(
        user_id=user_id,
    )

    return jsonify(
        {
            "success": True,
            "data": _private_profile_payload(profile),
        }
    ), 200


@profile_bp.get("/<int:user_id>")
def get_user_profile(user_id: int):
    profile = get_public_profile(
        user_id=user_id,
    )

    return jsonify(
        {
            "success": True,
            "data": profile.model_dump(
                mode="json"
            ),
        }
    ), 200


@profile_bp.patch("/me")
@role_required(*PROFILE_ROLES)
def update_my_profile():
    user_id = _current_user_id()

    payload = _payload()

    try:
        data = ProfileUpdateSchema.model_validate(
            payload
        )
    except PydanticValidationError as exc:
        raise exc

    profile = update_profile(
        user_id=user_id,
        **data.model_dump(
            exclude_unset=True
        ),
    )

    return jsonify(
        {
            "success": True,
            "message": "Profile updated successfully",
            "data": _private_profile_payload(profile),
        }
    ), 200


@profile_bp.post("/me/image")
@role_required(*PROFILE_ROLES)
def upload_my_profile_image():
    user_id = _current_user_id()

    image = request.files.get("image")

    if image is None:
        raise ValidationError(
            "Profile image file is required"
        )

    old_profile = get_profile(
        user_id=user_id,
    )

    old_storage_key = (
        old_profile.user.profile_image_storage_key
    )

    new_storage_key = save_profile_image(
        image
    )

    try:
        result = upload_profile_image(
            actor_user_id=user_id,
            storage_key=new_storage_key,
        )
    except Exception:
        delete_file(
            new_storage_key
        )
        raise

    if (
        old_storage_key
        and old_storage_key != new_storage_key
    ):
        delete_file(
            old_storage_key
        )

    return jsonify(
        {
            "success": True,
            "message": "Profile image updated successfully",
            "data": result.model_dump(
                mode="json"
            ),
        }
    ), 200


@profile_bp.delete("/me/image")
@role_required(*PROFILE_ROLES)
def delete_my_profile_image():
    user_id = _current_user_id()

    profile = get_profile(
        user_id=user_id,
    )

    storage_key = (
        profile.user.profile_image_storage_key
    )

    result = remove_profile_image(
        actor_user_id=user_id,
    )

    if storage_key:
        delete_file(
            storage_key
        )

    return jsonify(
        {
            "success": True,
            "message": "Profile image removed successfully",
            "data": result.model_dump(
                mode="json"
            ),
        }
    ), 200