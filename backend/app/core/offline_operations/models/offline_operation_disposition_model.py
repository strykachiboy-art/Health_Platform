from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OfflineOperationDisposition(db.Model):
    """Durable server decision made by a registered operation policy."""

    __tablename__ = "offline_operation_dispositions"

    __table_args__ = (
        db.UniqueConstraint(
            "clinic_id",
            "user_id",
            "operation_id",
            name="uq_offline_disposition_user_clinic_operation_id",
        ),
        db.UniqueConstraint(
            "clinic_id",
            "user_id",
            "operation_type",
            "idempotency_key",
            name="uq_offline_disposition_user_clinic_idempotency",
        ),
        db.CheckConstraint(
            "resolution = 'confirmed_no_effect'",
            name="ck_offline_disposition_resolution",
        ),
        db.CheckConstraint(
            "reason_code IN ('verified_not_sent', 'manual_reconciliation_no_effect')",
            name="ck_offline_disposition_reason_code",
        ),
        db.Index(
            "ix_offline_operation_dispositions_clinic_user_created_at",
            "clinic_id",
            "user_id",
            "created_at",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    operation_id = db.Column(db.String(128), nullable=False)
    operation_type = db.Column(db.String(120), nullable=False)
    idempotency_key = db.Column(db.String(255), nullable=False)
    resolution = db.Column(db.String(48), nullable=False)
    reason_code = db.Column(db.String(48), nullable=False)
    policy_version = db.Column(db.String(32), nullable=False)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    clinic_id = db.Column(
        db.Integer,
        db.ForeignKey("clinics.id"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    user = db.relationship("User")
    clinic = db.relationship("Clinic")

    def __repr__(self) -> str:
        return (
            "<OfflineOperationDisposition "
            f"{self.operation_type}:{self.operation_id}:{self.resolution}>"
        )