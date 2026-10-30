from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from numbers import Number
from typing import Any, Mapping

from app.core.clinical_safety.services.rule_engine_service import (
    ClinicalSafetyEvaluation,
    evaluate_clinical_rules,
    resolve_clinical_rules,
)
from app.core.clinical_safety.services.rule_evaluator import (
    OUTCOME_INFORMATION,
    OUTCOME_SAFE,
    OUTCOME_WARNING,
)
from app.core.enums.clinical_safety_enums import ClinicalRuleType
from app.core.exceptions import ValidationError
from app.modules.prescription.services.prescription_service import (
    check_interactions,
)


_INTERACTION_SEVERITY_RANK = {
    "severe": 0,
    "moderate": 1,
    "mild": 2,
}

_STRUCTURED_ITEM_RULE_TYPES = {
    ClinicalRuleType.MAX_DOSE.value,
    ClinicalRuleType.MIN_DOSE.value,
    ClinicalRuleType.FREQUENCY_LIMIT.value,
    ClinicalRuleType.DURATION_LIMIT.value,
}


@dataclass(frozen=True)
class MedicationSafetyEvaluation:
    clinic_id: int
    medication_ids: tuple[int, ...]
    known_interactions: tuple[dict[str, Any], ...]
    clinical_safety: ClinicalSafetyEvaluation
    outcome: str

    @property
    def blocked(self) -> bool:
        return self.clinical_safety.blocked

    @property
    def requires_acknowledgement(self) -> bool:
        return self.clinical_safety.requires_acknowledgement

    @property
    def requires_justification(self) -> bool:
        return self.clinical_safety.requires_justification

    @property
    def has_interaction_warnings(self) -> bool:
        return bool(self.known_interactions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clinic_id": self.clinic_id,
            "medication_ids": list(self.medication_ids),
            "known_interactions": [
                dict(item)
                for item in self.known_interactions
            ],
            "outcome": self.outcome,
            "blocked": self.blocked,
            "requires_acknowledgement": (
                self.requires_acknowledgement
            ),
            "requires_justification": (
                self.requires_justification
            ),
            "clinical_safety": (
                self.clinical_safety.to_dict()
            ),
        }


def _validate_clinic_id(
    clinic_id: int,
) -> None:
    if (
        isinstance(clinic_id, bool)
        or not isinstance(clinic_id, int)
        or clinic_id <= 0
    ):
        raise ValidationError(
            "Clinic ID must be a positive integer"
        )


def _normalize_medication_ids(
    medication_ids: list[int] | tuple[int, ...] | set[int],
) -> tuple[int, ...]:
    if not isinstance(
        medication_ids,
        (list, tuple, set),
    ):
        raise ValidationError(
            "medication_ids must be a collection"
        )

    normalized: set[int] = set()

    for medication_id in medication_ids:
        if (
            isinstance(medication_id, bool)
            or not isinstance(medication_id, int)
            or medication_id <= 0
        ):
            raise ValidationError(
                "All medication IDs must be positive integers"
            )

        normalized.add(
            medication_id
        )

    return tuple(
        sorted(normalized)
    )


def _normalize_context(
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if context is None:
        return {}

    if not isinstance(context, Mapping):
        raise ValidationError(
            "Medication safety context must be an object"
        )

    return dict(context)


def _normalize_proposed_medication(
    context: dict[str, Any],
) -> dict[str, Any] | None:
    proposed = context.get(
        "proposed_medication"
    )

    if proposed is None:
        return None

    if not isinstance(
        proposed,
        Mapping,
    ):
        raise ValidationError(
            "proposed_medication must be an object"
        )

    normalized = dict(proposed)

    if "drug_id" in normalized:
        drug_id = normalized["drug_id"]

        if (
            isinstance(drug_id, bool)
            or not isinstance(drug_id, int)
            or drug_id <= 0
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

    if "duration_days" not in normalized:
        start_at = normalized.get(
            "start_at"
        )
        end_at = normalized.get(
            "end_at"
        )

        if (
            isinstance(
                start_at,
                datetime,
            )
            and isinstance(
                end_at,
                datetime,
            )
            and start_at.tzinfo is not None
            and start_at.utcoffset() is not None
            and end_at.tzinfo is not None
            and end_at.utcoffset() is not None
            and end_at >= start_at
        ):
            normalized[
                "duration_days"
            ] = (
                end_at - start_at
            ).total_seconds() / 86400.0

    return normalized


def _normalize_context_medications(
    *,
    context: dict[str, Any],
    medication_ids: tuple[int, ...],
) -> dict[str, Any]:
    normalized = dict(context)

    proposed = _normalize_proposed_medication(
        normalized
    )

    resolved_ids = set(
        medication_ids
    )

    existing_context_ids = normalized.get(
        "medication_ids"
    )

    if existing_context_ids is not None:
        if not isinstance(
            existing_context_ids,
            (list, tuple, set),
        ):
            raise ValidationError(
                "Context medication_ids must be a collection"
            )

        resolved_ids.update(
            _normalize_medication_ids(
                existing_context_ids
            )
        )

    normalized["proposed_medication_ids"] = sorted(
        medication_ids
    )

    if proposed is not None:
        proposed_drug_id = proposed.get(
            "drug_id"
        )

        if proposed_drug_id is not None:
            resolved_ids.add(
                proposed_drug_id
            )

        normalized["proposed_medication"] = proposed

    normalized["medication_ids"] = sorted(
        resolved_ids
    )

    return normalized


def _rule_type_value(
    rule: Any,
) -> str | None:
    value = (
        rule.get("rule_type")
        if isinstance(rule, Mapping)
        else getattr(
            rule,
            "rule_type",
            None,
        )
    )

    if value is None:
        return None

    if hasattr(
        value,
        "value",
    ):
        return str(
            value.value
        )

    return str(
        value
    )


def evaluate_structured_medication_items(
    *,
    clinic_id: int,
    medication_ids: list[int] | tuple[int, ...] | set[int],
    context: Mapping[str, Any] | None,
    proposed_medications: list[Mapping[str, Any]],
    department_code: str | None = None,
    evaluation_at: datetime | None = None,
) -> tuple[ClinicalSafetyEvaluation, ...]:
    _validate_clinic_id(
        clinic_id
    )

    normalized_ids = _normalize_medication_ids(
        medication_ids
    )

    if not isinstance(
        proposed_medications,
        list,
    ):
        raise ValidationError(
            "proposed_medications must be a list"
        )

    base_context = _normalize_context(
        context
    )

    base_context = _normalize_context_medications(
        context=base_context,
        medication_ids=normalized_ids,
    )

    resolved_rules = resolve_clinical_rules(
        clinic_id=clinic_id,
        department_code=department_code,
        evaluation_at=evaluation_at,
    )

    item_rules = [
        rule
        for rule in resolved_rules
        if _rule_type_value(
            rule
        ) in _STRUCTURED_ITEM_RULE_TYPES
    ]

    evaluations: list[
        ClinicalSafetyEvaluation
    ] = []

    allowed_ids = set(
        normalized_ids
    )

    for index, proposed in enumerate(
        proposed_medications,
        start=1,
    ):
        if not isinstance(
            proposed,
            Mapping,
        ):
            raise ValidationError(
                f"Proposed medication {index} must be an object"
            )

        normalized_proposed = (
            _normalize_proposed_medication(
                {
                    "proposed_medication": dict(
                        proposed
                    )
                }
            )
        )

        if normalized_proposed is None:
            raise ValidationError(
                f"Proposed medication {index} is required"
            )

        proposed_drug_id = normalized_proposed.get(
            "drug_id"
        )

        if (
            proposed_drug_id is None
            or proposed_drug_id not in allowed_ids
        ):
            raise ValidationError(
                f"Proposed medication {index} drug_id "
                "must be included in medication_ids"
            )

        item_context = dict(
            base_context
        )

        item_context[
            "proposed_medication"
        ] = normalized_proposed

        evaluation = evaluate_clinical_rules(
            clinic_id=clinic_id,
            context=item_context,
            department_code=department_code,
            evaluation_at=evaluation_at,
            rules=item_rules,
        )

        evaluations.append(
            evaluation
        )

    return tuple(
        evaluations
    )


def _normalize_interactions(
    interactions: list[dict],
) -> tuple[dict[str, Any], ...]:
    if not isinstance(
        interactions,
        list,
    ):
        raise ValidationError(
            "Interaction service returned an invalid result"
        )

    normalized: list[dict[str, Any]] = []

    for interaction in interactions:
        if not isinstance(
            interaction,
            Mapping,
        ):
            raise ValidationError(
                "Interaction service returned an invalid interaction"
            )

        drug_a_id = interaction.get(
            "drug_a_id"
        )
        drug_b_id = interaction.get(
            "drug_b_id"
        )
        severity = interaction.get(
            "severity"
        )
        description = interaction.get(
            "description"
        )

        if (
            isinstance(drug_a_id, bool)
            or not isinstance(drug_a_id, int)
            or drug_a_id <= 0
        ):
            raise ValidationError(
                "Interaction drug_a_id is invalid"
            )

        if (
            isinstance(drug_b_id, bool)
            or not isinstance(drug_b_id, int)
            or drug_b_id <= 0
        ):
            raise ValidationError(
                "Interaction drug_b_id is invalid"
            )

        if drug_a_id == drug_b_id:
            raise ValidationError(
                "Interaction cannot reference the same drug twice"
            )

        if not isinstance(
            severity,
            str,
        ):
            raise ValidationError(
                "Interaction severity is invalid"
            )

        normalized_severity = severity.strip().lower()

        if normalized_severity not in _INTERACTION_SEVERITY_RANK:
            raise ValidationError(
                f"Unsupported interaction severity "
                f"'{severity}'"
            )

        if description is not None and not isinstance(
            description,
            str,
        ):
            raise ValidationError(
                "Interaction description is invalid"
            )

        normalized.append(
            {
                "drug_a_id": min(
                    drug_a_id,
                    drug_b_id,
                ),
                "drug_b_id": max(
                    drug_a_id,
                    drug_b_id,
                ),
                "severity": normalized_severity,
                "description": (
                    description.strip()
                    if isinstance(
                        description,
                        str,
                    )
                    else None
                ),
            }
        )

    normalized.sort(
        key=lambda item: (
            _INTERACTION_SEVERITY_RANK[
                item["severity"]
            ],
            item["drug_a_id"],
            item["drug_b_id"],
            item["description"] or "",
        )
    )

    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()

    for interaction in normalized:
        key = (
            interaction["drug_a_id"],
            interaction["drug_b_id"],
        )

        if key in seen:
            continue

        seen.add(key)
        deduplicated.append(
            interaction
        )

    return tuple(
        deduplicated
    )


def _aggregate_outcome(
    clinical_outcome: str,
    known_interactions: tuple[dict[str, Any], ...],
) -> str:
    if clinical_outcome != OUTCOME_SAFE:
        return clinical_outcome

    if known_interactions:
        return OUTCOME_WARNING

    return OUTCOME_SAFE


def evaluate_medication_safety(
    *,
    clinic_id: int,
    medication_ids: list[int] | tuple[int, ...] | set[int],
    context: Mapping[str, Any] | None = None,
    department_code: str | None = None,
    evaluation_at: datetime | None = None,
) -> MedicationSafetyEvaluation:
    _validate_clinic_id(
        clinic_id
    )

    normalized_ids = _normalize_medication_ids(
        medication_ids
    )

    normalized_context = _normalize_context(
        context
    )

    normalized_context = _normalize_context_medications(
        context=normalized_context,
        medication_ids=normalized_ids,
    )

    resolved_medication_ids = tuple(
        normalized_context["medication_ids"]
    )

    interaction_warnings = check_interactions(
        drug_ids=list(
            resolved_medication_ids
        ),
        clinic_id=clinic_id,
    )

    known_interactions = _normalize_interactions(
        interaction_warnings
    )

    clinical_safety = evaluate_clinical_rules(
        clinic_id=clinic_id,
        context=normalized_context,
        department_code=department_code,
        evaluation_at=evaluation_at,
    )

    outcome = _aggregate_outcome(
        clinical_safety.outcome,
        known_interactions,
    )

    return MedicationSafetyEvaluation(
        clinic_id=clinic_id,
        medication_ids=resolved_medication_ids,
        known_interactions=known_interactions,
        clinical_safety=clinical_safety,
        outcome=outcome,
    )