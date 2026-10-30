from __future__ import annotations

import re
from dataclasses import dataclass
from numbers import Number
from datetime import date, datetime, timezone
from typing import Any, Mapping

from sqlalchemy import or_, select

from app.core.enums.consultation_enums import (
    ConsultationStatus,
)
from app.core.enums.lab_enums import (
    LabOrderStatus,
)
from app.core.enums.prescription_enums import (
    PrescriptionStatus,
)
from app.core.exceptions import (
    NotFoundError,
    ValidationError,
)
from app.extensions import db
from app.modules.patient.models.patient_model import (
    Patient,
    PatientAllergy,
    PatientVitals,
)
from app.modules.consultation.models.consultation_model import (
    Consultation,
)
from app.modules.lab.models.lab_model import (
    LabOrder,
    LabOrderItem,
    LabTest,
)
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)


_LEGACY_ALLERGY_SPLIT = re.compile(
    r"[,;\n]+"
)


@dataclass(frozen=True)
class ClinicalSafetyContext:
    clinic_id: int
    patient_id: int
    context: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(
            self.context
        )


def _validate_positive_id(
    value: int,
    field_name: str,
) -> int:
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            int,
        )
        or value <= 0
    ):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )

    return value


def _normalize_evaluation_at(
    value: datetime | None,
) -> datetime:
    if value is None:
        return datetime.now(
            timezone.utc
        )

    if not isinstance(
        value,
        datetime,
    ):
        raise ValidationError(
            "evaluation_at must be a datetime"
        )

    if (
        value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValidationError(
            "evaluation_at must include timezone information"
        )

    return value.astimezone(
        timezone.utc
    )


def _enum_value(
    value,
):
    if value is None:
        return None

    if hasattr(
        value,
        "value",
    ):
        return value.value

    return value


def _age_years(
    *,
    date_of_birth: date | None,
    at: datetime,
) -> int | None:
    if date_of_birth is None:
        return None

    today = at.date()

    age = (
        today.year
        - date_of_birth.year
        - (
            (
                today.month,
                today.day,
            )
            < (
                date_of_birth.month,
                date_of_birth.day,
            )
        )
    )

    return max(
        age,
        0,
    )


def _get_patient(
    *,
    clinic_id: int,
    patient_id: int,
) -> Patient:
    clinic_id = _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    patient_id = _validate_positive_id(
        patient_id,
        "Patient ID",
    )

    patient = db.session.get(
        Patient,
        patient_id,
    )

    if patient is None:
        raise NotFoundError(
            f"Patient {patient_id} not found"
        )

    if patient.clinic_id != clinic_id:
        raise ValidationError(
            "Patient does not belong to clinic"
        )

    return patient


def _structured_allergies(
    *,
    clinic_id: int,
    patient_id: int,
    evaluation_at: datetime,
) -> list[dict[str, Any]]:
    rows = db.session.execute(
        select(
            PatientAllergy
        )
        .where(
            PatientAllergy.clinic_id == clinic_id,
            PatientAllergy.patient_id == patient_id,
            PatientAllergy.clinical_status == "active",
            PatientAllergy.verification_status
            != "refuted",
            PatientAllergy.recorded_at
            <= evaluation_at,
        )
        .order_by(
            PatientAllergy.recorded_at.desc(),
            PatientAllergy.id.desc(),
        )
    ).scalars().all()

    return [
        {
            "id": row.id,
            "allergen": row.allergen_name,
            "name": row.allergen_name,
            "allergen_code": row.allergen_code,
            "allergen_type": row.allergen_type,
            "reaction": row.reaction,
            "severity": row.severity,
            "clinical_status": row.clinical_status,
            "verification_status": (
                row.verification_status
            ),
        }
        for row in rows
    ]


def _legacy_allergies(
    value: str | None,
) -> list[dict[str, Any]]:
    if not isinstance(
        value,
        str,
    ):
        return []

    seen: set[str] = set()
    result: list[dict[str, Any]] = []

    for raw in _LEGACY_ALLERGY_SPLIT.split(
        value
    ):
        normalized = raw.strip()

        if not normalized:
            continue

        key = normalized.casefold()

        if key in seen:
            continue

        seen.add(
            key
        )

        result.append(
            {
                "allergen": normalized,
                "name": normalized,
                "allergen_code": None,
                "allergen_type": "legacy",
                "reaction": None,
                "severity": "unknown",
                "clinical_status": "active",
                "verification_status": (
                    "unconfirmed"
                ),
                "source": "legacy_patient_text",
            }
        )

    return result


def _active_medications(
    *,
    clinic_id: int,
    patient_id: int,
    evaluation_at: datetime,
) -> tuple[
    list[int],
    list[dict[str, Any]],
]:
    rows = db.session.execute(
        select(
            PrescriptionItem
        )
        .join(
            Prescription,
            Prescription.id
            == PrescriptionItem.prescription_id,
        )
        .where(
            Prescription.clinic_id == clinic_id,
            Prescription.patient_id == patient_id,
            Prescription.status
            == PrescriptionStatus.ACTIVE,
            Prescription.issued_at
            <= evaluation_at,
            or_(
                Prescription.expires_at.is_(None),
                Prescription.expires_at
                >= evaluation_at,
            ),
        )
        .order_by(
            Prescription.issued_at.desc(),
            Prescription.id.desc(),
            PrescriptionItem.id.asc(),
        )
    ).scalars().all()

    medication_ids: list[int] = []
    medications: list[dict[str, Any]] = []
    seen: set[int] = set()

    for row in rows:
        if row.drug_id not in seen:
            seen.add(
                row.drug_id
            )
            medication_ids.append(
                row.drug_id
            )

        medications.append(
            {
                "prescription_item_id": row.id,
                "prescription_id": (
                    row.prescription_id
                ),
                "drug_id": row.drug_id,
                "dose_value": (
                    float(row.dose_value)
                    if row.dose_value is not None
                    else None
                ),
                "dose_unit": row.dose_unit,
                "route_code": row.route_code,
                "frequency_code": (
                    row.frequency_code
                ),
                "frequency_interval": (
                    row.frequency_interval
                ),
                "frequency_interval_unit": (
                    row.frequency_interval_unit
                ),
                "is_prn": bool(
                    row.is_prn
                ),
                "order_priority": (
                    row.order_priority
                ),
                "order_type": (
                    row.order_type
                ),
            }
        )

    return (
        medication_ids,
        medications,
    )


def _latest_weight(
    *,
    patient_id: int,
    evaluation_at: datetime,
) -> float | None:
    row = db.session.execute(
        select(
            PatientVitals
        )
        .where(
            PatientVitals.patient_id
            == patient_id,
            PatientVitals.recorded_at
            <= evaluation_at,
        )
        .order_by(
            PatientVitals.recorded_at.desc(),
            PatientVitals.id.desc(),
        )
        .limit(
            1
        )
    ).scalar_one_or_none()

    if (
        row is None
        or row.weight_kg is None
    ):
        return None

    return float(
        row.weight_kg
    )


def _diagnosis_codes(
    *,
    clinic_id: int,
    patient_id: int,
    evaluation_at: datetime,
) -> list[str]:
    rows = db.session.execute(
        select(
            Consultation.icd10_code
        )
        .where(
            Consultation.clinic_id == clinic_id,
            Consultation.patient_id == patient_id,
            Consultation.status
            == ConsultationStatus.COMPLETED,
            Consultation.ended_at.is_not(
                None
            ),
            Consultation.ended_at
            <= evaluation_at,
            Consultation.icd10_code.is_not(
                None
            ),
        )
        .order_by(
            Consultation.id.desc()
        )
    ).scalars().all()

    result: list[str] = []
    seen: set[str] = set()

    for raw in rows:
        if not isinstance(
            raw,
            str,
        ):
            continue

        code = raw.strip().upper()

        if not code:
            continue

        key = code.casefold()

        if key in seen:
            continue

        seen.add(
            key
        )

        result.append(
            code
        )

    return result


def _coerce_lab_value(
    value,
):
    if value is None:
        return None

    text = str(
        value
    ).strip()

    if not text:
        return None

    try:
        return float(
            text.replace(
                ",",
                "",
            )
        )
    except ValueError:
        return text


def _latest_completed_labs(
    *,
    clinic_id: int,
    patient_id: int,
    evaluation_at: datetime,
) -> list[dict[str, Any]]:
    rows = db.session.execute(
        select(
            LabOrderItem,
            LabTest,
        )
        .join(
            LabOrder,
            LabOrder.id
            == LabOrderItem.order_id,
        )
        .join(
            LabTest,
            LabTest.id
            == LabOrderItem.test_id,
        )
        .where(
            LabOrder.clinic_id == clinic_id,
            LabOrder.patient_id == patient_id,
            LabOrder.status
            == LabOrderStatus.COMPLETED,
            LabOrderItem.result_value.is_not(
                None
            ),
            LabOrderItem.resulted_at.is_not(
                None
            ),
            LabOrderItem.resulted_at
            <= evaluation_at,
        )
        .order_by(
            LabOrderItem.resulted_at.desc(),
            LabOrderItem.id.desc(),
        )
    ).all()

    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item, test in rows:
        identity = (
            test.loinc_code
            or test.code
            or test.name
        )

        if not isinstance(
            identity,
            str,
        ):
            continue

        key = identity.strip().casefold()

        if not key or key in seen:
            continue

        seen.add(
            key
        )

        measure = (
            test.name.strip()
            if isinstance(
                test.name,
                str,
            )
            and test.name.strip()
            else identity.strip()
        )

        result.append(
            {
                "measure": measure,
                "value": _coerce_lab_value(
                    item.result_value
                ),
                "unit": test.unit,
                "flag": _enum_value(
                    item.flag
                ),
                "code": test.code,
                "loinc_code": (
                    test.loinc_code
                ),
                "resulted_at": (
                    item.resulted_at.isoformat()
                    if item.resulted_at
                    else None
                ),
            }
        )

    return result


def _lab_aliases(
    lab: Mapping[str, Any],
) -> set[str]:
    aliases: set[str] = set()

    for key in (
        "measure",
        "code",
        "loinc_code",
    ):
        value = lab.get(
            key
        )

        if (
            isinstance(
                value,
                str,
            )
            and value.strip()
        ):
            aliases.add(
                value.strip().casefold()
            )

    return aliases


def _function_values_from_labs(
    labs: list[dict[str, Any]],
    *,
    definitions: Mapping[
        str,
        set[str],
    ],
) -> dict[str, Any]:
    result: dict[str, Any] = {}

    normalized_definitions = {
        key: {
            item.casefold()
            for item in values
        }
        for key, values in definitions.items()
    }

    for lab in labs:
        aliases = _lab_aliases(
            lab
        )

        value = lab.get(
            "value"
        )

        if not isinstance(
            value,
            (int, float),
        ) or isinstance(
            value,
            bool,
        ):
            continue

        for target, accepted in normalized_definitions.items():
            if (
                target in result
                or not (
                    aliases
                    & accepted
                )
            ):
                continue

            result[
                target
            ] = value

    return result


def _renal_function_from_labs(
    labs: list[dict[str, Any]],
) -> dict[str, Any]:
    return _function_values_from_labs(
        labs,
        definitions={
            "creatinine": {
                "creatinine",
                "serum creatinine",
                "creat",
            },
            "egfr": {
                "egfr",
                "estimated glomerular filtration rate",
            },
            "bun": {
                "bun",
                "blood urea nitrogen",
                "urea nitrogen",
            },
        },
    )


def _hepatic_function_from_labs(
    labs: list[dict[str, Any]],
) -> dict[str, Any]:
    return _function_values_from_labs(
        labs,
        definitions={
            "alt": {
                "alt",
                "alanine aminotransferase",
                "sgpt",
            },
            "ast": {
                "ast",
                "aspartate aminotransferase",
                "sgot",
            },
            "alp": {
                "alp",
                "alkaline phosphatase",
            },
            "bilirubin": {
                "bilirubin",
                "total bilirubin",
                "bilirubin total",
            },
            "albumin": {
                "albumin",
            },
        },
    )

def _normalize_proposed_medication(
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None

    if not isinstance(
        value,
        Mapping,
    ):
        raise ValidationError(
            "proposed_medication must be an object"
        )

    normalized = dict(
        value
    )

    drug_id = normalized.get(
        "drug_id"
    )

    if (
        drug_id is not None
        and (
            isinstance(
                drug_id,
                bool,
            )
            or not isinstance(
                drug_id,
                int,
            )
            or drug_id <= 0
        )
    ):
        raise ValidationError(
            "proposed_medication.drug_id "
            "must be a positive integer"
        )

    dose_value = normalized.get(
        "dose_value"
    )

    if (
        "dose" not in normalized
        and isinstance(
            dose_value,
            Number,
        )
        and not isinstance(
            dose_value,
            bool,
        )
    ):
        normalized["dose"] = float(
            dose_value
        )

    scheduled_times = normalized.get(
        "scheduled_times"
    )

    if (
        "frequency_occurrences"
        not in normalized
        and isinstance(
            scheduled_times,
            (list, tuple),
        )
    ):
        normalized[
            "frequency_occurrences"
        ] = len(
            {
                str(item).strip()
                for item in scheduled_times
                if item is not None
                and str(item).strip()
            }
        )

    return normalized


def build_clinical_safety_context(
    *,
    clinic_id: int,
    patient_id: int,
    proposed_medication: (
        Mapping[str, Any]
        | None
    ) = None,
    evaluation_at: datetime | None = None,
) -> ClinicalSafetyContext:
    at = _normalize_evaluation_at(
        evaluation_at
    )

    patient = _get_patient(
        clinic_id=clinic_id,
        patient_id=patient_id,
    )

    allergies = _structured_allergies(
        clinic_id=clinic_id,
        patient_id=patient_id,
        evaluation_at=at,
    )

    legacy_allergies_used = False

    if not allergies:
        allergies = _legacy_allergies(
            patient.allergies
        )

        legacy_allergies_used = bool(
            allergies
        )

    medication_ids, medications = (
        _active_medications(
            clinic_id=clinic_id,
            patient_id=patient_id,
            evaluation_at=at,
        )
    )

    age_years = _age_years(
        date_of_birth=patient.date_of_birth,
        at=at,
    )

    gender = _enum_value(
        patient.gender
    )

    blood_type = _enum_value(
        patient.blood_type
    )

    weight_kg = _latest_weight(
        patient_id=patient.id,
        evaluation_at=at,
    )

    diagnosis_codes = _diagnosis_codes(
        clinic_id=clinic_id,
        patient_id=patient.id,
        evaluation_at=at,
    )

    labs = _latest_completed_labs(
        clinic_id=clinic_id,
        patient_id=patient.id,
        evaluation_at=at,
    )

    renal_function = _renal_function_from_labs(
        labs
    )

    hepatic_function = _hepatic_function_from_labs(
        labs
    )

    context: dict[str, Any] = {
        "clinic_id": clinic_id,
        "patient_id": patient.id,
        "patient_active": bool(
            patient.is_active
        ),
        "patient": {
            "id": patient.id,
            "age_years": age_years,
            "gender": gender,
            "blood_type": blood_type,
        },
        "age_years": age_years,
        "age": age_years,
        "gender": gender,
        "blood_type": blood_type,
        "weight_kg": weight_kg,
        "weight": weight_kg,
        "pregnancy_status": None,
        "diagnosis_codes": diagnosis_codes,
        "contraindication_codes": [],
        "labs": labs,
        "renal_function": renal_function,
        "hepatic_function": hepatic_function,
        "allergies": allergies,
        "allergy_codes": sorted(
            {
                item["allergen_code"]
                for item in allergies
                if item.get(
                    "allergen_code"
                )
            }
        ),
        "legacy_allergies_used": (
            legacy_allergies_used
        ),
        "medication_ids": medication_ids,
        "medications": medications,
        "evaluation_at": at.isoformat(),
    }

    proposed = _normalize_proposed_medication(
        proposed_medication
    )

    if proposed is not None:
        context[
            "proposed_medication"
        ] = proposed

    return ClinicalSafetyContext(
        clinic_id=clinic_id,
        patient_id=patient.id,
        context=context,
    )
