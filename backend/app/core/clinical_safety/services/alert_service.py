from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import func

from app.core.audit.services.audit_service import create_audit_log
from app.core.clinical_safety.models.alert_acknowledgement_model import (
    AlertAcknowledgement,
)
from app.core.clinical_safety.models.clinical_alert_model import (
    ClinicalAlert,
)
from app.core.clinical_safety.models.clinical_rule_model import (
    ClinicalRule,
)
from app.core.clinical_safety.schemas.alert_acknowledgement_schema import (
    AlertAcknowledgementCreateSchema,
)
from app.core.clinical_safety.services.rule_engine_service import (
    ClinicalSafetyEvaluation,
)
from app.core.clinical_safety.services.rule_evaluator import (
    RuleEvaluationResult,
)
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinical_safety_enums import (
    AlertAcknowledgementType,
    ClinicalAlertStatus,
    ClinicalRuleAction,
    ClinicalRuleSeverity,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.patient.models.patient_model import Patient
from app.core.auth.user.models.user_model import User


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500

_SOURCE_TYPE_PATTERN = re.compile(
    r"^[a-z][a-z0-9_.-]{0,99}$"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_positive_id(
    value: Any,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )

    return value


def _normalize_source_type(
    value: str,
) -> str:
    if not isinstance(value, str):
        raise ValidationError(
            "Source type must be a string"
        )

    value = value.strip().lower()

    if not value:
        raise ValidationError(
            "Source type is required"
        )

    if not _SOURCE_TYPE_PATTERN.fullmatch(value):
        raise ValidationError(
            "Source type contains invalid characters"
        )

    return value


def _validate_source_id(
    value: int | None,
) -> int | None:
    if value is None:
        return None

    return _validate_positive_id(
        value,
        "Source ID",
    )


def _validate_pagination(
    page: int,
    per_page: int,
) -> tuple[int, int]:
    if (
        isinstance(page, bool)
        or not isinstance(page, int)
        or page < 1
    ):
        raise ValidationError(
            "Page must be a positive integer"
        )

    if (
        isinstance(per_page, bool)
        or not isinstance(per_page, int)
        or per_page < 1
    ):
        raise ValidationError(
            "per_page must be a positive integer"
        )

    if per_page > MAX_PER_PAGE:
        raise ValidationError(
            f"per_page must not exceed {MAX_PER_PAGE}"
        )

    return page, per_page


def _get_alert(
    alert_id: int,
    *,
    for_update: bool = False,
) -> ClinicalAlert:
    alert_id = _validate_positive_id(
        alert_id,
        "Alert ID",
    )

    statement = (
        db.select(ClinicalAlert)
        .where(
            ClinicalAlert.id == alert_id,
        )
    )

    if for_update:
        statement = statement.with_for_update()

    alert = db.session.execute(
        statement
    ).scalar_one_or_none()

    if alert is None:
        raise NotFoundError(
            f"Clinical alert {alert_id} not found"
        )

    return alert


def _get_patient(
    patient_id: int,
) -> Patient:
    patient_id = _validate_positive_id(
        patient_id,
        "Patient ID",
    )

    patient = db.session.get(
        Patient,
        patient_id,
    )

    if patient is None:
        raise NotFoundError(
            f"Patient {patient_id} not found"
        )

    return patient


def _ensure_patient_in_clinic(
    patient_id: int,
    clinic_id: int,
) -> Patient:
    patient = _get_patient(
        patient_id
    )

    if patient.clinic_id != clinic_id:
        raise NotFoundError(
            f"Patient {patient_id} not found"
        )

    return patient


def _get_actor(
    actor_user_id: int,
) -> User:
    actor_user_id = _validate_positive_id(
        actor_user_id,
        "Actor user ID",
    )

    actor = db.session.get(
        User,
        actor_user_id,
    )

    if actor is None:
        raise NotFoundError(
            f"User {actor_user_id} not found"
        )

    if not actor.is_active:
        raise ValidationError(
            "Actor user is inactive"
        )

    return actor


def _validate_actor_clinic(
    actor: User,
    clinic_id: int,
) -> None:
    if actor.clinic_id != clinic_id:
        raise NotFoundError(
            "Clinical alert not found"
        )


def _canonical_context(
    context: Mapping[str, Any] | None,
) -> str:
    normalized = (
        dict(context)
        if context is not None
        else {}
    )

    if not isinstance(
        normalized,
        dict,
    ):
        raise ValidationError(
            "Alert context must be an object"
        )

    try:
        return json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "Alert context cannot be serialized"
        ) from exc


def build_alert_deduplication_key(
    *,
    patient_id: int,
    result: RuleEvaluationResult,
    source_type: str,
    source_id: int | None,
    context: Mapping[str, Any] | None,
) -> str:
    payload = {
        "patient_id": patient_id,
        "rule_id": result.rule_id,
        "rule_version": result.rule_version,
        "action": result.action,
        "source_type": source_type,
        "source_id": source_id,
        "context": (
            json.loads(
                _canonical_context(context)
            )
        ),
    }

    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _result_title(
    result: RuleEvaluationResult,
) -> str:
    return (
        f"Clinical Safety Alert: "
        f"{result.rule_code}"
    )


def _result_message(
    result: RuleEvaluationResult,
) -> str:
    return result.reason.strip() or (
        f"Clinical safety rule "
        f"{result.rule_code} was triggered"
    )


def _existing_alert_for_key(
    deduplication_key: str,
    *,
    for_update: bool = False,
) -> ClinicalAlert | None:
    statement = (
        db.select(ClinicalAlert)
        .where(
            ClinicalAlert.deduplication_key
            == deduplication_key,
        )
        .order_by(
            ClinicalAlert.id.desc(),
        )
        .limit(1)
    )

    if for_update:
        statement = statement.with_for_update()

    return db.session.execute(
        statement
    ).scalar_one_or_none()


def _create_alert_from_result(
    *,
    clinic_id: int,
    patient_id: int,
    result: RuleEvaluationResult,
    source_type: str,
    source_id: int | None,
    context: Mapping[str, Any] | None,
) -> ClinicalAlert:
    deduplication_key = (
        build_alert_deduplication_key(
            patient_id=patient_id,
            result=result,
            source_type=source_type,
            source_id=source_id,
            context=context,
        )
    )

    existing = _existing_alert_for_key(
        deduplication_key,
        for_update=True,
    )

    if existing is not None:
        return existing

    context_payload = (
        dict(context)
        if context is not None
        else {}
    )

    context_payload["rule_evaluation"] = (
        result.to_dict()
    )

    alert = ClinicalAlert(
        clinic_id=clinic_id,
        patient_id=patient_id,
        rule_id=result.rule_id,
        rule_version=result.rule_version,
        severity=result.severity,
        action=result.action,
        status=ClinicalAlertStatus.OPEN,
        title=_result_title(result),
        message=_result_message(result),
        context=context_payload,
        source_type=source_type,
        source_id=source_id,
        deduplication_key=deduplication_key,
        generated_at=_utcnow(),
    )

    db.session.add(alert)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="ClinicalAlert",
        entity_id=alert.id,
        description=(
            "Clinical safety alert generated"
        ),
        new_value={
            "clinic_id": clinic_id,
            "patient_id": patient_id,
            "rule_id": result.rule_id,
            "rule_version": result.rule_version,
            "severity": result.severity,
            "action": result.action,
            "status": ClinicalAlertStatus.OPEN.value,
            "source_type": source_type,
            "source_id": source_id,
            "deduplication_key": deduplication_key,
        },
    )

    return alert


@transactional
def create_alerts_from_evaluation(
    *,
    clinic_id: int,
    patient_id: int,
    evaluation: ClinicalSafetyEvaluation,
    source_type: str,
    source_id: int | None = None,
    context: Mapping[str, Any] | None = None,
) -> list[ClinicalAlert]:
    clinic_id = _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    patient_id = _validate_positive_id(
        patient_id,
        "Patient ID",
    )

    if evaluation.clinic_id != clinic_id:
        raise ValidationError(
            "Clinical safety evaluation clinic does not match alert clinic"
        )

    _ensure_patient_in_clinic(
        patient_id,
        clinic_id,
    )

    source_type = _normalize_source_type(
        source_type
    )

    source_id = _validate_source_id(
        source_id
    )

    if context is not None and not isinstance(
        context,
        Mapping,
    ):
        raise ValidationError(
            "Alert context must be an object"
        )

    alerts: list[ClinicalAlert] = []

    for result in evaluation.results:
        if not result.matched:
            continue

        alerts.append(
            _create_alert_from_result(
                clinic_id=clinic_id,
                patient_id=patient_id,
                result=result,
                source_type=source_type,
                source_id=source_id,
                context=context,
            )
        )

    return alerts


def get_clinical_alert(
    *,
    clinic_id: int,
    alert_id: int,
) -> ClinicalAlert:
    clinic_id = _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    alert = _get_alert(
        alert_id
    )

    if alert.clinic_id != clinic_id:
        raise NotFoundError(
            "Clinical alert not found"
        )

    return alert


def list_clinical_alerts(
    *,
    clinic_id: int,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
    patient_id: int | None = None,
    status: ClinicalAlertStatus | str | None = None,
    rule_id: int | None = None,
    severity: ClinicalRuleSeverity | str | None = None,
    action: ClinicalRuleAction | str | None = None,
    source_type: str | None = None,
    source_id: int | None = None,
) -> dict[str, Any]:
    clinic_id = _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    filters = [
        ClinicalAlert.clinic_id == clinic_id,
    ]

    if patient_id is not None:
        patient_id = _validate_positive_id(
            patient_id,
            "Patient ID",
        )

        filters.append(
            ClinicalAlert.patient_id
            == patient_id
        )

    if status is not None:
        try:
            status = ClinicalAlertStatus(
                status
            )
        except ValueError as exc:
            raise ValidationError(
                "Invalid clinical alert status"
            ) from exc

        filters.append(
            ClinicalAlert.status == status
        )

    if rule_id is not None:
        rule_id = _validate_positive_id(
            rule_id,
            "Rule ID",
        )

        filters.append(
            ClinicalAlert.rule_id == rule_id
        )

    if severity is not None:
        try:
            severity = ClinicalRuleSeverity(severity)
        except ValueError as exc:
            raise ValidationError(
                "Invalid clinical alert severity"
            ) from exc
        filters.append(ClinicalAlert.severity == severity)

    if action is not None:
        try:
            action = ClinicalRuleAction(action)
        except ValueError as exc:
            raise ValidationError(
                "Invalid clinical alert action"
            ) from exc
        filters.append(ClinicalAlert.action == action)

    if source_type is not None:
        source_type = _normalize_source_type(source_type)
        filters.append(ClinicalAlert.source_type == source_type)

    if source_id is not None:
        source_id = _validate_positive_id(source_id, "Source ID")
        filters.append(ClinicalAlert.source_id == source_id)

    total = db.session.execute(
        db.select(
            func.count()
        )
        .select_from(
            ClinicalAlert
        )
        .where(*filters)
    ).scalar_one()

    items = db.session.execute(
        db.select(
            ClinicalAlert
        )
        .where(*filters)
        .order_by(
            ClinicalAlert.generated_at.desc(),
            ClinicalAlert.id.desc(),
        )
        .offset(
            (page - 1) * per_page
        )
        .limit(
            per_page
        )
    ).scalars().all()

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@transactional
def resolve_clinical_alert(
    *,
    clinic_id: int,
    alert_id: int,
    actor_user_id: int,
) -> ClinicalAlert:
    clinic_id = _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    alert = _get_alert(
        alert_id,
        for_update=True,
    )

    if alert.clinic_id != clinic_id:
        raise NotFoundError(
            "Clinical alert not found"
        )

    actor = _get_actor(
        actor_user_id
    )

    _validate_actor_clinic(
        actor,
        clinic_id,
    )

    if alert.status in {
        ClinicalAlertStatus.RESOLVED,
        ClinicalAlertStatus.EXPIRED,
    }:
        raise ConflictError(
            f"Clinical alert {alert.id} "
            f"cannot be resolved from "
            f"status '{alert.status.value}'"
        )

    old_status = alert.status

    alert.status = ClinicalAlertStatus.RESOLVED
    alert.resolved_at = _utcnow()

    db.session.flush()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="ClinicalAlert",
        entity_id=alert.id,
        description="Clinical safety alert resolved",
        old_value={
            "status": old_status.value,
        },
        new_value={
            "status": alert.status.value,
            "resolved_at": alert.resolved_at.isoformat(),
        },
        user_id=actor.id,
    )

    return alert


@transactional
def acknowledge_clinical_alert(
    *,
    clinic_id: int,
    alert_id: int,
    actor_user_id: int,
    data: AlertAcknowledgementCreateSchema
    | Mapping[str, Any],
) -> AlertAcknowledgement:
    clinic_id = _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    alert = _get_alert(
        alert_id,
        for_update=True,
    )

    if alert.clinic_id != clinic_id:
        raise NotFoundError(
            "Clinical alert not found"
        )

    actor = _get_actor(
        actor_user_id
    )

    _validate_actor_clinic(
        actor,
        clinic_id,
    )

    if alert.status in {
        ClinicalAlertStatus.RESOLVED,
        ClinicalAlertStatus.EXPIRED,
    }:
        raise ConflictError(
            f"Clinical alert {alert.id} "
            f"cannot be acknowledged from "
            f"status '{alert.status.value}'"
        )

    if isinstance(data, Mapping):
        data = AlertAcknowledgementCreateSchema.model_validate(
            data
        )

    existing = db.session.execute(
        db.select(AlertAcknowledgement)
        .where(
            AlertAcknowledgement.alert_id
            == alert.id,
            AlertAcknowledgement.acknowledged_by_user_id
            == actor.id,
            AlertAcknowledgement.acknowledgement_type
            == data.acknowledgement_type,
        )
        .limit(1)
    ).scalar_one_or_none()

    if existing is not None:
        raise ConflictError(
            "This alert has already been acknowledged "
            "by this user with the same acknowledgement type"
        )

    if (
        data.acknowledgement_type
        == AlertAcknowledgementType.OVERRIDDEN
    ):
        rule = db.session.get(
            ClinicalRule,
            alert.rule_id,
        )

        if rule is None:
            raise ValidationError(
                "Clinical alert references a missing rule"
            )

        if (
            rule.is_hard_rule
            and str(getattr(alert.severity, "value", alert.severity)).lower()
            == "critical"
        ):
            raise ValidationError(
                "Critical hard clinical safety alerts "
                "cannot be overridden"
            )

    acknowledgement = AlertAcknowledgement(
        alert_id=alert.id,
        acknowledged_by_user_id=actor.id,
        acknowledgement_type=(
            data.acknowledgement_type
        ),
        justification=data.justification,
        acknowledged_at=_utcnow(),
    )

    db.session.add(
        acknowledgement
    )

    old_status = alert.status

    if (
        data.acknowledgement_type
        == AlertAcknowledgementType.OVERRIDDEN
    ):
        alert.status = (
            ClinicalAlertStatus.OVERRIDDEN
        )
    else:
        alert.status = (
            ClinicalAlertStatus.ACKNOWLEDGED
        )

    db.session.flush()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="ClinicalAlert",
        entity_id=alert.id,
        description=(
            "Clinical safety alert acknowledgement recorded"
        ),
        old_value={
            "status": old_status.value,
        },
        new_value={
            "status": alert.status.value,
            "acknowledgement_type": (
                data.acknowledgement_type.value
            ),
            "acknowledged_by_user_id": actor.id,
        },
        user_id=actor.id,
    )

    return acknowledgement

