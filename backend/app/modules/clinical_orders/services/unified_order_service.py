from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.extensions import db
from app.core.enums.clinical_order_enums import (
    ClinicalOrderLifecycleStatus,
    ClinicalOrderPriority,
    ClinicalOrderType,
)
from app.core.enums.lab_enums import LabOrderStatus
from app.core.enums.prescription_enums import PrescriptionStatus
from app.core.exceptions import (
    NotFoundError,
    ValidationError,
)
from app.modules.clinical_orders.models.blood_product_order_model import (
    BloodProductOrder,
)
from app.modules.clinical_orders.models.diet_order_model import DietOrder
from app.modules.clinical_orders.models.imaging_order_model import (
    ImagingOrder,
)
from app.modules.clinical_orders.models.nursing_order_model import (
    NursingOrder,
)
from app.modules.clinical_orders.models.procedure_order_model import (
    ProcedureOrder,
)
from app.modules.clinical_orders.schemas.clinical_order_schema import (
    ClinicalOrderSummarySchema,
)
from app.modules.clinical_orders.services.blood_product_order_service import (
    to_clinical_order_summary as blood_product_summary,
)
from app.modules.clinical_orders.services.diet_order_service import (
    to_clinical_order_summary as diet_summary,
)
from app.modules.clinical_orders.services.imaging_order_service import (
    to_clinical_order_summary as imaging_summary,
)
from app.modules.clinical_orders.services.nursing_order_service import (
    to_clinical_order_summary as nursing_summary,
)
from app.modules.clinical_orders.services.procedure_order_service import (
    to_clinical_order_summary as procedure_summary,
)
from app.modules.lab.models.lab_model import (
    LabOrder,
    LabOrderItem,
    LabTest,
)
from app.modules.patient.models.patient_model import Patient
from app.modules.pharmacy.models.pharmacy_model import Drug
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)


def _enum_value(value) -> str:
    return getattr(value, "value", value)


def _normalize_order_type(
    value,
) -> ClinicalOrderType | None:
    if value is None:
        return None

    try:
        return ClinicalOrderType(
            _enum_value(value)
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(
            "Invalid clinical order type"
        ) from exc


def _normalize_status(
    value,
) -> ClinicalOrderLifecycleStatus | None:
    if value is None:
        return None

    try:
        return ClinicalOrderLifecycleStatus(
            _enum_value(value)
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(
            "Invalid clinical order status"
        ) from exc


def _get_patient(
    patient_id: int,
    clinic_id: int,
) -> Patient:
    if (
        isinstance(patient_id, bool)
        or not isinstance(patient_id, int)
        or patient_id <= 0
    ):
        raise ValidationError(
            "patient_id must be a positive integer"
        )

    if (
        isinstance(clinic_id, bool)
        or not isinstance(clinic_id, int)
        or clinic_id <= 0
    ):
        raise ValidationError(
            "clinic_id must be a positive integer"
        )

    patient = db.session.execute(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()

    if patient is None:
        raise NotFoundError(
            f"Patient {patient_id} not found"
        )

    return patient


def _normalized_datetime(
    value: datetime | None,
) -> datetime:
    if value is None:
        return datetime.min

    if value.tzinfo is not None:
        return (
            value
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )

    return value


def _safe_display(
    value: str,
) -> str:
    value = value.strip()

    if not value:
        return "Clinical order"

    return value[:255]


def _normalize_medication_status(
    prescription: Prescription,
) -> tuple[
    ClinicalOrderLifecycleStatus,
    datetime | None,
    datetime | None,
    str | None,
]:
    source_status = _enum_value(
        prescription.status
    )

    if source_status == PrescriptionStatus.ACTIVE.value:
        return (
            ClinicalOrderLifecycleStatus.ACTIVE,
            None,
            None,
            None,
        )

    if source_status == PrescriptionStatus.COMPLETED.value:
        return (
            ClinicalOrderLifecycleStatus.COMPLETED,
            prescription.updated_at,
            None,
            "prescription.updated_at",
        )

    if source_status == PrescriptionStatus.CANCELLED.value:
        return (
            ClinicalOrderLifecycleStatus.CANCELLED,
            None,
            prescription.updated_at,
            "prescription.updated_at",
        )

    if source_status == PrescriptionStatus.EXPIRED.value:
        return (
            ClinicalOrderLifecycleStatus.COMPLETED,
            (
                prescription.expires_at
                or prescription.updated_at
            ),
            None,
            (
                "prescription.expires_at"
                if prescription.expires_at is not None
                else "prescription.updated_at"
            ),
        )

    raise ValidationError(
        "Unsupported prescription status "
        f"for CPOE normalization: {source_status}"
    )


def _medication_summary(
    item: PrescriptionItem,
    prescription: Prescription,
    drug: Drug,
) -> ClinicalOrderSummarySchema:
    (
        status,
        completed_at,
        cancelled_at,
        terminal_timestamp_source,
    ) = _normalize_medication_status(
        prescription
    )

    instructions = (
        item.administration_instructions
        or item.instructions
    )

    details = {
        "prescription_id": prescription.id,
        "drug_id": drug.id,
        "generic_name": drug.generic_name,
        "dosage": item.dosage,
        "frequency": item.frequency,
        "duration": item.duration,
        "quantity": item.quantity,
        "dose_value": item.dose_value,
        "dose_unit": item.dose_unit,
        "route_code": item.route_code,
        "route_display": item.route_display,
        "frequency_code": item.frequency_code,
        "frequency_interval": item.frequency_interval,
        "frequency_interval_unit": (
            item.frequency_interval_unit
        ),
        "scheduled_times": item.scheduled_times,
        "start_at": item.start_at,
        "end_at": item.end_at,
        "is_prn": item.is_prn,
        "prn_reason": item.prn_reason,
        "order_type": item.order_type,
        "legacy_admission_linkage": (
            "not_persisted_on_prescription"
        ),
    }

    if terminal_timestamp_source is not None:
        details["terminal_timestamp_source"] = (
            terminal_timestamp_source
        )

    return ClinicalOrderSummarySchema.model_validate(
        {
            "order_type": ClinicalOrderType.MEDICATION,
            "source_entity_type": "PrescriptionItem",
            "source_entity_id": item.id,
            "clinic_id": prescription.clinic_id,
            "patient_id": prescription.patient_id,
            "admission_id": None,
            "consultation_id": (
                prescription.consultation_id
            ),
            "ordered_by_staff_id": (
                prescription.prescribed_by_id
            ),
            "ordered_at": item.ordered_at,
            "priority": ClinicalOrderPriority(
                item.order_priority
            ),
            "status": status,
            "source_status": _enum_value(
                prescription.status
            ),
            "display_name": drug.name,
            "instructions": instructions,
            "scheduled_at": item.start_at,
            "due_at": item.start_at,
            "completed_at": completed_at,
            "cancelled_at": cancelled_at,
            "details": details,
        }
    )


def _normalize_lab_status(
    order: LabOrder,
) -> tuple[
    ClinicalOrderLifecycleStatus,
    datetime | None,
    datetime | None,
    str | None,
]:
    source_status = _enum_value(
        order.status
    )

    if source_status == LabOrderStatus.ORDERED.value:
        return (
            ClinicalOrderLifecycleStatus.PENDING,
            None,
            None,
            None,
        )

    if source_status in {
        LabOrderStatus.SAMPLE_COLLECTED.value,
        LabOrderStatus.IN_PROGRESS.value,
    }:
        return (
            ClinicalOrderLifecycleStatus.IN_PROGRESS,
            None,
            None,
            None,
        )

    if source_status == LabOrderStatus.COMPLETED.value:
        completed_at = (
            order.completed_at
            or order.updated_at
        )
        return (
            ClinicalOrderLifecycleStatus.COMPLETED,
            completed_at,
            None,
            (
                "lab_order.completed_at"
                if order.completed_at is not None
                else "lab_order.updated_at"
            ),
        )

    if source_status == LabOrderStatus.CANCELLED.value:
        return (
            ClinicalOrderLifecycleStatus.CANCELLED,
            None,
            order.updated_at,
            "lab_order.updated_at",
        )

    raise ValidationError(
        "Unsupported lab order status "
        f"for CPOE normalization: {source_status}"
    )


def _lab_summary(
    order: LabOrder,
    rows: list[
        tuple[
            LabOrderItem | None,
            LabTest | None,
        ]
    ],
) -> ClinicalOrderSummarySchema:
    (
        status,
        completed_at,
        cancelled_at,
        terminal_timestamp_source,
    ) = _normalize_lab_status(
        order
    )

    test_names = [
        test.name
        for _item, test in rows
        if test is not None
    ]

    display_name = (
        "Laboratory: "
        + ", ".join(test_names)
        if test_names
        else f"Laboratory order #{order.id}"
    )

    details = {
        "tests": [
            {
                "lab_order_item_id": (
                    item.id
                    if item is not None
                    else None
                ),
                "test_id": (
                    test.id
                    if test is not None
                    else None
                ),
                "test_name": (
                    test.name
                    if test is not None
                    else None
                ),
                "loinc_code": (
                    test.loinc_code
                    if test is not None
                    else None
                ),
                "result_value": (
                    item.result_value
                    if item is not None
                    else None
                ),
                "result_flag": (
                    _enum_value(item.flag)
                    if (
                        item is not None
                        and item.flag is not None
                    )
                    else None
                ),
                "resulted_at": (
                    item.resulted_at
                    if item is not None
                    else None
                ),
            }
            for item, test in rows
        ],
        "sample_collected_at": (
            order.sample_collected_at
        ),
        "processed_at": order.processed_at,
        "verified_at": order.verified_at,
        "cancellation_reason": (
            order.cancellation_reason
        ),
        "legacy_admission_linkage": (
            "not_persisted_on_lab_order"
        ),
    }

    if terminal_timestamp_source is not None:
        details["terminal_timestamp_source"] = (
            terminal_timestamp_source
        )

    return ClinicalOrderSummarySchema.model_validate(
        {
            "order_type": ClinicalOrderType.LABORATORY,
            "source_entity_type": "LabOrder",
            "source_entity_id": order.id,
            "clinic_id": order.clinic_id,
            "patient_id": order.patient_id,
            "admission_id": None,
            "consultation_id": order.consultation_id,
            "ordered_by_staff_id": (
                order.ordered_by_id
            ),
            "ordered_at": order.created_at,
            "priority": ClinicalOrderPriority.ROUTINE,
            "status": status,
            "source_status": _enum_value(
                order.status
            ),
            "display_name": _safe_display(
                display_name
            ),
            "instructions": None,
            "scheduled_at": None,
            "due_at": None,
            "completed_at": completed_at,
            "cancelled_at": cancelled_at,
            "details": details,
        }
    )


def _collect_medications(
    *,
    patient_id: int,
    clinic_id: int,
) -> list[ClinicalOrderSummarySchema]:
    rows = db.session.execute(
        select(
            PrescriptionItem,
            Prescription,
            Drug,
        )
        .join(
            Prescription,
            PrescriptionItem.prescription_id
            == Prescription.id,
        )
        .join(
            Drug,
            PrescriptionItem.drug_id
            == Drug.id,
        )
        .where(
            Prescription.clinic_id
            == clinic_id,
            Prescription.patient_id
            == patient_id,
        )
    ).all()

    return [
        _medication_summary(
            item,
            prescription,
            drug,
        )
        for item, prescription, drug in rows
    ]


def _collect_laboratory(
    *,
    patient_id: int,
    clinic_id: int,
) -> list[ClinicalOrderSummarySchema]:
    rows = db.session.execute(
        select(
            LabOrder,
            LabOrderItem,
            LabTest,
        )
        .outerjoin(
            LabOrderItem,
            LabOrderItem.order_id
            == LabOrder.id,
        )
        .outerjoin(
            LabTest,
            LabOrderItem.test_id
            == LabTest.id,
        )
        .where(
            LabOrder.clinic_id
            == clinic_id,
            LabOrder.patient_id
            == patient_id,
        )
        .order_by(
            LabOrder.id.asc(),
            LabOrderItem.id.asc(),
        )
    ).all()

    grouped: dict[
        int,
        tuple[
            LabOrder,
            list[
                tuple[
                    LabOrderItem | None,
                    LabTest | None,
                ]
            ],
        ],
    ] = {}

    for order, item, test in rows:
        if order.id not in grouped:
            grouped[order.id] = (
                order,
                [],
            )

        grouped[order.id][1].append(
            (
                item,
                test,
            )
        )

    return [
        _lab_summary(
            order,
            item_rows,
        )
        for order, item_rows in grouped.values()
    ]


def _collect_model_orders(
    *,
    model,
    normalizer,
    patient_id: int,
    clinic_id: int,
) -> list[ClinicalOrderSummarySchema]:
    rows = db.session.execute(
        select(model).where(
            model.clinic_id == clinic_id,
            model.patient_id == patient_id,
        )
    ).scalars().all()

    return [
        ClinicalOrderSummarySchema.model_validate(
            normalizer(order)
        )
        for order in rows
    ]


def list_unified_clinical_orders(
    *,
    patient_id: int,
    clinic_id: int,
    order_type=None,
    status=None,
) -> list[ClinicalOrderSummarySchema]:
    patient = _get_patient(
        patient_id,
        clinic_id,
    )

    requested_type = _normalize_order_type(
        order_type
    )
    requested_status = _normalize_status(
        status
    )

    items: list[
        ClinicalOrderSummarySchema
    ] = []

    if (
        requested_type is None
        or requested_type
        == ClinicalOrderType.MEDICATION
    ):
        items.extend(
            _collect_medications(
                patient_id=patient.id,
                clinic_id=clinic_id,
            )
        )

    if (
        requested_type is None
        or requested_type
        == ClinicalOrderType.LABORATORY
    ):
        items.extend(
            _collect_laboratory(
                patient_id=patient.id,
                clinic_id=clinic_id,
            )
        )

    domain_collectors = (
        (
            ClinicalOrderType.IMAGING,
            ImagingOrder,
            imaging_summary,
        ),
        (
            ClinicalOrderType.PROCEDURE,
            ProcedureOrder,
            procedure_summary,
        ),
        (
            ClinicalOrderType.BLOOD_PRODUCT,
            BloodProductOrder,
            blood_product_summary,
        ),
        (
            ClinicalOrderType.DIET,
            DietOrder,
            diet_summary,
        ),
        (
            ClinicalOrderType.NURSING,
            NursingOrder,
            nursing_summary,
        ),
    )

    for (
        domain_type,
        model,
        normalizer,
    ) in domain_collectors:
        if (
            requested_type is not None
            and requested_type != domain_type
        ):
            continue

        items.extend(
            _collect_model_orders(
                model=model,
                normalizer=normalizer,
                patient_id=patient.id,
                clinic_id=clinic_id,
            )
        )

    if requested_status is not None:
        items = [
            item
            for item in items
            if item.status
            == requested_status
        ]

    items.sort(
        key=lambda item: (
            _normalized_datetime(
                item.ordered_at
            ),
            item.order_type.value,
            item.source_entity_id,
        ),
        reverse=True,
    )

    return items
