from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ClinicalRecordAmendment(db.Model):
    __tablename__ = "clinical_record_amendments"

    __table_args__ = (
        db.CheckConstraint(
            "source_record_type IN ("
            "'consultation_note', "
            "'nursing_note', "
            "'medication_administration', "
            "'medication_reconciliation', "
            "'discharge_summary'"
            ")",
            name="ck_clinical_record_amendments_type",
        ),
        db.CheckConstraint(
            "source_record_id > 0",
            name="ck_clinical_record_amendments_source_id_positive",
        ),
        db.CheckConstraint(
            "previous_version >= 1",
            name="ck_clinical_record_amendments_previous_version_positive",
        ),
        db.CheckConstraint(
            "new_version = previous_version + 1",
            name="ck_clinical_record_amendments_version_contiguous",
        ),
        db.CheckConstraint(
            "length(reason) > 0",
            name="ck_clinical_record_amendments_reason_nonempty",
        ),
        db.CheckConstraint(
            "length(new_content_sha256) = 64",
            name="ck_clinical_record_amendments_sha256_length",
        ),
        db.UniqueConstraint(
            "clinic_id",
            "source_record_type",
            "source_record_id",
            "new_version",
            name="uq_clinical_record_amendments_source_version",
        ),
        db.Index(
            "ix_clinical_record_amendments_source_version",
            "clinic_id",
            "source_record_type",
            "source_record_id",
            "new_version",
        ),
        db.Index(
            "ix_clinical_record_amendments_patient_amended",
            "clinic_id",
            "patient_id",
            "amended_at",
            "id",
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

    source_record_type = db.Column(
        db.String(64),
        nullable=False,
        index=True,
    )

    source_record_id = db.Column(
        db.Integer,
        nullable=False,
        index=True,
    )

    previous_version = db.Column(
        db.Integer,
        nullable=False,
    )

    new_version = db.Column(
        db.Integer,
        nullable=False,
    )

    previous_amendment_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "clinical_record_amendments.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    amended_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )

    reason = db.Column(
        db.Text,
        nullable=False,
    )

    new_content = db.Column(
        db.JSON,
        nullable=False,
    )

    new_content_sha256 = db.Column(
        db.String(64),
        nullable=False,
    )

    amended_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        index=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    clinic = db.relationship(
        "Clinic",
    )

    patient = db.relationship(
        "Patient",
    )

    amended_by = db.relationship(
        "Staff",
        foreign_keys=[amended_by_staff_id],
    )

    previous_amendment = db.relationship(
        "ClinicalRecordAmendment",
        remote_side=[id],
        foreign_keys=[previous_amendment_id],
        uselist=False,
    )
