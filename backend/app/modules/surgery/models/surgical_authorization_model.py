"""CE1-S03: evidence for consent and procedure-scoped professional privileges.

These records do not authorize surgery by themselves. Signed documents and
credential source evidence must remain durable in a future protected document
store before a surgical execution endpoint is enabled.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SurgicalProfessionalPrivilege(db.Model):
    __tablename__ = "surgical_professional_privileges"
    __table_args__ = (
        db.CheckConstraint(
            "privilege_kind IN ('surgeon', 'anesthetist')",
            name="ck_surgical_privileges_kind",
        ),
        db.CheckConstraint("expires_at > granted_at", name="ck_surgical_privileges_duration"),
        db.CheckConstraint("version >= 1", name="ck_surgical_privileges_version"),
        db.CheckConstraint(
            "revoked_at IS NULL OR (revoked_by_staff_id IS NOT NULL AND "
            "revocation_reason IS NOT NULL)",
            name="ck_surgical_privileges_revoke_evidence",
        ),
        db.Index(
            "uq_surgical_privileges_active_scope",
            "clinic_id", "staff_id", "privilege_kind", "procedure_code",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey("clinics.id"), nullable=False, index=True)
    staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False, index=True)
    privilege_kind = db.Column(db.String(24), nullable=False)
    procedure_code = db.Column(db.String(64), nullable=False)
    license_reference = db.Column(db.String(128), nullable=False)
    credential_document_ref = db.Column(db.String(255), nullable=False)
    credential_sha256 = db.Column(db.String(64), nullable=False)
    verification_source = db.Column(db.String(255), nullable=False)
    granted_by_staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=False
    )
    granted_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked_at = db.Column(db.DateTime, nullable=True)
    revoked_by_staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=True
    )
    revocation_reason = db.Column(db.String(500), nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1)

    clinic = db.relationship("Clinic")
    professional = db.relationship("Staff", foreign_keys=[staff_id])
    granted_by = db.relationship("Staff", foreign_keys=[granted_by_staff_id])
    revoked_by = db.relationship("Staff", foreign_keys=[revoked_by_staff_id])


class SurgicalConsentEvidence(db.Model):
    __tablename__ = "surgical_consent_evidence"
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('recorded', 'verified', 'withdrawn')",
            name="ck_surgical_consent_status",
        ),
        db.CheckConstraint(
            "signer_kind IN ('patient', 'guardian', 'authorized_representative')",
            name="ck_surgical_consent_signer",
        ),
        db.CheckConstraint(
            "signer_kind = 'patient' OR (signer_authority IS NOT NULL AND "
            "length(signer_authority) > 0)",
            name="ck_surgical_consent_authority",
        ),
        db.CheckConstraint(
            "expires_at > signed_at", name="ck_surgical_consent_duration"
        ),
        db.CheckConstraint(
            "status != 'verified' OR (verified_at IS NOT NULL AND "
            "verified_by_staff_id IS NOT NULL)",
            name="ck_surgical_consent_verified_evidence",
        ),
        db.CheckConstraint(
            "status != 'withdrawn' OR (withdrawn_at IS NOT NULL AND "
            "withdrawn_by_staff_id IS NOT NULL AND withdrawal_reason IS NOT NULL)",
            name="ck_surgical_consent_withdrawn_evidence",
        ),
        db.CheckConstraint("version >= 1", name="ck_surgical_consent_version"),
        db.Index(
            "uq_surgical_consent_open_case", "surgical_case_id", unique=True,
            postgresql_where=text("status IN ('recorded', 'verified')"),
            sqlite_where=text("status IN ('recorded', 'verified')"),
        ),
        db.Index(
            "ix_surgical_consent_clinic_case_status",
            "clinic_id", "surgical_case_id", "status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey("clinics.id"), nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    surgical_case_id = db.Column(
        db.Integer, db.ForeignKey("surgical_cases.id"), nullable=False
    )
    procedure_order_id = db.Column(
        db.Integer, db.ForeignKey("procedure_orders.id"), nullable=False
    )
    procedure_order_version = db.Column(db.Integer, nullable=False)
    procedure_code = db.Column(db.String(64), nullable=False)
    procedure_name = db.Column(db.String(255), nullable=False)
    body_site = db.Column(db.String(255), nullable=True)
    laterality = db.Column(db.String(32), nullable=True)
    signer_kind = db.Column(db.String(32), nullable=False)
    signer_name = db.Column(db.String(255), nullable=False)
    signer_authority = db.Column(db.String(255), nullable=True)
    signed_document_ref = db.Column(db.String(255), nullable=False)
    signed_document_sha256 = db.Column(db.String(64), nullable=False)
    signed_at = db.Column(db.DateTime, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="recorded")
    recorded_by_staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=False
    )
    recorded_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    verified_by_staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=True
    )
    verified_at = db.Column(db.DateTime, nullable=True)
    withdrawn_by_staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=True
    )
    withdrawn_at = db.Column(db.DateTime, nullable=True)
    withdrawal_reason = db.Column(db.String(500), nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1)

    clinic = db.relationship("Clinic")
    patient = db.relationship("Patient")
    surgical_case = db.relationship("SurgicalCase")
    procedure_order = db.relationship("ProcedureOrder")
    recorded_by = db.relationship("Staff", foreign_keys=[recorded_by_staff_id])
    verified_by = db.relationship("Staff", foreign_keys=[verified_by_staff_id])
    withdrawn_by = db.relationship("Staff", foreign_keys=[withdrawn_by_staff_id])
