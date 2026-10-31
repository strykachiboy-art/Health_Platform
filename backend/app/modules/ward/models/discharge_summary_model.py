from __future__ import annotations

from datetime import datetime, timezone

from app.core.enums.clinical_record_enums import ClinicalRecordStatus
from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DischargeSummary(db.Model):
    __tablename__ = "discharge_summaries"

    __table_args__ = (
        db.UniqueConstraint(
            "admission_id",
            name="uq_discharge_summaries_admission",
        ),
        db.CheckConstraint(
            "record_status IN ('draft', 'final', 'amended')",
            name="ck_discharge_summary_record_status",
        ),
        db.CheckConstraint(
            "record_version >= 1",
            name="ck_discharge_summary_record_version_positive",
        ),
        db.CheckConstraint(
            "("
            "record_status = 'draft' "
            "AND record_version = 1 "
            "AND finalized_by_staff_id IS NULL "
            "AND finalized_at IS NULL "
            "AND latest_amendment_id IS NULL"
            ") OR ("
            "record_status = 'final' "
            "AND record_version = 1 "
            "AND finalized_by_staff_id IS NOT NULL "
            "AND finalized_at IS NOT NULL "
            "AND latest_amendment_id IS NULL"
            ") OR ("
            "record_status = 'amended' "
            "AND record_version >= 2 "
            "AND finalized_by_staff_id IS NOT NULL "
            "AND finalized_at IS NOT NULL "
            "AND latest_amendment_id IS NOT NULL"
            ")",
            name="ck_discharge_summary_record_linkage",
        ),
        db.Index(
            "ix_discharge_summary_clinic_patient_discharged",
            "clinic_id",
            "patient_id",
            "discharged_at",
            "id",
        ),
        db.Index(
            "ix_discharge_summary_clinic_record_status",
            "clinic_id",
            "record_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    clinic_id = db.Column(
        db.Integer,
        db.ForeignKey("clinics.id"),
        nullable=False,
        index=True,
    )

    patient_id = db.Column(
        db.Integer,
        db.ForeignKey("patients.id"),
        nullable=False,
        index=True,
    )

    admission_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "admissions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    medication_reconciliation_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "medication_reconciliations.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    discharged_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "staff.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    primary_diagnosis = db.Column(db.Text, nullable=False)
    secondary_diagnoses = db.Column(
        db.JSON,
        nullable=False,
        default=list,
    )
    condition_at_discharge = db.Column(db.Text, nullable=False)
    instructions = db.Column(db.Text, nullable=False)
    follow_up_plan = db.Column(db.Text, nullable=False)
    final_medication_list = db.Column(
        db.JSON,
        nullable=False,
        default=list,
    )
    destination = db.Column(
        db.String(255),
        nullable=False,
    )
    discharged_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    record_status = db.Column(
        db.String(16),
        default=ClinicalRecordStatus.DRAFT.value,
        server_default=ClinicalRecordStatus.DRAFT.value,
        nullable=False,
        index=True,
    )
    record_version = db.Column(
        db.Integer,
        default=1,
        server_default="1",
        nullable=False,
    )
    finalized_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "staff.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    finalized_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )
    latest_amendment_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "clinical_record_amendments.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    clinic = db.relationship("Clinic")
    patient = db.relationship("Patient")
    admission = db.relationship("Admission")
    medication_reconciliation = db.relationship(
        "MedicationReconciliation"
    )
    discharged_by = db.relationship(
        "Staff",
        foreign_keys=[discharged_by_staff_id],
    )
    finalized_by = db.relationship(
        "Staff",
        foreign_keys=[finalized_by_staff_id],
    )
    latest_amendment = db.relationship(
        "ClinicalRecordAmendment",
        foreign_keys=[latest_amendment_id],
        uselist=False,
    )
