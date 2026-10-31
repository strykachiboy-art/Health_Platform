from __future__ import annotations

from datetime import datetime, timezone

from app.core.enums.emar_enums import (
    BarcodeVerificationFailureCode,
    BarcodeVerificationResult,
    MedicationAdministrationStatus,
)
from app.core.enums.clinical_record_enums import (
    ClinicalRecordStatus,
)
from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MedicationAdministration(db.Model):
    """Electronic medication administration record."""

    __tablename__ = "medication_administrations"

    __table_args__ = (
        db.CheckConstraint(
            "dose_value IS NULL OR dose_value > 0",
            name="ck_med_admin_dose_positive",
        ),
        db.CheckConstraint(
            (
                "source_order_version IS NULL "
                "OR source_order_version > 0"
            ),
            name=(
                "ck_med_admin_source_order_version_positive"
            ),
        ),
        db.CheckConstraint(
            (
                "status NOT IN ('scheduled', 'due') "
                "OR scheduled_at IS NOT NULL"
            ),
            name=(
                "ck_med_admin_scheduled_requires_time"
            ),
        ),
        db.CheckConstraint(
            (
                "status != 'administered' "
                "OR (administered_at IS NOT NULL "
                "AND administered_by_staff_id IS NOT NULL)"
            ),
            name=(
                "ck_med_admin_administered_requires_actor_time"
            ),
        ),
        db.CheckConstraint(
            (
                "status != 'held' "
                "OR (held_at IS NOT NULL "
                "AND held_by_staff_id IS NOT NULL "
                "AND hold_reason IS NOT NULL)"
            ),
            name=(
                "ck_med_admin_held_requires_evidence"
            ),
        ),
        db.CheckConstraint(
            (
                "status != 'refused' "
                "OR (refused_at IS NOT NULL "
                "AND refused_recorded_by_staff_id IS NOT NULL "
                "AND refusal_reason IS NOT NULL)"
            ),
            name=(
                "ck_med_admin_refused_requires_evidence"
            ),
        ),
        db.CheckConstraint(
            (
                "status != 'missed' "
                "OR (missed_at IS NOT NULL "
                "AND missed_reason IS NOT NULL)"
            ),
            name=(
                "ck_med_admin_missed_requires_evidence"
            ),
        ),
        db.CheckConstraint(
            (
                "status != 'cancelled' "
                "OR (cancelled_at IS NOT NULL "
                "AND cancel_reason IS NOT NULL)"
            ),
            name=(
                "ck_med_admin_cancelled_requires_evidence"
            ),
        ),
        db.UniqueConstraint(
            "admission_id",
            "prescription_item_id",
            "scheduled_at",
            name=(
                "uq_med_admin_"
                "admission_item_scheduled"
            ),
        ),
        db.UniqueConstraint(
            "clinic_id",
            "idempotency_key",
            name=(
                "uq_med_admin_clinic_idempotency_key"
            ),
        ),
        db.CheckConstraint(
            (
                "verification_result IS NULL "
                "OR verification_result IN "
                "('barcode_verified', 'not_barcode_verified')"
            ),
            name="ck_med_admin_barcode_verification_result",
        ),
        db.CheckConstraint(
            (
                "verification_result IS NULL "
                "OR (patient_barcode IS NOT NULL "
                "AND medication_barcode IS NOT NULL "
                "AND scan_timestamp IS NOT NULL "
                "AND scan_device IS NOT NULL "
                "AND barcode_verified_by_staff_id IS NOT NULL)"
            ),
            name="ck_med_admin_barcode_verification_evidence",
        ),
        db.Index(
            "ix_med_admin_clinic_patient_scheduled",
            "clinic_id",
            "patient_id",
            "scheduled_at",
            "id",
        ),
        db.Index(
            "ix_med_admin_admission_status_scheduled",
            "admission_id",
            "status",
            "scheduled_at",
            "id",
        ),
        db.Index(
            "ix_med_admin_prescription_item_scheduled",
            "prescription_item_id",
            "scheduled_at",
            "id",
        ),
        db.Index(
            "ix_med_admin_barcode_verification",
            "clinic_id",
            "verification_result",
            "scan_timestamp",
            "id",
        ),
        db.CheckConstraint(
            "record_status IN ('draft', 'final', 'amended')",
            name="ck_med_admin_record_status",
        ),
        db.CheckConstraint(
            "record_version >= 1",
            name="ck_med_admin_record_version_positive",
        ),
        db.CheckConstraint(
            "("
            "record_status = 'draft' "
            "AND record_version = 1 "
            "AND finalized_at IS NULL "
            "AND latest_amendment_id IS NULL"
            ") OR ("
            "record_status = 'final' "
            "AND record_version = 1 "
            "AND finalized_at IS NOT NULL "
            "AND latest_amendment_id IS NULL"
            ") OR ("
            "record_status = 'amended' "
            "AND record_version >= 2 "
            "AND finalized_at IS NOT NULL "
            "AND latest_amendment_id IS NOT NULL"
            ")",
            name="ck_med_admin_record_linkage",
        ),
        db.Index(
            "ix_med_admin_clinic_record_status",
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
        db.ForeignKey("admissions.id"),
        nullable=False,
        index=True,
    )

    prescription_id = db.Column(
        db.Integer,
        db.ForeignKey("prescriptions.id"),
        nullable=False,
        index=True,
    )

    prescription_item_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "prescription_items.id"
        ),
        nullable=False,
        index=True,
    )

    scheduled_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    dose_value = db.Column(
        db.Numeric(
            12,
            4,
        ),
        nullable=True,
    )

    dose_unit = db.Column(
        db.String(50),
        nullable=True,
    )

    route_code = db.Column(
        db.String(100),
        nullable=True,
    )

    status = db.Column(
        db.Enum(
            MedicationAdministrationStatus,
            name=(
                "medication_administration_status"
            ),
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_cls: [
                member.value
                for member in enum_cls
            ],
        ),
        default=(
            MedicationAdministrationStatus
            .SCHEDULED
        ),
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

    administered_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    administered_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )

    held_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    held_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
    )

    hold_reason = db.Column(
        db.String(500),
        nullable=True,
    )

    refused_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    refused_recorded_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
    )

    refusal_reason = db.Column(
        db.String(500),
        nullable=True,
    )

    missed_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    missed_reason = db.Column(
        db.String(500),
        nullable=True,
    )

    cancelled_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    cancel_reason = db.Column(
        db.String(500),
        nullable=True,
    )

    source_order_version = db.Column(
        db.Integer,
        nullable=True,
    )

    idempotency_key = db.Column(
        db.String(255),
        nullable=True,
    )

    patient_barcode = db.Column(db.String(128), nullable=True)
    medication_barcode = db.Column(db.String(128), nullable=True)
    scan_timestamp = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    scan_device = db.Column(db.String(255), nullable=True)
    verification_result = db.Column(
        db.Enum(
            BarcodeVerificationResult,
            name="barcode_verification_result",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_cls: [
                member.value for member in enum_cls
            ],
        ),
        nullable=True,
        index=True,
    )
    barcode_verified_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )

    barcode_verification_event_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "medication_administration_events.id",
            name=(
                "fk_med_admin_barcode_verification_event"
            ),
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )

    notes = db.Column(
        db.Text,
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
        index=True,
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
    prescription = db.relationship("Prescription")
    prescription_item = db.relationship(
        "PrescriptionItem"
    )

    administered_by = db.relationship(
        "Staff",
        foreign_keys=[
            administered_by_staff_id
        ],
    )

    held_by = db.relationship(
        "Staff",
        foreign_keys=[
            held_by_staff_id
        ],
    )

    refusal_recorded_by = db.relationship(
        "Staff",
        foreign_keys=[
            refused_recorded_by_staff_id
        ],
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

    barcode_verified_by = db.relationship(
        "Staff",
        foreign_keys=[barcode_verified_by_staff_id],
    )

    barcode_events = db.relationship(
        "MedicationAdministrationEvent",
        back_populates="medication_administration",
        order_by="MedicationAdministrationEvent.scan_timestamp",
        foreign_keys=(
            "MedicationAdministrationEvent."
            "medication_administration_id"
        ),
    )

    bound_barcode_verification = db.relationship(
        "MedicationAdministrationEvent",
        foreign_keys=[barcode_verification_event_id],
        uselist=False,
    )

    def __repr__(self) -> str:
        status = (
            self.status.value
            if self.status is not None
            else "unknown"
        )

        return (
            "<MedicationAdministration "
            f"{self.id} "
            f"Admission {self.admission_id} "
            f"Item {self.prescription_item_id} "
            f"({status})>"
        )


class MedicationAdministrationEvent(db.Model):
    """Append-only barcode verification evidence."""

    __tablename__ = "medication_administration_events"

    __table_args__ = (
        db.CheckConstraint(
            "expected_dose_value > 0",
            name="ck_med_admin_event_expected_dose_positive",
        ),
        db.CheckConstraint(
            "source_order_version > 0",
            name="ck_med_admin_event_source_order_version_positive",
        ),
        db.CheckConstraint(
            "verification_policy_version > 0",
            name="ck_med_admin_event_policy_version_positive",
        ),
        db.CheckConstraint(
            "verification_result IN ('barcode_verified', 'not_barcode_verified')",
            name="ck_med_admin_event_verification_result",
        ),
        db.CheckConstraint(
            (
                "verification_result != 'barcode_verified' OR "
                "(patient_match AND medication_match AND dose_match "
                "AND route_match AND time_window_match "
                "AND failure_code IS NULL AND failure_reason IS NULL)"
            ),
            name="ck_med_admin_event_verified_consistency",
        ),
        db.CheckConstraint(
            (
                "verification_result != 'barcode_verified' OR "
                "(patient_id IS NOT NULL "
                "AND admission_id IS NOT NULL "
                "AND medication_administration_id IS NOT NULL "
                "AND prescription_id IS NOT NULL "
                "AND prescription_item_id IS NOT NULL "
                "AND scanned_patient_id IS NOT NULL "
                "AND scanned_drug_id IS NOT NULL "
                "AND expected_drug_id IS NOT NULL "
                "AND expected_dose_value IS NOT NULL "
                "AND expected_dose_unit IS NOT NULL "
                "AND expected_route_code IS NOT NULL "
                "AND source_order_version IS NOT NULL)"
            ),
            name="ck_med_admin_event_verified_context",
        ),
        db.CheckConstraint(
            (
                "verification_result != 'not_barcode_verified' OR "
                "(failure_code IS NOT NULL AND failure_reason IS NOT NULL)"
            ),
            name="ck_med_admin_event_failed_consistency",
        ),
        db.UniqueConstraint(
            "clinic_id",
            "operation_identity",
            name="uq_med_admin_event_clinic_operation",
        ),
        db.Index(
            "ix_med_admin_event_admin_scan",
            "medication_administration_id",
            "scan_timestamp",
            "id",
        ),
        db.Index(
            "ix_med_admin_event_clinic_patient_scan",
            "clinic_id",
            "patient_id",
            "scan_timestamp",
            "id",
        ),
        db.Index(
            "ix_med_admin_event_clinic_result_scan",
            "clinic_id",
            "verification_result",
            "scan_timestamp",
            "id",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey("clinics.id"), nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=True, index=True)
    admission_id = db.Column(db.Integer, db.ForeignKey("admissions.id"), nullable=True, index=True)
    medication_administration_id = db.Column(
        db.Integer,
        db.ForeignKey("medication_administrations.id"),
        nullable=True,
        index=True,
    )
    prescription_id = db.Column(db.Integer, db.ForeignKey("prescriptions.id"), nullable=True, index=True)
    prescription_item_id = db.Column(db.Integer, db.ForeignKey("prescription_items.id"), nullable=True, index=True)
    actor_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False, index=True)
    scanned_patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=True, index=True)
    scanned_drug_id = db.Column(db.Integer, db.ForeignKey("drugs.id"), nullable=True, index=True)
    patient_barcode = db.Column(db.Text, nullable=False)
    medication_barcode = db.Column(db.Text, nullable=False)
    scan_timestamp = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    scan_device = db.Column(db.String(255), nullable=False)
    verification_result = db.Column(
        db.Enum(
            BarcodeVerificationResult,
            name="barcode_event_verification_result",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
    )
    failure_code = db.Column(
        db.Enum(
            BarcodeVerificationFailureCode,
            name="barcode_verification_failure_code",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=True,
    )
    failure_reason = db.Column(db.String(500), nullable=True)
    patient_match = db.Column(db.Boolean, nullable=False)
    medication_match = db.Column(db.Boolean, nullable=False)
    dose_match = db.Column(db.Boolean, nullable=False)
    route_match = db.Column(db.Boolean, nullable=False)
    time_window_match = db.Column(db.Boolean, nullable=False)
    expected_drug_id = db.Column(db.Integer, db.ForeignKey("drugs.id"), nullable=True)
    expected_dose_value = db.Column(db.Numeric(12, 4), nullable=True)
    expected_dose_unit = db.Column(db.String(50), nullable=True)
    expected_route_code = db.Column(db.String(100), nullable=True)
    scheduled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    verification_window_start = db.Column(db.DateTime(timezone=True), nullable=True)
    verification_window_end = db.Column(db.DateTime(timezone=True), nullable=True)
    source_order_version = db.Column(db.Integer, nullable=True)
    verification_policy_version = db.Column(db.Integer, nullable=False, default=1)
    operation_identity = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False, index=True)

    medication_administration = db.relationship(
        "MedicationAdministration",
        back_populates="barcode_events",
        foreign_keys=[medication_administration_id],
    )
    clinic = db.relationship("Clinic")
    patient = db.relationship("Patient", foreign_keys=[patient_id])
    scanned_patient = db.relationship("Patient", foreign_keys=[scanned_patient_id])
    admission = db.relationship("Admission")
    prescription = db.relationship("Prescription")
    prescription_item = db.relationship("PrescriptionItem")
    actor = db.relationship("Staff", foreign_keys=[actor_staff_id])
    scanned_drug = db.relationship("Drug", foreign_keys=[scanned_drug_id])
    expected_drug = db.relationship("Drug", foreign_keys=[expected_drug_id])

    def __repr__(self) -> str:
        result = self.verification_result.value if self.verification_result is not None else "unknown"
        return (
            "<MedicationAdministrationEvent "
            f"{self.id} Administration {self.medication_administration_id} "
            f"({result})>"
        )
