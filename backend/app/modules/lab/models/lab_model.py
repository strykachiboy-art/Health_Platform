from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db
from app.core.enums.lab_enums import (
    LabOrderStatus,
    LabResultFlag,
    SampleType,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LabTest(db.Model):
    __tablename__ = "lab_tests"

    __table_args__ = (
        db.CheckConstraint(
            "critical_low IS NULL "
            "OR critical_high IS NULL "
            "OR critical_low < critical_high",
            name="ck_lab_tests_valid_critical_range",
        ),
        db.CheckConstraint(
            "price IS NULL OR price >= 0",
            name="ck_lab_tests_price_non_negative",
        ),
        db.Index(
            "ix_lab_tests_clinic_active_name",
            "clinic_id",
            "is_active",
            "name",
        ),
        db.Index(
            "ix_lab_tests_clinic_sample_type",
            "clinic_id",
            "sample_type",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    # NULL = global/shared laboratory test.
    # Non-NULL = clinic-specific laboratory test.
    clinic_id = db.Column(
        db.Integer,
        db.ForeignKey("clinics.id"),
        nullable=True,
        index=True,
    )

    # Standard LOINC terminology code.
    loinc_code = db.Column(
        db.String(20),
        nullable=True,
        index=True,
    )

    name = db.Column(
        db.String(150),
        nullable=False,
    )

    # Internal test code.
    code = db.Column(
        db.String(50),
        unique=True,
        nullable=True,
        index=True,
    )

    sample_type = db.Column(
        db.Enum(SampleType),
        default=SampleType.BLOOD,
        nullable=False,
        index=True,
    )

    reference_range = db.Column(
        db.String(150),
        nullable=True,
    )

    unit = db.Column(
        db.String(30),
        nullable=True,
    )

    price = db.Column(
        db.Numeric(10, 2),
        nullable=True,
    )

    # Numeric critical thresholds.
    #
    # These are separate from reference_range because
    # reference_range is descriptive/display information,
    # while these values are used for automatic CRITICAL
    # flagging.
    critical_low = db.Column(
        db.Numeric(10, 3),
        nullable=True,
    )

    critical_high = db.Column(
        db.Numeric(10, 3),
        nullable=True,
    )

    is_active = db.Column(
        db.Boolean,
        default=True,
        nullable=False,
        index=True,
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

    orders = db.relationship(
        "LabOrderItem",
        back_populates="test",
    )

    def __repr__(self):
        return f"<LabTest {self.name}>"


class LabOrder(db.Model):
    __tablename__ = "lab_orders"

    __table_args__ = (
        db.CheckConstraint("patient_release_withdrawal_reason IS NULL OR (patient_release_withdrawn_at IS NOT NULL AND patient_release_withdrawal_reason IN ('clinical_correction','identity_review','other_safety_review'))", name="ck_lab_patient_release_withdraw_reason"),
        db.CheckConstraint('(patient_released_at IS NULL AND patient_released_by_id IS NULL AND patient_release_user_id IS NULL AND patient_release_snapshot IS NULL) OR (patient_released_at IS NOT NULL AND patient_released_by_id IS NOT NULL AND patient_release_user_id IS NOT NULL AND patient_release_snapshot IS NOT NULL AND completed_at IS NOT NULL AND verified_at IS NOT NULL AND patient_released_at >= completed_at)', name="ck_lab_patient_release_pair"),
        db.CheckConstraint('(patient_release_withdrawn_at IS NULL AND patient_release_withdrawn_by_id IS NULL) OR (patient_release_withdrawn_at IS NOT NULL AND patient_release_withdrawn_by_id IS NOT NULL AND patient_released_at IS NOT NULL AND patient_release_withdrawn_at >= patient_released_at)', name="ck_lab_patient_release_withdrawal"),
        db.Index("ix_lab_patient_release_lookup", "clinic_id", "patient_id", "patient_release_user_id", "patient_released_at"),
        # ---------------------------------------------------------
        # Collection timestamp integrity
        # ---------------------------------------------------------
        db.CheckConstraint(
            "sample_collected_at IS NULL "
            "OR sample_collected_at >= created_at",
            name="ck_lab_orders_sample_collected_after_created",
        ),

        db.CheckConstraint(
            "(collected_by_id IS NULL AND sample_collected_at IS NULL) "
            "OR "
            "(collected_by_id IS NOT NULL AND sample_collected_at IS NOT NULL)",
            name="ck_lab_orders_collection_actor_timestamp_pair",
        ),

        # ---------------------------------------------------------
        # Processing timestamp integrity
        # ---------------------------------------------------------
        db.CheckConstraint(
            "processed_at IS NULL "
            "OR processed_at >= created_at",
            name="ck_lab_orders_processed_after_created",
        ),

        db.CheckConstraint(
            "processed_at IS NULL "
            "OR sample_collected_at IS NULL "
            "OR processed_at >= sample_collected_at",
            name="ck_lab_orders_processed_after_sample",
        ),

        db.CheckConstraint(
            "(processed_by_id IS NULL AND processed_at IS NULL) "
            "OR "
            "(processed_by_id IS NOT NULL AND processed_at IS NOT NULL)",
            name="ck_lab_orders_processing_actor_timestamp_pair",
        ),

        # ---------------------------------------------------------
        # Verification timestamp integrity
        # ---------------------------------------------------------
        db.CheckConstraint(
            "verified_at IS NULL "
            "OR verified_at >= created_at",
            name="ck_lab_orders_verified_after_created",
        ),

        db.CheckConstraint(
            "verified_at IS NULL "
            "OR processed_at IS NULL "
            "OR verified_at >= processed_at",
            name="ck_lab_orders_verified_after_processed",
        ),

        db.CheckConstraint(
            "(verified_by_id IS NULL AND verified_at IS NULL) "
            "OR "
            "(verified_by_id IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_lab_orders_verification_actor_timestamp_pair",
        ),

        # ---------------------------------------------------------
        # Completion timestamp integrity
        # ---------------------------------------------------------
        db.CheckConstraint(
            "completed_at IS NULL "
            "OR completed_at >= created_at",
            name="ck_lab_orders_completed_after_created",
        ),

        db.CheckConstraint(
            "completed_at IS NULL "
            "OR sample_collected_at IS NULL "
            "OR completed_at >= sample_collected_at",
            name="ck_lab_orders_completed_after_sample",
        ),

        db.CheckConstraint(
            "completed_at IS NULL "
            "OR processed_at IS NULL "
            "OR completed_at >= processed_at",
            name="ck_lab_orders_completed_after_processed",
        ),

        db.CheckConstraint(
            "completed_at IS NULL "
            "OR verified_at IS NULL "
            "OR completed_at >= verified_at",
            name="ck_lab_orders_completed_after_verified",
        ),

        # ---------------------------------------------------------
        # Workflow/index optimization
        # ---------------------------------------------------------
        db.Index(
            "ix_lab_orders_clinic_status_created",
            "clinic_id",
            "status",
            "created_at",
        ),
        db.Index(
            "ix_lab_orders_clinic_patient_created",
            "clinic_id",
            "patient_id",
            "created_at",
        ),
        db.Index(
            "ix_lab_orders_patient_created",
            "patient_id",
            "created_at",
        ),
        db.Index(
            "ix_lab_orders_clinic_ordered_by_created",
            "clinic_id",
            "ordered_by_id",
            "created_at",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    # -------------------------------------------------------------
    # Tenant ownership
    # -------------------------------------------------------------

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

    consultation_id = db.Column(
        db.Integer,
        db.ForeignKey("consultations.id"),
        nullable=True,
        index=True,
    )

    # -------------------------------------------------------------
    # Clinical actors
    # -------------------------------------------------------------

    ordered_by_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )

    collected_by_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )

    processed_by_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )

    verified_by_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )

    # -------------------------------------------------------------
    # Order lifecycle
    # -------------------------------------------------------------

    status = db.Column(
        db.Enum(LabOrderStatus),
        default=LabOrderStatus.ORDERED,
        nullable=False,
        index=True,
    )

    qr_code = db.Column(
        db.String(150),
        unique=True,
        nullable=True,
        index=True,
    )

    # -------------------------------------------------------------
    # Collection
    # -------------------------------------------------------------

    sample_collected_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    # -------------------------------------------------------------
    # Processing
    # -------------------------------------------------------------

    processed_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    # -------------------------------------------------------------
    # Verification
    # -------------------------------------------------------------

    verified_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    # -------------------------------------------------------------
    # Equipment
    # -------------------------------------------------------------

    equipment_reference_id = db.Column(
        db.String(150),
        nullable=True,
        index=True,
    )

    # -------------------------------------------------------------
    # Cancellation
    # -------------------------------------------------------------

    cancellation_reason = db.Column(
        db.String(255),
        nullable=True,
    )

    # -------------------------------------------------------------
    # Timestamps
    # -------------------------------------------------------------

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

    completed_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    # Explicit patient publication is not equivalent to lab completion.
    patient_released_at = db.Column(db.DateTime(timezone=True), nullable=True)
    patient_released_by_id = db.Column(db.Integer, db.ForeignKey("staff.id", name="fk_lab_release_doctor"), nullable=True)
    patient_release_user_id = db.Column(db.Integer, db.ForeignKey("users.id", name="fk_lab_release_recipient"), nullable=True)
    patient_release_snapshot = db.Column(db.JSON(none_as_null=True), nullable=True)
    patient_release_withdrawn_at = db.Column(db.DateTime(timezone=True), nullable=True)
    patient_release_withdrawn_by_id = db.Column(db.Integer, db.ForeignKey("staff.id", name="fk_lab_release_withdrawal_doctor"), nullable=True)
    patient_release_withdrawal_reason = db.Column(db.String(32), nullable=True)

    # -------------------------------------------------------------
    # Relationships
    # -------------------------------------------------------------

    clinic = db.relationship(
        "Clinic",
        back_populates="lab_orders",
    )

    patient = db.relationship(
        "Patient",
        back_populates="lab_orders",
    )

    consultation = db.relationship(
        "Consultation",
        back_populates="lab_orders",
    )

    ordered_by = db.relationship(
        "Staff",
        foreign_keys=[ordered_by_id],
        back_populates="lab_orders",
    )

    collected_by = db.relationship(
        "Staff",
        foreign_keys=[collected_by_id],
    )

    processed_by = db.relationship(
        "Staff",
        foreign_keys=[processed_by_id],
    )

    verified_by = db.relationship(
        "Staff",
        foreign_keys=[verified_by_id],
    )

    items = db.relationship(
        "LabOrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return (
            f"<LabOrder {self.id} - "
            f"Patient {self.patient_id} "
            f"({self.status.value})>"
        )


class LabOrderItem(db.Model):
    """
    A single laboratory test within a lab order.
    """

    __tablename__ = "lab_order_items"

    __table_args__ = (
        db.UniqueConstraint(
            "order_id",
            "test_id",
            name="uq_lab_order_item_order_test",
        ),
        db.Index(
            "ix_lab_order_items_order_resulted_at",
            "order_id",
            "resulted_at",
        ),
        db.Index(
            "ix_lab_order_items_test_flag",
            "test_id",
            "flag",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    order_id = db.Column(
        db.Integer,
        db.ForeignKey("lab_orders.id"),
        nullable=False,
        index=True,
    )

    test_id = db.Column(
        db.Integer,
        db.ForeignKey("lab_tests.id"),
        nullable=False,
        index=True,
    )

    result_value = db.Column(
        db.String(150),
        nullable=True,
    )

    # Capture display context at result entry; never guess legacy units/ranges.
    result_display_metadata = db.Column(db.JSON(none_as_null=True), nullable=True)

    flag = db.Column(
        db.Enum(LabResultFlag),
        nullable=True,
        index=True,
    )

    result_notes = db.Column(
        db.Text,
        nullable=True,
    )

    result_file_url = db.Column(
        db.String(255),
        nullable=True,
    )

    resulted_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    order = db.relationship(
        "LabOrder",
        back_populates="items",
    )

    test = db.relationship(
        "LabTest",
        back_populates="orders",
    )

    def __repr__(self):
        return (
            f"<LabOrderItem "
            f"Test {self.test_id} - "
            f"Order {self.order_id}>"
        )

class LabReportPublication(db.Model):
    """Publication evidence only; corrected content lives in ClinicalRecordAmendment."""
    __tablename__ = 'lab_report_publications'
    __table_args__ = (
        db.UniqueConstraint('order_id','version',name='uq_lab_report_publication_version'),
        db.CheckConstraint('version >= 2',name='ck_lab_report_publication_version'),
        db.CheckConstraint('length(content_sha256) = 64',name='ck_lab_report_publication_hash'),
        db.CheckConstraint('(withdrawn_at IS NULL AND withdrawn_by_id IS NULL) OR (withdrawn_at IS NOT NULL AND withdrawn_by_id IS NOT NULL AND withdrawn_at >= released_at)',name='ck_lab_report_publication_withdrawal'),
        db.CheckConstraint("(withdrawn_at IS NULL AND withdrawal_reason IS NULL) OR (withdrawn_at IS NOT NULL AND withdrawal_reason IS NOT NULL AND withdrawal_reason IN ('clinical_correction','identity_review','other_safety_review'))",name='ck_lab_report_publication_reason'),
        db.Index('uq_lab_report_current_publication','order_id',unique=True,postgresql_where=db.text('withdrawn_at IS NULL'),sqlite_where=db.text('withdrawn_at IS NULL')),
    )
    id = db.Column(db.Integer,primary_key=True)
    order_id = db.Column(db.Integer,db.ForeignKey('lab_orders.id',ondelete='RESTRICT'),nullable=False)
    version = db.Column(db.Integer,nullable=False)
    amendment_id = db.Column(db.Integer,db.ForeignKey('clinical_record_amendments.id',ondelete='RESTRICT'),nullable=False,unique=True)
    recipient_user_id = db.Column(db.Integer,db.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    content_sha256 = db.Column(db.String(64),nullable=False)
    released_at = db.Column(db.DateTime(timezone=True),nullable=False)
    released_by_id = db.Column(db.Integer,db.ForeignKey('staff.id',ondelete='RESTRICT'),nullable=False)
    withdrawn_at = db.Column(db.DateTime(timezone=True),nullable=True)
    withdrawn_by_id = db.Column(db.Integer,db.ForeignKey('staff.id',ondelete='RESTRICT'),nullable=True)
    withdrawal_reason = db.Column(db.String(32),nullable=True)
