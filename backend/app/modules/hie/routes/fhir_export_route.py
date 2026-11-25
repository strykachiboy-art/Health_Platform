"""FHIR R4 export routes.

Provides admin-only endpoints that export internal clinic data as FHIR R4
JSON Bundles.  These endpoints are purely outbound (they generate data *from*
this system for external consumers) and are entirely separate from the inbound
HIE query flow.

Registered under ``/api/v1/hie/fhir/export/`` by the shared
``register_api_blueprint`` helper (same as every other module blueprint).

Endpoints
---------
GET /hie/fhir/export/patient/<patient_id>
    Full patient FHIR Bundle: Patient + Encounters + Observations +
    AllergyIntolerances + MedicationRequests.

GET /hie/fhir/export/patient/<patient_id>/patient
    Patient resource only.

GET /hie/fhir/export/patient/<patient_id>/encounters
    Encounter resources (admissions).

GET /hie/fhir/export/patient/<patient_id>/observations
    Observation resources (vitals).

GET /hie/fhir/export/patient/<patient_id>/allergies
    AllergyIntolerance resources.

GET /hie/fhir/export/patient/<patient_id>/medications
    MedicationRequest resources (prescription items).

Security
--------
* All endpoints require ``ADMIN`` role (matches existing HIE admin-only
  pattern for sensitive data export).
* Clinic-scoping is enforced: patient must belong to the caller's clinic.
* No PHI is ever written to logs.
* JSON response Content-Type is ``application/fhir+json`` as required by
  the FHIR R4 specification.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, Response, jsonify, make_response
from flask_jwt_extended import get_jwt_identity

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.exceptions import NotFoundError, ValidationError
from app.core.utils.decorators import get_current_clinic_id, role_required
from app.extensions import db
from app.modules.patient.models.patient_model import Patient, PatientAllergy
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)
from app.modules.ward.models.ward_model import Admission
from app.modules.hie.providers.fhir_export import (
    admission_to_fhir_encounter,
    allergy_to_fhir,
    build_fhir_collection_bundle,
    patient_to_fhir,
    prescription_to_fhir_medication_request,
    vitals_to_fhir_observation,
)


fhir_export_bp = Blueprint(
    "fhir_export",
    __name__,
    url_prefix="/hie/fhir/export",
)

_FHIR_CONTENT_TYPE = "application/fhir+json"
_EXPORT_ROLES = (Role.ADMIN,)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_clinic_patient(patient_id: int, clinic_id: int) -> Patient:
    """Load patient and enforce clinic-scoping."""
    if (
        isinstance(patient_id, bool)
        or not isinstance(patient_id, int)
        or patient_id <= 0
    ):
        raise ValidationError("Invalid patient ID")

    patient = db.session.get(Patient, patient_id)

    if patient is None or patient.clinic_id != clinic_id:
        raise NotFoundError(f"Patient {patient_id} not found")

    return patient


def _fhir_response(bundle: dict[str, Any]) -> Response:
    response = make_response(jsonify(bundle), 200)
    response.headers["Content-Type"] = _FHIR_CONTENT_TYPE
    return response


def _load_admissions(patient_id: int) -> list[Admission]:
    return (
        db.session.execute(
            db.select(Admission)
            .where(Admission.patient_id == patient_id)
            .order_by(Admission.admitted_at.desc())
        )
        .scalars()
        .all()
    )


def _load_vitals(patient_id: int) -> list[Any]:
    from app.modules.patient.models.patient_model import PatientVitals

    return (
        db.session.execute(
            db.select(PatientVitals)
            .where(PatientVitals.patient_id == patient_id)
            .order_by(PatientVitals.recorded_at.desc())
            .limit(50)  # Cap: most recent 50 readings
        )
        .scalars()
        .all()
    )


def _load_allergies(patient_id: int, clinic_id: int) -> list[PatientAllergy]:
    return (
        db.session.execute(
            db.select(PatientAllergy)
            .where(
                PatientAllergy.patient_id == patient_id,
                PatientAllergy.clinic_id == clinic_id,
            )
            .order_by(PatientAllergy.recorded_at.desc())
        )
        .scalars()
        .all()
    )


def _load_prescription_items(patient_id: int, clinic_id: int) -> list[PrescriptionItem]:
    """Load all PrescriptionItems for a patient via their parent Prescriptions."""
    return (
        db.session.execute(
            db.select(PrescriptionItem)
            .join(Prescription, PrescriptionItem.prescription_id == Prescription.id)
            .where(
                Prescription.patient_id == patient_id,
                Prescription.clinic_id == clinic_id,
            )
            .order_by(Prescription.issued_at.desc())
            .limit(200)  # Cap: last 200 items
        )
        .scalars()
        .all()
    )


class _PrescriptionItemFHIRAdapter:
    """Thin adapter so fhir_export.prescription_to_fhir_medication_request
    can consume a PrescriptionItem with its parent Prescription's patient_id,
    status and issued_at without coupling the mapper to the ORM relationship.
    """

    __slots__ = (
        "id",
        "patient_id",
        "medication_name",
        "drug_name",
        "dosage",
        "frequency",
        "status",
        "prescribed_at",
        "created_at",
    )

    def __init__(self, item: PrescriptionItem) -> None:
        prescription: Prescription = item.prescription
        self.id = item.id
        self.patient_id = prescription.patient_id
        # drug_name comes from the related Drug; fall back gracefully.
        drug = getattr(item, "drug", None)
        self.medication_name = getattr(drug, "name", None)
        self.drug_name = self.medication_name
        self.dosage = item.dosage
        self.frequency = item.frequency
        self.status = (
            prescription.status.value
            if hasattr(prescription.status, "value")
            else str(prescription.status)
        )
        self.prescribed_at = prescription.issued_at
        self.created_at = prescription.created_at


class _VitalsAdapter:
    """Adapts PatientVitals field names to what fhir_export expects."""

    __slots__ = (
        "id",
        "patient_id",
        "recorded_at",
        "temperature",
        "heart_rate",
        "blood_pressure_systolic",
        "blood_pressure_diastolic",
        "respiratory_rate",
        "oxygen_saturation",
    )

    def __init__(self, v: Any) -> None:
        self.id = v.id
        self.patient_id = v.patient_id
        self.recorded_at = v.recorded_at
        self.temperature = (
            float(v.temperature_c) if v.temperature_c is not None else None
        )
        self.heart_rate = v.heart_rate_bpm
        self.blood_pressure_systolic = v.blood_pressure_systolic
        self.blood_pressure_diastolic = v.blood_pressure_diastolic
        self.respiratory_rate = v.respiratory_rate
        self.oxygen_saturation = (
            float(v.oxygen_saturation) if v.oxygen_saturation is not None else None
        )


class _AllergyAdapter:
    """Adapts PatientAllergy field names to what fhir_export expects."""

    __slots__ = (
        "id",
        "patient_id",
        "allergen",
        "allergy_type",
        "severity",
        "clinical_status",
        "verification_status",
        "recorded_at",
        "created_at",
    )

    def __init__(self, a: PatientAllergy) -> None:
        self.id = a.id
        self.patient_id = a.patient_id
        self.allergen = a.allergen_name
        self.allergy_type = a.allergen_type
        self.severity = a.severity
        self.clinical_status = a.clinical_status
        self.verification_status = a.verification_status
        self.recorded_at = a.recorded_at
        self.created_at = a.created_at


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@fhir_export_bp.get("/patient/<int:patient_id>")
@role_required(*_EXPORT_ROLES)
def export_patient_bundle(patient_id: int) -> Response:
    """Full FHIR R4 Bundle for a patient (all supported resource types)."""
    clinic_id = get_current_clinic_id(required=True)
    patient = _require_clinic_patient(patient_id, clinic_id)

    resources: list[dict[str, Any]] = []

    # Patient
    resources.append(patient_to_fhir(patient))

    # Encounters (admissions)
    for admission in _load_admissions(patient_id):
        resources.append(admission_to_fhir_encounter(admission))

    # Observations (vitals)
    for vitals_row in _load_vitals(patient_id):
        resources.extend(vitals_to_fhir_observation(_VitalsAdapter(vitals_row)))

    # AllergyIntolerances
    for allergy in _load_allergies(patient_id, clinic_id):
        resources.append(allergy_to_fhir(_AllergyAdapter(allergy)))

    # MedicationRequests (prescription items)
    for item in _load_prescription_items(patient_id, clinic_id):
        try:
            adapter = _PrescriptionItemFHIRAdapter(item)
            resources.append(prescription_to_fhir_medication_request(adapter))
        except Exception:
            # Never let one malformed item break the entire export.
            continue

    bundle = build_fhir_collection_bundle(
        resources,
        bundle_id=f"patient-{patient_id}-bundle",
        timestamp=_utcnow_iso(),
    )

    return _fhir_response(bundle)


@fhir_export_bp.get("/patient/<int:patient_id>/patient")
@role_required(*_EXPORT_ROLES)
def export_patient_resource(patient_id: int) -> Response:
    """FHIR R4 Patient resource only."""
    clinic_id = get_current_clinic_id(required=True)
    patient = _require_clinic_patient(patient_id, clinic_id)
    bundle = build_fhir_collection_bundle(
        [patient_to_fhir(patient)],
        bundle_id=f"patient-{patient_id}-demographics",
        timestamp=_utcnow_iso(),
    )
    return _fhir_response(bundle)


@fhir_export_bp.get("/patient/<int:patient_id>/encounters")
@role_required(*_EXPORT_ROLES)
def export_patient_encounters(patient_id: int) -> Response:
    """FHIR R4 Encounter resources (admissions)."""
    clinic_id = get_current_clinic_id(required=True)
    _require_clinic_patient(patient_id, clinic_id)
    resources = [
        admission_to_fhir_encounter(a)
        for a in _load_admissions(patient_id)
    ]
    bundle = build_fhir_collection_bundle(
        resources,
        bundle_id=f"patient-{patient_id}-encounters",
        timestamp=_utcnow_iso(),
    )
    return _fhir_response(bundle)


@fhir_export_bp.get("/patient/<int:patient_id>/observations")
@role_required(*_EXPORT_ROLES)
def export_patient_observations(patient_id: int) -> Response:
    """FHIR R4 Observation resources (vitals)."""
    clinic_id = get_current_clinic_id(required=True)
    _require_clinic_patient(patient_id, clinic_id)
    resources: list[dict[str, Any]] = []
    for v in _load_vitals(patient_id):
        resources.extend(vitals_to_fhir_observation(_VitalsAdapter(v)))
    bundle = build_fhir_collection_bundle(
        resources,
        bundle_id=f"patient-{patient_id}-observations",
        timestamp=_utcnow_iso(),
    )
    return _fhir_response(bundle)


@fhir_export_bp.get("/patient/<int:patient_id>/allergies")
@role_required(*_EXPORT_ROLES)
def export_patient_allergies(patient_id: int) -> Response:
    """FHIR R4 AllergyIntolerance resources."""
    clinic_id = get_current_clinic_id(required=True)
    _require_clinic_patient(patient_id, clinic_id)
    resources = [
        allergy_to_fhir(_AllergyAdapter(a))
        for a in _load_allergies(patient_id, clinic_id)
    ]
    bundle = build_fhir_collection_bundle(
        resources,
        bundle_id=f"patient-{patient_id}-allergies",
        timestamp=_utcnow_iso(),
    )
    return _fhir_response(bundle)


@fhir_export_bp.get("/patient/<int:patient_id>/medications")
@role_required(*_EXPORT_ROLES)
def export_patient_medications(patient_id: int) -> Response:
    """FHIR R4 MedicationRequest resources (prescription items)."""
    clinic_id = get_current_clinic_id(required=True)
    _require_clinic_patient(patient_id, clinic_id)
    resources: list[dict[str, Any]] = []
    for item in _load_prescription_items(patient_id, clinic_id):
        try:
            adapter = _PrescriptionItemFHIRAdapter(item)
            resources.append(prescription_to_fhir_medication_request(adapter))
        except Exception:
            continue
    bundle = build_fhir_collection_bundle(
        resources,
        bundle_id=f"patient-{patient_id}-medications",
        timestamp=_utcnow_iso(),
    )
    return _fhir_response(bundle)
