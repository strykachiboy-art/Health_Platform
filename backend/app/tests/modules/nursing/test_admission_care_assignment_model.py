"""CT1B1: admission assignment database invariants."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import (
    AdmissionStatus,
    BedStatus,
    WardType,
)
from app.extensions import db
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.ward.models.ward_model import Admission, Bed, Ward


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _case(clinic, make_patient, make_staff):
    patient = make_patient(clinic)
    admin = make_staff(clinic, role=Role.ADMIN)
    doctor = make_staff(clinic, role=Role.DOCTOR)
    nurse = make_staff(clinic, role=Role.NURSE)

    ward = Ward(
        clinic_id=clinic.id,
        name="CT1B Ward",
        ward_type=WardType.GENERAL,
        capacity=5,
    )
    db.session.add(ward)
    db.session.flush()

    bed = Bed(
        ward_id=ward.id,
        bed_number="CT1B-001",
        status=BedStatus.OCCUPIED,
    )
    db.session.add(bed)
    db.session.flush()

    admission = Admission(
        patient_id=patient.id,
        bed_id=bed.id,
        admitted_by_id=doctor.id,
        status=AdmissionStatus.ADMITTED,
    )
    db.session.add(admission)
    db.session.commit()

    return patient, admin, doctor, nurse, admission


def _assignment(clinic, patient, admin, staff, admission, kind):
    return AdmissionCareAssignment(
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        staff_id=staff.id,
        assignment_kind=kind,
        assigned_by_user_id=admin.user_id,
        assignment_reason="Clinical allocation",
        assigned_at=_now(),
    )


def test_model_registered_with_partial_active_uniqueness():
    assert "admission_care_assignments" in db.metadata.tables

    table = AdmissionCareAssignment.__table__
    columns = set(table.columns.keys())

    assert {
        "clinic_id", "patient_id", "admission_id", "staff_id",
        "assignment_kind", "assigned_by_user_id",
        "assignment_reason", "assigned_at", "ended_at",
        "ended_by_user_id", "end_reason",
    }.issubset(columns)

    unique_index = next(
        index for index in table.indexes
        if index.name == "uq_admission_care_assignment_active_kind"
    )

    assert unique_index.unique

    assert str(
        unique_index.dialect_options["postgresql"]["where"]
    ) == "ended_at IS NULL"

    assert str(
        unique_index.dialect_options["sqlite"]["where"]
    ) == "ended_at IS NULL"


def test_doctor_and_nurse_have_distinct_primary_slots(
    clinic, make_patient, make_staff,
):
    patient, admin, doctor, nurse, admission = _case(
        clinic, make_patient, make_staff,
    )

    db.session.add_all([
        _assignment(
            clinic, patient, admin, doctor,
            admission, "primary_doctor",
        ),
        _assignment(
            clinic, patient, admin, nurse,
            admission, "primary_nurse",
        ),
    ])
    db.session.commit()

    assert db.session.query(AdmissionCareAssignment).count() == 2


def test_second_active_doctor_is_blocked_by_database(
    clinic, make_patient, make_staff,
):
    patient, admin, doctor, _nurse, admission = _case(
        clinic, make_patient, make_staff,
    )

    another = make_staff(clinic, role=Role.DOCTOR)

    db.session.add(_assignment(
        clinic, patient, admin, doctor,
        admission, "primary_doctor",
    ))
    db.session.commit()

    db.session.add(_assignment(
        clinic, patient, admin, another,
        admission, "primary_doctor",
    ))

    with pytest.raises(IntegrityError):
        db.session.flush()

    db.session.rollback()


def test_ended_assignment_preserves_history_and_allows_successor(
    clinic, make_patient, make_staff,
):
    patient, admin, doctor, _nurse, admission = _case(
        clinic, make_patient, make_staff,
    )

    another = make_staff(clinic, role=Role.DOCTOR)

    first = _assignment(
        clinic, patient, admin, doctor,
        admission, "primary_doctor",
    )

    db.session.add(first)
    db.session.commit()

    # Read expired actor identity before dirtying the assignment.
    # Otherwise the ORM may autoflush an incomplete end transition.
    end_actor_user_id = admin.user_id
    first.ended_at = first.assigned_at + timedelta(seconds=1)
    first.ended_by_user_id = end_actor_user_id
    first.end_reason = "Clinical reassignment"
    db.session.flush()

    second = _assignment(
        clinic, patient, admin, another,
        admission, "primary_doctor",
    )

    db.session.add(second)
    db.session.commit()

    rows = db.session.query(AdmissionCareAssignment).order_by(
        AdmissionCareAssignment.id,
    ).all()

    assert len(rows) == 2
    assert rows[0].ended_at is not None
    assert rows[1].ended_at is None


def test_ending_requires_actor_and_reason(
    clinic, make_patient, make_staff,
):
    patient, admin, doctor, _nurse, admission = _case(
        clinic, make_patient, make_staff,
    )

    first = _assignment(
        clinic, patient, admin, doctor,
        admission, "primary_doctor",
    )

    db.session.add(first)
    db.session.commit()

    first.ended_at = first.assigned_at + timedelta(seconds=1)

    with pytest.raises(IntegrityError):
        db.session.flush()

    db.session.rollback()


def test_unsupported_assignment_kind_rejected(
    clinic, make_patient, make_staff,
):
    patient, admin, doctor, _nurse, admission = _case(
        clinic, make_patient, make_staff,
    )

    db.session.add(_assignment(
        clinic, patient, admin, doctor,
        admission, "historical_consultant",
    ))

    with pytest.raises(IntegrityError):
        db.session.flush()

    db.session.rollback()