from __future__ import annotations

"""
Gate 21 — Golden Hospital Workflow E2E

Source lock used to author this test:
    branch: feat/hospital-core-nursing-workflows
    head:   cae5862907a76b0599c382e3e6109a34d27df4ab

This is intentionally a focused Gate 21 acceptance test. It does NOT run the
full backend regression; that remains reserved for the final gate.

Live-contract mappings where the roadmap names a capability more broadly than
the current bounded backend API:

* "Check patient in" -> appointment confirmation. This source lock has no
  separate check-in endpoint.
* "Nursing assessment" -> a signed NursingNote with note_type="assessment".
  The bounded nursing API models assessment as a clinical nursing note type.
* "Create + sign/finalize discharge summary" -> the discharge command performs
  these atomically and returns the FINAL DischargeSummary.
* Patient/drug barcode values are test-fixture setup fields because the current
  registration/catalog APIs do not expose barcode assignment; the actual
  barcode verification and administration flow is exercised through real HTTP.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.core.audit.models.audit_model import AuditLog
from app.core.clinical_safety.services.clinical_safety_context_service import (
    build_clinical_safety_context,
)
from app.core.enums.role_enums import Role
from app.modules.billing.models.billing_model import Invoice
from app.modules.emar.models.emar_model import (
    MedicationAdministration,
    MedicationAdministrationEvent,
)
from app.modules.hie.providers import registry
from app.modules.hie.providers.fhir_contract import validate_fhir_resource
from app.modules.patient.models.patient_model import Patient
from app.modules.pharmacy.models.pharmacy_model import Drug, DrugBatch
from app.modules.ward.models.ward_model import Admission, Bed


def _headers(
    login: dict,
    *,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    headers = {
        "Authorization": (
            f"Bearer {login['access_token']}"
        ),
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _expect(response, status_code: int) -> dict:
    body = response.get_json()
    assert response.status_code == status_code, body
    return body


def _enum_value(value):
    return getattr(value, "value", value)


class Gate21FHIRProvider:
    """Deterministic HIE provider used through the real HIE service/route."""

    def __init__(self):
        self.calls: list[dict] = []

    def query_clinical_data(
        self,
        patient_identifier,
        filters=None,
    ):
        self.calls.append(
            {
                "patient_identifier": patient_identifier,
                "filters": filters,
            }
        )
        return {
            "status_code": 200,
            "external_reference": "GATE21-FHIR-PATIENT",
            "records": [
                {
                    "resourceType": "Patient",
                    "id": str(patient_identifier),
                }
            ],
        }


def test_gate21_golden_hospital_workflow(
    client,
    db,
    clinic,
    make_clinic,
    make_staff,
    e2e_login,
):
    # ------------------------------------------------------------------
    # SETUP — clinic exists from fixture; create the required hospital roles.
    # ------------------------------------------------------------------
    admin_staff = make_staff(
        clinic=clinic,
        role=Role.ADMIN,
        user_overrides={
            "email": "gate21-admin@test.com",
        },
    )
    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "gate21-doctor@test.com",
        },
    )
    nurse_staff = make_staff(
        clinic=clinic,
        role=Role.NURSE,
        user_overrides={
            "email": "gate21-nurse@test.com",
        },
    )
    pharmacist_staff = make_staff(
        clinic=clinic,
        role=Role.PHARMACIST,
        user_overrides={
            "email": "gate21-pharmacist@test.com",
        },
    )
    lab_staff = make_staff(
        clinic=clinic,
        role=Role.LAB_TECHNICIAN,
        user_overrides={
            "email": "gate21-lab@test.com",
        },
    )
    receptionist_staff = make_staff(
        clinic=clinic,
        role=Role.RECEPTIONIST,
        user_overrides={
            "email": "gate21-reception@test.com",
        },
    )

    admin_login = e2e_login("gate21-admin@test.com")
    doctor_login = e2e_login("gate21-doctor@test.com")
    nurse_login = e2e_login("gate21-nurse@test.com")
    pharmacist_login = e2e_login("gate21-pharmacist@test.com")
    lab_login = e2e_login("gate21-lab@test.com")
    receptionist_login = e2e_login("gate21-reception@test.com")

    assert admin_login["user_id"] == admin_staff.user.id
    assert doctor_login["user_id"] == doctor_staff.user.id
    assert nurse_login["user_id"] == nurse_staff.user.id
    assert pharmacist_login["user_id"] == pharmacist_staff.user.id
    assert lab_login["user_id"] == lab_staff.user.id
    assert receptionist_login["user_id"] == receptionist_staff.user.id

    # ------------------------------------------------------------------
    # 1. Register patient.
    # ------------------------------------------------------------------
    patient_body = _expect(
        client.post(
            "/api/v1/patients",
            json={
                "first_name": "Gate21",
                "last_name": "GoldenPatient",
            },
            headers=_headers(receptionist_login),
        ),
        201,
    )
    patient_id = patient_body["data"]["id"]

    patient = db.session.get(Patient, patient_id)
    assert patient is not None
    assert patient.clinic_id == clinic.id

    # Barcode and DOB are fixture setup values for domains whose current
    # public create APIs do not expose these fields.
    patient.barcode = f"G21-PAT-{patient.id}"
    patient.date_of_birth = date(1990, 1, 15)
    db.session.flush()

    # ------------------------------------------------------------------
    # 2. Record structured allergy.
    # ------------------------------------------------------------------
    allergy_body = _expect(
        client.post(
            f"/api/v1/patients/{patient_id}/allergies",
            json={
                "allergen_name": "Peanut",
                "allergen_code": "PEANUT",
                "allergen_type": "food",
                "reaction": "Urticaria",
                "severity": "moderate",
                "notes": "Gate 21 structured allergy",
            },
            headers=_headers(nurse_login),
        ),
        201,
    )
    assert allergy_body["data"]["patient_id"] == patient_id
    allergy_id = allergy_body["data"]["id"]

    # ------------------------------------------------------------------
    # 3. Create appointment.
    # ------------------------------------------------------------------
    scheduled_start = (
        datetime.now(timezone.utc)
        + timedelta(hours=1)
    )
    scheduled_end = scheduled_start + timedelta(minutes=30)

    appointment_body = _expect(
        client.post(
            "/api/v1/appointments/",
            json={
                "patient_id": patient_id,
                "staff_id": doctor_staff.id,
                "scheduled_start": scheduled_start.isoformat(),
                "scheduled_end": scheduled_end.isoformat(),
                "appointment_type": "in_person",
                "reason": "Gate 21 golden hospital workflow",
            },
            headers=_headers(
                doctor_login,
                idempotency_key="gate21-appointment",
            ),
        ),
        201,
    )
    appointment_id = appointment_body["data"]["id"]

    # ------------------------------------------------------------------
    # 4. Check patient in.
    # Current source lock has no dedicated check-in endpoint; appointment
    # confirmation is the real transition immediately preceding consultation.
    # ------------------------------------------------------------------
    confirmed = _expect(
        client.post(
            f"/api/v1/appointments/{appointment_id}/confirm",
            headers=_headers(receptionist_login),
        ),
        200,
    )
    assert confirmed["data"]["status"] == "confirmed"

    # ------------------------------------------------------------------
    # 5. Record initial vitals.
    # ------------------------------------------------------------------
    _expect(
        client.post(
            f"/api/v1/patients/{patient_id}/vitals",
            json={
                "temperature": "37.1",
                "blood_pressure_systolic": 118,
                "blood_pressure_diastolic": 76,
                "heart_rate": 82,
                "respiratory_rate": 18,
                "oxygen_saturation": "98.0",
                "weight": "70.0",
                "height": "175.0",
            },
            headers=_headers(nurse_login),
        ),
        201,
    )

    # ------------------------------------------------------------------
    # 6–8. Start consultation; record chief complaint and symptoms.
    # ------------------------------------------------------------------
    consultation_body = _expect(
        client.post(
            "/api/v1/consultations/",
            json={
                "patient_id": patient_id,
                "staff_id": doctor_staff.id,
                "appointment_id": appointment_id,
                "consultation_type": "general",
                "chief_complaint": "Fever and malaise",
                "symptoms": "Fever, fatigue, reduced appetite",
            },
            headers=_headers(
                doctor_login,
                idempotency_key="gate21-consultation",
            ),
        ),
        201,
    )
    consultation_id = consultation_body["data"]["id"]
    assert consultation_body["data"]["status"] == "in_progress"

    # ------------------------------------------------------------------
    # 9. Record diagnosis.
    # ------------------------------------------------------------------
    diagnosis_body = _expect(
        client.patch(
            f"/api/v1/consultations/{consultation_id}",
            json={
                "icd10_code": "R50.9",
                "diagnosis": "Acute febrile illness",
                "treatment_plan": (
                    "Laboratory evaluation, oral medication, "
                    "inpatient observation"
                ),
                "notes": "Gate 21 diagnostic documentation",
            },
            headers=_headers(doctor_login),
        ),
        200,
    )
    assert diagnosis_body["data"]["diagnosis"] == (
        "Acute febrile illness"
    )

    # ------------------------------------------------------------------
    # 10–13. Lab order -> collection -> processing -> result -> verify.
    # ------------------------------------------------------------------
    lab_test_body = _expect(
        client.post(
            "/api/v1/lab/tests",
            json={
                "name": "Gate 21 Complete Blood Count",
                "loinc_code": "57021-8",
                "code": "G21-CBC",
                "sample_type": "blood",
                "reference_range": "4.0-11.0",
                "unit": "10^9/L",
                "price": "25.00",
                "critical_low": "2.0",
                "critical_high": "20.0",
                "is_active": True,
            },
            headers=_headers(admin_login),
        ),
        201,
    )
    lab_test_id = lab_test_body["data"]["id"]

    lab_order_body = _expect(
        client.post(
            "/api/v1/lab/orders",
            json={
                "patient_id": patient_id,
                "test_ids": [lab_test_id],
                "consultation_id": consultation_id,
            },
            headers=_headers(doctor_login),
        ),
        201,
    )
    lab_order_id = lab_order_body["data"]["id"]
    lab_order_item_id = lab_order_body["data"]["items"][0]["id"]
    lab_qr_code = lab_order_body["data"]["qr_code"]

    collected = _expect(
        client.post(
            f"/api/v1/lab/orders/{lab_order_id}/collect-sample",
            json={"scanned_qr_code": lab_qr_code},
            headers=_headers(lab_login),
        ),
        200,
    )
    assert collected["data"]["status"] == "sample_collected"

    processing = _expect(
        client.post(
            f"/api/v1/lab/orders/{lab_order_id}/process",
            json={},
            headers=_headers(lab_login),
        ),
        200,
    )
    assert processing["data"]["status"] == "in_progress"

    _expect(
        client.post(
            f"/api/v1/lab/order-items/{lab_order_item_id}/result",
            json={
                "result_value": "7.2",
                "flag": "normal",
                "result_notes": "Within reference range",
            },
            headers=_headers(lab_login),
        ),
        200,
    )

    verified = _expect(
        client.post(
            f"/api/v1/lab/orders/{lab_order_id}/verify",
            json={},
            headers=_headers(lab_login),
        ),
        200,
    )
    assert verified["data"]["verified_by_id"] == lab_staff.id

    completed_lab = _expect(
        client.post(
            f"/api/v1/lab/orders/{lab_order_id}/complete",
            json={},
            headers=_headers(lab_login),
        ),
        200,
    )
    assert completed_lab["data"]["status"] == "completed"

    # ------------------------------------------------------------------
    # Pharmacy catalog setup for steps 14–19.
    # ------------------------------------------------------------------
    drug_body = _expect(
        client.post(
            "/api/v1/pharmacy/drugs",
            json={
                "name": "Gate 21 Amoxicillin",
                "generic_name": "Amoxicillin",
                "dosage_form": "capsule",
                "strength": "500 mg",
                "unit_price": "5.00",
                "is_controlled": False,
            },
            headers=_headers(pharmacist_login),
        ),
        201,
    )
    drug_id = drug_body["data"]["id"]

    drug = db.session.get(Drug, drug_id)
    assert drug is not None
    drug.barcode = f"G21-MED-{drug.id}"
    db.session.flush()

    batch_body = _expect(
        client.post(
            "/api/v1/pharmacy/batches",
            json={
                "drug_id": drug_id,
                "batch_number": "G21-BATCH-001",
                "quantity_on_hand": 100,
                "expiry_date": (
                    date.today() + timedelta(days=180)
                ).isoformat(),
                "reorder_level": 10,
            },
            headers=_headers(pharmacist_login),
        ),
        201,
    )
    batch_id = batch_body["data"]["id"]

    medication_start = datetime.now(timezone.utc) - timedelta(minutes=5)
    medication_end = medication_start + timedelta(days=5)
    administration_time = datetime.now(timezone.utc)
    scheduled_clock = administration_time.strftime("%H:%M")

    proposed_medication = {
        "drug_id": drug_id,
        "dose_value": 500,
        "dose_unit": "mg",
        "route_code": "oral",
        "route_display": "Oral",
        "frequency_code": "Q24H",
        "frequency_interval": 24,
        "frequency_interval_unit": "hour",
        "scheduled_times": [scheduled_clock],
        "start_at": medication_start,
        "end_at": medication_end,
        "is_prn": False,
        "max_single_dose": 500,
        "max_daily_dose": 1000,
        "order_priority": "routine",
        "order_type": "inpatient",
    }

    # ------------------------------------------------------------------
    # 15–16. Build authoritative clinical-safety context.
    # ------------------------------------------------------------------
    safety_context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient_id,
        proposed_medication=proposed_medication,
    ).to_dict()

    assert safety_context["clinic_id"] == clinic.id
    assert safety_context["patient_id"] == patient_id
    assert safety_context["patient_active"] is True
    assert any(
        item["id"] == allergy_id
        for item in safety_context["allergies"]
    )
    assert "medication_ids" in safety_context
    assert "medications" in safety_context
    assert (
        safety_context["proposed_medication"]["drug_id"]
        == drug_id
    )
    assert (
        safety_context["proposed_medication"]["dose_value"]
        == 500
    )
    assert (
        safety_context["proposed_medication"]["route_code"]
        == "oral"
    )
    assert safety_context["age_years"] is not None
    assert safety_context["weight_kg"] == 70.0
    assert safety_context["labs"]

    # ------------------------------------------------------------------
    # 14 + 17. Create medication order through real prescription/CDS path.
    # A 201 proves the authoritative CDS path permitted persistence.
    # ------------------------------------------------------------------
    prescription_body = _expect(
        client.post(
            "/api/v1/prescriptions",
            json={
                "patient_id": patient_id,
                "consultation_id": consultation_id,
                "items": [
                    {
                        "drug_id": drug_id,
                        "dosage": "500 mg",
                        "frequency": "once daily",
                        "duration": "5 days",
                        "quantity": 10,
                        "instructions": "Take as directed",
                        "dose_value": 500,
                        "dose_unit": "mg",
                        "route_code": "oral",
                        "route_display": "Oral",
                        "frequency_code": "Q24H",
                        "frequency_interval": 24,
                        "frequency_interval_unit": "hour",
                        "scheduled_times": [scheduled_clock],
                        "start_at": medication_start.isoformat(),
                        "end_at": medication_end.isoformat(),
                        "is_prn": False,
                        "max_single_dose": 500,
                        "max_daily_dose": 1000,
                        "administration_instructions": (
                            "Administer orally with water"
                        ),
                        "order_priority": "routine",
                        "order_type": "inpatient",
                    }
                ],
                "notes": "Gate 21 CDS-backed inpatient medication order",
            },
            headers=_headers(
                doctor_login,
                idempotency_key="gate21-medication-order",
            ),
        ),
        201,
    )
    assert prescription_body["success"] is True
    assert prescription_body["data"]["status"] == "active"
    prescription_id = prescription_body["data"]["id"]
    prescription_item_id = (
        prescription_body["data"]["items"][0]["id"]
    )
    assert (
        prescription_body["data"]["items"][0]["order_type"]
        == "inpatient"
    )

    # ------------------------------------------------------------------
    # 18. Pharmacy receives medication order.
    # ------------------------------------------------------------------
    pharmacy_received = _expect(
        client.get(
            f"/api/v1/prescriptions/{prescription_id}",
            headers=_headers(pharmacist_login),
        ),
        200,
    )
    assert pharmacy_received["data"]["id"] == prescription_id
    assert pharmacy_received["data"]["patient_id"] == patient_id

    # ------------------------------------------------------------------
    # 19. Pharmacy dispenses.
    # ------------------------------------------------------------------
    dispensed = _expect(
        client.post(
            "/api/v1/pharmacy/dispense",
            json={
                "prescription_id": prescription_id,
                "items": [
                    {
                        "prescription_item_id": prescription_item_id,
                        "batch_id": batch_id,
                        "quantity": 10,
                    }
                ],
                "notes": "Gate 21 inpatient dispense",
            },
            headers=_headers(pharmacist_login),
        ),
        201,
    )
    assert dispensed["data"]["status"] == "dispensed"

    # ------------------------------------------------------------------
    # 20–21. Admit patient to ward and assign bed.
    # ------------------------------------------------------------------
    ward_body = _expect(
        client.post(
            "/api/v1/wards",
            json={
                "name": "Gate 21 General Ward",
                "ward_type": "general",
                "capacity": 2,
            },
            headers=_headers(admin_login),
        ),
        201,
    )
    ward_id = ward_body["ward"]["id"]

    bed_one_body = _expect(
        client.post(
            f"/api/v1/wards/{ward_id}/beds",
            json={"bed_number": "G21-BED-01"},
            headers=_headers(admin_login),
        ),
        201,
    )
    bed_one_id = bed_one_body["bed"]["id"]

    bed_two_body = _expect(
        client.post(
            f"/api/v1/wards/{ward_id}/beds",
            json={"bed_number": "G21-BED-02"},
            headers=_headers(admin_login),
        ),
        201,
    )
    bed_two_id = bed_two_body["bed"]["id"]

    reservation_body = _expect(
        client.post(
            "/api/v1/wards/reservations",
            json={
                "patient_id": patient_id,
                "bed_id": bed_one_id,
                "reason": "Gate 21 inpatient observation",
            },
            headers=_headers(nurse_login),
        ),
        201,
    )
    reservation_id = reservation_body["reservation"]["id"]

    admitted_body = _expect(
        client.post(
            f"/api/v1/wards/reservations/{reservation_id}/admit",
            json={
                "reason": "Gate 21 admission",
            },
            headers=_headers(nurse_login),
        ),
        201,
    )
    admission_id = admitted_body["admission"]["id"]

    admission = db.session.get(Admission, admission_id)
    assert admission is not None
    assert admission.patient_id == patient_id
    assert admission.bed_id == bed_one_id
    assert _enum_value(admission.status) == "admitted"

    # ------------------------------------------------------------------
    # 22. Admission medication reconciliation.
    # ------------------------------------------------------------------
    admission_recon_body = _expect(
        client.post(
            "/api/v1/medication-reconciliations",
            json={
                "admission_id": admission_id,
                "event_type": "admission",
                "no_medications_reported": False,
                "notes": "Gate 21 admission reconciliation",
                "items": [
                    {
                        "source_prescription_item_id": (
                            prescription_item_id
                        ),
                        "medication_name": "Amoxicillin 500 mg",
                        "decision": "continue",
                        "dose_text": "500 mg",
                        "route_text": "oral",
                        "frequency_text": "once daily",
                        "instructions": "Continue inpatient order",
                        "rationale": "Active treatment plan",
                    }
                ],
            },
            headers=_headers(doctor_login),
        ),
        201,
    )
    admission_recon_id = (
        admission_recon_body["reconciliation"]["id"]
    )
    assert (
        admission_recon_body["reconciliation"]["record_status"]
        == "draft"
    )

    admission_recon_final = _expect(
        client.post(
            (
                "/api/v1/medication-reconciliations/"
                f"{admission_recon_id}/finalize"
            ),
            json={"expected_version": 1},
            headers=_headers(
                doctor_login,
                idempotency_key="gate21-admission-reconciliation",
            ),
        ),
        200,
    )
    assert (
        admission_recon_final["reconciliation"]["record_status"]
        == "final"
    )

    # ------------------------------------------------------------------
    # 23. Nursing assessment.
    # The bounded nursing API represents assessment as a typed nursing note.
    # ------------------------------------------------------------------
    assessment_body = _expect(
        client.post(
            f"/api/v1/nursing/admissions/{admission_id}/notes",
            json={
                "note_type": "assessment",
                "note_text": (
                    "Alert, oriented, ambulatory with mild fatigue; "
                    "baseline inpatient nursing assessment completed."
                ),
                "observed_at": (
                    datetime.now(timezone.utc).isoformat()
                ),
            },
            headers=_headers(nurse_login),
        ),
        201,
    )
    assessment_note_id = assessment_body["note"]["id"]

    signed_assessment = _expect(
        client.post(
            f"/api/v1/nursing/notes/{assessment_note_id}/sign",
            headers=_headers(
                nurse_login,
                idempotency_key="gate21-nursing-assessment-sign",
            ),
        ),
        200,
    )
    assert signed_assessment["note"]["status"] == "signed"
    assert signed_assessment["note"]["record_status"] == "final"

    # ------------------------------------------------------------------
    # 24. Create nursing care plan.
    # ------------------------------------------------------------------
    care_plan_body = _expect(
        client.post(
            f"/api/v1/nursing/admissions/{admission_id}/care-plans",
            json={
                "title": "Gate 21 fever monitoring",
                "problem": "Acute febrile illness with fatigue",
                "goal": (
                    "Maintain stable vital signs and tolerate oral therapy"
                ),
                "target_at": (
                    datetime.now(timezone.utc)
                    + timedelta(hours=24)
                ).isoformat(),
            },
            headers=_headers(nurse_login),
        ),
        201,
    )
    care_plan_id = care_plan_body["care_plan"]["id"]
    assert care_plan_body["care_plan"]["status"] == "active"

    # ------------------------------------------------------------------
    # 25. Generate scheduled medication administration.
    # ------------------------------------------------------------------
    schedule_body = _expect(
        client.post(
            "/api/v1/emar/scheduled",
            json={
                "patient_id": patient_id,
                "admission_id": admission_id,
                "prescription_item_id": prescription_item_id,
                "scheduled_at": administration_time.isoformat(),
            },
            headers=_headers(nurse_login),
        ),
        201,
    )
    administration_id = schedule_body["administration"]["id"]
    assert schedule_body["administration"]["status"] == "scheduled"

    # ------------------------------------------------------------------
    # Barcode verification immediately precedes administration.
    # ------------------------------------------------------------------
    barcode_body = _expect(
        client.post(
            "/api/v1/emar/barcode/verify",
            json={
                "patient_barcode": patient.barcode,
                "medication_barcode": drug.barcode,
                "scan_timestamp": administration_time.isoformat(),
                "scan_device": "gate21-rover-01",
            },
            headers=_headers(
                nurse_login,
                idempotency_key="gate21-barcode-verification",
            ),
        ),
        200,
    )
    verification = barcode_body["verification"]
    assert verification["verification_result"] == "barcode_verified"
    assert verification["patient_match"] is True
    assert verification["medication_match"] is True
    assert verification["dose_match"] is True
    assert verification["route_match"] is True
    assert verification["time_window_match"] is True
    assert (
        verification["medication_administration_id"]
        == administration_id
    )
    verification_event_id = verification["id"]

    # ------------------------------------------------------------------
    # 26. Nurse administers medication.
    # ------------------------------------------------------------------
    administered_body = _expect(
        client.post(
            f"/api/v1/emar/{administration_id}/administer",
            json={
                "barcode_verification_event_id": verification_event_id,
                "administered_at": administration_time.isoformat(),
                "notes": "Gate 21 barcode-verified administration",
            },
            headers=_headers(
                nurse_login,
                idempotency_key="gate21-administer-dose",
            ),
        ),
        200,
    )
    administration = administered_body["administration"]
    assert administration["status"] == "administered"
    assert (
        administration["barcode_verification_event_id"]
        == verification_event_id
    )
    assert administration["patient_barcode"] == patient.barcode
    assert administration["medication_barcode"] == drug.barcode
    assert administration["scan_device"] == "gate21-rover-01"
    assert administration["verification_result"] == "barcode_verified"

    # ------------------------------------------------------------------
    # 27. eMAR records exactly one administration.
    # Replay same command with same idempotency identity and prove one row.
    # ------------------------------------------------------------------
    replay_body = _expect(
        client.post(
            f"/api/v1/emar/{administration_id}/administer",
            json={
                "barcode_verification_event_id": verification_event_id,
                "administered_at": administration_time.isoformat(),
                "notes": "Gate 21 barcode-verified administration",
            },
            headers=_headers(
                nurse_login,
                idempotency_key="gate21-administer-dose",
            ),
        ),
        200,
    )
    assert replay_body["administration"]["id"] == administration_id
    assert replay_body["administration"]["status"] == "administered"

    persisted_administrations = (
        db.session.execute(
            db.select(MedicationAdministration)
            .where(
                MedicationAdministration.clinic_id == clinic.id,
                MedicationAdministration.patient_id == patient_id,
                MedicationAdministration.admission_id == admission_id,
                MedicationAdministration.prescription_item_id
                == prescription_item_id,
            )
        )
        .scalars()
        .all()
    )
    assert len(persisted_administrations) == 1
    persisted_administration = persisted_administrations[0]
    assert persisted_administration.id == administration_id
    assert _enum_value(persisted_administration.status) == "administered"
    assert (
        persisted_administration.barcode_verification_event_id
        == verification_event_id
    )

    persisted_event = db.session.get(
        MedicationAdministrationEvent,
        verification_event_id,
    )
    assert persisted_event is not None
    assert (
        persisted_event.medication_administration_id
        == administration_id
    )
    assert (
        _enum_value(persisted_event.verification_result)
        == "barcode_verified"
    )

    # ------------------------------------------------------------------
    # 28. Record subsequent vitals.
    # ------------------------------------------------------------------
    _expect(
        client.post(
            f"/api/v1/patients/{patient_id}/vitals",
            json={
                "temperature": "36.8",
                "blood_pressure_systolic": 116,
                "blood_pressure_diastolic": 74,
                "heart_rate": 76,
                "respiratory_rate": 17,
                "oxygen_saturation": "99.0",
                "weight": "70.0",
                "height": "175.0",
                "consultation_id": consultation_id,
            },
            headers=_headers(nurse_login),
        ),
        201,
    )

    # ------------------------------------------------------------------
    # 29. Record and finalize nursing progress note.
    # ------------------------------------------------------------------
    progress_body = _expect(
        client.post(
            f"/api/v1/nursing/admissions/{admission_id}/notes",
            json={
                "note_type": "progress",
                "note_text": (
                    "Medication tolerated; temperature improved; "
                    "continue observation and oral hydration."
                ),
                "observed_at": (
                    datetime.now(timezone.utc).isoformat()
                ),
            },
            headers=_headers(nurse_login),
        ),
        201,
    )
    progress_note_id = progress_body["note"]["id"]

    signed_progress = _expect(
        client.post(
            f"/api/v1/nursing/notes/{progress_note_id}/sign",
            headers=_headers(
                nurse_login,
                idempotency_key="gate21-progress-note-sign",
            ),
        ),
        200,
    )
    assert signed_progress["note"]["status"] == "signed"
    assert signed_progress["note"]["record_status"] == "final"

    # ------------------------------------------------------------------
    # 30. Transfer patient to another bed.
    # ------------------------------------------------------------------
    transfer_body = _expect(
        client.post(
            f"/api/v1/wards/admissions/{admission_id}/transfer",
            json={
                "to_bed_id": bed_two_id,
                "reason": "Gate 21 continuing inpatient care",
            },
            headers=_headers(nurse_login),
        ),
        201,
    )
    transfer_id = transfer_body["transfer"]["id"]
    assert transfer_body["transfer"]["from_bed_id"] == bed_one_id
    assert transfer_body["transfer"]["to_bed_id"] == bed_two_id

    admission = db.session.get(Admission, admission_id)
    db.session.refresh(admission)
    assert admission.bed_id == bed_two_id

    old_bed = db.session.get(Bed, bed_one_id)
    new_bed = db.session.get(Bed, bed_two_id)
    db.session.refresh(old_bed)
    db.session.refresh(new_bed)
    assert _enum_value(old_bed.status) == "available"
    assert _enum_value(new_bed.status) == "occupied"

    # ------------------------------------------------------------------
    # 31. Continue care plan after transfer.
    # ------------------------------------------------------------------
    continued_plan = _expect(
        client.get(
            f"/api/v1/nursing/care-plans/{care_plan_id}",
            headers=_headers(nurse_login),
        ),
        200,
    )
    assert continued_plan["admission_id"] == admission_id
    assert continued_plan["status"] == "active"

    # ------------------------------------------------------------------
    # 32. Perform discharge medication reconciliation.
    # ------------------------------------------------------------------
    discharge_recon_body = _expect(
        client.post(
            "/api/v1/medication-reconciliations",
            json={
                "admission_id": admission_id,
                "event_type": "discharge",
                "no_medications_reported": False,
                "notes": "Gate 21 discharge reconciliation",
                "items": [
                    {
                        "source_prescription_item_id": (
                            prescription_item_id
                        ),
                        "medication_name": "Amoxicillin 500 mg",
                        "decision": "continue",
                        "dose_text": "500 mg",
                        "route_text": "oral",
                        "frequency_text": "once daily",
                        "instructions": "Complete prescribed course",
                        "rationale": "Continue treatment after discharge",
                    }
                ],
            },
            headers=_headers(doctor_login),
        ),
        201,
    )
    discharge_recon_id = (
        discharge_recon_body["reconciliation"]["id"]
    )

    discharge_recon_final = _expect(
        client.post(
            (
                "/api/v1/medication-reconciliations/"
                f"{discharge_recon_id}/finalize"
            ),
            json={"expected_version": 1},
            headers=_headers(
                doctor_login,
                idempotency_key="gate21-discharge-reconciliation",
            ),
        ),
        200,
    )
    assert (
        discharge_recon_final["reconciliation"]["record_status"]
        == "final"
    )

    # ------------------------------------------------------------------
    # 33–35. Create discharge summary, sign/finalize documentation,
    # and discharge patient. Live discharge performs these atomically.
    # ------------------------------------------------------------------
    discharge_body = _expect(
        client.post(
            f"/api/v1/wards/admissions/{admission_id}/discharge",
            json={
                "reason": "Clinical discharge complete",
                "primary_diagnosis": "Acute febrile illness",
                "secondary_diagnoses": [],
                "condition_at_discharge": "Stable and ambulatory",
                "instructions": (
                    "Complete medication course, hydrate, "
                    "return for worsening symptoms"
                ),
                "follow_up_plan": "Clinic review in seven days",
                "destination": "Home",
            },
            headers=_headers(
                doctor_login,
                idempotency_key="gate21-discharge",
            ),
        ),
        200,
    )
    assert discharge_body["admission"]["status"] == "discharged"
    summary = discharge_body["discharge_summary"]
    assert summary["admission_id"] == admission_id
    assert summary["patient_id"] == patient_id
    assert (
        summary["medication_reconciliation_id"]
        == discharge_recon_id
    )
    assert summary["record_status"] == "final"
    assert summary["finalized_by_staff_id"] == doctor_staff.id
    assert summary["finalized_at"] is not None

    # ------------------------------------------------------------------
    # 36. Verify bed release.
    # ------------------------------------------------------------------
    admission = db.session.get(Admission, admission_id)
    db.session.refresh(admission)
    assert _enum_value(admission.status) == "discharged"

    released_bed = db.session.get(Bed, bed_two_id)
    db.session.refresh(released_bed)
    assert _enum_value(released_bed.status) == "available"

    # ------------------------------------------------------------------
    # 37. Verify billing state using the live billing API.
    # ------------------------------------------------------------------
    invoice_body = _expect(
        client.post(
            "/api/v1/billing/invoices",
            json={
                "patient_id": patient_id,
                "currency": "NGN",
                "appointment_id": appointment_id,
                "due_date": (
                    date.today() + timedelta(days=7)
                ).isoformat(),
                "items": [
                    {
                        "description": "Consultation",
                        "quantity": 1,
                        "unit_price": "100.00",
                    },
                    {
                        "description": "Laboratory service",
                        "quantity": 1,
                        "unit_price": "25.00",
                    },
                    {
                        "description": "Inpatient medication",
                        "quantity": 10,
                        "unit_price": "5.00",
                    },
                ],
            },
            headers=_headers(admin_login),
        ),
        201,
    )
    invoice_id = invoice_body["data"]["id"]
    assert invoice_body["data"]["status"] == "issued"
    assert invoice_body["data"]["total_amount"] == "175.00"

    invoice = db.session.get(Invoice, invoice_id)
    assert invoice is not None
    assert invoice.patient_id == patient_id
    assert invoice.clinic_id == clinic.id
    assert invoice.total_amount == Decimal("175.00")

    # ------------------------------------------------------------------
    # 38. Verify pharmacy inventory.
    # ------------------------------------------------------------------
    stock_body = _expect(
        client.get(
            f"/api/v1/pharmacy/drugs/{drug_id}/stock-summary",
            headers=_headers(pharmacist_login),
        ),
        200,
    )
    assert stock_body["data"]["quantity_on_hand"] == 90

    persisted_batch = db.session.get(DrugBatch, batch_id)
    assert persisted_batch is not None
    assert persisted_batch.quantity_on_hand == 90

    # ------------------------------------------------------------------
    # 39. Verify immutable audit evidence.
    #
    # Patient-scoped operations must leave patient-linked audit evidence.
    # eMAR separately audits the MedicationAdministration aggregate using
    # its administration ID as the audited entity identity.
    # ------------------------------------------------------------------
    patient_audit_rows = (
        db.session.execute(
            db.select(AuditLog)
            .where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.patient_id == patient_id,
            )
            .order_by(AuditLog.id.asc())
        )
        .scalars()
        .all()
    )
    assert patient_audit_rows
    assert all(
        row.evidence_sha256
        and len(row.evidence_sha256) == 64
        for row in patient_audit_rows
    )

    emar_audit_rows = (
        db.session.execute(
            db.select(AuditLog)
            .where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "MedicationAdministration",
                AuditLog.entity_id == administration_id,
            )
            .order_by(AuditLog.id.asc())
        )
        .scalars()
        .all()
    )
    assert emar_audit_rows
    assert all(
        row.evidence_sha256
        and len(row.evidence_sha256) == 64
        for row in emar_audit_rows
    )

    # ------------------------------------------------------------------
    # 40. Verify HIE/FHIR eligible record path.
    # ------------------------------------------------------------------
    provider_name = "gate21-fhir"
    provider = Gate21FHIRProvider()

    registry.unregister_provider(provider_name)
    registry.register_provider(
        provider_name,
        lambda _endpoint: provider,
    )

    try:
        integration_body = _expect(
            client.post(
                "/api/v1/hie/integrations",
                json={
                    "provider": provider_name,
                    "endpoint_url": (
                        "https://fhir.partnerhealth.org/fhir/r4"
                    ),
                    "organization_id": "GATE21-ORG",
                    "facility_id": "GATE21-FACILITY",
                },
                headers=_headers(admin_login),
            ),
            201,
        )
        integration_id = integration_body["data"]["id"]

        active_integration = _expect(
            client.patch(
                f"/api/v1/hie/integrations/{integration_id}",
                json={"status": "active"},
                headers=_headers(admin_login),
            ),
            200,
        )
        assert active_integration["data"]["status"] == "active"

        patient = db.session.get(Patient, patient_id)
        assert patient is not None
        patient_identifier = (
            patient.patient_number
            if patient.patient_number
            else str(patient.id)
        )

        hie_body = _expect(
            client.post(
                "/api/v1/hie/queries/clinical-data",
                json={
                    "patient_identifier": patient_identifier,
                    "purpose_of_use": "treatment",
                    "filters": {
                        "resource_type": "Patient",
                    },
                    "integration_id": integration_id,
                },
                headers=_headers(doctor_login),
            ),
            200,
        )

        assert hie_body["success"] is True
        assert hie_body["data"]["status_code"] == 200
        assert provider.calls == [
            {
                "patient_identifier": patient_identifier,
                "filters": {
                    "resource_type": "Patient",
                },
            }
        ]

        fhir_patient = hie_body["data"]["records"][0]
        validated_fhir_patient = validate_fhir_resource(
            fhir_patient,
            "Patient",
        )
        assert validated_fhir_patient["resourceType"] == "Patient"

        # --------------------------------------------------------------
        # 41. Verify no cross-tenant leakage.
        # --------------------------------------------------------------
        foreign_clinic = make_clinic(
            name="Gate 21 Foreign Clinic",
        )
        foreign_doctor = make_staff(
            clinic=foreign_clinic,
            role=Role.DOCTOR,
            user_overrides={
                "email": "gate21-foreign-doctor@test.com",
            },
        )
        foreign_login = e2e_login(
            "gate21-foreign-doctor@test.com"
        )
        assert foreign_login["user_id"] == foreign_doctor.user.id

        foreign_lab_read = client.get(
            f"/api/v1/lab/orders/{lab_order_id}",
            headers=_headers(foreign_login),
        )
        assert foreign_lab_read.status_code == 404

        foreign_hie_read = client.get(
            f"/api/v1/hie/integrations/{integration_id}",
            headers=_headers(foreign_login),
        )
        assert foreign_hie_read.status_code == 422

        foreign_payload = str(foreign_lab_read.get_json())
        assert patient_identifier not in foreign_payload
        assert "GoldenPatient" not in foreign_payload

    finally:
        registry.unregister_provider(provider_name)

    # Transfer ID retained as workflow evidence.
    assert transfer_id > 0

    print("GATE21_GOLDEN_HOSPITAL_WORKFLOW=PASS")
