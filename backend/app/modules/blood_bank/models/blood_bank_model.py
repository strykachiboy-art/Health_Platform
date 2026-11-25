from __future__ import annotations

from datetime import datetime, timezone

from app.core.enums.blood_bank_enums import (
    BloodBarcodeFailureCode,
    BloodUnitSourceType,
    BloodUnitStatus,
    DonationStatus,
    DonorDeferralStatus,
    TransfusionReactionSeverity,
    TransfusionReactionType,
    TransfusionStatus,
    TtiScreeningStatus,
)
from app.core.enums.emar_enums import BarcodeVerificationResult
from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


_VALID_BLOOD_TYPES_SQL = (
    "('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-')"
)
_VALID_DONOR_BLOOD_TYPES_SQL = (
    "('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-', 'unknown')"
)
_VALID_PRODUCT_TYPES_SQL = (
    "('packed_red_blood_cells', 'whole_blood', 'platelets', "
    "'fresh_frozen_plasma', 'cryoprecipitate')"
)


class BloodDonor(db.Model):
    __tablename__ = "blood_donors"

    __table_args__ = (
        db.CheckConstraint(
            f"blood_type IN {_VALID_DONOR_BLOOD_TYPES_SQL}",
            name="ck_blood_donors_blood_type",
        ),
        db.CheckConstraint(
            "deferral_status IN "
            "('eligible', 'temporary_deferral', 'permanent_deferral')",
            name="ck_blood_donors_deferral_status",
        ),
        db.CheckConstraint(
            "cooldown_days > 0",
            name="ck_blood_donors_cooldown_days",
        ),
        db.CheckConstraint(
            "total_donations >= 0",
            name="ck_blood_donors_total_donations",
        ),
        db.CheckConstraint(
            "deferral_status != 'temporary_deferral' "
            "OR (deferred_until IS NOT NULL AND deferral_reason IS NOT NULL)",
            name="ck_blood_donors_temporary_deferral_evidence",
        ),
        db.CheckConstraint(
            "deferral_status != 'permanent_deferral' "
            "OR deferral_reason IS NOT NULL",
            name="ck_blood_donors_permanent_deferral_evidence",
        ),
        db.UniqueConstraint(
            "clinic_id",
            "donor_code",
            name="uq_blood_donors_clinic_donor_code",
        ),
        db.Index(
            "ix_blood_donors_clinic_blood_deferral",
            "clinic_id",
            "blood_type",
            "deferral_status",
            "last_donated_at",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(
        db.Integer,
        db.ForeignKey("clinics.id"),
        nullable=False,
        index=True,
    )
    donor_code = db.Column(db.String(64), nullable=False)
    full_name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    email = db.Column(db.String(255), nullable=True)
    date_of_birth = db.Column(db.Date, nullable=True)
    blood_type = db.Column(db.String(16), nullable=False, index=True)
    deferral_status = db.Column(
        db.String(32),
        nullable=False,
        default=DonorDeferralStatus.ELIGIBLE.value,
        index=True,
    )
    deferral_reason = db.Column(db.Text, nullable=True)
    deferred_until = db.Column(db.DateTime(timezone=True), nullable=True)
    last_donated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    cooldown_days = db.Column(db.Integer, nullable=False, default=56)
    total_donations = db.Column(db.Integer, nullable=False, default=0)
    consent_to_contact = db.Column(db.Boolean, nullable=False, default=True)
    notes = db.Column(db.Text, nullable=True)
    registered_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    clinic = db.relationship("Clinic")
    registered_by = db.relationship(
        "Staff",
        foreign_keys=[registered_by_staff_id],
    )
    donations = db.relationship(
        "BloodDonation",
        back_populates="donor",
        order_by="BloodDonation.collected_at.desc()",
    )


class BloodDonation(db.Model):
    __tablename__ = "blood_donations"

    __table_args__ = (
        db.CheckConstraint(
            "volume_ml > 0",
            name="ck_blood_donations_volume_ml",
        ),
        db.CheckConstraint(
            "tti_screening_status IN "
            "('pending', 'cleared', 'reactive', 'indeterminate')",
            name="ck_blood_donations_tti_status",
        ),
        db.CheckConstraint(
            "status IN "
            "('collected', 'screening_pending', 'cleared', "
            "'reactively_discarded')",
            name="ck_blood_donations_status",
        ),
        db.CheckConstraint(
            "status != 'cleared' OR tti_screening_status = 'cleared'",
            name="ck_blood_donations_cleared_gate",
        ),
        db.CheckConstraint(
            "tti_screening_status = 'pending' "
            "OR (screened_at IS NOT NULL AND screened_by_staff_id IS NOT NULL)",
            name="ck_blood_donations_screened_evidence",
        ),
        db.UniqueConstraint(
            "clinic_id",
            "donation_number",
            name="uq_blood_donations_clinic_number",
        ),
        db.Index(
            "ix_blood_donations_clinic_donor_collected",
            "clinic_id",
            "donor_id",
            "collected_at",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(
        db.Integer,
        db.ForeignKey("clinics.id"),
        nullable=False,
        index=True,
    )
    donor_id = db.Column(
        db.Integer,
        db.ForeignKey("blood_donors.id"),
        nullable=False,
        index=True,
    )
    donation_number = db.Column(db.String(64), nullable=False)
    collected_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )
    collected_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        index=True,
    )
    volume_ml = db.Column(db.Integer, nullable=False)
    hemoglobin_g_dl = db.Column(db.Numeric(5, 2), nullable=True)
    systolic_bp = db.Column(db.Integer, nullable=True)
    diastolic_bp = db.Column(db.Integer, nullable=True)
    pulse_bpm = db.Column(db.Integer, nullable=True)
    intake_passed = db.Column(db.Boolean, nullable=False, default=True)
    intake_notes = db.Column(db.Text, nullable=True)
    tti_screening_status = db.Column(
        db.String(24),
        nullable=False,
        default=TtiScreeningStatus.PENDING.value,
        index=True,
    )
    tti_results = db.Column(db.JSON, nullable=True)
    screened_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )
    screened_at = db.Column(db.DateTime(timezone=True), nullable=True)
    status = db.Column(
        db.String(32),
        nullable=False,
        default=DonationStatus.SCREENING_PENDING.value,
        index=True,
    )
    replacement_for_order_id = db.Column(
        db.Integer,
        db.ForeignKey("blood_product_orders.id"),
        nullable=True,
        index=True,
    )
    replacement_for_admission_id = db.Column(
        db.Integer,
        db.ForeignKey("admissions.id"),
        nullable=True,
        index=True,
    )
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    clinic = db.relationship("Clinic")
    donor = db.relationship("BloodDonor", back_populates="donations")
    collected_by = db.relationship(
        "Staff",
        foreign_keys=[collected_by_staff_id],
    )
    screened_by = db.relationship(
        "Staff",
        foreign_keys=[screened_by_staff_id],
    )
    replacement_for_order = db.relationship(
        "BloodProductOrder",
        foreign_keys=[replacement_for_order_id],
    )
    replacement_for_admission = db.relationship(
        "Admission",
        foreign_keys=[replacement_for_admission_id],
    )
    units = db.relationship(
        "BloodUnit",
        back_populates="donation",
    )


class BloodUnit(db.Model):
    __tablename__ = "blood_units"

    __table_args__ = (
        db.CheckConstraint(
            "source_type IN ('in_house_donation', 'external_blood_bank')",
            name="ck_blood_units_source_type",
        ),
        db.CheckConstraint(
            f"product_type IN {_VALID_PRODUCT_TYPES_SQL}",
            name="ck_blood_units_product_type",
        ),
        db.CheckConstraint(
            f"blood_type IN {_VALID_BLOOD_TYPES_SQL}",
            name="ck_blood_units_blood_type",
        ),
        db.CheckConstraint(
            "volume_ml > 0",
            name="ck_blood_units_volume_ml",
        ),
        db.CheckConstraint(
            "tti_screening_status IN "
            "('pending', 'cleared', 'reactive', 'indeterminate')",
            name="ck_blood_units_tti_status",
        ),
        db.CheckConstraint(
            "status IN "
            "('quarantined', 'available', 'reserved', 'issued', "
            "'transfused', 'discarded', 'expired')",
            name="ck_blood_units_status",
        ),
        db.CheckConstraint(
            "expires_at > collected_at",
            name="ck_blood_units_expiry_after_collection",
        ),
        db.CheckConstraint(
            "source_type != 'in_house_donation' OR donation_id IS NOT NULL",
            name="ck_blood_units_in_house_donation_link",
        ),
        db.CheckConstraint(
            "source_type != 'external_blood_bank' "
            "OR external_supplier_name IS NOT NULL",
            name="ck_blood_units_external_supplier",
        ),
        db.CheckConstraint(
            "status NOT IN ('available', 'reserved', 'issued', 'transfused') "
            "OR tti_screening_status = 'cleared'",
            name="ck_blood_units_cleared_for_use_gate",
        ),
        db.CheckConstraint(
            "status != 'reserved' OR reserved_for_order_id IS NOT NULL",
            name="ck_blood_units_reserved_order_link",
        ),
        db.CheckConstraint(
            "status NOT IN ('issued', 'transfused') "
            "OR issued_to_order_id IS NOT NULL",
            name="ck_blood_units_issued_order_link",
        ),
        db.CheckConstraint(
            "status != 'discarded' "
            "OR (discarded_at IS NOT NULL AND discard_reason IS NOT NULL)",
            name="ck_blood_units_discard_evidence",
        ),
        db.UniqueConstraint(
            "clinic_id",
            "unit_barcode",
            name="uq_blood_units_clinic_barcode",
        ),
        db.UniqueConstraint(
            "clinic_id",
            "din",
            "product_type",
            name="uq_blood_units_clinic_din_product",
        ),
        db.Index(
            "ix_blood_units_clinic_inventory_lookup",
            "clinic_id",
            "status",
            "product_type",
            "blood_type",
            "expires_at",
        ),
        db.Index(
            "ix_blood_units_clinic_barcode",
            "clinic_id",
            "unit_barcode",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(
        db.Integer,
        db.ForeignKey("clinics.id"),
        nullable=False,
        index=True,
    )
    unit_barcode = db.Column(db.String(128), nullable=False)
    din = db.Column(db.String(64), nullable=False)
    source_type = db.Column(db.String(32), nullable=False, index=True)
    donation_id = db.Column(
        db.Integer,
        db.ForeignKey("blood_donations.id"),
        nullable=True,
        index=True,
    )
    external_supplier_name = db.Column(db.String(200), nullable=True)
    external_reference = db.Column(db.String(128), nullable=True)
    product_type = db.Column(db.String(40), nullable=False, index=True)
    blood_type = db.Column(db.String(16), nullable=False, index=True)
    volume_ml = db.Column(db.Integer, nullable=False)
    collected_at = db.Column(db.DateTime(timezone=True), nullable=False)
    expires_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    tti_screening_status = db.Column(
        db.String(24),
        nullable=False,
        default=TtiScreeningStatus.PENDING.value,
        index=True,
    )
    status = db.Column(
        db.String(24),
        nullable=False,
        default=BloodUnitStatus.QUARANTINED.value,
        index=True,
    )
    reserved_for_order_id = db.Column(
        db.Integer,
        db.ForeignKey("blood_product_orders.id"),
        nullable=True,
        index=True,
    )
    issued_to_order_id = db.Column(
        db.Integer,
        db.ForeignKey("blood_product_orders.id"),
        nullable=True,
        index=True,
    )
    received_or_created_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )
    released_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
    )
    released_at = db.Column(db.DateTime(timezone=True), nullable=True)
    reserved_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
    )
    reserved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    issued_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
    )
    issued_at = db.Column(db.DateTime(timezone=True), nullable=True)
    discarded_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
    )
    discarded_at = db.Column(db.DateTime(timezone=True), nullable=True)
    discard_reason = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    clinic = db.relationship("Clinic")
    donation = db.relationship("BloodDonation", back_populates="units")
    reserved_for_order = db.relationship(
        "BloodProductOrder",
        foreign_keys=[reserved_for_order_id],
    )
    issued_to_order = db.relationship(
        "BloodProductOrder",
        foreign_keys=[issued_to_order_id],
    )
    received_or_created_by = db.relationship(
        "Staff",
        foreign_keys=[received_or_created_by_staff_id],
    )


class BloodStockThreshold(db.Model):
    __tablename__ = "blood_stock_thresholds"

    __table_args__ = (
        db.CheckConstraint(
            f"product_type IN {_VALID_PRODUCT_TYPES_SQL}",
            name="ck_blood_stock_thresholds_product_type",
        ),
        db.CheckConstraint(
            f"blood_type IN {_VALID_BLOOD_TYPES_SQL}",
            name="ck_blood_stock_thresholds_blood_type",
        ),
        db.CheckConstraint(
            "minimum_units >= 0 AND critical_units >= 0 "
            "AND critical_units <= minimum_units",
            name="ck_blood_stock_thresholds_units",
        ),
        db.UniqueConstraint(
            "clinic_id",
            "product_type",
            "blood_type",
            name="uq_blood_stock_thresholds_clinic_product_blood",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(
        db.Integer,
        db.ForeignKey("clinics.id"),
        nullable=False,
        index=True,
    )
    product_type = db.Column(db.String(40), nullable=False)
    blood_type = db.Column(db.String(16), nullable=False)
    minimum_units = db.Column(db.Integer, nullable=False, default=2)
    critical_units = db.Column(db.Integer, nullable=False, default=1)
    updated_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )


class BloodTransfusionBarcodeEvent(db.Model):
    """Append-only bedside blood transfusion barcode verification evidence."""

    __tablename__ = "blood_transfusion_barcode_events"

    __table_args__ = (
        db.CheckConstraint(
            "verification_policy_version > 0",
            name="ck_blood_barcode_event_policy_version_positive",
        ),
        db.CheckConstraint(
            "verification_result IN ('barcode_verified', 'not_barcode_verified')",
            name="ck_blood_barcode_event_verification_result",
        ),
        db.CheckConstraint(
            "verification_result != 'barcode_verified' "
            "OR (patient_match AND unit_match AND blood_type_match "
            "AND expiry_match AND order_status_match "
            "AND failure_code IS NULL AND failure_reason IS NULL)",
            name="ck_blood_barcode_event_verified_consistency",
        ),
        db.CheckConstraint(
            "verification_result != 'barcode_verified' "
            "OR (patient_id IS NOT NULL AND admission_id IS NOT NULL "
            "AND blood_product_order_id IS NOT NULL "
            "AND blood_unit_id IS NOT NULL "
            "AND scanned_patient_id IS NOT NULL "
            "AND scanned_unit_id IS NOT NULL "
            "AND source_order_version IS NOT NULL)",
            name="ck_blood_barcode_event_verified_context",
        ),
        db.CheckConstraint(
            "verification_result != 'not_barcode_verified' "
            "OR (failure_code IS NOT NULL AND failure_reason IS NOT NULL)",
            name="ck_blood_barcode_event_failed_consistency",
        ),
        db.UniqueConstraint(
            "clinic_id",
            "operation_identity",
            name="uq_blood_barcode_event_clinic_operation",
        ),
        db.Index(
            "ix_blood_barcode_event_order_scan",
            "blood_product_order_id",
            "scan_timestamp",
            "id",
        ),
        db.Index(
            "ix_blood_barcode_event_clinic_result_scan",
            "clinic_id",
            "verification_result",
            "scan_timestamp",
            "id",
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
        nullable=True,
        index=True,
    )
    admission_id = db.Column(
        db.Integer,
        db.ForeignKey("admissions.id"),
        nullable=True,
        index=True,
    )
    blood_product_order_id = db.Column(
        db.Integer,
        db.ForeignKey("blood_product_orders.id"),
        nullable=True,
        index=True,
    )
    blood_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("blood_units.id"),
        nullable=True,
        index=True,
    )
    actor_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )
    scanned_patient_id = db.Column(
        db.Integer,
        db.ForeignKey("patients.id"),
        nullable=True,
        index=True,
    )
    scanned_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("blood_units.id"),
        nullable=True,
        index=True,
    )
    patient_barcode = db.Column(db.Text, nullable=False)
    unit_barcode = db.Column(db.Text, nullable=False)
    scan_timestamp = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    scan_device = db.Column(db.String(255), nullable=False)
    verification_result = db.Column(
        db.Enum(
            BarcodeVerificationResult,
            name="blood_barcode_event_verification_result",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_cls: [
                member.value for member in enum_cls
            ],
        ),
        nullable=False,
        index=True,
    )
    failure_code = db.Column(
        db.Enum(
            BloodBarcodeFailureCode,
            name="blood_barcode_verification_failure_code",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_cls: [
                member.value for member in enum_cls
            ],
        ),
        nullable=True,
    )
    failure_reason = db.Column(db.String(500), nullable=True)
    patient_match = db.Column(db.Boolean, nullable=False)
    unit_match = db.Column(db.Boolean, nullable=False)
    blood_type_match = db.Column(db.Boolean, nullable=False)
    expiry_match = db.Column(db.Boolean, nullable=False)
    order_status_match = db.Column(db.Boolean, nullable=False)
    expected_patient_blood_type = db.Column(db.String(16), nullable=True)
    scanned_unit_blood_type = db.Column(db.String(16), nullable=True)
    source_order_version = db.Column(db.Integer, nullable=True)
    verification_policy_version = db.Column(
        db.Integer,
        nullable=False,
        default=1,
    )
    operation_identity = db.Column(db.String(255), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        index=True,
    )

    clinic = db.relationship("Clinic")
    patient = db.relationship("Patient", foreign_keys=[patient_id])
    scanned_patient = db.relationship(
        "Patient",
        foreign_keys=[scanned_patient_id],
    )
    admission = db.relationship("Admission")
    blood_product_order = db.relationship("BloodProductOrder")
    blood_unit = db.relationship("BloodUnit", foreign_keys=[blood_unit_id])
    scanned_unit = db.relationship("BloodUnit", foreign_keys=[scanned_unit_id])
    actor = db.relationship("Staff", foreign_keys=[actor_staff_id])


class BloodTransfusion(db.Model):
    __tablename__ = "blood_transfusions"

    __table_args__ = (
        db.CheckConstraint(
            "status IN "
            "('in_progress', 'completed', 'stopped_reaction', 'stopped_other')",
            name="ck_blood_transfusions_status",
        ),
        db.CheckConstraint(
            "reaction_type IN "
            "('none', 'febrile_non_hemolytic', 'allergic', "
            "'acute_hemolytic', 'delayed_hemolytic', 'trali', "
            "'taco', 'sepsis_contamination', 'other')",
            name="ck_blood_transfusions_reaction_type",
        ),
        db.CheckConstraint(
            "reaction_severity IN "
            "('none', 'mild', 'moderate', 'severe', 'life_threatening')",
            name="ck_blood_transfusions_reaction_severity",
        ),
        db.CheckConstraint(
            "NOT reaction_reported "
            "OR (reaction_type != 'none' AND reaction_severity != 'none' "
            "AND reaction_reported_at IS NOT NULL "
            "AND reaction_reported_by_staff_id IS NOT NULL)",
            name="ck_blood_transfusions_reaction_evidence",
        ),
        db.CheckConstraint(
            "status != 'completed' OR ended_at IS NOT NULL",
            name="ck_blood_transfusions_completed_ended",
        ),
        db.UniqueConstraint(
            "clinic_id",
            "blood_unit_id",
            name="uq_blood_transfusions_clinic_unit",
        ),
        db.Index(
            "ix_blood_transfusions_clinic_patient_started",
            "clinic_id",
            "patient_id",
            "started_at",
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
        db.ForeignKey("admissions.id"),
        nullable=False,
        index=True,
    )
    blood_product_order_id = db.Column(
        db.Integer,
        db.ForeignKey("blood_product_orders.id"),
        nullable=False,
        index=True,
    )
    blood_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("blood_units.id"),
        nullable=False,
        index=True,
    )
    barcode_verification_event_id = db.Column(
        db.Integer,
        db.ForeignKey("blood_transfusion_barcode_events.id"),
        nullable=False,
        index=True,
    )
    administered_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )
    started_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    ended_at = db.Column(db.DateTime(timezone=True), nullable=True)
    status = db.Column(
        db.String(24),
        nullable=False,
        default=TransfusionStatus.IN_PROGRESS.value,
        index=True,
    )
    pre_vitals = db.Column(db.JSON, nullable=True)
    post_vitals = db.Column(db.JSON, nullable=True)
    reaction_reported = db.Column(db.Boolean, nullable=False, default=False)
    reaction_type = db.Column(
        db.String(32),
        nullable=False,
        default=TransfusionReactionType.NONE.value,
    )
    reaction_severity = db.Column(
        db.String(24),
        nullable=False,
        default=TransfusionReactionSeverity.NONE.value,
    )
    reaction_notes = db.Column(db.Text, nullable=True)
    reaction_reported_at = db.Column(db.DateTime(timezone=True), nullable=True)
    reaction_reported_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
    )
    lookback_triggered = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.Text, nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
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
    blood_product_order = db.relationship("BloodProductOrder")
    blood_unit = db.relationship("BloodUnit")
    barcode_verification_event = db.relationship(
        "BloodTransfusionBarcodeEvent",
        foreign_keys=[barcode_verification_event_id],
    )
    administered_by = db.relationship(
        "Staff",
        foreign_keys=[administered_by_staff_id],
    )
    reaction_reported_by = db.relationship(
        "Staff",
        foreign_keys=[reaction_reported_by_staff_id],
    )
