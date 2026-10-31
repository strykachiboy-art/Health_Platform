from datetime import datetime, timezone

from app.extensions import db
from app.core.enums.consultation_enums import (
    ConsultationStatus,
    ConsultationType,
)
from app.core.enums.clinical_record_enums import ClinicalRecordStatus


def _utcnow():
    return datetime.now(timezone.utc)


class Consultation(db.Model):
    __tablename__ = "consultations"

    __table_args__ = (
        db.CheckConstraint(
            "ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at",
            name="ck_consultations_valid_time_range",
        ),
        db.Index(
            "ix_consultations_clinic_patient",
            "clinic_id",
            "patient_id",
        ),
        db.Index(
            "ix_consultations_clinic_staff",
            "clinic_id",
            "staff_id",
        ),
        db.Index(
            "ix_consultations_clinic_status",
            "clinic_id",
            "status",
        ),
        db.Index(
            "ix_consultations_clinic_started_at",
            "clinic_id",
            "started_at",
        ),
        db.CheckConstraint(
            "record_status IN ('draft', 'final', 'amended')",
            name="ck_consultations_record_status",
        ),
        db.CheckConstraint(
            "record_version >= 1",
            name="ck_consultations_record_version_positive",
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
            name="ck_consultations_record_finalization_consistency",
        ),
        db.Index(
            "ix_consultations_clinic_record_status",
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

    staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )

    appointment_id = db.Column(
        db.Integer,
        db.ForeignKey("appointments.id"),
        nullable=True,
        index=True,
    )

    icd10_code = db.Column(
        db.String(10),
        nullable=True,
    )

    consultation_type = db.Column(
        db.Enum(ConsultationType),
        default=ConsultationType.GENERAL,
        nullable=False,
        index=True,
    )

    status = db.Column(
        db.Enum(ConsultationStatus),
        default=ConsultationStatus.IN_PROGRESS,
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

    chief_complaint = db.Column(
        db.Text,
        nullable=True,
    )

    symptoms = db.Column(
        db.Text,
        nullable=True,
    )

    diagnosis = db.Column(
        db.Text,
        nullable=True,
    )

    treatment_plan = db.Column(
        db.Text,
        nullable=True,
    )

    notes = db.Column(
        db.Text,
        nullable=True,
    )

    voice_note_url = db.Column(
        db.String(255),
        nullable=True,
    )

    transcribed_text = db.Column(
        db.Text,
        nullable=True,
    )

    template_id = db.Column(
        db.Integer,
        db.ForeignKey("consultation_templates.id"),
        nullable=True,
        index=True,
    )

    started_at = db.Column(
        db.DateTime,
        default=_utcnow,
        nullable=False,
    )

    ended_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        default=_utcnow,
        nullable=False,
    )

    updated_at = db.Column(
        db.DateTime,
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    clinic = db.relationship(
        "Clinic",
        back_populates="consultations",
    )

    patient = db.relationship(
        "Patient",
        back_populates="consultations",
    )

    staff = db.relationship(
        "Staff",
        foreign_keys=[staff_id],
        back_populates="consultations",
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

    appointment = db.relationship(
        "Appointment",
        back_populates="consultation",
    )

    template = db.relationship(
        "ConsultationTemplate",
        back_populates="consultations",
    )

    prescriptions = db.relationship(
        "Prescription",
        back_populates="consultation",
    )

    lab_orders = db.relationship(
        "LabOrder",
        back_populates="consultation",
    )

    def __repr__(self):
        return (
            f"<Consultation {self.id} - "
            f"Patient {self.patient_id} "
            f"({self.status.value})>"
        )


class ConsultationTemplate(db.Model):
    __tablename__ = "consultation_templates"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    clinic_id = db.Column(
        db.Integer,
        db.ForeignKey("clinics.id"),
        nullable=True,
        index=True,
    )

    name = db.Column(
        db.String(150),
        nullable=False,
    )

    specialty = db.Column(
        db.String(100),
        nullable=True,
    )

    structure = db.Column(
        db.JSON,
        nullable=False,
    )

    is_active = db.Column(
        db.Boolean,
        default=True,
        nullable=False,
        index=True,
    )

    created_at = db.Column(
        db.DateTime,
        default=_utcnow,
        nullable=False,
    )

    consultations = db.relationship(
        "Consultation",
        back_populates="template",
    )

    def __repr__(self):
        return f"<ConsultationTemplate {self.name}>"