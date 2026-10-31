from __future__ import annotations

from datetime import datetime, timezone

from app.core.enums.clinical_record_enums import (
    ClinicalRecordStatus,
)
from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MedicationReconciliation(db.Model):
    __tablename__ = "medication_reconciliations"

    __table_args__ = (
        db.CheckConstraint(
            "event_type IN ('admission', 'transfer', 'discharge')",
            name="ck_med_recon_event_type",
        ),
        db.CheckConstraint(
            "record_status IN ('draft', 'final', 'amended')",
            name="ck_med_recon_record_status",
        ),
        db.CheckConstraint(
            "record_version >= 1",
            name="ck_med_recon_record_version_positive",
        ),
        db.CheckConstraint(
            "("
            "event_type = 'transfer' AND ward_transfer_id IS NOT NULL"
            ") OR ("
            "event_type != 'transfer' AND ward_transfer_id IS NULL"
            ")",
            name="ck_med_recon_transfer_reference",
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
            name="ck_med_recon_record_linkage",
        ),
        db.UniqueConstraint(
            "clinic_id",
            "event_identity",
            name="uq_med_recon_clinic_event_identity",
        ),
        db.Index(
            "ix_med_recon_admission_event_created",
            "admission_id",
            "event_type",
            "created_at",
            "id",
        ),
        db.Index(
            "ix_med_recon_clinic_patient_created",
            "clinic_id",
            "patient_id",
            "created_at",
            "id",
        ),
        db.Index(
            "ix_med_recon_clinic_record_status",
            "clinic_id",
            "record_status",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

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

    ward_transfer_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ward_transfers.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    event_type = db.Column(
        db.String(16),
        nullable=False,
        index=True,
    )

    event_identity = db.Column(
        db.String(128),
        nullable=False,
    )

    created_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "staff.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    no_medications_reported = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false(),
    )

    notes = db.Column(
        db.Text,
        nullable=True,
    )

    record_status = db.Column(
        db.String(16),
        nullable=False,
        default=ClinicalRecordStatus.DRAFT.value,
        server_default=ClinicalRecordStatus.DRAFT.value,
        index=True,
    )

    record_version = db.Column(
        db.Integer,
        nullable=False,
        default=1,
        server_default="1",
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
        nullable=False,
        default=_utcnow,
        index=True,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    clinic = db.relationship("Clinic")
    patient = db.relationship("Patient")
    admission = db.relationship("Admission")
    ward_transfer = db.relationship("WardTransfer")

    created_by = db.relationship(
        "Staff",
        foreign_keys=[created_by_staff_id],
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

    items = db.relationship(
        "MedicationReconciliationItem",
        back_populates="reconciliation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="MedicationReconciliationItem.id",
    )

    def __repr__(self) -> str:
        return (
            "<MedicationReconciliation "
            f"{self.id} Admission {self.admission_id} "
            f"{self.event_type} ({self.record_status})>"
        )


class MedicationReconciliationItem(db.Model):
    __tablename__ = "medication_reconciliation_items"

    __table_args__ = (
        db.CheckConstraint(
            "decision IN ("
            "'continue', 'hold', 'stop', 'replace', "
            "'new', 'verify', 'unknown'"
            ")",
            name="ck_med_recon_item_decision",
        ),
        db.CheckConstraint(
            "("
            "decision = 'replace' "
            "AND replacement_medication_name IS NOT NULL"
            ") OR ("
            "decision != 'replace' "
            "AND replacement_medication_name IS NULL"
            ")",
            name="ck_med_recon_item_replacement",
        ),
        db.Index(
            "ix_med_recon_items_reconciliation_decision",
            "reconciliation_id",
            "decision",
            "id",
        ),
        db.Index(
            "ix_med_recon_items_source_item",
            "source_prescription_item_id",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    reconciliation_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "medication_reconciliations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    source_prescription_item_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "prescription_items.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    medication_name = db.Column(
        db.String(255),
        nullable=False,
    )

    decision = db.Column(
        db.String(16),
        nullable=False,
        index=True,
    )

    replacement_medication_name = db.Column(
        db.String(255),
        nullable=True,
    )

    dose_text = db.Column(
        db.String(255),
        nullable=True,
    )

    route_text = db.Column(
        db.String(128),
        nullable=True,
    )

    frequency_text = db.Column(
        db.String(255),
        nullable=True,
    )

    instructions = db.Column(
        db.String(1000),
        nullable=True,
    )

    rationale = db.Column(
        db.String(1000),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    reconciliation = db.relationship(
        "MedicationReconciliation",
        back_populates="items",
    )

    source_prescription_item = db.relationship(
        "PrescriptionItem",
    )

    def __repr__(self) -> str:
        return (
            "<MedicationReconciliationItem "
            f"{self.id} {self.medication_name} "
            f"({self.decision})>"
        )
