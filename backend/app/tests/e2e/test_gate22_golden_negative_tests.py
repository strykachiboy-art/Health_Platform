from __future__ import annotations

"""
Gate 22 — Golden Negative Tests

Source lock used to author this focused gate:
    branch: feat/hospital-core-nursing-workflows
    head:   6c9fef60f8ba0302ff741a00cd200be55e56dded

Purpose:
    Mirror the critical failure boundaries of Gate 21 and prove that unsafe,
    unauthorized, stale, cross-tenant, and incomplete hospital actions fail
    closed.

This is intentionally a focused Gate 22 suite. It does NOT run module-wide or
full-backend regression; full regression remains reserved for the final gate.

Live contracts exercised:
* Failed barcode scans are durable HTTP 200 verification outcomes with explicit
  failure codes; they do NOT authorize medication administration.
* eMAR writes are nurse-only and medication administration requires an
  Idempotency-Key.
* A successful barcode verification event is bound to the exact scheduled
  administration, actor/context, and permitted administration window.
* Cross-clinic patient barcodes fail closed without linking/leaking the foreign
  patient.
* Discharge is doctor-only and requires a finalized discharge medication
  reconciliation.
* Cross-tenant admission reads resolve as not found.
"""

from datetime import datetime, timedelta, timezone

from app.core.enums.emar_enums import (
    MedicationAdministrationStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import (
    AdmissionStatus,
    BedStatus,
)
from app.modules.emar.models.emar_model import (
    MedicationAdministrationEvent,
)
from app.modules.emar.services import emar_service
from app.tests.modules.emar.test_barcode_verification_service import (
    _context,
)
from app.tests.modules.nursing.test_nursing_service_invariants import (
    _setup_admission,
)


def _with_idempotency(
    headers: dict[str, str],
    key: str,
) -> dict[str, str]:
    return {
        **headers,
        "Idempotency-Key": key,
    }


def _expect_status(
    response,
    status_code: int,
) -> dict:
    body = response.get_json()
    assert response.status_code == status_code, body
    return body


def _refresh(db, *objects) -> None:
    for obj in objects:
        db.session.refresh(obj)


def _discharge_payload() -> dict:
    return {
        "reason": "Gate 22 negative discharge attempt",
        "primary_diagnosis": "Acute illness",
        "secondary_diagnoses": [],
        "condition_at_discharge": "Stable",
        "instructions": "Follow discharge plan",
        "follow_up_plan": "Clinic review in seven days",
        "destination": "Home",
    }


def test_gate22_unknown_patient_barcode_is_durable_fail_closed(
    client,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
    auth_headers_for,
):
    (
        nurse,
        drug,
        _admission,
        _prescription,
        _item,
        administration,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    headers = auth_headers_for(
        nurse.user,
        role=Role.NURSE,
    )

    body = _expect_status(
        client.post(
            "/api/v1/emar/barcode/verify",
            json={
                "patient_barcode": "GATE22-UNKNOWN-PATIENT",
                "medication_barcode": drug.barcode,
                "scan_timestamp": scheduled_at.isoformat(),
                "scan_device": "gate22-rover-01",
            },
            headers=_with_idempotency(
                headers,
                "gate22-unknown-patient",
            ),
        ),
        200,
    )

    verification = body["verification"]

    assert verification["verification_result"] == "not_barcode_verified"
    assert verification["failure_code"] == "unknown_patient_barcode"
    assert verification["patient_id"] is None
    assert verification["medication_administration_id"] is None

    _refresh(db, administration)
    assert administration.status == MedicationAdministrationStatus.SCHEDULED
    assert administration.administered_at is None


def test_gate22_wrong_medication_scan_cannot_authorize_administration(
    client,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
    auth_headers_for,
):
    (
        nurse,
        _drug,
        _admission,
        _prescription,
        _item,
        administration,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    wrong_drug = make_drug(
        clinic,
        is_active=True,
    )
    wrong_drug.barcode = (
        f"GATE22-WRONG-MED-{wrong_drug.id}"
    )
    db.session.commit()

    headers = auth_headers_for(
        nurse.user,
        role=Role.NURSE,
    )

    verification_body = _expect_status(
        client.post(
            "/api/v1/emar/barcode/verify",
            json={
                "patient_barcode": patient.barcode,
                "medication_barcode": wrong_drug.barcode,
                "scan_timestamp": scheduled_at.isoformat(),
                "scan_device": "gate22-rover-02",
            },
            headers=_with_idempotency(
                headers,
                "gate22-wrong-medication-scan",
            ),
        ),
        200,
    )

    verification = verification_body["verification"]

    assert verification["verification_result"] == "not_barcode_verified"
    assert verification["failure_code"] == "wrong_medication"

    failed_event_id = verification["id"]
    assert failed_event_id > 0

    failed_event = db.session.get(
        MedicationAdministrationEvent,
        failed_event_id,
    )
    assert failed_event is not None

    rejected = _expect_status(
        client.post(
            f"/api/v1/emar/{administration.id}/administer",
            json={
                "barcode_verification_event_id": failed_event_id,
                "administered_at": scheduled_at.isoformat(),
            },
            headers=_with_idempotency(
                headers,
                "gate22-failed-scan-admin",
            ),
        ),
        409,
    )

    assert "successful barcode verification" in rejected["error"].lower()

    _refresh(db, administration)
    assert administration.status == MedicationAdministrationStatus.SCHEDULED
    assert administration.administered_at is None


def test_gate22_verification_event_cannot_be_reused_for_different_administration(
    client,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
    auth_headers_for,
):
    (
        nurse,
        drug,
        admission,
        _prescription,
        item,
        first_administration,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    headers = auth_headers_for(
        nurse.user,
        role=Role.NURSE,
    )

    verified = _expect_status(
        client.post(
            "/api/v1/emar/barcode/verify",
            json={
                "patient_barcode": patient.barcode,
                "medication_barcode": drug.barcode,
                "scan_timestamp": scheduled_at.isoformat(),
                "scan_device": "gate22-rover-03",
            },
            headers=_with_idempotency(
                headers,
                "gate22-bound-event",
            ),
        ),
        200,
    )["verification"]

    assert verified["verification_result"] == "barcode_verified"
    assert (
        verified["medication_administration_id"]
        == first_administration.id
    )

    second_at = scheduled_at + timedelta(hours=8)

    second_administration = (
        emar_service.schedule_medication_administration(
            clinic_id=clinic.id,
            patient_id=patient.id,
            admission_id=admission.id,
            prescription_item_id=item.id,
            scheduled_at=second_at,
            actor_staff_id=nurse.id,
        )
    )

    rejected = _expect_status(
        client.post(
            f"/api/v1/emar/{second_administration.id}/administer",
            json={
                "barcode_verification_event_id": verified["id"],
                "administered_at": second_at.isoformat(),
            },
            headers=_with_idempotency(
                headers,
                "gate22-reused-event-other-admin",
            ),
        ),
        409,
    )

    assert "does not match" in rejected["error"].lower()

    _refresh(
        db,
        first_administration,
        second_administration,
    )

    assert (
        first_administration.status
        == MedicationAdministrationStatus.SCHEDULED
    )
    assert (
        second_administration.status
        == MedicationAdministrationStatus.SCHEDULED
    )


def test_gate22_verified_event_expires_outside_administration_window(
    client,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
    auth_headers_for,
):
    (
        nurse,
        drug,
        _admission,
        _prescription,
        _item,
        administration,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    headers = auth_headers_for(
        nurse.user,
        role=Role.NURSE,
    )

    verified = _expect_status(
        client.post(
            "/api/v1/emar/barcode/verify",
            json={
                "patient_barcode": patient.barcode,
                "medication_barcode": drug.barcode,
                "scan_timestamp": scheduled_at.isoformat(),
                "scan_device": "gate22-rover-04",
            },
            headers=_with_idempotency(
                headers,
                "gate22-window-verification",
            ),
        ),
        200,
    )["verification"]

    assert verified["verification_result"] == "barcode_verified"

    rejected = _expect_status(
        client.post(
            f"/api/v1/emar/{administration.id}/administer",
            json={
                "barcode_verification_event_id": verified["id"],
                "administered_at": (
                    scheduled_at + timedelta(hours=2)
                ).isoformat(),
            },
            headers=_with_idempotency(
                headers,
                "gate22-expired-window-admin",
            ),
        ),
        409,
    )

    assert "permitted administration window" in rejected["error"].lower()

    _refresh(db, administration)
    assert administration.status == MedicationAdministrationStatus.SCHEDULED
    assert administration.administered_at is None


def test_gate22_emar_write_rbac_and_idempotency_header_fail_closed(
    client,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
    auth_headers_for,
):
    (
        nurse,
        drug,
        _admission,
        _prescription,
        _item,
        administration,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    _doctor, doctor_headers = make_authenticated_staff(
        clinic,
        Role.DOCTOR,
    )

    doctor_denied = _expect_status(
        client.post(
            "/api/v1/emar/barcode/verify",
            json={
                "patient_barcode": patient.barcode,
                "medication_barcode": drug.barcode,
                "scan_timestamp": scheduled_at.isoformat(),
                "scan_device": "gate22-rover-05",
            },
            headers=_with_idempotency(
                doctor_headers,
                "gate22-doctor-emar-write",
            ),
        ),
        403,
    )
    assert "permission" in doctor_denied["error"].lower()

    nurse_headers = auth_headers_for(
        nurse.user,
        role=Role.NURSE,
    )

    missing_idempotency = _expect_status(
        client.post(
            "/api/v1/emar/barcode/verify",
            json={
                "patient_barcode": patient.barcode,
                "medication_barcode": drug.barcode,
                "scan_timestamp": scheduled_at.isoformat(),
                "scan_device": "gate22-rover-06",
            },
            headers=nurse_headers,
        ),
        422,
    )

    assert "idempotency-key" in missing_idempotency["error"].lower()

    _refresh(db, administration)
    assert administration.status == MedicationAdministrationStatus.SCHEDULED
    assert administration.administered_at is None


def test_gate22_cross_tenant_patient_barcode_fails_without_identity_leak(
    client,
    db,
    clinic,
    patient,
    make_clinic,
    make_patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
    auth_headers_for,
):
    (
        nurse,
        drug,
        _admission,
        _prescription,
        _item,
        administration,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    foreign_clinic = make_clinic(
        name="Gate 22 Foreign Barcode Clinic",
    )
    foreign_patient = make_patient(
        foreign_clinic,
        first_name="Foreign",
        last_name="BarcodePatient",
        patient_number="G22-FOREIGN-PATIENT",
    )
    foreign_patient.barcode = "G22-CROSS-TENANT-BARCODE"
    db.session.commit()

    headers = auth_headers_for(
        nurse.user,
        role=Role.NURSE,
    )

    body = _expect_status(
        client.post(
            "/api/v1/emar/barcode/verify",
            json={
                "patient_barcode": foreign_patient.barcode,
                "medication_barcode": drug.barcode,
                "scan_timestamp": scheduled_at.isoformat(),
                "scan_device": "gate22-rover-07",
            },
            headers=_with_idempotency(
                headers,
                "gate22-cross-tenant-barcode",
            ),
        ),
        200,
    )

    verification = body["verification"]

    assert verification["verification_result"] == "not_barcode_verified"
    assert verification["failure_code"] == "cross_clinic_patient_barcode"
    assert verification["patient_id"] is None
    assert verification["scanned_patient_id"] is None

    response_text = str(body)
    assert foreign_patient.patient_number not in response_text
    assert "BarcodePatient" not in response_text

    _refresh(db, administration)
    assert administration.status == MedicationAdministrationStatus.SCHEDULED


def test_gate22_discharge_requires_reconciliation_and_doctor_role(
    client,
    db,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    (
        _patient,
        nurse,
        doctor,
        _ward,
        bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    doctor_headers = auth_headers_for(
        doctor.user,
        role=Role.DOCTOR,
    )
    nurse_headers = auth_headers_for(
        nurse.user,
        role=Role.NURSE,
    )

    no_reconciliation = _expect_status(
        client.post(
            f"/api/v1/wards/admissions/{admission.id}/discharge",
            json=_discharge_payload(),
            headers=_with_idempotency(
                doctor_headers,
                "gate22-discharge-no-reconciliation",
            ),
        ),
        404,
    )

    assert "medication reconciliation not found" in (
        no_reconciliation["error"].lower()
    )

    _refresh(db, admission, bed)
    assert admission.status == AdmissionStatus.ADMITTED
    assert bed.status == BedStatus.OCCUPIED
    assert admission.discharged_at is None

    nurse_denied = _expect_status(
        client.post(
            f"/api/v1/wards/admissions/{admission.id}/discharge",
            json=_discharge_payload(),
            headers=_with_idempotency(
                nurse_headers,
                "gate22-nurse-discharge",
            ),
        ),
        403,
    )

    assert "permission" in nurse_denied["error"].lower()

    _refresh(db, admission, bed)
    assert admission.status == AdmissionStatus.ADMITTED
    assert bed.status == BedStatus.OCCUPIED
    assert admission.discharged_at is None


def test_gate22_draft_discharge_reconciliation_blocks_discharge(
    client,
    db,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    (
        _patient,
        _nurse,
        doctor,
        _ward,
        bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    doctor_headers = auth_headers_for(
        doctor.user,
        role=Role.DOCTOR,
    )

    draft = _expect_status(
        client.post(
            "/api/v1/medication-reconciliations",
            json={
                "admission_id": admission.id,
                "event_type": "discharge",
                "no_medications_reported": True,
                "notes": "Gate 22 intentionally left draft",
                "items": [],
            },
            headers=doctor_headers,
        ),
        201,
    )

    assert draft["reconciliation"]["record_status"] == "draft"

    blocked = _expect_status(
        client.post(
            f"/api/v1/wards/admissions/{admission.id}/discharge",
            json=_discharge_payload(),
            headers=_with_idempotency(
                doctor_headers,
                "gate22-draft-reconciliation-discharge",
            ),
        ),
        409,
    )

    assert "not finalized" in blocked["error"].lower()

    _refresh(db, admission, bed)
    assert admission.status == AdmissionStatus.ADMITTED
    assert bed.status == BedStatus.OCCUPIED
    assert admission.discharged_at is None


def test_gate22_cross_tenant_admission_read_is_not_found_and_does_not_leak(
    client,
    db,
    clinic,
    make_clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    (
        patient,
        _nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    foreign_clinic = make_clinic(
        name="Gate 22 Foreign Admission Clinic",
    )
    foreign_doctor = make_staff(
        clinic=foreign_clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "gate22-foreign-doctor@test.com",
        },
    )

    foreign_headers = auth_headers_for(
        foreign_doctor.user,
        role=Role.DOCTOR,
    )

    response = client.get(
        f"/api/v1/wards/admissions/{admission.id}",
        headers=foreign_headers,
    )
    body = _expect_status(
        response,
        404,
    )

    response_text = str(body)

    assert patient.patient_number not in response_text
    assert patient.first_name not in response_text
    assert patient.last_name not in response_text

    _refresh(db, admission)
    assert admission.status == AdmissionStatus.ADMITTED
