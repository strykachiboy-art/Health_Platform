
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.core.enums.clinical_order_enums import (
    ClinicalOrderLifecycleStatus,
    ClinicalOrderPriority,
    ClinicalOrderType,
)
from app.modules.clinical_orders.schemas.clinical_order_schema import (
    ClinicalOrderListResponseSchema,
    ClinicalOrderSummarySchema,
)


def _now():
    return datetime.now(
        timezone.utc
    )


def _payload(
    order_type=ClinicalOrderType.MEDICATION,
):
    return {
        "order_type": order_type,
        "source_entity_type": "SourceEntity",
        "source_entity_id": 101,
        "clinic_id": 11,
        "patient_id": 12,
        "admission_id": 13,
        "consultation_id": 14,
        "ordered_by_staff_id": 15,
        "ordered_at": _now(),
        "priority": ClinicalOrderPriority.ROUTINE,
        "status": ClinicalOrderLifecycleStatus.ACTIVE,
        "source_status": "active",
        "display_name": "Clinical order",
        "instructions": "Follow source instructions",
        "details": {
            "source_owned": True,
        },
    }


def test_order_type_taxonomy_is_exact():
    assert {
        member.value
        for member in ClinicalOrderType
    } == {
        "medication",
        "laboratory",
        "imaging",
        "procedure",
        "blood_product",
        "diet",
        "nursing",
    }


def test_order_priority_taxonomy_is_exact():
    assert {
        member.value
        for member in ClinicalOrderPriority
    } == {
        "routine",
        "urgent",
        "stat",
    }


def test_lifecycle_taxonomy_is_exact():
    assert {
        member.value
        for member in ClinicalOrderLifecycleStatus
    } == {
        "pending",
        "active",
        "in_progress",
        "on_hold",
        "completed",
        "cancelled",
    }


@pytest.mark.parametrize(
    "order_type",
    tuple(ClinicalOrderType),
)
def test_contract_accepts_every_gate10_order_type(
    order_type,
):
    item = ClinicalOrderSummarySchema.model_validate(
        _payload(order_type)
    )

    assert item.order_type == order_type
    assert item.source_entity_id == 101
    assert item.details == {
        "source_owned": True,
    }


def test_contract_requires_authoritative_source_reference():
    payload = _payload()
    payload["source_entity_id"] = 0

    with pytest.raises(
        ValidationError,
    ):
        ClinicalOrderSummarySchema.model_validate(
            payload
        )


def test_contract_forbids_unknown_fields():
    payload = _payload()
    payload["snapshot_copy"] = {
        "unsafe": True,
    }

    with pytest.raises(
        ValidationError,
    ):
        ClinicalOrderSummarySchema.model_validate(
            payload
        )


def test_optional_instructions_normalize_blank_to_none():
    payload = _payload()
    payload["instructions"] = "   "

    item = ClinicalOrderSummarySchema.model_validate(
        payload
    )

    assert item.instructions is None


def test_completed_status_requires_completed_at():
    payload = _payload()
    payload["status"] = (
        ClinicalOrderLifecycleStatus.COMPLETED
    )

    with pytest.raises(
        ValidationError,
        match="completed status requires completed_at",
    ):
        ClinicalOrderSummarySchema.model_validate(
            payload
        )


def test_cancelled_status_requires_cancelled_at():
    payload = _payload()
    payload["status"] = (
        ClinicalOrderLifecycleStatus.CANCELLED
    )

    with pytest.raises(
        ValidationError,
        match="cancelled status requires cancelled_at",
    ):
        ClinicalOrderSummarySchema.model_validate(
            payload
        )


def test_terminal_timestamps_are_mutually_exclusive():
    payload = _payload()
    payload["status"] = (
        ClinicalOrderLifecycleStatus.COMPLETED
    )
    payload["completed_at"] = _now()
    payload["cancelled_at"] = _now()

    with pytest.raises(
        ValidationError,
        match="cannot both be set",
    ):
        ClinicalOrderSummarySchema.model_validate(
            payload
        )


def test_non_terminal_status_rejects_terminal_timestamps():
    payload = _payload()
    payload["completed_at"] = _now()

    with pytest.raises(
        ValidationError,
        match="non-terminal status",
    ):
        ClinicalOrderSummarySchema.model_validate(
            payload
        )


def test_list_contract_requires_exact_total():
    item = ClinicalOrderSummarySchema.model_validate(
        _payload()
    )

    response = ClinicalOrderListResponseSchema.model_validate(
        {
            "items": [item],
            "total": 1,
        }
    )

    assert response.total == 1

    with pytest.raises(
        ValidationError,
        match="total must equal",
    ):
        ClinicalOrderListResponseSchema.model_validate(
            {
                "items": [item],
                "total": 2,
            }
        )
