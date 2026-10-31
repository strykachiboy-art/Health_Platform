from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import event

from app.core.audit.integrity import (
    calculate_audit_evidence_sha256,
)
from app.core.enums.audit_enums import AuditAction
from app.core.exceptions import ConflictError
from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    __table_args__ = (
        db.Index(
            "ix_audit_logs_clinic_created_id",
            "clinic_id",
            "created_at",
            "id",
        ),
        db.Index(
            "ix_audit_logs_clinic_user_created_id",
            "clinic_id",
            "user_id",
            "created_at",
            "id",
        ),
        db.Index(
            "ix_audit_logs_clinic_entity_created_id",
            "clinic_id",
            "entity_type",
            "entity_id",
            "created_at",
            "id",
        ),
        db.Index(
            "ix_audit_logs_clinic_operation_created_id",
            "clinic_id",
            "operation_id",
            "created_at",
            "id",
        ),
        db.CheckConstraint(
            "length(evidence_sha256) = 64",
            name="ck_audit_logs_evidence_sha256_length",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    clinic_id = db.Column(
        db.Integer,
        db.ForeignKey("clinics.id"),
        nullable=True,
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    action = db.Column(
        db.Enum(AuditAction),
        nullable=False,
        index=True,
    )

    entity_type = db.Column(
        db.String(80),
        nullable=False,
        index=True,
    )

    entity_id = db.Column(
        db.Integer,
        nullable=False,
        index=True,
    )

    description = db.Column(
        db.String(255),
        nullable=True,
    )

    reason = db.Column(
        db.String(1000),
        nullable=True,
    )

    operation_id = db.Column(
        db.String(255),
        nullable=True,
        index=True,
    )

    old_value = db.Column(
        db.JSON,
        nullable=True,
    )

    new_value = db.Column(
        db.JSON,
        nullable=True,
    )

    ip_address = db.Column(
        db.String(45),
        nullable=True,
        index=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
        index=True,
    )

    evidence_sha256 = db.Column(
        db.String(64),
        nullable=False,
        index=True,
    )

    clinic = db.relationship(
        "Clinic",
    )

    user = db.relationship(
        "User",
        back_populates="audit_logs",
    )

    def __repr__(self):
        return (
            f"<AuditLog "
            f"{self.action.value} "
            f"{self.entity_type}#{self.entity_id}>"
        )


@event.listens_for(
    AuditLog,
    "before_insert",
)
def _seal_audit_record(
    _mapper,
    _connection,
    target: AuditLog,
) -> None:
    if target.created_at is None:
        target.created_at = _utcnow()

    target.evidence_sha256 = (
        calculate_audit_evidence_sha256(
            target
        )
    )


@event.listens_for(
    AuditLog,
    "before_update",
)
def _reject_audit_update(
    _mapper,
    _connection,
    _target: AuditLog,
) -> None:
    raise ConflictError(
        "Audit records are immutable and cannot be updated"
    )


@event.listens_for(
    AuditLog,
    "before_delete",
)
def _reject_audit_delete(
    _mapper,
    _connection,
    _target: AuditLog,
) -> None:
    raise ConflictError(
        "Audit records are immutable and cannot be deleted"
    )
