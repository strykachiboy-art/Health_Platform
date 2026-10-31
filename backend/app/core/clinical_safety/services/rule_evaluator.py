from __future__ import annotations

from dataclasses import dataclass, field
from numbers import Number
from typing import Any, Mapping

from app.core.enums.clinical_safety_enums import (
    ClinicalRuleAction,
    ClinicalRuleType,
)
from app.core.exceptions import ValidationError


OUTCOME_SAFE = "safe"
OUTCOME_INFORMATION = "information"
OUTCOME_WARNING = "warning"
OUTCOME_ACKNOWLEDGEMENT_REQUIRED = "acknowledgement_required"
OUTCOME_JUSTIFICATION_REQUIRED = "justification_required"
OUTCOME_BLOCKED = "blocked"


@dataclass(frozen=True)
class RuleEvaluationResult:
    rule_id: int
    rule_code: str
    rule_version: int
    severity: str
    action: str
    priority: int
    hard_rule: bool
    matched: bool
    outcome: str
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.outcome == OUTCOME_BLOCKED

    @property
    def requires_acknowledgement(self) -> bool:
        return self.outcome == OUTCOME_ACKNOWLEDGEMENT_REQUIRED

    @property
    def requires_justification(self) -> bool:
        return self.outcome == OUTCOME_JUSTIFICATION_REQUIRED

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_code": self.rule_code,
            "rule_version": self.rule_version,
            "severity": self.severity,
            "action": self.action,
            "priority": self.priority,
            "hard_rule": self.hard_rule,
            "matched": self.matched,
            "outcome": self.outcome,
            "reason": self.reason,
            "evidence": dict(self.evidence),
        }


def _value(
    rule: Any,
    field_name: str,
    default: Any = None,
) -> Any:
    if isinstance(rule, Mapping):
        return rule.get(field_name, default)

    return getattr(rule, field_name, default)


def _enum_value(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)

    return str(value)


def _normalize_rule(rule: Any) -> dict[str, Any]:
    return {
        "id": _value(rule, "id"),
        "rule_code": _value(rule, "rule_code"),
        "version": _value(rule, "version"),
        "severity": _enum_value(_value(rule, "severity")),
        "action": _enum_value(_value(rule, "action")),
        "rule_type": _enum_value(_value(rule, "rule_type")),
        "priority": _value(rule, "priority", 100),
        "is_hard_rule": bool(
            _value(rule, "is_hard_rule", False)
        ),
        "conditions": _value(rule, "conditions") or {},
        "configuration": _value(rule, "configuration") or {},
    }


def _validate_rule_identity(
    rule: dict[str, Any],
) -> None:
    rule_id = rule["id"]

    if (
        isinstance(rule_id, bool)
        or not isinstance(rule_id, int)
        or rule_id <= 0
    ):
        raise ValidationError(
            "Clinical rule ID must be a positive integer"
        )

    if not isinstance(rule["rule_code"], str) or not rule["rule_code"].strip():
        raise ValidationError(
            "Clinical rule code is required"
        )

    if (
        isinstance(rule["version"], bool)
        or not isinstance(rule["version"], int)
        or rule["version"] <= 0
    ):
        raise ValidationError(
            "Clinical rule version must be a positive integer"
        )

    if (
        isinstance(rule["priority"], bool)
        or not isinstance(rule["priority"], int)
        or rule["priority"] < 0
    ):
        raise ValidationError(
            "Clinical rule priority must be a non-negative integer"
        )

    if not isinstance(rule["conditions"], Mapping):
        raise ValidationError(
            "Clinical rule conditions must be an object"
        )

    if not isinstance(rule["configuration"], Mapping):
        raise ValidationError(
            "Clinical rule configuration must be an object"
        )


def _context_value(
    context: Mapping[str, Any],
    path: str,
) -> Any:
    current: Any = context

    for part in path.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return None

            current = current[part]
            continue

        if isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (TypeError, ValueError, IndexError):
                return None
            continue

        return None

    return current


def _compare(
    actual: Any,
    operator: str,
    expected: Any,
) -> bool:
    operator = operator.lower()

    if operator == "eq":
        return actual == expected

    if operator == "ne":
        return actual != expected

    if operator == "in":
        if not isinstance(expected, (list, tuple, set, frozenset)):
            raise ValidationError(
                "Operator 'in' requires a collection"
            )
        return actual in expected

    if operator == "not_in":
        if not isinstance(expected, (list, tuple, set, frozenset)):
            raise ValidationError(
                "Operator 'not_in' requires a collection"
            )
        return actual not in expected

    if operator == "contains":
        if isinstance(actual, str):
            return str(expected).lower() in actual.lower()

        if isinstance(actual, (list, tuple, set, frozenset)):
            return expected in actual

        return False

    if operator == "not_contains":
        if isinstance(actual, str):
            return str(expected).lower() not in actual.lower()

        if isinstance(actual, (list, tuple, set, frozenset)):
            return expected not in actual

        return True

    if operator in {"gt", "gte", "lt", "lte"}:
        if not isinstance(actual, Number) or isinstance(actual, bool):
            return False

        if not isinstance(expected, Number) or isinstance(expected, bool):
            raise ValidationError(
                f"Operator '{operator}' requires a numeric threshold"
            )

        if operator == "gt":
            return actual > expected

        if operator == "gte":
            return actual >= expected

        if operator == "lt":
            return actual < expected

        return actual <= expected

    if operator == "between":
        if not isinstance(expected, (list, tuple)) or len(expected) != 2:
            raise ValidationError(
                "Operator 'between' requires exactly two values"
            )

        if (
            not isinstance(actual, Number)
            or isinstance(actual, bool)
            or not all(
                isinstance(item, Number)
                and not isinstance(item, bool)
                for item in expected
            )
        ):
            return False

        return expected[0] <= actual <= expected[1]

    if operator == "exists":
        expected_bool = bool(expected)
        return (actual is not None) is expected_bool

    raise ValidationError(
        f"Unsupported clinical rule operator '{operator}'"
    )


def _context_path_state(
    context: Mapping[str, Any],
    path: str,
) -> tuple[bool, Any]:
    current: Any = context

    for part in path.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return False, None
            current = current[part]
            continue

        if isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (TypeError, ValueError, IndexError):
                return False, None
            continue

        return False, None

    return True, current


def _condition_context_dependencies(
    conditions: Mapping[str, Any],
) -> list[tuple[str, str]]:
    dependencies: list[tuple[str, str]] = []

    def visit(value: Any) -> None:
        if not isinstance(value, Mapping):
            return

        field_name = value.get("field")
        operator = value.get("operator")

        if (
            isinstance(field_name, str)
            and field_name.strip()
            and isinstance(operator, str)
            and operator.strip()
        ):
            dependencies.append(
                (
                    field_name.strip(),
                    operator.strip().lower(),
                )
            )

        for key in ("all", "any"):
            nested = value.get(key)
            if isinstance(nested, list):
                for item in nested:
                    visit(item)

    visit(conditions)
    return dependencies


def _has_collection(
    context: Mapping[str, Any],
    key: str,
) -> bool:
    present, value = _context_path_state(context, key)
    return present and isinstance(
        value,
        (list, tuple, set, frozenset),
    )


def _missing_context_dependencies(
    rule: Mapping[str, Any],
    context: Mapping[str, Any],
) -> list[str]:
    """
    Detect unavailable clinical context before evaluating a rule.

    Empty known collections are considered evaluated "none present".
    Missing scalars / missing configured measures are unavailable.
    """
    missing: set[str] = set()

    for field_name, operator in _condition_context_dependencies(
        rule["conditions"]
    ):
        if operator == "exists":
            continue

        present, value = _context_path_state(
            context,
            field_name,
        )
        if not present or value is None:
            missing.add(field_name)

    rule_type = rule["rule_type"]
    configuration = rule["configuration"]

    medication_context_available = (
        _has_collection(context, "medication_ids")
        or _has_collection(context, "medications")
    )

    if rule_type == ClinicalRuleType.DRUG_INTERACTION.value:
        if not medication_context_available:
            missing.add("medication_ids")

    elif rule_type == ClinicalRuleType.ALLERGY_CONFLICT.value:
        if not _has_collection(context, "allergies"):
            missing.add("allergies")

    elif rule_type == ClinicalRuleType.CONTRAINDICATION.value:
        if not _has_collection(
            context,
            "contraindication_codes",
        ):
            missing.add("contraindication_codes")

    elif rule_type in {
        ClinicalRuleType.MAX_DOSE.value,
        ClinicalRuleType.MIN_DOSE.value,
    }:
        present, proposed = _context_path_state(
            context,
            "proposed_medication",
        )

        if not present or not isinstance(proposed, Mapping):
            missing.add("proposed_medication")
        else:
            proposed_drug_id = proposed.get("drug_id")
            configured_drug_id = configuration.get("drug_id")

            if proposed_drug_id is None:
                missing.add("proposed_medication.drug_id")
            elif proposed_drug_id == configured_drug_id:
                dose = proposed.get("dose")
                if (
                    not isinstance(dose, Number)
                    or isinstance(dose, bool)
                ):
                    missing.add("proposed_medication.dose")

    elif rule_type == ClinicalRuleType.AGE_RESTRICTION.value:
        age = context.get("age", context.get("age_years"))
        if not isinstance(age, Number) or isinstance(age, bool):
            missing.add("age")

    elif rule_type == ClinicalRuleType.WEIGHT_RESTRICTION.value:
        weight = context.get(
            "weight",
            context.get("weight_kg"),
        )
        if not isinstance(weight, Number) or isinstance(weight, bool):
            missing.add("weight")

    elif rule_type == ClinicalRuleType.PREGNANCY_RESTRICTION.value:
        status = context.get("pregnancy_status")
        if status is None or (
            isinstance(status, str)
            and not status.strip()
        ):
            missing.add("pregnancy_status")

    elif rule_type in {
        ClinicalRuleType.DUPLICATE_THERAPY.value,
        ClinicalRuleType.THERAPEUTIC_DUPLICATION.value,
    }:
        configured_drug_ids = configuration.get("drug_ids")

        if isinstance(configured_drug_ids, list):
            if not medication_context_available:
                missing.add("medication_ids")
        elif not _has_collection(context, "medications"):
            missing.add("medications")

    elif rule_type == ClinicalRuleType.LAB_CONFLICT.value:
        labs = context.get("labs")
        measure = configuration.get("measure")

        if not isinstance(labs, (list, tuple)):
            missing.add("labs")
        elif isinstance(measure, str) and measure.strip():
            target = measure.strip().lower()

            found_measure = False
            for lab in labs:
                if not isinstance(lab, Mapping):
                    continue

                if (
                    str(lab.get("measure", "")).strip().lower()
                    == target
                    and lab.get("value") is not None
                ):
                    found_measure = True
                    break

            if not found_measure:
                missing.add(f"labs.{measure}")

    elif rule_type in {
        ClinicalRuleType.RENAL_FUNCTION.value,
        ClinicalRuleType.HEPATIC_FUNCTION.value,
    }:
        context_key = (
            "renal_function"
            if rule_type == ClinicalRuleType.RENAL_FUNCTION.value
            else "hepatic_function"
        )
        measure = configuration.get("measure")
        function_values = context.get(context_key)

        if not isinstance(function_values, Mapping):
            missing.add(context_key)
        elif (
            isinstance(measure, str)
            and measure.strip()
            and function_values.get(measure) is None
        ):
            missing.add(f"{context_key}.{measure}")

    elif rule_type == ClinicalRuleType.DIAGNOSIS_CONFLICT.value:
        if not _has_collection(context, "diagnosis_codes"):
            missing.add("diagnosis_codes")

    elif rule_type == ClinicalRuleType.FREQUENCY_LIMIT.value:
        proposed = context.get("proposed_medication")

        if isinstance(proposed, Mapping):
            occurrences = proposed.get(
                "frequency_occurrences",
                context.get("frequency_occurrences"),
            )
        else:
            occurrences = context.get("frequency_occurrences")

        if not isinstance(occurrences, int) or isinstance(
            occurrences,
            bool,
        ):
            missing.add("frequency_occurrences")

    elif rule_type == ClinicalRuleType.DURATION_LIMIT.value:
        proposed = context.get("proposed_medication")

        if isinstance(proposed, Mapping):
            duration_days = proposed.get(
                "duration_days",
                context.get("duration_days"),
            )
        else:
            duration_days = context.get("duration_days")

        if not isinstance(duration_days, Number) or isinstance(
            duration_days,
            bool,
        ):
            missing.add("duration_days")

    elif (
        rule_type
        == ClinicalRuleType.PATIENT_SPECIFIC_RESTRICTION.value
    ):
        field_name = configuration.get("field")
        operator = configuration.get("operator")

        if (
            isinstance(field_name, str)
            and field_name.strip()
            and str(operator).strip().lower() != "exists"
        ):
            present, value = _context_path_state(
                context,
                field_name.strip(),
            )
            if not present or value is None:
                missing.add(field_name.strip())

    return sorted(missing)


def _missing_context_result(
    rule: Mapping[str, Any],
    missing_context: list[str],
) -> RuleEvaluationResult:
    if rule["is_hard_rule"]:
        return RuleEvaluationResult(
            rule_id=rule["id"],
            rule_code=rule["rule_code"],
            rule_version=rule["version"],
            severity=rule["severity"],
            action=rule["action"],
            priority=rule["priority"],
            hard_rule=True,
            matched=True,
            outcome=OUTCOME_BLOCKED,
            reason=(
                "Hard clinical safety rule could not be evaluated "
                "because required clinical context is unavailable"
            ),
            evidence={
                "evaluated": False,
                "missing_context": list(missing_context),
                "context_policy": "hard_fail_closed",
            },
        )

    return RuleEvaluationResult(
        rule_id=rule["id"],
        rule_code=rule["rule_code"],
        rule_version=rule["version"],
        severity=rule["severity"],
        action=rule["action"],
        priority=rule["priority"],
        hard_rule=False,
        matched=False,
        outcome=OUTCOME_SAFE,
        reason=(
            "Soft clinical safety rule was not evaluated "
            "because required clinical context is unavailable"
        ),
        evidence={
            "evaluated": False,
            "missing_context": list(missing_context),
            "context_policy": "soft_not_evaluated",
        },
    )


def _evaluate_conditions(
    conditions: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[bool, dict[str, Any]]:
    if not conditions:
        return True, {}

    evidence: dict[str, Any] = {}

    if "all" in conditions:
        checks = conditions["all"]

        if not isinstance(checks, list):
            raise ValidationError(
                "Clinical rule 'all' conditions must be a list"
            )

        results = []

        for condition in checks:
            results.append(
                _evaluate_single_condition(
                    condition,
                    context,
                )
            )

        matched = all(result[0] for result in results)

        evidence["all"] = [
            result[1]
            for result in results
        ]

        if not matched:
            return False, evidence

    if "any" in conditions:
        checks = conditions["any"]

        if not isinstance(checks, list):
            raise ValidationError(
                "Clinical rule 'any' conditions must be a list"
            )

        results = []

        for condition in checks:
            results.append(
                _evaluate_single_condition(
                    condition,
                    context,
                )
            )

        matched = any(result[0] for result in results)

        evidence["any"] = [
            result[1]
            for result in results
        ]

        if not matched:
            return False, evidence

    if "field" in conditions:
        matched, condition_evidence = (
            _evaluate_single_condition(
                conditions,
                context,
            )
        )

        evidence["condition"] = condition_evidence

        if not matched:
            return False, evidence

    return True, evidence


def _evaluate_single_condition(
    condition: Any,
    context: Mapping[str, Any],
) -> tuple[bool, dict[str, Any]]:
    if not isinstance(condition, Mapping):
        raise ValidationError(
            "Clinical rule condition must be an object"
        )

    field_name = condition.get("field")
    operator = condition.get("operator")
    expected = condition.get("value")

    if not isinstance(field_name, str) or not field_name.strip():
        raise ValidationError(
            "Clinical rule condition field is required"
        )

    if not isinstance(operator, str) or not operator.strip():
        raise ValidationError(
            "Clinical rule condition operator is required"
        )

    actual = _context_value(
        context,
        field_name,
    )

    matched = _compare(
        actual,
        operator,
        expected,
    )

    return matched, {
        "field": field_name,
        "operator": operator,
        "expected": expected,
        "actual": actual,
        "matched": matched,
    }


def _medication_ids(
    context: Mapping[str, Any],
) -> list[int]:
    values = context.get("medication_ids")

    if isinstance(values, (list, tuple, set, frozenset)):
        return [
            value
            for value in values
            if isinstance(value, int)
            and not isinstance(value, bool)
        ]

    medications = context.get("medications", [])

    if not isinstance(medications, (list, tuple)):
        return []

    ids: list[int] = []

    for medication in medications:
        if isinstance(medication, Mapping):
            drug_id = medication.get("drug_id")
        else:
            drug_id = getattr(
                medication,
                "drug_id",
                None,
            )

        if (
            isinstance(drug_id, int)
            and not isinstance(drug_id, bool)
        ):
            ids.append(drug_id)

    return ids


def _proposed_medication(
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    medication = context.get(
        "proposed_medication",
        {},
    )

    if medication is None:
        return {}

    if not isinstance(medication, Mapping):
        raise ValidationError(
            "Proposed medication must be an object"
        )

    return medication


def _normalized_strings(
    value: Any,
) -> set[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()

    return {
        str(item).strip().lower()
        for item in value
        if item is not None
        and str(item).strip()
    }


def _evaluate_drug_interaction(
    configuration: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[bool, dict[str, Any], str]:
    drug_a_id = configuration.get("drug_a_id")
    drug_b_id = configuration.get("drug_b_id")

    medication_ids = set(
        _medication_ids(context)
    )

    matched = (
        isinstance(drug_a_id, int)
        and isinstance(drug_b_id, int)
        and drug_a_id != drug_b_id
        and drug_a_id in medication_ids
        and drug_b_id in medication_ids
    )

    return (
        matched,
        {
            "drug_a_id": drug_a_id,
            "drug_b_id": drug_b_id,
            "medication_ids": sorted(
                medication_ids
            ),
        },
        "Configured drug interaction detected"
        if matched
        else "Configured drug interaction not detected",
    )


def _evaluate_allergy_conflict(
    configuration: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[bool, dict[str, Any], str]:
    allergen = configuration.get("allergen")
    target_drug_id = configuration.get("drug_id")

    if not isinstance(allergen, str) or not allergen.strip():
        raise ValidationError(
            "Allergy conflict rule requires an allergen"
        )

    normalized_allergen = allergen.strip().lower()
    allergies = context.get("allergies", [])

    matched_allergy = None

    if isinstance(allergies, (list, tuple)):
        for allergy in allergies:
            if isinstance(allergy, Mapping):
                candidate = allergy.get(
                    "allergen",
                    allergy.get("name"),
                )
                drug_id = allergy.get("drug_id")
            else:
                candidate = str(allergy)
                drug_id = None

            if (
                isinstance(candidate, str)
                and candidate.strip().lower()
                == normalized_allergen
            ):
                if (
                    target_drug_id is None
                    or drug_id == target_drug_id
                ):
                    matched_allergy = allergy
                    break

    matched = matched_allergy is not None

    return (
        matched,
        {
            "allergen": allergen,
            "drug_id": target_drug_id,
            "matched_allergy": matched_allergy,
        },
        "Configured allergy conflict detected"
        if matched
        else "Configured allergy conflict not detected",
    )


def _evaluate_code_intersection(
    configuration: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    configuration_key: str,
    context_key: str,
    description: str,
) -> tuple[bool, dict[str, Any], str]:
    expected = _normalized_strings(
        configuration.get(configuration_key)
    )
    actual = _normalized_strings(
        context.get(context_key)
    )

    matched_values = sorted(
        expected & actual
    )

    matched = bool(matched_values)

    return (
        matched,
        {
            "expected": sorted(expected),
            "actual": sorted(actual),
            "matched": matched_values,
        },
        f"{description} detected"
        if matched
        else f"{description} not detected",
    )


def _evaluate_dose(
    configuration: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    minimum: bool,
) -> tuple[bool, dict[str, Any], str]:
    proposed = _proposed_medication(context)

    drug_id = configuration.get("drug_id")
    threshold = configuration.get("threshold")
    dose = proposed.get("dose")

    if (
        isinstance(drug_id, bool)
        or not isinstance(drug_id, int)
        or drug_id <= 0
    ):
        raise ValidationError(
            "Dose rule requires a positive drug_id"
        )

    if (
        not isinstance(threshold, Number)
        or isinstance(threshold, bool)
        or threshold <= 0
    ):
        raise ValidationError(
            "Dose rule threshold must be positive"
        )

    if (
        proposed.get("drug_id") != drug_id
        or not isinstance(dose, Number)
        or isinstance(dose, bool)
    ):
        return (
            False,
            {
                "drug_id": drug_id,
                "dose": dose,
                "threshold": threshold,
            },
            "Dose rule did not apply to the proposed medication",
        )

    if minimum:
        matched = dose < threshold
        description = "Minimum dose violation"
    else:
        matched = dose > threshold
        description = "Maximum dose violation"

    return (
        matched,
        {
            "drug_id": drug_id,
            "dose": dose,
            "threshold": threshold,
            "unit": configuration.get("unit"),
            "frequency": configuration.get("frequency"),
        },
        description
        if matched
        else "Dose is within configured limits",
    )


def _evaluate_age_restriction(
    configuration: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[bool, dict[str, Any], str]:
    age = context.get("age")

    if (
        not isinstance(age, Number)
        or isinstance(age, bool)
    ):
        return (
            False,
            {"age": age},
            "Patient age is unavailable",
        )

    min_age = configuration.get("min_age")
    max_age = configuration.get("max_age")

    matched = False

    if (
        isinstance(min_age, Number)
        and age < min_age
    ):
        matched = True

    if (
        isinstance(max_age, Number)
        and age > max_age
    ):
        matched = True

    return (
        matched,
        {
            "age": age,
            "min_age": min_age,
            "max_age": max_age,
        },
        "Patient age violates restriction"
        if matched
        else "Patient age is within configured limits",
    )


def _evaluate_weight_restriction(
    configuration: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[bool, dict[str, Any], str]:
    weight = context.get("weight")

    if (
        not isinstance(weight, Number)
        or isinstance(weight, bool)
    ):
        return (
            False,
            {"weight": weight},
            "Patient weight is unavailable",
        )

    min_weight = configuration.get("min_weight")
    max_weight = configuration.get("max_weight")

    matched = False

    if (
        isinstance(min_weight, Number)
        and weight < min_weight
    ):
        matched = True

    if (
        isinstance(max_weight, Number)
        and weight > max_weight
    ):
        matched = True

    return (
        matched,
        {
            "weight": weight,
            "min_weight": min_weight,
            "max_weight": max_weight,
        },
        "Patient weight violates restriction"
        if matched
        else "Patient weight is within configured limits",
    )


def _evaluate_pregnancy_restriction(
    configuration: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[bool, dict[str, Any], str]:
    status = context.get("pregnancy_status")

    restricted_statuses = _normalized_strings(
        configuration.get("restricted_statuses")
    )

    actual = (
        str(status).strip().lower()
        if status is not None
        else None
    )

    matched = (
        actual is not None
        and actual in restricted_statuses
    )

    return (
        matched,
        {
            "pregnancy_status": status,
            "restricted_statuses": sorted(
                restricted_statuses
            ),
        },
        "Pregnancy restriction detected"
        if matched
        else "Pregnancy restriction not detected",
    )


def _evaluate_duplicate_therapy(
    configuration: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[bool, dict[str, Any], str]:
    configured_drug_ids = configuration.get("drug_ids")

    if isinstance(configured_drug_ids, list):
        ids = [
            item
            for item in configured_drug_ids
            if isinstance(item, int)
            and not isinstance(item, bool)
        ]

        present = [
            drug_id
            for drug_id in ids
            if drug_id in _medication_ids(context)
        ]

        matched = len(set(present)) >= 2

        return (
            matched,
            {
                "configured_drug_ids": ids,
                "present_drug_ids": sorted(
                    set(present)
                ),
            },
            "Duplicate therapy detected"
            if matched
            else "Duplicate therapy not detected",
        )

    therapy_group = configuration.get(
        "therapy_group"
    )

    if not isinstance(therapy_group, str) or not therapy_group.strip():
        raise ValidationError(
            "Duplicate therapy rule requires therapy_group or drug_ids"
        )

    medications = context.get(
        "medications",
        [],
    )

    matches = []

    if isinstance(medications, (list, tuple)):
        for medication in medications:
            if not isinstance(medication, Mapping):
                continue

            if (
                medication.get("therapy_group")
                == therapy_group
            ):
                matches.append(medication)

    matched = len(matches) >= 2

    return (
        matched,
        {
            "therapy_group": therapy_group,
            "matching_medications": matches,
        },
        "Duplicate therapy detected"
        if matched
        else "Duplicate therapy not detected",
    )


def _evaluate_lab_conflict(
    configuration: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[bool, dict[str, Any], str]:
    measure = configuration.get("measure")
    operator = configuration.get("operator")
    threshold = configuration.get("threshold")

    labs = context.get("labs", [])

    if not isinstance(measure, str) or not measure.strip():
        raise ValidationError(
            "Lab conflict rule requires a measure"
        )

    if not isinstance(operator, str) or not operator.strip():
        raise ValidationError(
            "Lab conflict rule requires an operator"
        )

    if not isinstance(labs, (list, tuple)):
        return (
            False,
            {"labs": labs},
            "No laboratory results available",
        )

    matches = []

    for lab in labs:
        if not isinstance(lab, Mapping):
            continue

        if (
            str(lab.get("measure", "")).strip().lower()
            != measure.strip().lower()
        ):
            continue

        value = lab.get("value")

        if _compare(
            value,
            operator,
            threshold,
        ):
            matches.append(lab)

    matched = bool(matches)

    return (
        matched,
        {
            "measure": measure,
            "operator": operator,
            "threshold": threshold,
            "matched_labs": matches,
        },
        "Laboratory conflict detected"
        if matched
        else "Laboratory conflict not detected",
    )


def _evaluate_function_restriction(
    configuration: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    context_key: str,
    description: str,
) -> tuple[bool, dict[str, Any], str]:
    measure = configuration.get("measure")
    operator = configuration.get("operator")
    threshold = configuration.get("threshold")

    actual = context.get(context_key)

    if not isinstance(measure, str) or not measure.strip():
        raise ValidationError(
            f"{description} rule requires a measure"
        )

    if not isinstance(operator, str) or not operator.strip():
        raise ValidationError(
            f"{description} rule requires an operator"
        )

    if isinstance(actual, Mapping):
        actual = actual.get(measure)

    if actual is None:
        return (
            False,
            {
                "measure": measure,
                "actual": None,
                "threshold": threshold,
            },
            f"{description} value is unavailable",
        )

    matched = _compare(
        actual,
        operator,
        threshold,
    )

    return (
        matched,
        {
            "measure": measure,
            "actual": actual,
            "operator": operator,
            "threshold": threshold,
        },
        f"{description} restriction detected"
        if matched
        else f"{description} restriction not detected",
    )


def _evaluate_frequency_limit(
    configuration: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[bool, dict[str, Any], str]:
    proposed = _proposed_medication(context)

    max_occurrences = configuration.get(
        "max_occurrences"
    )
    interval_hours = configuration.get(
        "interval_hours"
    )

    occurrences = proposed.get(
        "frequency_occurrences",
        context.get("frequency_occurrences"),
    )

    if (
        not isinstance(max_occurrences, int)
        or isinstance(max_occurrences, bool)
        or max_occurrences <= 0
    ):
        raise ValidationError(
            "Frequency limit requires positive max_occurrences"
        )

    if (
        not isinstance(interval_hours, int)
        or isinstance(interval_hours, bool)
        or interval_hours <= 0
    ):
        raise ValidationError(
            "Frequency limit requires positive interval_hours"
        )

    if (
        not isinstance(occurrences, int)
        or isinstance(occurrences, bool)
    ):
        return (
            False,
            {
                "occurrences": occurrences,
                "max_occurrences": max_occurrences,
                "interval_hours": interval_hours,
            },
            "Frequency occurrence data is unavailable",
        )

    matched = occurrences > max_occurrences

    return (
        matched,
        {
            "occurrences": occurrences,
            "max_occurrences": max_occurrences,
            "interval_hours": interval_hours,
        },
        "Frequency limit exceeded"
        if matched
        else "Frequency is within configured limit",
    )


def _evaluate_duration_limit(
    configuration: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[bool, dict[str, Any], str]:
    proposed = _proposed_medication(context)

    maximum_days = configuration.get(
        "maximum_days"
    )
    duration_days = proposed.get(
        "duration_days",
        context.get("duration_days"),
    )

    if (
        not isinstance(maximum_days, Number)
        or isinstance(maximum_days, bool)
        or maximum_days <= 0
    ):
        raise ValidationError(
            "Duration limit requires positive maximum_days"
        )

    if (
        not isinstance(duration_days, Number)
        or isinstance(duration_days, bool)
    ):
        return (
            False,
            {
                "duration_days": duration_days,
                "maximum_days": maximum_days,
            },
            "Duration data is unavailable",
        )

    matched = duration_days > maximum_days

    return (
        matched,
        {
            "duration_days": duration_days,
            "maximum_days": maximum_days,
        },
        "Duration limit exceeded"
        if matched
        else "Duration is within configured limit",
    )


def _evaluate_patient_specific_restriction(
    configuration: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[bool, dict[str, Any], str]:
    field_name = configuration.get("field")
    operator = configuration.get("operator")
    expected = configuration.get("value")

    matched, evidence = _evaluate_single_condition(
        {
            "field": field_name,
            "operator": operator,
            "value": expected,
        },
        context,
    )

    return (
        matched,
        evidence,
        "Patient-specific restriction detected"
        if matched
        else "Patient-specific restriction not detected",
    )


def _evaluate_by_type(
    rule_type: str,
    configuration: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[bool, dict[str, Any], str]:
    if rule_type == ClinicalRuleType.DRUG_INTERACTION.value:
        return _evaluate_drug_interaction(
            configuration,
            context,
        )

    if rule_type == ClinicalRuleType.ALLERGY_CONFLICT.value:
        return _evaluate_allergy_conflict(
            configuration,
            context,
        )

    if rule_type == ClinicalRuleType.CONTRAINDICATION.value:
        return _evaluate_code_intersection(
            configuration,
            context,
            configuration_key="contraindication_codes",
            context_key="contraindication_codes",
            description="Contraindication",
        )

    if rule_type == ClinicalRuleType.MAX_DOSE.value:
        return _evaluate_dose(
            configuration,
            context,
            minimum=False,
        )

    if rule_type == ClinicalRuleType.MIN_DOSE.value:
        return _evaluate_dose(
            configuration,
            context,
            minimum=True,
        )

    if rule_type == ClinicalRuleType.AGE_RESTRICTION.value:
        return _evaluate_age_restriction(
            configuration,
            context,
        )

    if rule_type == ClinicalRuleType.WEIGHT_RESTRICTION.value:
        return _evaluate_weight_restriction(
            configuration,
            context,
        )

    if rule_type == ClinicalRuleType.PREGNANCY_RESTRICTION.value:
        return _evaluate_pregnancy_restriction(
            configuration,
            context,
        )

    if rule_type in {
        ClinicalRuleType.DUPLICATE_THERAPY.value,
        ClinicalRuleType.THERAPEUTIC_DUPLICATION.value,
    }:
        return _evaluate_duplicate_therapy(
            configuration,
            context,
        )

    if rule_type == ClinicalRuleType.LAB_CONFLICT.value:
        return _evaluate_lab_conflict(
            configuration,
            context,
        )

    if rule_type == ClinicalRuleType.RENAL_FUNCTION.value:
        return _evaluate_function_restriction(
            configuration,
            context,
            context_key="renal_function",
            description="Renal function",
        )

    if rule_type == ClinicalRuleType.HEPATIC_FUNCTION.value:
        return _evaluate_function_restriction(
            configuration,
            context,
            context_key="hepatic_function",
            description="Hepatic function",
        )

    if rule_type == ClinicalRuleType.DIAGNOSIS_CONFLICT.value:
        return _evaluate_code_intersection(
            configuration,
            context,
            configuration_key="diagnosis_codes",
            context_key="diagnosis_codes",
            description="Diagnosis conflict",
        )

    if rule_type == ClinicalRuleType.FREQUENCY_LIMIT.value:
        return _evaluate_frequency_limit(
            configuration,
            context,
        )

    if rule_type == ClinicalRuleType.DURATION_LIMIT.value:
        return _evaluate_duration_limit(
            configuration,
            context,
        )

    if rule_type == ClinicalRuleType.PATIENT_SPECIFIC_RESTRICTION.value:
        return _evaluate_patient_specific_restriction(
            configuration,
            context,
        )

    raise ValidationError(
        f"Unsupported clinical rule type '{rule_type}'"
    )


def _outcome_for_action(
    action: str,
) -> str:
    if action == ClinicalRuleAction.INFORM.value:
        return OUTCOME_INFORMATION

    if action == ClinicalRuleAction.ALERT.value:
        return OUTCOME_WARNING

    if action == ClinicalRuleAction.REQUIRE_ACKNOWLEDGEMENT.value:
        return OUTCOME_ACKNOWLEDGEMENT_REQUIRED

    if action == ClinicalRuleAction.REQUIRE_JUSTIFICATION.value:
        return OUTCOME_JUSTIFICATION_REQUIRED

    if action == ClinicalRuleAction.BLOCK.value:
        return OUTCOME_BLOCKED

    raise ValidationError(
        f"Unsupported clinical rule action '{action}'"
    )


def _safe_invalid_rule_result(
    rule: dict[str, Any],
    error: Exception,
) -> RuleEvaluationResult:
    return RuleEvaluationResult(
        rule_id=rule["id"],
        rule_code=rule["rule_code"],
        rule_version=rule["version"],
        severity=rule["severity"],
        action=rule["action"],
        priority=rule["priority"],
        hard_rule=True,
        matched=True,
        outcome=OUTCOME_BLOCKED,
        reason=(
            "Hard clinical safety rule could not be evaluated safely"
        ),
        evidence={
            "evaluated": False,
            "missing_context": [],
            "context_policy": "hard_configuration_fail_closed",
            "error": str(error),
            "configuration_error": True,
        },
    )


def evaluate_rule(
    rule: Any,
    context: Mapping[str, Any],
) -> RuleEvaluationResult:
    if not isinstance(context, Mapping):
        raise ValidationError(
            "Clinical evaluation context must be an object"
        )

    normalized_rule = _normalize_rule(
        rule
    )

    _validate_rule_identity(
        normalized_rule
    )

    missing_context = _missing_context_dependencies(
        normalized_rule,
        context,
    )

    if missing_context:
        return _missing_context_result(
            normalized_rule,
            missing_context,
        )

    try:
        action = normalized_rule["action"]
        rule_type = normalized_rule["rule_type"]

        condition_match, condition_evidence = (
            _evaluate_conditions(
                normalized_rule["conditions"],
                context,
            )
        )

        if not condition_match:
            return RuleEvaluationResult(
                rule_id=normalized_rule["id"],
                rule_code=normalized_rule["rule_code"],
                rule_version=normalized_rule["version"],
                severity=normalized_rule["severity"],
                action=action,
                priority=normalized_rule["priority"],
                hard_rule=normalized_rule["is_hard_rule"],
                matched=False,
                outcome=OUTCOME_SAFE,
                reason="Clinical rule conditions did not match",
                evidence={
                    "evaluated": True,
                    "missing_context": [],
                    "conditions": condition_evidence,
                },
            )

        matched, type_evidence, reason = _evaluate_by_type(
            rule_type,
            normalized_rule["configuration"],
            context,
        )

        if not matched:
            return RuleEvaluationResult(
                rule_id=normalized_rule["id"],
                rule_code=normalized_rule["rule_code"],
                rule_version=normalized_rule["version"],
                severity=normalized_rule["severity"],
                action=action,
                priority=normalized_rule["priority"],
                hard_rule=normalized_rule["is_hard_rule"],
                matched=False,
                outcome=OUTCOME_SAFE,
                reason=reason,
                evidence={
                    "evaluated": True,
                    "missing_context": [],
                    "conditions": condition_evidence,
                    "evaluation": type_evidence,
                },
            )

        outcome = _outcome_for_action(
            action
        )

        if (
            normalized_rule["is_hard_rule"]
            and outcome != OUTCOME_BLOCKED
        ):
            outcome = OUTCOME_BLOCKED
            reason = (
                "Hard clinical safety rule matched; "
                "action cannot be weakened"
            )

        return RuleEvaluationResult(
            rule_id=normalized_rule["id"],
            rule_code=normalized_rule["rule_code"],
            rule_version=normalized_rule["version"],
            severity=normalized_rule["severity"],
            action=action,
            priority=normalized_rule["priority"],
            hard_rule=normalized_rule["is_hard_rule"],
            matched=True,
            outcome=outcome,
            reason=reason,
            evidence={
                "evaluated": True,
                "missing_context": [],
                "conditions": condition_evidence,
                "evaluation": type_evidence,
            },
        )

    except ValidationError as exc:
        if normalized_rule["is_hard_rule"]:
            return _safe_invalid_rule_result(
                normalized_rule,
                exc,
            )

        raise