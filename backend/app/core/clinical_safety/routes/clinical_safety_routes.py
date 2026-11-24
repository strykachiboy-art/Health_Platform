from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity

from app.core.auth.user.models.user_model import User
from app.core.clinical_safety.schemas.alert_acknowledgement_schema import (
    AlertAcknowledgementCreateSchema,
    AlertAcknowledgementResponseSchema,
)
from app.core.clinical_safety.schemas.clinical_alert_schema import (
    ClinicalAlertListQuerySchema,
    ClinicalAlertResponseSchema,
)
from app.core.clinical_safety.schemas.clinical_rule_schema import (
    ClinicalRuleCreateSchema,
    ClinicalRuleListQuerySchema,
    ClinicalRuleResponseSchema,
    ClinicalRuleUpdateSchema,
)
from app.core.clinical_safety.services.alert_service import (
    acknowledge_clinical_alert,
    get_clinical_alert,
    list_clinical_alerts,
    resolve_clinical_alert,
)
from app.core.clinical_safety.services.clinical_rule_service import (
    create_clinical_rule,
    disable_clinical_rule,
    get_clinical_rule,
    list_clinical_rules,
    update_clinical_rule,
)
from app.core.enums.clinical_safety_enums import ClinicalAlertStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.core.utils.decorators import get_current_clinic_id, role_required
from app.extensions import db


clinical_safety_bp = Blueprint(
    "clinical_safety",
    __name__,
    url_prefix="/clinical-safety",
)


RULE_MANAGEMENT_ROLES = (
    Role.ADMIN,
    Role.SUPER_ADMIN,
)

CLINICAL_ALERT_ROLES = (
    Role.ADMIN,
    Role.SUPER_ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
    Role.PARAMEDIC,
    Role.EMT,
)

ALERT_RESOLUTION_ROLES = (
    Role.ADMIN,
    Role.SUPER_ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
    Role.PARAMEDIC,
    Role.EMT,
)


def _current_user() -> User:
    identity = get_jwt_identity()

    try:
        user_id = int(identity)
    except (TypeError, ValueError) as exc:
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


def _current_clinic_id(
    user: User,
) -> int | None:
    return get_current_clinic_id()


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


def _serialize_rule(rule):
    return ClinicalRuleResponseSchema.model_validate(
        rule
    ).model_dump(
        mode="json"
    )


def _serialize_alert(alert):
    return ClinicalAlertResponseSchema.model_validate(
        alert
    ).model_dump(
        mode="json"
    )


def _serialize_acknowledgement(acknowledgement):
    return AlertAcknowledgementResponseSchema.model_validate(
        acknowledgement
    ).model_dump(
        mode="json"
    )


@clinical_safety_bp.route(
    "/rules",
    methods=["POST"],
)
@role_required(*RULE_MANAGEMENT_ROLES)
def create_clinical_rule_route():
    user = _current_user()

    payload = ClinicalRuleCreateSchema.model_validate(
        _request_json()
    )

    clinic_id = _current_clinic_id(
        user
    )

    if (
        payload.scope.value == "global"
        and user.role != Role.SUPER_ADMIN
    ):
        raise ValidationError(
            "Only super admins can create global clinical rules"
        )

    if (
        payload.scope.value != "global"
        and clinic_id is None
    ):
        raise ValidationError(
            "Clinic-scoped clinical rules require a clinic-assigned user"
        )

    rule = create_clinical_rule(
        actor_user_id=user.id,
        clinic_id=(
            None
            if payload.scope.value == "global"
            else clinic_id
        ),
        data=payload,
        is_hard_rule=False,
    )

    return jsonify(
        {
            "message": "Clinical rule created successfully",
            "rule": _serialize_rule(rule),
        }
    ), 201


@clinical_safety_bp.route(
    "/rules",
    methods=["GET"],
)
@role_required(*RULE_MANAGEMENT_ROLES)
def list_clinical_rules_route():
    user = _current_user()

    payload = ClinicalRuleListQuerySchema.model_validate(
        _query_params()
    )

    result = list_clinical_rules(
        actor_user_id=user.id,
        clinic_id=_current_clinic_id(user),
        query=payload,
    )

    return jsonify(
        {
            "items": [
                _serialize_rule(rule)
                for rule in result["items"]
            ],
            "total": result["total"],
            "page": result["page"],
            "per_page": result["per_page"],
        }
    ), 200


@clinical_safety_bp.route(
    "/rules/<int:rule_id>",
    methods=["GET"],
)
@role_required(*RULE_MANAGEMENT_ROLES)
def get_clinical_rule_route(
    rule_id: int,
):
    user = _current_user()

    rule = get_clinical_rule(
        rule_id=rule_id,
        actor_user_id=user.id,
    )

    return jsonify(
        {
            "rule": _serialize_rule(rule),
        }
    ), 200


@clinical_safety_bp.route(
    "/rules/<int:rule_id>",
    methods=["PATCH"],
)
@role_required(*RULE_MANAGEMENT_ROLES)
def update_clinical_rule_route(
    rule_id: int,
):
    user = _current_user()

    payload = ClinicalRuleUpdateSchema.model_validate(
        _request_json()
    )

    rule = update_clinical_rule(
        rule_id=rule_id,
        actor_user_id=user.id,
        data=payload,
    )

    return jsonify(
        {
            "message": "Clinical rule updated successfully",
            "rule": _serialize_rule(rule),
        }
    ), 200


@clinical_safety_bp.route(
    "/rules/<int:rule_id>/disable",
    methods=["POST"],
)
@role_required(*RULE_MANAGEMENT_ROLES)
def disable_clinical_rule_route(
    rule_id: int,
):
    user = _current_user()

    rule = disable_clinical_rule(
        rule_id=rule_id,
        actor_user_id=user.id,
    )

    return jsonify(
        {
            "message": "Clinical rule disabled successfully",
            "rule": _serialize_rule(rule),
        }
    ), 200


@clinical_safety_bp.route(
    "/alerts",
    methods=["GET"],
)
@role_required(*CLINICAL_ALERT_ROLES)
def list_clinical_alerts_route():
    user = _current_user()

    clinic_id = _current_clinic_id(
        user
    )

    if clinic_id is None:
        raise ValidationError(
            "Clinical alert access requires a clinic-assigned user"
        )

    payload = ClinicalAlertListQuerySchema.model_validate(
        _query_params()
    )

    result = list_clinical_alerts(
        clinic_id=clinic_id,
        page=payload.page,
        per_page=payload.per_page,
        patient_id=payload.patient_id,
        status=payload.status,
        rule_id=payload.rule_id,
        severity=payload.severity,
        action=payload.action,
        source_type=payload.source_type,
        source_id=payload.source_id,
    )

    return jsonify(
        {
            "items": [
                _serialize_alert(alert)
                for alert in result["items"]
            ],
            "total": result["total"],
            "page": result["page"],
            "per_page": result["per_page"],
        }
    ), 200


@clinical_safety_bp.route(
    "/alerts/<int:alert_id>",
    methods=["GET"],
)
@role_required(*CLINICAL_ALERT_ROLES)
def get_clinical_alert_route(
    alert_id: int,
):
    user = _current_user()

    clinic_id = _current_clinic_id(
        user
    )

    if clinic_id is None:
        raise ValidationError(
            "Clinical alert access requires a clinic-assigned user"
        )

    alert = get_clinical_alert(
        clinic_id=clinic_id,
        alert_id=alert_id,
    )

    return jsonify(
        {
            "alert": _serialize_alert(alert),
        }
    ), 200


@clinical_safety_bp.route(
    "/alerts/<int:alert_id>/acknowledge",
    methods=["POST"],
)
@role_required(*CLINICAL_ALERT_ROLES)
def acknowledge_clinical_alert_route(
    alert_id: int,
):
    user = _current_user()

    clinic_id = _current_clinic_id(
        user
    )

    if clinic_id is None:
        raise ValidationError(
            "Clinical alert acknowledgement requires a clinic-assigned user"
        )

    payload = AlertAcknowledgementCreateSchema.model_validate(
        _request_json()
    )

    acknowledgement = acknowledge_clinical_alert(
        clinic_id=clinic_id,
        alert_id=alert_id,
        actor_user_id=user.id,
        data=payload,
    )

    return jsonify(
        {
            "message": "Clinical alert acknowledgement recorded",
            "acknowledgement": _serialize_acknowledgement(
                acknowledgement
            ),
        }
    ), 201


@clinical_safety_bp.route(
    "/alerts/<int:alert_id>/resolve",
    methods=["POST"],
)
@role_required(*ALERT_RESOLUTION_ROLES)
def resolve_clinical_alert_route(
    alert_id: int,
):
    user = _current_user()

    clinic_id = _current_clinic_id(
        user
    )

    if clinic_id is None:
        raise ValidationError(
            "Clinical alert resolution requires a clinic-assigned user"
        )

    alert = resolve_clinical_alert(
        clinic_id=clinic_id,
        alert_id=alert_id,
        actor_user_id=user.id,
    )

    return jsonify(
        {
            "message": "Clinical alert resolved successfully",
            "alert": _serialize_alert(alert),
        }
    ), 200
