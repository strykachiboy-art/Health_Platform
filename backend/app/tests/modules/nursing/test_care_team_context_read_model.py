"""CT1A: existing evidence cannot be mistaken for current ownership."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.enums.nursing_enums import NursingHandoffStatus
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import (
    AdmissionStatus,
    BedStatus,
    WardType,
)
from app.core.exceptions import NotFoundError
from app.extensions import db
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.nursing.services import nursing_service
from app.modules.nursing.services.care_team_context_service import (
    get_admission_care_context,
)
from app.modules.ward.models.ward_model import Admission, Bed, Ward


def _case(clinic, make_patient, make_staff):
    patient = make_patient(clinic)
    doctor = make_staff(clinic, role=Role.DOCTOR)
    nurse = make_staff(clinic, role=Role.NURSE)

    ward = Ward(
        clinic_id=clinic.id,
        name="CT1A Ward",
        ward_type=WardType.GENERAL,
        capacity=5,
    )
    db.session.add(ward)
    db.session.flush()

    bed = Bed(
        ward_id=ward.id,
        bed_number="CT1A-1",
        status=BedStatus.OCCUPIED,
    )
    db.session.add(bed)
    db.session.flush()

    admission = Admission(
        patient_id=patient.id,
        bed_id=bed.id,
        admitted_by_id=doctor.id,
        status=AdmissionStatus.ADMITTED,
        reason="CT1A evidence test",
    )
    db.session.add(admission)
    db.session.commit()

    return patient, doctor, nurse, admission


def test_admitting_doctor_is_not_automatically_primary(
    clinic, make_patient, make_staff,
):
    patient, doctor, nurse, admission = _case(
        clinic, make_patient, make_staff,
    )

    result = get_admission_care_context(
        admission_id=admission.id,
        clinic_id=clinic.id,
    )

    assert result["patient_id"] == patient.id
    assert result["admission_id"] == admission.id
    assert result["responsibility"] == {
        "primary_doctor_staff_id": None,
        "primary_nurse_staff_id": None,
        "confirmed": False,
    }
    assert result["authoritative_assignment_available"] is False
    assert result["handoff_evidence"]["pending_ready"] is None


def test_cross_clinic_admission_is_hidden(
    clinic, make_clinic, make_patient, make_staff,
):
    _patient, _doctor, _nurse, admission = _case(
        clinic, make_patient, make_staff,
    )

    other = make_clinic(name="CT1A Other Clinic")

    with pytest.raises(NotFoundError):
        get_admission_care_context(
            admission_id=admission.id,
            clinic_id=other.id,
        )


def test_task_assignment_is_evidence_not_primary_ownership(
    clinic, make_patient, make_staff, monkeypatch,
):
    _patient, _doctor, nurse, admission = _case(
        clinic, make_patient, make_staff,
    )

    monkeypatch.setattr(
        nursing_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    plan = nursing_service.create_care_plan(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        title="Mobility",
        problem="Immobility",
        goal="Safe movement",
    )

    nursing_service.create_care_task(
        care_plan_id=plan.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        description="Assist walking",
        assigned_to_staff_id=nurse.id,
    )

    result = get_admission_care_context(
        admission_id=admission.id,
        clinic_id=clinic.id,
    )

    assignees = result["involvement_evidence"][
        "active_nursing_task_assignees"
    ]

    assert [item["staff_id"] for item in assignees] == [nurse.id]
    assert result["responsibility"]["primary_nurse_staff_id"] is None
    assert result["responsibility"]["confirmed"] is False


def test_ready_handoff_does_not_transfer_responsibility(
    clinic, make_patient, make_staff,
):
    patient, _doctor, outgoing, admission = _case(
        clinic, make_patient, make_staff,
    )

    incoming = make_staff(clinic, role=Role.NURSE)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    handoff = NursingHandoff(
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        outgoing_staff_id=outgoing.id,
        incoming_staff_id=incoming.id,
        status=NursingHandoffStatus.READY,
        shift_start_at=now,
        shift_end_at=now + timedelta(hours=8),
        ready_at=now,
        version=1,
    )

    db.session.add(handoff)
    db.session.commit()

    result = get_admission_care_context(
        admission_id=admission.id,
        clinic_id=clinic.id,
    )

    pending = result["handoff_evidence"]["pending_ready"]

    assert pending["handoff_id"] == handoff.id
    assert pending["incoming_staff_id"] == incoming.id
    assert result["handoff_evidence"]["latest_acknowledged"] is None
    assert result["responsibility"]["primary_nurse_staff_id"] is None