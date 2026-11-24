from datetime import datetime, timezone

from app.extensions import db
from app.core.enums.clinic_enums import (
    ClinicStatus,
    ClinicType,
)


def _utcnow():
    return datetime.now(timezone.utc)


class Clinic(db.Model):
    __tablename__ = "clinics"

    __table_args__ = (
        db.CheckConstraint(
            "id != parent_clinic_id",
            name="ck_clinics_not_self_parent",
        ),
        db.CheckConstraint(
            "ai_credits >= 0",
            name="ck_clinics_ai_credits_non_negative",
        ),
        db.CheckConstraint(
            "ai_requests_this_month >= 0",
            name="ck_clinics_ai_requests_non_negative",
        ),
        db.CheckConstraint(
            "opening_time IS NULL OR closing_time IS NULL "
            "OR opening_time <> closing_time "
            "OR (CAST(opening_time AS VARCHAR) IN ('00:00:00', '00:00:00.000000') "
            "AND CAST(closing_time AS VARCHAR) IN ('00:00:00', '00:00:00.000000'))",
            name="ck_clinics_valid_opening_hours",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    name = db.Column(
        db.String(150),
        nullable=False,
    )

    clinic_type = db.Column(
        db.Enum(ClinicType),
        default=ClinicType.GENERAL,
        nullable=False,
    )

    status = db.Column(
        db.Enum(ClinicStatus),
        default=ClinicStatus.ACTIVE,
        nullable=False,
    )

    parent_clinic_id = db.Column(
        db.Integer,
        db.ForeignKey("clinics.id"),
        nullable=True,
        index=True,
    )

    is_headquarters = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
    )

    address = db.Column(
        db.String(255),
        nullable=True,
    )

    city = db.Column(
        db.String(100),
        nullable=True,
    )

    country = db.Column(
        db.String(100),
        nullable=True,
    )

    phone = db.Column(
        db.String(30),
        nullable=True,
    )

    email = db.Column(
        db.String(120),
        nullable=True,
    )

    timezone = db.Column(
        db.String(50),
        default="UTC",
        nullable=False,
    )

    opening_time = db.Column(
        db.Time,
        nullable=True,
    )

    closing_time = db.Column(
        db.Time,
        nullable=True,
    )

    ai_credits = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    api_token = db.Column(
        db.String(255),
        nullable=True,
        unique = True,
    )

    ai_requests_this_month = db.Column(
        db.Integer,
        nullable=False,
        default=0,
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

    # ---------------------------------------------------------
    # Clinic hierarchy
    # ---------------------------------------------------------

    parent_clinic = db.relationship(
        "Clinic",
        remote_side=[id],
        foreign_keys=[parent_clinic_id],
        back_populates="branches",
    )

    branches = db.relationship(
        "Clinic",
        foreign_keys=[parent_clinic_id],
        back_populates="parent_clinic",
    )

    # ---------------------------------------------------------
    # Related entities
    # ---------------------------------------------------------

    appointments = db.relationship(
        "Appointment",
        back_populates="clinic",
    )

    drugs = db.relationship(
        "Drug",
        back_populates="clinic",
    )

    drug_batches = db.relationship(
        "DrugBatch",
        back_populates="clinic",
    )

    invoices = db.relationship(
        "Invoice",
        back_populates="clinic",
    )

    patients = db.relationship(
        "Patient",
        back_populates="clinic",
    )

    staff = db.relationship(
        "Staff",
        back_populates="clinic",
    )
    
    departments = db.relationship(
        "Department",
        back_populates="clinic",
    )

    ai_logs = db.relationship(
        "AILog",
        back_populates="clinic",
    )

    wards = db.relationship(
        "Ward",
        back_populates="clinic",
    )

    lab_orders = db.relationship(
        "LabOrder",
        back_populates="clinic",
    )

    prescriptions = db.relationship(
        "Prescription",
        back_populates="clinic",
    )

    inventory_items = db.relationship(
        "InventoryItem",
        back_populates="clinic",
    )

    inventory_suppliers = db.relationship(
        "InventorySupplier",
        back_populates="clinic",
    )

    hie_integrations = db.relationship(
        "HIEIntegration",
        back_populates="clinic",
    )

    hie_submissions = db.relationship(
        "HIESubmission",
        back_populates="clinic",
    )

    consultations = db.relationship(
        "Consultation",
        back_populates="clinic",
    )

    generated_reports = db.relationship(
        "GeneratedReport",
        back_populates="clinic",
    )

    ambulance_vehicles = db.relationship(
        "AmbulanceVehicle",
        back_populates="clinic",
    )

    ambulance_trips = db.relationship(
        "AmbulanceTrip",
        back_populates="clinic",
    )
    
    messages = db.relationship(
        "Message",
         back_populates="clinic",
    )

    notifications = db.relationship(
        "Notification",
         back_populates="clinic",
    )
    
    settings = db.relationship(
        "ClinicSettings",
        back_populates="clinic",
        uselist=False,
        cascade="all, delete-orphan",
   )
    
    integration_configs = db.relationship(
        "IntegrationConfig",
        back_populates="clinic",
        cascade="all, delete-orphan",
   )
    
    assets = db.relationship(
        "Asset",
        back_populates="clinic",
        cascade="all, delete-orphan",
   )
    
    feedback_items = db.relationship(
        "Feedback",
        back_populates="clinic",
    )
    
    def __repr__(self):
        return (
            f"<Clinic {self.name} "
            f"({self.status.value})>"
        )