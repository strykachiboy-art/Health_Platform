"""Unit tests for FHIR R4 export mapper (fhir_export.py).

All tests are pure-function and require no Flask app context, no database, and
no network.  Input objects are plain Python namespaces (types.SimpleNamespace)
to decouple these tests from SQLAlchemy model details.
"""

from __future__ import annotations

import types
from datetime import date, datetime, timezone
from typing import Any

import pytest

from app.modules.hie.providers.fhir_export import (
    admission_to_fhir_encounter,
    allergy_to_fhir,
    build_fhir_collection_bundle,
    patient_to_fhir,
    prescription_to_fhir_medication_request,
    vitals_to_fhir_observation,
)


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def _ns(**kwargs: Any) -> types.SimpleNamespace:
    return types.SimpleNamespace(**kwargs)


def _patient(**overrides) -> types.SimpleNamespace:
    defaults = dict(
        id=1,
        first_name="Fatima",
        last_name="Al-Hassan",
        date_of_birth=date(1985, 3, 20),
        gender=_ns(value="female"),
        phone="+971501234567",
        email="fatima@example.com",
        address="Villa 7, Dubai",
        is_active=True,
        patient_number="PN-0001",
        emirates_id="784-1985-0000001-1",
        umrn="UMRN-99",
    )
    defaults.update(overrides)
    return _ns(**defaults)


def _admission(**overrides) -> types.SimpleNamespace:
    defaults = dict(
        id=10,
        patient_id=1,
        admitted_at=datetime(2024, 6, 1, 8, 0, tzinfo=timezone.utc),
        discharged_at=None,
        status=_ns(value="admitted"),
    )
    defaults.update(overrides)
    return _ns(**defaults)


def _prescription(**overrides) -> types.SimpleNamespace:
    defaults = dict(
        id=20,
        patient_id=1,
        medication_name="Amoxicillin",
        drug_name="Amoxicillin",
        dosage="500mg",
        frequency="Three times daily",
        status="active",
        prescribed_at=datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc),
        created_at=datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return _ns(**defaults)


def _allergy(**overrides) -> types.SimpleNamespace:
    defaults = dict(
        id=30,
        patient_id=1,
        allergen="Penicillin",
        allergy_type="drug",
        severity="severe",
        clinical_status="active",
        verification_status="confirmed",
        recorded_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
        created_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return _ns(**defaults)


def _vitals(**overrides) -> types.SimpleNamespace:
    defaults = dict(
        id=40,
        patient_id=1,
        recorded_at=datetime(2024, 6, 1, 9, 0, tzinfo=timezone.utc),
        temperature=37.2,
        heart_rate=72,
        blood_pressure_systolic=120,
        blood_pressure_diastolic=80,
        respiratory_rate=16,
        oxygen_saturation=98.5,
    )
    defaults.update(overrides)
    return _ns(**defaults)


# ---------------------------------------------------------------------------
# patient_to_fhir
# ---------------------------------------------------------------------------


def test_patient_to_fhir_resourcetype():
    p = patient_to_fhir(_patient())
    assert p["resourceType"] == "Patient"


def test_patient_to_fhir_id_as_string():
    p = patient_to_fhir(_patient(id=42))
    assert p["id"] == "42"


def test_patient_to_fhir_name():
    p = patient_to_fhir(_patient())
    assert p["name"][0]["family"] == "Al-Hassan"
    assert p["name"][0]["given"] == ["Fatima"]


def test_patient_to_fhir_gender_female():
    p = patient_to_fhir(_patient(gender=_ns(value="female")))
    assert p["gender"] == "female"


def test_patient_to_fhir_gender_unknown_fallback():
    p = patient_to_fhir(_patient(gender=None))
    assert p["gender"] == "unknown"


def test_patient_to_fhir_gender_unrecognised_fallback():
    p = patient_to_fhir(_patient(gender=_ns(value="nonbinary")))
    assert p["gender"] == "unknown"


def test_patient_to_fhir_birth_date():
    p = patient_to_fhir(_patient())
    assert p["birthDate"] == "1985-03-20"


def test_patient_to_fhir_no_birth_date_omitted():
    p = patient_to_fhir(_patient(date_of_birth=None))
    assert "birthDate" not in p


def test_patient_to_fhir_identifiers_include_system_id():
    p = patient_to_fhir(_patient())
    systems = [i["system"] for i in p["identifier"]]
    assert "urn:clinic-system:patient" in systems


def test_patient_to_fhir_includes_emirates_id():
    p = patient_to_fhir(_patient())
    systems = [i["system"] for i in p["identifier"]]
    assert "urn:ae:emirates-id" in systems


def test_patient_to_fhir_omits_emirates_id_when_none():
    p = patient_to_fhir(_patient(emirates_id=None))
    systems = [i["system"] for i in p["identifier"]]
    assert "urn:ae:emirates-id" not in systems


def test_patient_to_fhir_includes_phone_telecom():
    p = patient_to_fhir(_patient())
    systems = [t["system"] for t in p.get("telecom", [])]
    assert "phone" in systems


def test_patient_to_fhir_includes_email_telecom():
    p = patient_to_fhir(_patient())
    systems = [t["system"] for t in p.get("telecom", [])]
    assert "email" in systems


def test_patient_to_fhir_no_telecom_when_none():
    p = patient_to_fhir(_patient(phone=None, email=None))
    assert "telecom" not in p


def test_patient_to_fhir_includes_address():
    p = patient_to_fhir(_patient())
    assert p["address"][0]["text"] == "Villa 7, Dubai"


def test_patient_to_fhir_active_flag():
    p = patient_to_fhir(_patient(is_active=True))
    assert p["active"] is True


def test_patient_to_fhir_inactive_flag():
    p = patient_to_fhir(_patient(is_active=False))
    assert p["active"] is False


# ---------------------------------------------------------------------------
# admission_to_fhir_encounter
# ---------------------------------------------------------------------------


def test_encounter_resourcetype():
    e = admission_to_fhir_encounter(_admission())
    assert e["resourceType"] == "Encounter"


def test_encounter_status_in_progress_when_not_discharged():
    e = admission_to_fhir_encounter(_admission(discharged_at=None))
    assert e["status"] == "in-progress"


def test_encounter_status_finished_when_discharged():
    e = admission_to_fhir_encounter(
        _admission(
            discharged_at=datetime(2024, 6, 5, 12, 0, tzinfo=timezone.utc)
        )
    )
    assert e["status"] == "finished"


def test_encounter_period_start():
    e = admission_to_fhir_encounter(_admission())
    assert "start" in e["period"]


def test_encounter_period_end_when_discharged():
    discharged = datetime(2024, 6, 5, 12, 0, tzinfo=timezone.utc)
    e = admission_to_fhir_encounter(_admission(discharged_at=discharged))
    assert "end" in e["period"]


def test_encounter_subject_reference():
    e = admission_to_fhir_encounter(_admission(patient_id=5))
    assert e["subject"]["reference"] == "Patient/5"


def test_encounter_class_code_is_imp():
    e = admission_to_fhir_encounter(_admission())
    assert e["class"]["code"] == "IMP"


# ---------------------------------------------------------------------------
# prescription_to_fhir_medication_request
# ---------------------------------------------------------------------------


def test_medication_request_resourcetype():
    m = prescription_to_fhir_medication_request(_prescription())
    assert m["resourceType"] == "MedicationRequest"


def test_medication_request_intent_is_order():
    m = prescription_to_fhir_medication_request(_prescription())
    assert m["intent"] == "order"


def test_medication_request_medication_name():
    m = prescription_to_fhir_medication_request(
        _prescription(medication_name="Metformin")
    )
    assert m["medicationCodeableConcept"]["text"] == "Metformin"


def test_medication_request_status_mapping_active():
    m = prescription_to_fhir_medication_request(_prescription(status="active"))
    assert m["status"] == "active"


def test_medication_request_status_mapping_completed():
    m = prescription_to_fhir_medication_request(_prescription(status="completed"))
    assert m["status"] == "completed"


def test_medication_request_status_unknown_fallback():
    m = prescription_to_fhir_medication_request(_prescription(status="dispensed"))
    assert m["status"] == "unknown"


def test_medication_request_subject_reference():
    m = prescription_to_fhir_medication_request(_prescription(patient_id=7))
    assert m["subject"]["reference"] == "Patient/7"


def test_medication_request_authored_on_present():
    m = prescription_to_fhir_medication_request(_prescription())
    assert "authoredOn" in m


def test_medication_request_dosage_instruction():
    m = prescription_to_fhir_medication_request(
        _prescription(dosage="500mg", frequency="TID")
    )
    assert m["dosageInstruction"][0]["text"] == "500mg"


def test_medication_request_no_dosage_instruction_when_empty():
    m = prescription_to_fhir_medication_request(
        _prescription(dosage=None, frequency=None)
    )
    assert "dosageInstruction" not in m


# ---------------------------------------------------------------------------
# allergy_to_fhir
# ---------------------------------------------------------------------------


def test_allergy_resourcetype():
    a = allergy_to_fhir(_allergy())
    assert a["resourceType"] == "AllergyIntolerance"


def test_allergy_code_text():
    a = allergy_to_fhir(_allergy(allergen="Penicillin"))
    assert a["code"]["text"] == "Penicillin"


def test_allergy_patient_reference():
    a = allergy_to_fhir(_allergy(patient_id=3))
    assert a["patient"]["reference"] == "Patient/3"


def test_allergy_clinical_status_coding():
    a = allergy_to_fhir(_allergy(clinical_status="active"))
    code = a["clinicalStatus"]["coding"][0]["code"]
    assert code == "active"


def test_allergy_verification_status_coding():
    a = allergy_to_fhir(_allergy(verification_status="confirmed"))
    code = a["verificationStatus"]["coding"][0]["code"]
    assert code == "confirmed"


def test_allergy_severity_severe():
    a = allergy_to_fhir(_allergy(severity="severe"))
    assert a["reaction"][0]["severity"] == "severe"


def test_allergy_type_drug():
    a = allergy_to_fhir(_allergy(allergy_type="allergy"))
    assert a["type"] == "allergy"


def test_allergy_type_omitted_for_unrecognised():
    a = allergy_to_fhir(_allergy(allergy_type="latex"))
    assert "type" not in a


def test_allergy_recorded_date_present():
    a = allergy_to_fhir(_allergy())
    assert "recordedDate" in a


# ---------------------------------------------------------------------------
# vitals_to_fhir_observation
# ---------------------------------------------------------------------------


def test_vitals_returns_list():
    obs = vitals_to_fhir_observation(_vitals())
    assert isinstance(obs, list)


def test_vitals_all_six_observations_when_all_present():
    obs = vitals_to_fhir_observation(_vitals())
    assert len(obs) == 6


def test_vitals_omits_none_fields():
    v = _vitals(temperature=None, heart_rate=None)
    obs = vitals_to_fhir_observation(v)
    assert len(obs) == 4


def test_vitals_all_none_returns_empty_list():
    v = _vitals(
        temperature=None,
        heart_rate=None,
        blood_pressure_systolic=None,
        blood_pressure_diastolic=None,
        respiratory_rate=None,
        oxygen_saturation=None,
    )
    obs = vitals_to_fhir_observation(v)
    assert obs == []


def test_vitals_resource_type():
    obs = vitals_to_fhir_observation(_vitals())
    for o in obs:
        assert o["resourceType"] == "Observation"


def test_vitals_category_is_vital_signs():
    obs = vitals_to_fhir_observation(_vitals())
    for o in obs:
        assert o["category"][0]["coding"][0]["code"] == "vital-signs"


def test_vitals_loinc_system():
    obs = vitals_to_fhir_observation(_vitals())
    for o in obs:
        assert o["code"]["coding"][0]["system"] == "http://loinc.org"


def test_vitals_temperature_loinc_code():
    obs = vitals_to_fhir_observation(_vitals())
    temp_obs = [
        o for o in obs if o["code"]["coding"][0]["code"] == "8310-5"
    ]
    assert len(temp_obs) == 1
    assert temp_obs[0]["valueQuantity"]["value"] == 37.2


def test_vitals_subject_reference():
    obs = vitals_to_fhir_observation(_vitals(patient_id=11))
    for o in obs:
        assert o["subject"]["reference"] == "Patient/11"


def test_vitals_effective_datetime_present():
    obs = vitals_to_fhir_observation(_vitals())
    for o in obs:
        assert "effectiveDateTime" in o


# ---------------------------------------------------------------------------
# build_fhir_collection_bundle
# ---------------------------------------------------------------------------


def test_bundle_resourcetype():
    b = build_fhir_collection_bundle([])
    assert b["resourceType"] == "Bundle"


def test_bundle_type_is_collection():
    b = build_fhir_collection_bundle([])
    assert b["type"] == "collection"


def test_bundle_total_matches_entries():
    resources = [
        {"resourceType": "Patient", "id": "1"},
        {"resourceType": "Patient", "id": "2"},
    ]
    b = build_fhir_collection_bundle(resources)
    assert b["total"] == 2
    assert len(b["entry"]) == 2


def test_bundle_entry_contains_resource_key():
    resources = [{"resourceType": "Patient", "id": "1"}]
    b = build_fhir_collection_bundle(resources)
    assert "resource" in b["entry"][0]


def test_bundle_id_set_when_provided():
    b = build_fhir_collection_bundle([], bundle_id="test-bundle-001")
    assert b["id"] == "test-bundle-001"


def test_bundle_timestamp_set_when_provided():
    b = build_fhir_collection_bundle([], timestamp="2024-06-01T00:00:00+00:00")
    assert b["timestamp"] == "2024-06-01T00:00:00+00:00"


def test_bundle_no_id_when_not_provided():
    b = build_fhir_collection_bundle([])
    assert "id" not in b


def test_bundle_filters_none_resources():
    resources = [{"resourceType": "Patient", "id": "1"}, None]
    b = build_fhir_collection_bundle(resources)
    assert b["total"] == 1
