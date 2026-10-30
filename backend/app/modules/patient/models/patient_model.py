from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Index

from app.extensions import db
from app.core.enums.patient_enums import (
    AllergyClinicalStatus,
    AllergySeverity,
    AllergyType,
    AllergyVerificationStatus,
    BloodType,
    FamilyRelation,
    Gender,
)


def _utcnow():
    return datetime.now(timezone.utc)


class Patient(db.Model):
    __tablename__ = "patients"

    __table_args__ = (
        Index(
            "ix_patients_clinic_active_name",
            "clinic_id",
            "is_active",
            "last_name",
            "first_name",
            "id",
        ),
        db.UniqueConstraint(
            "clinic_id",
            "barcode",
            name="uq_patients_clinic_barcode",
        ),
        Index(
            "ix_patients_clinic_barcode",
            "clinic_id",
            "barcode",
        ),
        CheckConstraint(
            "length(trim(first_name)) > 0",
            name="ck_patients_first_name_nonempty",
        ),
        CheckConstraint(
            "length(trim(last_name)) > 0",
            name="ck_patients_last_name_nonempty",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
        unique=True,
        index=True,
    )

    clinic_id = db.Column(
        db.Integer,
        db.ForeignKey("clinics.id"),
        nullable=False,
        index=True,
    )

    emirates_id = db.Column(
        db.String(50),
        unique=True,
        nullable=True,
        index=True,
    )

    umrn = db.Column(
        db.String(50),
        unique=True,
        nullable=True,
        index=True,
    )

    first_name = db.Column(
        db.String(80),
        nullable=False,
    )

    last_name = db.Column(
        db.String(80),
        nullable=False,
    )

    date_of_birth = db.Column(
        db.Date,
        nullable=True,
    )

    gender = db.Column(
        db.Enum(Gender),
        nullable=True,
    )

    blood_type = db.Column(
        db.Enum(BloodType),
        default=BloodType.UNKNOWN,
        nullable=False,
    )

    phone = db.Column(
        db.String(30),
        nullable=True,
    )

    email = db.Column(
        db.String(120),
        nullable=True,
    )

    address = db.Column(
        db.String(255),
        nullable=True,
    )

    allergies = db.Column(
        db.Text,
        nullable=True,
    )

    chronic_conditions = db.Column(
        db.Text,
        nullable=True,
    )

    patient_number = db.Column(
        db.String(50),
        unique=True,
        nullable=False,
        index=True,
    )

    barcode = db.Column(
        db.String(128),
        nullable=True,
    )

    is_active = db.Column(
        db.Boolean,
        default=True,
        nullable=False,
        index=True,
    )

    ai_risk_score = db.Column(
        db.String(20),
        nullable=True,
    )

    ai_summary = db.Column(
        db.Text,
        nullable=True,
    )

    ai_triage_data = db.Column(
        db.JSON,
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

    clinic = db.relationship(
        "Clinic",
        back_populates="patients",
    )

    user = db.relationship(
        "User",
        back_populates="patient",
        foreign_keys=[user_id],
        uselist=False,
    )

    family_members = db.relationship(
        "PatientFamilyMember",
        back_populates="patient",
        cascade="all, delete-orphan",
        foreign_keys="PatientFamilyMember.patient_id",
    )

    insurances = db.relationship(
        "PatientInsurance",
        back_populates="patient",
        cascade="all, delete-orphan",
    )

    vitals_history = db.relationship(
        "PatientVitals",
        back_populates="patient",
        cascade="all, delete-orphan",
    )

    ai_logs = db.relationship(
        "AILog",
        back_populates="patient",
    )

    appointments = db.relationship(
        "Appointment",
        back_populates="patient",
    )

    invoices = db.relationship(
        "Invoice",
        back_populates="patient",
    )

    consultations = db.relationship(
        "Consultation",
        back_populates="patient",
    )

    lab_orders = db.relationship(
        "LabOrder",
        back_populates="patient",
    )

    prescriptions = db.relationship(
        "Prescription",
        back_populates="patient",
    )

    allergy_records = db.relationship(
        "PatientAllergy",
        back_populates="patient",
        cascade="all, delete-orphan",
        order_by="PatientAllergy.recorded_at.desc()",
    )

    admissions = db.relationship(
        "Admission",
        back_populates="patient",
    )

    bed_reservations = db.relationship(
        "BedReservation",
        back_populates="patient",
    )

    hie_submissions = db.relationship(
        "HIESubmission",
        back_populates="patient",
    )

    ambulance_trips = db.relationship(
        "AmbulanceTrip",
        back_populates="patient",
    )

    def __repr__(self):
        return (
            f"<Patient {self.first_name} "
            f"{self.last_name} ({self.patient_number})>"
        )


class PatientAllergy(db.Model):
    __tablename__ = "patient_allergies"

    __table_args__ = (
        Index(
            "ix_patient_allergies_clinic_patient_status",
            "clinic_id",
            "patient_id",
            "clinical_status",
            "verification_status",
            "id",
        ),
        Index(
            "ix_patient_allergies_patient_allergen",
            "patient_id",
            "allergen_name",
            "allergen_code",
            "id",
        ),
        CheckConstraint(
            "length(trim(allergen_name)) > 0",
            name="ck_patient_allergies_name_nonempty",
        ),
        CheckConstraint(
            "allergen_type IN "
            "('drug', 'food', 'environmental', "
            "'latex', 'biologic', 'other')",
            name="ck_patient_allergies_type",
        ),
        CheckConstraint(
            "severity IN "
            "('unknown', 'mild', 'moderate', "
            "'severe', 'life_threatening')",
            name="ck_patient_allergies_severity",
        ),
        CheckConstraint(
            "clinical_status IN "
            "('active', 'inactive', 'resolved')",
            name="ck_patient_allergies_clinical_status",
        ),
        CheckConstraint(
            "verification_status IN "
            "('unconfirmed', 'confirmed', 'refuted')",
            name="ck_patient_allergies_verification_status",
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
        db.ForeignKey(
            "patients.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    allergen_name = db.Column(
        db.String(255),
        nullable=False,
    )

    allergen_code = db.Column(
        db.String(64),
        nullable=True,
        index=True,
    )

    allergen_type = db.Column(
        db.String(32),
        default=AllergyType.OTHER.value,
        nullable=False,
    )

    reaction = db.Column(
        db.String(500),
        nullable=True,
    )

    severity = db.Column(
        db.String(32),
        default=AllergySeverity.UNKNOWN.value,
        nullable=False,
    )

    clinical_status = db.Column(
        db.String(16),
        default=AllergyClinicalStatus.ACTIVE.value,
        nullable=False,
        index=True,
    )

    verification_status = db.Column(
        db.String(16),
        default=AllergyVerificationStatus.UNCONFIRMED.value,
        nullable=False,
        index=True,
    )

    onset_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    recorded_at = db.Column(
        db.DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
        index=True,
    )

    recorded_by_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "staff.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    notes = db.Column(
        db.String(1000),
        nullable=True,
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

    patient = db.relationship(
        "Patient",
        back_populates="allergy_records",
    )

    recorded_by = db.relationship(
        "Staff",
        foreign_keys=[recorded_by_id],
    )

    def __repr__(self):
        return (
            f"<PatientAllergy "
            f"{self.patient_id} {self.allergen_name}>"
        )

class PatientFamilyMember(db.Model):
    __tablename__ = "patient_family_members"

    __table_args__ = (
        Index(
            "ix_patient_family_members_patient_emergency_name",
            "patient_id",
            "is_emergency_contact",
            "full_name",
            "id",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    patient_id = db.Column(
        db.Integer,
        db.ForeignKey("patients.id"),
        nullable=False,
        index=True,
    )

    related_patient_id = db.Column(
        db.Integer,
        db.ForeignKey("patients.id"),
        nullable=True,
        index=True,
    )

    full_name = db.Column(
        db.String(150),
        nullable=False,
    )

    relation = db.Column(
        db.Enum(FamilyRelation),
        nullable=False,
    )

    phone = db.Column(
        db.String(30),
        nullable=True,
    )

    is_emergency_contact = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
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

    patient = db.relationship(
        "Patient",
        back_populates="family_members",
        foreign_keys=[patient_id],
    )

    related_patient = db.relationship(
        "Patient",
        foreign_keys=[related_patient_id],
    )

    def __repr__(self):
        return (
            f"<PatientFamilyMember "
            f"{self.full_name} ({self.relation.value})>"
        )


class PatientInsurance(db.Model):
    __tablename__ = "patient_insurances"

    __table_args__ = (
        Index(
            "ix_patient_insurances_patient_priority",
            "patient_id",
            "is_primary",
            "is_active",
            "created_at",
            "id",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    patient_id = db.Column(
        db.Integer,
        db.ForeignKey("patients.id"),
        nullable=False,
        index=True,
    )

    provider_name = db.Column(
        db.String(150),
        nullable=False,
    )

    policy_number = db.Column(
        db.String(100),
        nullable=False,
    )

    plan_type = db.Column(
        db.String(100),
        nullable=True,
    )

    coverage_start = db.Column(
        db.Date,
        nullable=True,
    )

    coverage_end = db.Column(
        db.Date,
        nullable=True,
    )

    is_primary = db.Column(
        db.Boolean,
        default=True,
        nullable=False,
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

    patient = db.relationship(
        "Patient",
        back_populates="insurances",
    )

    def __repr__(self):
        return (
            f"<PatientInsurance "
            f"{self.provider_name} - {self.policy_number}>"
        )


class PatientVitals(db.Model):
    __tablename__ = "patient_vitals"

    __table_args__ = (
        Index(
            "ix_patient_vitals_patient_recorded",
            "patient_id",
            "recorded_at",
            "id",
        ),
        Index(
            "ix_patient_vitals_patient_consultation",
            "patient_id",
            "consultation_id",
            "id",
        ),
        CheckConstraint(
            "temperature_c IS NULL OR "
            "(temperature_c >= 0 AND temperature_c <= 100)",
            name="ck_patient_vitals_temperature",
        ),
        CheckConstraint(
            "blood_pressure_systolic IS NULL OR "
            "(blood_pressure_systolic >= 0 AND blood_pressure_systolic <= 400)",
            name="ck_patient_vitals_systolic",
        ),
        CheckConstraint(
            "blood_pressure_diastolic IS NULL OR "
            "(blood_pressure_diastolic >= 0 AND blood_pressure_diastolic <= 300)",
            name="ck_patient_vitals_diastolic",
        ),
        CheckConstraint(
            "heart_rate_bpm IS NULL OR "
            "(heart_rate_bpm >= 0 AND heart_rate_bpm <= 400)",
            name="ck_patient_vitals_heart_rate",
        ),
        CheckConstraint(
            "respiratory_rate IS NULL OR "
            "(respiratory_rate >= 0 AND respiratory_rate <= 200)",
            name="ck_patient_vitals_respiratory_rate",
        ),
        CheckConstraint(
            "oxygen_saturation IS NULL OR "
            "(oxygen_saturation >= 0 AND oxygen_saturation <= 100)",
            name="ck_patient_vitals_oxygen",
        ),
        CheckConstraint(
            "weight_kg IS NULL OR weight_kg >= 0",
            name="ck_patient_vitals_weight",
        ),
        CheckConstraint(
            "height_cm IS NULL OR height_cm >= 0",
            name="ck_patient_vitals_height",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
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

    recorded_by_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )

    temperature_c = db.Column(
        db.Numeric(4, 1),
        nullable=True,
    )

    blood_pressure_systolic = db.Column(
        db.Integer,
        nullable=True,
    )

    blood_pressure_diastolic = db.Column(
        db.Integer,
        nullable=True,
    )

    heart_rate_bpm = db.Column(
        db.Integer,
        nullable=True,
    )

    respiratory_rate = db.Column(
        db.Integer,
        nullable=True,
    )

    oxygen_saturation = db.Column(
        db.Numeric(4, 1),
        nullable=True,
    )

    weight_kg = db.Column(
        db.Numeric(5, 2),
        nullable=True,
    )

    height_cm = db.Column(
        db.Numeric(5, 2),
        nullable=True,
    )

    recorded_at = db.Column(
        db.DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
        index=True,
    )

    patient = db.relationship(
        "Patient",
        back_populates="vitals_history",
    )

    def __repr__(self):
        return (
            f"<PatientVitals Patient "
            f"{self.patient_id} @ {self.recorded_at}>"
        )