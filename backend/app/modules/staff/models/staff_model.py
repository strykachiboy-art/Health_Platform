from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db
from app.modules.staff.models.staff_department_model import StaffDepartment

from app.core.enums.staff_enums import (
    LeaveStatus,
    LeaveType,
    StaffStatus,
)


def _utcnow():
    return datetime.now(timezone.utc).replace(
        tzinfo=None
    )


class Staff(db.Model):
    __tablename__ = "staff"

    __table_args__ = (
        db.UniqueConstraint(
            "id",
            "clinic_id",
            name="uq_staff_id_clinic",
        ),
        db.Index(
            "ix_staff_clinic_status_name",
            "clinic_id",
            "status",
            "last_name",
            "first_name",
            "id",
        ),
        db.Index(
            "ix_staff_clinic_specialty",
            "clinic_id",
            "specialty",
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

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
        unique=True,
        index=True,
    )

    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id"),
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

    specialty = db.Column(
        db.String(100),
        nullable=True,
        index=True,
    )

    phone = db.Column(
        db.String(30),
        nullable=True,
    )

    email = db.Column(
        db.String(120),
        nullable=True,
    )

    status = db.Column(
        db.Enum(StaffStatus),
        default=StaffStatus.ACTIVE,
        nullable=False,
        index=True,
    )

    hired_at = db.Column(
        db.Date,
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
        back_populates="staff",
    )

    user = db.relationship(
        "User",
        back_populates="staff",
    )

    department_memberships = db.relationship(
        "StaffDepartment",
        back_populates="staff",
        foreign_keys=lambda: [
            StaffDepartment.staff_id,
            StaffDepartment.clinic_id,
        ],
        overlaps="department,clinic,staff_memberships",
    )

    departments = db.relationship(
        "Department",
        secondary="staff_departments",
        primaryjoin=(
            "and_("
            "Staff.id == StaffDepartment.staff_id, "
            "Staff.clinic_id == StaffDepartment.clinic_id"
            ")"
        ),
        secondaryjoin=(
            "and_("
            "Department.id == StaffDepartment.department_id, "
            "Department.clinic_id == StaffDepartment.clinic_id"
            ")"
        ),
        viewonly=True,
    )

    department = db.relationship(
        "Department",
        foreign_keys=[department_id],
        viewonly=True,
    )

    appointments = db.relationship(
        "Appointment",
        back_populates="staff",
    )

    consultations = db.relationship(
        "Consultation",
        foreign_keys="Consultation.staff_id",
        back_populates="staff",
    )

    lab_orders = db.relationship(
        "LabOrder",
        foreign_keys="LabOrder.ordered_by_id",
        back_populates="ordered_by",
    )

    bed_reservations = db.relationship(
        "BedReservation",
        back_populates="reserved_by",
    )

    prescriptions = db.relationship(
        "Prescription",
        back_populates="prescribed_by",
    )

    dispense_records = db.relationship(
        "DispenseRecord",
        back_populates="dispensed_by",
    )

    stock_movements = db.relationship(
        "StockMovement",
        back_populates="performed_by",
    )

    generated_reports = db.relationship(
        "GeneratedReport",
        back_populates="generated_by",
    )

    admissions = db.relationship(
        "Admission",
        back_populates="admitted_by",
    )

    driver_trips = db.relationship(
        "AmbulanceTrip",
        foreign_keys="AmbulanceTrip.driver_id",
        back_populates="driver",
    )

    paramedic_trips = db.relationship(
        "AmbulanceTrip",
        foreign_keys="AmbulanceTrip.paramedic_id",
        back_populates="paramedic",
    )

    payroll_records = db.relationship(
        "PayrollRecord",
        back_populates="staff",
        cascade="all, delete-orphan",
    )

    leave_requests = db.relationship(
        "LeaveRequest",
        back_populates="staff",
        cascade="all, delete-orphan",
        foreign_keys="LeaveRequest.staff_id",
    )

    excuses = db.relationship(
        "Excuse",
        back_populates="staff",
        cascade="all, delete-orphan",
        foreign_keys="Excuse.staff_id",
    )

    def __repr__(self):
        return (
            f"<Staff {self.first_name} "
            f"{self.last_name}>"
        )


class PayrollRecord(db.Model):
    __tablename__ = "payroll_records"

    __table_args__ = (
        db.UniqueConstraint(
            "staff_id",
            "pay_period_start",
            "pay_period_end",
            name="uq_payroll_staff_period",
        ),
        db.CheckConstraint(
            "pay_period_start <= pay_period_end",
            name="ck_payroll_valid_period",
        ),
        db.CheckConstraint(
            "base_salary >= 0",
            name="ck_payroll_base_salary_non_negative",
        ),
        db.CheckConstraint(
            "bonuses >= 0",
            name="ck_payroll_bonuses_non_negative",
        ),
        db.CheckConstraint(
            "deductions >= 0",
            name="ck_payroll_deductions_non_negative",
        ),
        db.CheckConstraint(
            "net_pay >= 0",
            name="ck_payroll_net_pay_non_negative",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )

    pay_period_start = db.Column(
        db.Date,
        nullable=False,
    )

    pay_period_end = db.Column(
        db.Date,
        nullable=False,
    )

    base_salary = db.Column(
        db.Numeric(12, 2),
        nullable=False,
    )

    bonuses = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0,
    )

    deductions = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0,
    )

    net_pay = db.Column(
        db.Numeric(12, 2),
        nullable=False,
    )

    paid_at = db.Column(
        db.DateTime,
        nullable=True,
        index=True,
    )

    created_at = db.Column(
        db.DateTime,
        default=_utcnow,
        nullable=False,
        index=True,
    )

    updated_at = db.Column(
        db.DateTime,
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    staff = db.relationship(
        "Staff",
        back_populates="payroll_records",
    )

    def __repr__(self):
        return (
            f"<PayrollRecord Staff {self.staff_id} "
            f"({self.pay_period_start} - "
            f"{self.pay_period_end})>"
        )


class LeaveRequest(db.Model):
    __tablename__ = "leave_requests"

    __table_args__ = (
        db.CheckConstraint(
            "start_date <= end_date",
            name="ck_leave_requests_valid_period",
        ),
        db.Index(
            "ix_leave_requests_staff_status_period",
            "staff_id",
            "status",
            "start_date",
            "end_date",
            "id",
        ),
        db.Index(
            "ix_leave_requests_status_period",
            "status",
            "start_date",
            "end_date",
            "id",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )

    leave_type = db.Column(
        db.Enum(LeaveType),
        nullable=False,
        index=True,
    )

    status = db.Column(
        db.Enum(LeaveStatus),
        default=LeaveStatus.PENDING,
        nullable=False,
        index=True,
    )

    start_date = db.Column(
        db.Date,
        nullable=False,
    )

    end_date = db.Column(
        db.Date,
        nullable=False,
    )

    reason = db.Column(
        db.Text,
        nullable=True,
    )

    reviewed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    reviewed_at = db.Column(
        db.DateTime,
        nullable=True,
        index=True,
    )

    created_at = db.Column(
        db.DateTime,
        default=_utcnow,
        nullable=False,
        index=True,
    )

    updated_at = db.Column(
        db.DateTime,
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    staff = db.relationship(
        "Staff",
        back_populates="leave_requests",
        foreign_keys=[staff_id],
    )

    reviewed_by = db.relationship(
        "User",
        foreign_keys=[reviewed_by_user_id],
    )

    excuses = db.relationship(
        "Excuse",
        back_populates="leave_request",
        cascade="all, delete-orphan",
        foreign_keys="Excuse.leave_request_id",
    )

    def __repr__(self):
        return (
            f"<LeaveRequest Staff {self.staff_id} "
            f"({self.status.value})>"
        )