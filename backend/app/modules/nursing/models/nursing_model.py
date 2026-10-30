from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db

from app.core.enums.nursing_enums import (
    NursingCarePlanStatus,
    NursingCareTaskPriority,
    NursingCareTaskStatus,
    NursingHandoffStatus,
    NursingNoteStatus,
    NursingNoteType,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NursingCarePlan(db.Model):
    __tablename__ = "nursing_care_plans"

    __table_args__ = (
        db.CheckConstraint(
            "version >= 1",
            name="ck_nursing_care_plans_version_positive",
        ),
        db.CheckConstraint(
            "target_at IS NULL OR target_at >= started_at",
            name="ck_nursing_care_plans_target_after_start",
        ),
        db.CheckConstraint(
            "status != 'COMPLETED' OR completed_at IS NOT NULL",
            name="ck_nursing_care_plans_completed_evidence",
        ),
        db.CheckConstraint(
            "status != 'CANCELLED' OR cancelled_at IS NOT NULL",
            name="ck_nursing_care_plans_cancelled_evidence",
        ),
        db.CheckConstraint(
            "completed_at IS NULL OR cancelled_at IS NULL",
            name="ck_nursing_care_plans_terminal_exclusive",
        ),
        db.Index(
            "ix_nursing_care_plans_clinic_admission_status",
            "clinic_id",
            "admission_id",
            "status",
        ),
        db.Index(
            "ix_nursing_care_plans_patient_admission_created",
            "patient_id",
            "admission_id",
            "created_at",
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

    created_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )

    title = db.Column(
        db.String(200),
        nullable=False,
    )

    problem = db.Column(
        db.Text,
        nullable=False,
    )

    goal = db.Column(
        db.Text,
        nullable=False,
    )

    status = db.Column(
        db.Enum(
            NursingCarePlanStatus,
            name="nursingcareplanstatus",
        ),
        default=NursingCarePlanStatus.ACTIVE,
        nullable=False,
        index=True,
    )

    started_at = db.Column(
        db.DateTime,
        default=_utcnow,
        nullable=False,
    )

    target_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    completed_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    cancelled_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    version = db.Column(
        db.Integer,
        default=1,
        nullable=False,
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
        foreign_keys=[clinic_id],
    )

    patient = db.relationship(
        "Patient",
        foreign_keys=[patient_id],
    )

    admission = db.relationship(
        "Admission",
        foreign_keys=[admission_id],
    )

    created_by = db.relationship(
        "Staff",
        foreign_keys=[created_by_staff_id],
    )

    tasks = db.relationship(
        "NursingCareTask",
        back_populates="care_plan",
    )

    def __repr__(self) -> str:
        return (
            f"<NursingCarePlan {self.id} "
            f"Admission {self.admission_id} "
            f"({self.status.value})>"
        )


class NursingCareTask(db.Model):
    __tablename__ = "nursing_care_tasks"

    __table_args__ = (
        db.CheckConstraint(
            "version >= 1",
            name="ck_nursing_care_tasks_version_positive",
        ),
        db.CheckConstraint(
            "status != 'COMPLETED' OR "
            "(completed_at IS NOT NULL "
            "AND completed_by_staff_id IS NOT NULL)",
            name="ck_nursing_care_tasks_completed_evidence",
        ),
        db.CheckConstraint(
            "status != 'CANCELLED' OR cancelled_at IS NOT NULL",
            name="ck_nursing_care_tasks_cancelled_evidence",
        ),
        db.CheckConstraint(
            "completed_at IS NULL OR cancelled_at IS NULL",
            name="ck_nursing_care_tasks_terminal_exclusive",
        ),
        db.Index(
            "ix_nursing_care_tasks_clinic_admission_status_due",
            "clinic_id",
            "admission_id",
            "status",
            "due_at",
        ),
        db.Index(
            "ix_nursing_care_tasks_plan_status_priority",
            "care_plan_id",
            "status",
            "priority",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    care_plan_id = db.Column(
        db.Integer,
        db.ForeignKey("nursing_care_plans.id"),
        nullable=False,
        index=True,
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

    created_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )

    assigned_to_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )

    completed_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )

    description = db.Column(
        db.Text,
        nullable=False,
    )

    instructions = db.Column(
        db.Text,
        nullable=True,
    )

    priority = db.Column(
        db.Enum(
            NursingCareTaskPriority,
            name="nursingcaretaskpriority",
        ),
        default=NursingCareTaskPriority.ROUTINE,
        nullable=False,
        index=True,
    )

    status = db.Column(
        db.Enum(
            NursingCareTaskStatus,
            name="nursingcaretaskstatus",
        ),
        default=NursingCareTaskStatus.PENDING,
        nullable=False,
        index=True,
    )

    due_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    started_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    completed_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    cancelled_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    cancellation_reason = db.Column(
        db.Text,
        nullable=True,
    )

    version = db.Column(
        db.Integer,
        default=1,
        nullable=False,
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

    care_plan = db.relationship(
        "NursingCarePlan",
        back_populates="tasks",
    )

    clinic = db.relationship(
        "Clinic",
        foreign_keys=[clinic_id],
    )

    patient = db.relationship(
        "Patient",
        foreign_keys=[patient_id],
    )

    admission = db.relationship(
        "Admission",
        foreign_keys=[admission_id],
    )

    created_by = db.relationship(
        "Staff",
        foreign_keys=[created_by_staff_id],
    )

    assigned_to = db.relationship(
        "Staff",
        foreign_keys=[assigned_to_staff_id],
    )

    completed_by = db.relationship(
        "Staff",
        foreign_keys=[completed_by_staff_id],
    )

    def __repr__(self) -> str:
        return (
            f"<NursingCareTask {self.id} "
            f"Plan {self.care_plan_id} "
            f"({self.status.value})>"
        )


class NursingNote(db.Model):
    __tablename__ = "nursing_notes"

    __table_args__ = (
        db.CheckConstraint(
            "version >= 1",
            name="ck_nursing_notes_version_positive",
        ),
        db.CheckConstraint(
            "status != 'SIGNED' OR signed_at IS NOT NULL",
            name="ck_nursing_notes_signed_evidence",
        ),
        db.Index(
            "ix_nursing_notes_clinic_admission_observed",
            "clinic_id",
            "admission_id",
            "observed_at",
        ),
        db.Index(
            "ix_nursing_notes_patient_admission_status",
            "patient_id",
            "admission_id",
            "status",
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

    author_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )

    note_type = db.Column(
        db.Enum(
            NursingNoteType,
            name="nursingnotetype",
        ),
        default=NursingNoteType.PROGRESS,
        nullable=False,
        index=True,
    )

    status = db.Column(
        db.Enum(
            NursingNoteStatus,
            name="nursingnotestatus",
        ),
        default=NursingNoteStatus.DRAFT,
        nullable=False,
        index=True,
    )

    note_text = db.Column(
        db.Text,
        nullable=False,
    )

    observed_at = db.Column(
        db.DateTime,
        default=_utcnow,
        nullable=False,
    )

    signed_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    version = db.Column(
        db.Integer,
        default=1,
        nullable=False,
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
        foreign_keys=[clinic_id],
    )

    patient = db.relationship(
        "Patient",
        foreign_keys=[patient_id],
    )

    admission = db.relationship(
        "Admission",
        foreign_keys=[admission_id],
    )

    author = db.relationship(
        "Staff",
        foreign_keys=[author_staff_id],
    )

    def __repr__(self) -> str:
        return (
            f"<NursingNote {self.id} "
            f"Admission {self.admission_id} "
            f"({self.status.value})>"
        )


class NursingHandoff(db.Model):
    __tablename__ = "nursing_handoffs"

    __table_args__ = (
        db.CheckConstraint(
            "version >= 1",
            name="ck_nursing_handoffs_version_positive",
        ),
        db.CheckConstraint(
            "shift_end_at > shift_start_at",
            name="ck_nursing_handoffs_shift_window",
        ),
        db.CheckConstraint(
            "incoming_staff_id IS NULL "
            "OR incoming_staff_id != outgoing_staff_id",
            name="ck_nursing_handoffs_distinct_staff",
        ),
        db.CheckConstraint(
            "status != 'READY' OR ready_at IS NOT NULL",
            name="ck_nursing_handoffs_ready_evidence",
        ),
        db.CheckConstraint(
            "status != 'ACKNOWLEDGED' OR "
            "(ready_at IS NOT NULL "
            "AND incoming_staff_id IS NOT NULL "
            "AND acknowledged_at IS NOT NULL "
            "AND acknowledged_by_staff_id IS NOT NULL)",
            name="ck_nursing_handoffs_ack_evidence",
        ),
        db.CheckConstraint(
            "status != 'CANCELLED' OR cancelled_at IS NOT NULL",
            name="ck_nursing_handoffs_cancelled_evidence",
        ),
        db.Index(
            "ix_nursing_handoffs_clinic_admission_status_shift",
            "clinic_id",
            "admission_id",
            "status",
            "shift_end_at",
        ),
        db.Index(
            "ix_nursing_handoffs_incoming_status_created",
            "incoming_staff_id",
            "status",
            "created_at",
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

    outgoing_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )

    incoming_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )

    acknowledged_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )

    status = db.Column(
        db.Enum(
            NursingHandoffStatus,
            name="nursinghandoffstatus",
        ),
        default=NursingHandoffStatus.DRAFT,
        nullable=False,
        index=True,
    )

    shift_start_at = db.Column(
        db.DateTime,
        nullable=False,
    )

    shift_end_at = db.Column(
        db.DateTime,
        nullable=False,
    )

    situation = db.Column(
        db.Text,
        nullable=True,
    )

    background = db.Column(
        db.Text,
        nullable=True,
    )

    assessment = db.Column(
        db.Text,
        nullable=True,
    )

    recommendation = db.Column(
        db.Text,
        nullable=True,
    )

    safety_concerns = db.Column(
        db.Text,
        nullable=True,
    )

    pending_actions = db.Column(
        db.Text,
        nullable=True,
    )

    ready_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    acknowledged_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    cancelled_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    version = db.Column(
        db.Integer,
        default=1,
        nullable=False,
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
        foreign_keys=[clinic_id],
    )

    patient = db.relationship(
        "Patient",
        foreign_keys=[patient_id],
    )

    admission = db.relationship(
        "Admission",
        foreign_keys=[admission_id],
    )

    outgoing_staff = db.relationship(
        "Staff",
        foreign_keys=[outgoing_staff_id],
    )

    incoming_staff = db.relationship(
        "Staff",
        foreign_keys=[incoming_staff_id],
    )

    acknowledged_by = db.relationship(
        "Staff",
        foreign_keys=[acknowledged_by_staff_id],
    )

    def __repr__(self) -> str:
        return (
            f"<NursingHandoff {self.id} "
            f"Admission {self.admission_id} "
            f"({self.status.value})>"
        )
