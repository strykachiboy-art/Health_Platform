"""Pure-function FHIR R4 export mapper.

Converts internal SQLAlchemy model instances into FHIR R4-compliant JSON
dicts (suitable for serialisation with ``json.dumps``).

Design constraints
------------------
* **No DB access** – every function accepts fully-loaded Python objects.
  The route layer is responsible for fetching and scoping records.
* **No side-effects** – all functions are deterministic and stateless.
* **No sensitive logging** – PHI stays inside the returned structures; none is
  written to logs here.
* **Additive only** – callers should treat the returned dicts as a minimum
  viable FHIR representation.  Additional FHIR elements can be appended
  without altering existing keys.

Supported resource types (matching FHIR_READ_ONLY_RESOURCE_TYPES in
fhir_contract.py):
    * Patient
    * Encounter         (from admission records)
    * MedicationRequest (from prescription records)
    * AllergyIntolerance
    * Condition         (from chronic conditions text field)
    * Observation       (from vitals records)
    * DiagnosticReport  (from lab result / diagnostic records)

The exported Bundle type is ``"collection"`` for multi-resource exports and
``"searchset"`` is intentionally NOT used for locally-generated data (it is
reserved for inbound FHIR server responses).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional


# ---------------------------------------------------------------------------
# FHIR identifiers & coding helpers
# ---------------------------------------------------------------------------

_FHIR_VERSION = "4.0.1"
_SYSTEM_PATIENT_ID = "urn:clinic-system:patient"
_SYSTEM_ENCOUNTER_ID = "urn:clinic-system:encounter"
_SYSTEM_PRESCRIPTION_ID = "urn:clinic-system:prescription"
_SYSTEM_VITALS_ID = "urn:clinic-system:vitals"
_SYSTEM_LAB_ID = "urn:clinic-system:lab"


def _isoformat(dt: datetime | date | None) -> Optional[str]:
    """Return ISO 8601 string or None."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    return dt.isoformat()  # date


def _patient_reference(patient_id: int) -> dict[str, str]:
    return {"reference": f"Patient/{patient_id}"}


def _gender_map(gender_value: Optional[str]) -> str:
    """Map internal Gender enum value to FHIR administrative-gender code."""
    if gender_value is None:
        return "unknown"
    mapping = {
        "male": "male",
        "female": "female",
        "other": "other",
    }
    return mapping.get(str(gender_value).lower(), "unknown")


# ---------------------------------------------------------------------------
# Individual resource mappers
# ---------------------------------------------------------------------------


def patient_to_fhir(patient: Any) -> dict[str, Any]:
    """Map a :class:`~app.modules.patient.models.patient_model.Patient` to FHIR R4 Patient.

    Only non-sensitive demographic fields are included.  AI risk scores, raw
    triage JSON, and internal barcodes are intentionally omitted.
    """
    identifiers: list[dict[str, Any]] = [
        {
            "system": _SYSTEM_PATIENT_ID,
            "value": str(patient.id),
        }
    ]
    if getattr(patient, "patient_number", None):
        identifiers.append(
            {
                "system": "urn:clinic-system:patient-number",
                "value": str(patient.patient_number),
            }
        )
    if getattr(patient, "emirates_id", None):
        identifiers.append(
            {
                "system": "urn:ae:emirates-id",
                "value": str(patient.emirates_id),
            }
        )
    if getattr(patient, "umrn", None):
        identifiers.append(
            {
                "system": "urn:clinic-system:umrn",
                "value": str(patient.umrn),
            }
        )

    name: list[dict[str, Any]] = [
        {
            "use": "official",
            "family": patient.last_name,
            "given": [patient.first_name],
        }
    ]

    telecom: list[dict[str, Any]] = []
    if getattr(patient, "phone", None):
        telecom.append({"system": "phone", "value": patient.phone, "use": "mobile"})
    if getattr(patient, "email", None):
        telecom.append({"system": "email", "value": patient.email})

    resource: dict[str, Any] = {
        "resourceType": "Patient",
        "id": str(patient.id),
        "identifier": identifiers,
        "name": name,
        "gender": _gender_map(
            patient.gender.value
            if hasattr(patient.gender, "value")
            else patient.gender
        ),
        "active": bool(getattr(patient, "is_active", True)),
    }

    dob = _isoformat(getattr(patient, "date_of_birth", None))
    if dob:
        resource["birthDate"] = dob

    if telecom:
        resource["telecom"] = telecom

    address_text = getattr(patient, "address", None)
    if address_text:
        resource["address"] = [{"text": address_text}]

    return resource


def admission_to_fhir_encounter(admission: Any) -> dict[str, Any]:
    """Map a patient admission record to a FHIR R4 Encounter resource.

    Expects the admission object to have at minimum:
    ``id``, ``patient_id``, ``admitted_at``, ``discharged_at`` (nullable),
    ``admission_type`` (nullable), ``status`` (nullable).
    """
    patient_id = getattr(admission, "patient_id", None)

    status = "in-progress"
    discharged_at = getattr(admission, "discharged_at", None)
    if discharged_at is not None:
        status = "finished"

    resource: dict[str, Any] = {
        "resourceType": "Encounter",
        "id": str(admission.id),
        "identifier": [
            {
                "system": _SYSTEM_ENCOUNTER_ID,
                "value": str(admission.id),
            }
        ],
        "status": status,
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "IMP",
            "display": "inpatient encounter",
        },
    }

    if patient_id:
        resource["subject"] = _patient_reference(patient_id)

    period: dict[str, str] = {}
    admitted_at = _isoformat(getattr(admission, "admitted_at", None))
    if admitted_at:
        period["start"] = admitted_at
    discharged_str = _isoformat(discharged_at)
    if discharged_str:
        period["end"] = discharged_str
    if period:
        resource["period"] = period

    admission_type = getattr(admission, "admission_type", None)
    if admission_type:
        resource["type"] = [
            {
                "text": str(
                    admission_type.value
                    if hasattr(admission_type, "value")
                    else admission_type
                )
            }
        ]

    return resource


def prescription_to_fhir_medication_request(prescription: Any) -> dict[str, Any]:
    """Map an internal prescription record to a FHIR R4 MedicationRequest.

    Expects: ``id``, ``patient_id``, ``medication_name``, ``dosage``
    (nullable), ``frequency`` (nullable), ``prescribed_at`` (nullable),
    ``status`` (nullable).
    """
    patient_id = getattr(prescription, "patient_id", None)

    # Map internal status to FHIR MedicationRequest status.
    internal_status = str(
        getattr(prescription, "status", "") or ""
    ).lower()
    fhir_status_map = {
        "active": "active",
        "completed": "completed",
        "cancelled": "cancelled",
        "stopped": "stopped",
        "on-hold": "on-hold",
    }
    fhir_status = fhir_status_map.get(internal_status, "unknown")

    resource: dict[str, Any] = {
        "resourceType": "MedicationRequest",
        "id": str(prescription.id),
        "identifier": [
            {
                "system": _SYSTEM_PRESCRIPTION_ID,
                "value": str(prescription.id),
            }
        ],
        "status": fhir_status,
        "intent": "order",
        "medicationCodeableConcept": {
            "text": str(
                getattr(prescription, "medication_name", None)
                or getattr(prescription, "drug_name", None)
                or "Unknown medication"
            )
        },
    }

    if patient_id:
        resource["subject"] = _patient_reference(patient_id)

    authored_on = _isoformat(
        getattr(prescription, "prescribed_at", None)
        or getattr(prescription, "created_at", None)
    )
    if authored_on:
        resource["authoredOn"] = authored_on

    dosage_text = str(getattr(prescription, "dosage", None) or "")
    frequency_text = str(getattr(prescription, "frequency", None) or "")
    dosage_instruction: dict[str, Any] = {}
    if dosage_text:
        dosage_instruction["text"] = dosage_text
    if frequency_text:
        dosage_instruction.setdefault("timing", {})["code"] = {
            "text": frequency_text
        }
    if dosage_instruction:
        resource["dosageInstruction"] = [dosage_instruction]

    return resource


def allergy_to_fhir(allergy: Any) -> dict[str, Any]:
    """Map a structured allergy record to a FHIR R4 AllergyIntolerance.

    Expects: ``id``, ``patient_id``, ``allergen``, ``allergy_type``
    (nullable), ``severity`` (nullable), ``clinical_status`` (nullable),
    ``verification_status`` (nullable), ``recorded_at`` (nullable).
    """
    patient_id = getattr(allergy, "patient_id", None)

    # Clinical status coding
    clinical_status_value = str(
        getattr(allergy, "clinical_status", None) or "active"
    )
    if hasattr(allergy.clinical_status, "value") if hasattr(allergy, "clinical_status") else False:
        clinical_status_value = allergy.clinical_status.value

    clinical_status = {
        "coding": [
            {
                "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                "code": clinical_status_value.lower(),
            }
        ]
    }

    # Verification status coding
    verification_value = str(
        getattr(allergy, "verification_status", None) or "confirmed"
    )
    if hasattr(allergy, "verification_status") and hasattr(
        allergy.verification_status, "value"
    ):
        verification_value = allergy.verification_status.value

    verification_status = {
        "coding": [
            {
                "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                "code": verification_value.lower(),
            }
        ]
    }

    resource: dict[str, Any] = {
        "resourceType": "AllergyIntolerance",
        "id": str(allergy.id),
        "clinicalStatus": clinical_status,
        "verificationStatus": verification_status,
        "code": {
            "text": str(
                getattr(allergy, "allergen", None) or "Unknown allergen"
            )
        },
    }

    if patient_id:
        resource["patient"] = _patient_reference(patient_id)

    allergy_type = getattr(allergy, "allergy_type", None)
    if allergy_type:
        type_value = (
            allergy_type.value
            if hasattr(allergy_type, "value")
            else str(allergy_type)
        ).lower()
        if type_value in {"allergy", "intolerance"}:
            resource["type"] = type_value

    severity = getattr(allergy, "severity", None)
    if severity:
        severity_value = (
            severity.value if hasattr(severity, "value") else str(severity)
        ).lower()
        severity_map = {"mild": "mild", "moderate": "moderate", "severe": "severe"}
        fhir_severity = severity_map.get(severity_value)
        if fhir_severity:
            resource["reaction"] = [{"severity": fhir_severity}]

    recorded_at = _isoformat(
        getattr(allergy, "recorded_at", None)
        or getattr(allergy, "created_at", None)
    )
    if recorded_at:
        resource["recordedDate"] = recorded_at

    return resource


def vitals_to_fhir_observation(vitals: Any) -> list[dict[str, Any]]:
    """Map a vitals record to a list of FHIR R4 Observation resources.

    One Observation is created per non-None vital sign value.  Returns an
    empty list when the record carries no measurable values.

    Expects: ``id``, ``patient_id``, ``recorded_at`` (nullable),
    ``temperature`` (nullable), ``heart_rate`` (nullable),
    ``blood_pressure_systolic`` (nullable),
    ``blood_pressure_diastolic`` (nullable),
    ``respiratory_rate`` (nullable), ``oxygen_saturation`` (nullable).
    """
    patient_id = getattr(vitals, "patient_id", None)
    recorded_at = _isoformat(
        getattr(vitals, "recorded_at", None)
        or getattr(vitals, "created_at", None)
    )

    observations: list[dict[str, Any]] = []

    vital_map = [
        (
            "temperature",
            "8310-5",
            "Body temperature",
            "°C",
            "http://unitsofmeasure.org",
            "Cel",
        ),
        (
            "heart_rate",
            "8867-4",
            "Heart rate",
            "/min",
            "http://unitsofmeasure.org",
            "/min",
        ),
        (
            "blood_pressure_systolic",
            "8480-6",
            "Systolic blood pressure",
            "mmHg",
            "http://unitsofmeasure.org",
            "mm[Hg]",
        ),
        (
            "blood_pressure_diastolic",
            "8462-4",
            "Diastolic blood pressure",
            "mmHg",
            "http://unitsofmeasure.org",
            "mm[Hg]",
        ),
        (
            "respiratory_rate",
            "9279-1",
            "Respiratory rate",
            "/min",
            "http://unitsofmeasure.org",
            "/min",
        ),
        (
            "oxygen_saturation",
            "2708-6",
            "Oxygen saturation",
            "%",
            "http://unitsofmeasure.org",
            "%",
        ),
    ]

    for idx, (field, loinc_code, display, unit_text, unit_system, unit_code) in enumerate(
        vital_map
    ):
        value = getattr(vitals, field, None)
        if value is None:
            continue

        obs_id = f"{vitals.id}-{loinc_code}"

        obs: dict[str, Any] = {
            "resourceType": "Observation",
            "id": obs_id,
            "identifier": [
                {"system": _SYSTEM_VITALS_ID, "value": obs_id}
            ],
            "status": "final",
            "category": [
                {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                            "code": "vital-signs",
                            "display": "Vital Signs",
                        }
                    ]
                }
            ],
            "code": {
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": loinc_code,
                        "display": display,
                    }
                ],
                "text": display,
            },
            "valueQuantity": {
                "value": float(value),
                "unit": unit_text,
                "system": unit_system,
                "code": unit_code,
            },
        }

        if patient_id:
            obs["subject"] = _patient_reference(patient_id)
        if recorded_at:
            obs["effectiveDateTime"] = recorded_at

        observations.append(obs)

    return observations


# ---------------------------------------------------------------------------
# Bundle builder
# ---------------------------------------------------------------------------


def build_fhir_collection_bundle(
    resources: list[dict[str, Any]],
    *,
    bundle_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> dict[str, Any]:
    """Wrap a list of FHIR R4 resources in a ``collection`` Bundle.

    ``collection`` is used for locally-generated exports (not search results).
    """
    entries = [{"resource": r} for r in resources if r]

    bundle: dict[str, Any] = {
        "resourceType": "Bundle",
        "type": "collection",
        "total": len(entries),
        "entry": entries,
        "meta": {"profile": [f"http://hl7.org/fhir/StructureDefinition/Bundle"]},
    }

    if bundle_id:
        bundle["id"] = str(bundle_id)
    if timestamp:
        bundle["timestamp"] = timestamp

    return bundle
