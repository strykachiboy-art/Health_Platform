from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import inspect as sa_inspect

from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.prescription_enums import (
    DrugInteractionSeverity,
    PrescriptionStatus,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)

import app.modules.prescription.services.prescription_service as service


# ============================================================================
# FIXTURES / HELPERS
# ============================================================================


@pytest.fixture()
def prescription_service(monkeypatch):
    """
    Return the Prescription service module with audit logging mocked.

    Business behavior remains real and database-backed.
    """
    monkeypatch.setattr(
        service,
        "create_audit_log",
        Mock(),
    )
    return service


@pytest.fixture()
def active_clinic(clinic):
    """
    Ensure the standard clinic fixture is active.
    """
    clinic.status = ClinicStatus.ACTIVE
    return clinic


def _future(days=30):
    return (
        datetime.now(timezone.utc)
        + timedelta(days=days)
    )


def _past(days=1):
    return (
        datetime.now(timezone.utc)
        - timedelta(days=days)
    )


def _naive_future(days=30):
    return (
        datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(days=days)
    )


def _make_second_clinic(
    db,
    source_clinic,
):
    """
    Create a second active clinic without assuming a fixed Clinic constructor.
    """
    clinic_model = type(source_clinic)
    mapper = clinic_model.__mapper__

    excluded = {
        "id",
        "created_at",
        "updated_at",
    }

    values = {}

    for column in mapper.columns:
        if column.name in excluded:
            continue

        value = getattr(
            source_clinic,
            column.name,
            None,
        )

        if value is not None:
            values[column.name] = value

    if hasattr(clinic_model, "name"):
        values["name"] = (
            f"Other Prescription Clinic {source_clinic.id}"
        )

    if hasattr(clinic_model, "code"):
        source_code = getattr(
            source_clinic,
            "code",
            None,
        )

        if source_code:
            values["code"] = (
                f"{source_code}-RX-{source_clinic.id}"
            )

    if hasattr(clinic_model, "slug"):
        source_slug = getattr(
            source_clinic,
            "slug",
            None,
        )

        if source_slug:
            values["slug"] = (
                f"{source_slug}-rx-{source_clinic.id}"
            )

    if hasattr(clinic_model, "status"):
        values["status"] = ClinicStatus.ACTIVE

    other_clinic = clinic_model(
        **values
    )

    db.session.add(
        other_clinic
    )
    db.session.flush()

    return other_clinic


def _make_doctor(
    make_authenticated_staff,
    clinic,
):
    staff, user_data = make_authenticated_staff(
        clinic,
        Role.DOCTOR,
    )

    return staff, user_data


def _make_inactive_staff(staff):
    """
    Select a real non-active StaffStatus enum member.
    """
    status_enum = type(
        staff.status
    )

    inactive_status = next(
        (
            status
            for status in status_enum
            if status.value != "active"
        ),
        None,
    )

    if inactive_status is None:
        pytest.fail(
            "StaffStatus enum does not contain a non-active status"
        )

    staff.status = inactive_status

    return inactive_status


def _make_prescriptions(
    make_prescription,
    clinic,
    patient,
    staff,
    count,
):
    """
    Create a predictable collection for pagination tests.
    """
    prescriptions = []

    for _ in range(count):
        prescriptions.append(
            make_prescription(
                clinic,
                patient,
                staff,
                status=PrescriptionStatus.ACTIVE,
            )
        )

    return prescriptions


# ============================================================================
# CLINIC VALIDATION
# ============================================================================


def test_ensure_clinic_active_accepts_active_clinic(
    prescription_service,
    active_clinic,
):
    result = prescription_service._ensure_clinic_active(
        active_clinic.id
    )

    assert result.id == active_clinic.id


def test_ensure_clinic_active_rejects_missing_clinic(
    prescription_service,
    app,
):
    with pytest.raises(
        NotFoundError,
        match="Clinic 999999 not found",
    ):
        prescription_service._ensure_clinic_active(
            999999
        )


def test_ensure_clinic_active_rejects_inactive_clinic(
    prescription_service,
    active_clinic,
):
    active_clinic.status = ClinicStatus.SUSPENDED

    with pytest.raises(
        ValidationError,
        match="not active",
    ):
        prescription_service._ensure_clinic_active(
            active_clinic.id
        )


# ============================================================================
# PATIENT VALIDATION
# ============================================================================


def test_validate_patient_for_clinic_success(
    prescription_service,
    active_clinic,
    patient,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    result = prescription_service._validate_patient_for_clinic(
        patient_id=patient.id,
        clinic_id=active_clinic.id,
    )

    assert result.id == patient.id


def test_validate_patient_not_found(
    prescription_service,
    active_clinic,
):
    with pytest.raises(
        NotFoundError,
        match="Patient 999999 not found",
    ):
        prescription_service._validate_patient_for_clinic(
            patient_id=999999,
            clinic_id=active_clinic.id,
        )


def test_validate_patient_rejects_other_clinic(
    prescription_service,
    active_clinic,
    patient,
):
    patient.clinic_id = active_clinic.id + 999
    patient.is_active = True

    with pytest.raises(
        ValidationError,
        match="does not belong to clinic",
    ):
        prescription_service._validate_patient_for_clinic(
            patient_id=patient.id,
            clinic_id=active_clinic.id,
        )


def test_validate_patient_rejects_inactive_patient(
    prescription_service,
    active_clinic,
    patient,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = False

    with pytest.raises(
        ValidationError,
        match="inactive",
    ):
        prescription_service._validate_patient_for_clinic(
            patient_id=patient.id,
            clinic_id=active_clinic.id,
        )


# ============================================================================
# PRESCRIBER VALIDATION
# ============================================================================


def test_validate_prescriber_accepts_active_doctor(
    prescription_service,
    active_clinic,
    make_authenticated_staff,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    result = prescription_service._validate_prescriber(
        prescribed_by_id=staff.id,
        clinic_id=active_clinic.id,
    )

    assert result.id == staff.id
    assert result.user is not None
    assert result.user.id == staff.user.id


def test_validate_prescriber_rejects_missing_staff(
    prescription_service,
    active_clinic,
):
    with pytest.raises(
        NotFoundError,
        match="Staff 999999 not found",
    ):
        prescription_service._validate_prescriber(
            prescribed_by_id=999999,
            clinic_id=active_clinic.id,
        )


def test_validate_prescriber_rejects_other_clinic(
    prescription_service,
    active_clinic,
    make_authenticated_staff,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    staff.clinic_id = active_clinic.id + 999

    with pytest.raises(
        ValidationError,
        match="does not belong to clinic",
    ):
        prescription_service._validate_prescriber(
            prescribed_by_id=staff.id,
            clinic_id=active_clinic.id,
        )


def test_validate_prescriber_rejects_inactive_staff(
    prescription_service,
    active_clinic,
    make_authenticated_staff,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    _make_inactive_staff(
        staff
    )

    with pytest.raises(
        ValidationError,
        match="not active",
    ):
        prescription_service._validate_prescriber(
            prescribed_by_id=staff.id,
            clinic_id=active_clinic.id,
        )


def test_validate_prescriber_rejects_missing_user(
    prescription_service,
    active_clinic,
    make_authenticated_staff,
    db,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    staff.user = None
    db.session.flush()

    with pytest.raises(
        ValidationError,
        match="no linked user account",
    ):
        prescription_service._validate_prescriber(
            prescribed_by_id=staff.id,
            clinic_id=active_clinic.id,
        )


def test_validate_prescriber_rejects_inactive_user(
    prescription_service,
    active_clinic,
    make_authenticated_staff,
    db,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    assert staff.user is not None

    staff.user.is_active = False
    db.session.flush()

    with pytest.raises(
        ValidationError,
        match="user account is inactive",
    ):
        prescription_service._validate_prescriber(
            prescribed_by_id=staff.id,
            clinic_id=active_clinic.id,
        )


def test_validate_prescriber_rejects_non_doctor(
    prescription_service,
    active_clinic,
    make_authenticated_staff,
):
    staff, _ = make_authenticated_staff(
        active_clinic,
        Role.PHARMACIST,
    )

    with pytest.raises(
        ValidationError,
        match="Only doctors can prescribe",
    ):
        prescription_service._validate_prescriber(
            prescribed_by_id=staff.id,
            clinic_id=active_clinic.id,
        )


# ============================================================================
# CONSULTATION VALIDATION
# ============================================================================


def test_validate_consultation_none_returns_none(
    prescription_service,
    active_clinic,
    patient,
):
    result = prescription_service._validate_consultation(
        consultation_id=None,
        clinic_id=active_clinic.id,
        patient_id=patient.id,
    )

    assert result is None


def test_validate_consultation_not_found(
    prescription_service,
    active_clinic,
    patient,
):
    with pytest.raises(
        NotFoundError,
        match="Consultation 999999 not found",
    ):
        prescription_service._validate_consultation(
            consultation_id=999999,
            clinic_id=active_clinic.id,
            patient_id=patient.id,
        )


def test_validate_consultation_rejects_other_clinic(
    prescription_service,
    active_clinic,
    patient,
    make_consultation,
    make_authenticated_staff,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    consultation = make_consultation(
        active_clinic,
        patient,
        staff,
    )

    consultation.clinic_id = active_clinic.id + 999

    with pytest.raises(
        ValidationError,
        match="does not belong to clinic",
    ):
        prescription_service._validate_consultation(
            consultation_id=consultation.id,
            clinic_id=active_clinic.id,
            patient_id=patient.id,
        )


def test_validate_consultation_rejects_other_patient(
    prescription_service,
    active_clinic,
    patient,
    make_consultation,
    make_authenticated_staff,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    consultation = make_consultation(
        active_clinic,
        patient,
        staff,
    )

    consultation.clinic_id = active_clinic.id
    consultation.patient_id = patient.id + 999

    with pytest.raises(
        ValidationError,
        match="does not belong to patient",
    ):
        prescription_service._validate_consultation(
            consultation_id=consultation.id,
            clinic_id=active_clinic.id,
            patient_id=patient.id,
        )


def test_validate_consultation_success(
    prescription_service,
    active_clinic,
    patient,
    make_consultation,
    make_authenticated_staff,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    consultation = make_consultation(
        active_clinic,
        patient,
        staff,
    )

    consultation.clinic_id = active_clinic.id
    consultation.patient_id = patient.id

    result = prescription_service._validate_consultation(
        consultation_id=consultation.id,
        clinic_id=active_clinic.id,
        patient_id=patient.id,
    )

    assert result.id == consultation.id


# ============================================================================
# DRUG VALIDATION
# ============================================================================


def test_validate_drug_for_clinic_accepts_clinic_drug(
    prescription_service,
    active_clinic,
    make_drug,
):
    drug = make_drug(
        active_clinic,
        is_active=True,
    )

    result = prescription_service._validate_drug_for_clinic(
        drug_id=drug.id,
        clinic_id=active_clinic.id,
    )

    assert result.id == drug.id


def test_validate_drug_for_clinic_accepts_global_drug(
    prescription_service,
    active_clinic,
    make_drug,
):
    drug = make_drug(
        None,
        is_active=True,
    )

    result = prescription_service._validate_drug_for_clinic(
        drug_id=drug.id,
        clinic_id=active_clinic.id,
    )

    assert result.id == drug.id
    assert result.clinic_id is None


def test_validate_drug_for_clinic_rejects_missing_drug(
    prescription_service,
    active_clinic,
):
    with pytest.raises(
        NotFoundError,
        match="Drug 999999 not found",
    ):
        prescription_service._validate_drug_for_clinic(
            drug_id=999999,
            clinic_id=active_clinic.id,
        )


def test_validate_drug_for_clinic_rejects_inactive_drug(
    prescription_service,
    active_clinic,
    make_drug,
):
    drug = make_drug(
        active_clinic,
        is_active=False,
    )

    with pytest.raises(
        ValidationError,
        match="inactive",
    ):
        prescription_service._validate_drug_for_clinic(
            drug_id=drug.id,
            clinic_id=active_clinic.id,
        )


def test_validate_drug_for_clinic_rejects_other_clinic_drug(
    prescription_service,
    active_clinic,
    make_drug,
    db,
):
    other_clinic = _make_second_clinic(
        db,
        active_clinic,
    )

    drug = make_drug(
        other_clinic,
        is_active=True,
    )

    with pytest.raises(
        ValidationError,
        match="does not belong to clinic",
    ):
        prescription_service._validate_drug_for_clinic(
            drug_id=drug.id,
            clinic_id=active_clinic.id,
        )


# ============================================================================
# EXPIRY VALIDATION
# ============================================================================


def test_validate_expiry_none(
    prescription_service,
):
    assert prescription_service._validate_expiry(
        None
    ) is None


def test_validate_expiry_accepts_aware_future(
    prescription_service,
):
    value = _future()

    result = prescription_service._validate_expiry(
        value
    )

    assert result == value
    assert result.tzinfo is not None


def test_validate_expiry_converts_to_utc(
    prescription_service,
):
    value = (
        datetime.now(timezone.utc)
        .astimezone(
            timezone(
                timedelta(hours=2)
            )
        )
        + timedelta(days=30)
    )

    result = prescription_service._validate_expiry(
        value
    )

    assert result.tzinfo == timezone.utc
    assert result == value.astimezone(
        timezone.utc
    )


def test_validate_expiry_treats_naive_as_utc(
    prescription_service,
):
    value = _naive_future()

    result = prescription_service._validate_expiry(
        value
    )

    assert result.tzinfo == timezone.utc
    assert result.replace(
        tzinfo=None
    ) == value


@pytest.mark.parametrize(
    "value",
    [
        _past(),
        datetime.now(timezone.utc),
    ],
)
def test_validate_expiry_rejects_non_future(
    prescription_service,
    value,
):
    with pytest.raises(
        ValidationError,
        match="must be in the future",
    ):
        prescription_service._validate_expiry(
            value
        )


# ============================================================================
# PAGINATION VALIDATION
# ============================================================================


@pytest.mark.parametrize(
    "page",
    [
        0,
        -1,
        True,
        False,
        "1",
        1.5,
    ],
)
def test_validate_pagination_rejects_invalid_page(
    prescription_service,
    page,
):
    with pytest.raises(
        ValidationError,
        match="Page must be a positive integer",
    ):
        prescription_service._validate_pagination(
            page=page,
            per_page=50,
        )


@pytest.mark.parametrize(
    "per_page",
    [
        0,
        -1,
        True,
        False,
        "50",
        1.5,
    ],
)
def test_validate_pagination_rejects_invalid_per_page(
    prescription_service,
    per_page,
):
    with pytest.raises(
        ValidationError,
        match="per_page must be a positive integer",
    ):
        prescription_service._validate_pagination(
            page=1,
            per_page=per_page,
        )


def test_validate_pagination_rejects_excessive_per_page(
    prescription_service,
):
    with pytest.raises(
        ValidationError,
        match="must not exceed 500",
    ):
        prescription_service._validate_pagination(
            page=1,
            per_page=501,
        )


def test_validate_pagination_accepts_boundary_values(
    prescription_service,
):
    assert prescription_service._validate_pagination(
        page=1,
        per_page=500,
    ) == (
        1,
        500,
    )


# ============================================================================
# ITEM VALIDATION
# ============================================================================


def test_validate_items_rejects_empty_items(
    prescription_service,
    active_clinic,
):
    with pytest.raises(
        ValidationError,
        match="at least one item",
    ):
        prescription_service._validate_items(
            items=[],
            clinic_id=active_clinic.id,
        )


def test_validate_items_rejects_non_dict(
    prescription_service,
    active_clinic,
):
    with pytest.raises(
        ValidationError,
        match="must be an object",
    ):
        prescription_service._validate_items(
            items=["invalid"],
            clinic_id=active_clinic.id,
        )


@pytest.mark.parametrize(
    "drug_id",
    [
        None,
        0,
        -1,
        True,
        False,
        "7",
    ],
)
def test_validate_items_rejects_invalid_drug_id(
    prescription_service,
    active_clinic,
    drug_id,
):
    with pytest.raises(
        ValidationError,
        match="invalid drug_id",
    ):
        prescription_service._validate_items(
            items=[
                {
                    "drug_id": drug_id,
                }
            ],
            clinic_id=active_clinic.id,
        )


def test_validate_items_rejects_duplicate_drugs(
    prescription_service,
    active_clinic,
    make_drug,
):
    drug = make_drug(
        active_clinic
    )

    with pytest.raises(
        ValidationError,
        match="appears more than once",
    ):
        prescription_service._validate_items(
            items=[
                {"drug_id": drug.id},
                {"drug_id": drug.id},
            ],
            clinic_id=active_clinic.id,
        )


@pytest.mark.parametrize(
    "quantity",
    [
        0,
        -1,
        True,
        False,
        "10",
        10.5,
    ],
)
def test_validate_items_rejects_invalid_quantity(
    prescription_service,
    active_clinic,
    make_drug,
    quantity,
):
    drug = make_drug(
        active_clinic
    )

    with pytest.raises(
        ValidationError,
        match="greater than zero",
    ):
        prescription_service._validate_items(
            items=[
                {
                    "drug_id": drug.id,
                    "quantity": quantity,
                }
            ],
            clinic_id=active_clinic.id,
        )


def test_validate_items_accepts_valid_items(
    prescription_service,
    active_clinic,
    make_drug,
):
    first = make_drug(
        active_clinic
    )

    second = make_drug(
        None
    )

    result = prescription_service._validate_items(
        items=[
            {
                "drug_id": first.id,
                "quantity": 10,
            },
            {
                "drug_id": second.id,
            },
        ],
        clinic_id=active_clinic.id,
    )

    assert [
        drug.id
        for drug in result
    ] == [
        first.id,
        second.id,
    ]


# ============================================================================
# INTERACTION NORMALIZATION
# ============================================================================


def test_normalize_interaction_pair_orders_ids(
    prescription_service,
):
    assert (
        prescription_service._normalize_interaction_pair(
            10,
            5,
        )
        == (
            5,
            10,
        )
    )


def test_normalize_interaction_pair_keeps_sorted_ids(
    prescription_service,
):
    assert (
        prescription_service._normalize_interaction_pair(
            5,
            10,
        )
        == (
            5,
            10,
        )
    )


def test_normalize_interaction_pair_rejects_self(
    prescription_service,
):
    with pytest.raises(
        ValidationError,
        match="cannot interact with itself",
    ):
        prescription_service._normalize_interaction_pair(
            5,
            5,
        )


@pytest.mark.parametrize(
    "drug_a_id,drug_b_id",
    [
        (0, 5),
        (-1, 5),
        (True, 5),
        (False, 5),
        ("5", 6),
        (5, 0),
        (5, -1),
        (5, True),
        (5, False),
        (5, "6"),
    ],
)
def test_normalize_interaction_pair_rejects_invalid_ids(
    prescription_service,
    drug_a_id,
    drug_b_id,
):
    with pytest.raises(
        ValidationError,
        match="greater than zero",
    ):
        prescription_service._normalize_interaction_pair(
            drug_a_id,
            drug_b_id,
        )


# ============================================================================
# DRUG INTERACTION CREATION
# ============================================================================


def test_create_drug_interaction_success(
    prescription_service,
    make_drug,
):
    drug_a = make_drug(
        None,
        is_active=True,
    )

    drug_b = make_drug(
        None,
        is_active=True,
    )

    interaction = (
        prescription_service.create_drug_interaction(
            drug_a_id=drug_b.id,
            drug_b_id=drug_a.id,
            severity=DrugInteractionSeverity.SEVERE,
            description="Serious interaction",
        )
    )

    assert interaction.id is not None
    assert interaction.drug_a_id == min(
        drug_a.id,
        drug_b.id,
    )
    assert interaction.drug_b_id == max(
        drug_a.id,
        drug_b.id,
    )
    assert interaction.severity == (
        DrugInteractionSeverity.SEVERE
    )
    assert interaction.description == (
        "Serious interaction"
    )


def test_create_drug_interaction_normalizes_blank_description(
    prescription_service,
    make_drug,
):
    drug_a = make_drug(None)
    drug_b = make_drug(None)

    interaction = (
        prescription_service.create_drug_interaction(
            drug_a_id=drug_a.id,
            drug_b_id=drug_b.id,
            severity=DrugInteractionSeverity.MILD,
            description="   ",
        )
    )

    assert interaction.description is None


def test_create_drug_interaction_rejects_self(
    prescription_service,
    make_drug,
):
    drug = make_drug(
        None
    )

    with pytest.raises(
        ValidationError,
        match="cannot interact with itself",
    ):
        prescription_service.create_drug_interaction(
            drug_a_id=drug.id,
            drug_b_id=drug.id,
            severity=DrugInteractionSeverity.MILD,
        )


def test_create_drug_interaction_rejects_clinic_drug(
    prescription_service,
    active_clinic,
    make_drug,
):
    clinic_drug = make_drug(
        active_clinic
    )

    global_drug = make_drug(
        None
    )

    with pytest.raises(
        ValidationError,
        match="clinic-specific",
    ):
        prescription_service.create_drug_interaction(
            drug_a_id=clinic_drug.id,
            drug_b_id=global_drug.id,
            severity=DrugInteractionSeverity.MODERATE,
        )


def test_create_drug_interaction_rejects_inactive_drug(
    prescription_service,
    make_drug,
):
    active_drug = make_drug(
        None,
        is_active=True,
    )

    inactive_drug = make_drug(
        None,
        is_active=False,
    )

    with pytest.raises(
        ValidationError,
        match="inactive",
    ):
        prescription_service.create_drug_interaction(
            drug_a_id=active_drug.id,
            drug_b_id=inactive_drug.id,
            severity=DrugInteractionSeverity.MODERATE,
        )


def test_create_drug_interaction_rejects_duplicate(
    prescription_service,
    make_drug,
):
    drug_a = make_drug(
        None
    )

    drug_b = make_drug(
        None
    )

    prescription_service.create_drug_interaction(
        drug_a_id=drug_a.id,
        drug_b_id=drug_b.id,
        severity=DrugInteractionSeverity.MILD,
    )

    with pytest.raises(
        ConflictError,
        match="already exists",
    ):
        prescription_service.create_drug_interaction(
            drug_a_id=drug_b.id,
            drug_b_id=drug_a.id,
            severity=DrugInteractionSeverity.SEVERE,
        )


def test_find_interaction_finds_normalized_pair(
    prescription_service,
    make_drug,
):
    drug_a = make_drug(
        None
    )

    drug_b = make_drug(
        None
    )

    created = prescription_service.create_drug_interaction(
        drug_a_id=drug_a.id,
        drug_b_id=drug_b.id,
        severity=DrugInteractionSeverity.MODERATE,
    )

    found = prescription_service.find_interaction(
        drug_b.id,
        drug_a.id,
    )

    assert found is not None
    assert found.id == created.id


def test_find_interaction_rejects_self_pair(
    prescription_service,
):
    with pytest.raises(
        ValidationError,
        match="cannot interact with itself",
    ):
        prescription_service.find_interaction(
            5,
            5,
        )


# ============================================================================
# INTERACTION CHECKING
# ============================================================================


def test_check_interactions_requires_list(
    prescription_service,
    active_clinic,
):
    with pytest.raises(
        ValidationError,
        match="must be a list",
    ):
        prescription_service.check_interactions(
            drug_ids="1,2",
            clinic_id=active_clinic.id,
        )


def test_check_interactions_empty_list_returns_empty(
    prescription_service,
    active_clinic,
):
    assert (
        prescription_service.check_interactions(
            drug_ids=[],
            clinic_id=active_clinic.id,
        )
        == []
    )


@pytest.mark.parametrize(
    "drug_ids",
    [
        [0, 1],
        [-1, 1],
        [True, 2],
        [False, 2],
        ["1", 2],
    ],
)
def test_check_interactions_rejects_invalid_ids(
    prescription_service,
    active_clinic,
    drug_ids,
):
    with pytest.raises(
        ValidationError,
        match="greater than zero",
    ):
        prescription_service.check_interactions(
            drug_ids=drug_ids,
            clinic_id=active_clinic.id,
        )


def test_check_interactions_single_unique_drug_returns_empty(
    prescription_service,
    active_clinic,
    make_drug,
):
    drug = make_drug(
        active_clinic
    )

    result = prescription_service.check_interactions(
        drug_ids=[
            drug.id,
            drug.id,
        ],
        clinic_id=active_clinic.id,
    )

    assert result == []


def test_check_interactions_finds_normalized_pair(
    prescription_service,
    make_drug,
):
    drug_a = make_drug(
        None
    )

    drug_b = make_drug(
        None
    )

    prescription_service.create_drug_interaction(
        drug_a_id=drug_a.id,
        drug_b_id=drug_b.id,
        severity=DrugInteractionSeverity.SEVERE,
        description="Known interaction",
    )

    result = prescription_service.check_interactions(
        drug_ids=[
            drug_b.id,
            drug_a.id,
        ],
        clinic_id=999,
    )

    assert len(result) == 1
    assert result[0]["drug_a_id"] == min(
        drug_a.id,
        drug_b.id,
    )
    assert result[0]["drug_b_id"] == max(
        drug_a.id,
        drug_b.id,
    )
    assert result[0]["severity"] == "severe"
    assert result[0]["description"] == (
        "Known interaction"
    )


def test_check_interactions_returns_multiple_warnings(
    prescription_service,
    make_drug,
):
    drugs = [
        make_drug(None)
        for _ in range(3)
    ]

    prescription_service.create_drug_interaction(
        drug_a_id=drugs[0].id,
        drug_b_id=drugs[1].id,
        severity=DrugInteractionSeverity.MILD,
    )

    prescription_service.create_drug_interaction(
        drug_a_id=drugs[1].id,
        drug_b_id=drugs[2].id,
        severity=DrugInteractionSeverity.SEVERE,
    )

    result = prescription_service.check_interactions(
        drug_ids=[
            drug.id
            for drug in drugs
        ],
        clinic_id=999,
    )

    assert len(result) == 2


def test_check_interactions_deduplicates_ids_before_pair_generation(
    prescription_service,
    make_drug,
):
    drug_a = make_drug(None)
    drug_b = make_drug(None)

    prescription_service.create_drug_interaction(
        drug_a_id=drug_a.id,
        drug_b_id=drug_b.id,
        severity=DrugInteractionSeverity.MODERATE,
    )

    result = prescription_service.check_interactions(
        drug_ids=[
            drug_a.id,
            drug_b.id,
            drug_a.id,
            drug_b.id,
        ],
        clinic_id=999,
    )

    assert len(result) == 1


def test_check_interactions_rejects_other_clinic_drug(
    prescription_service,
    active_clinic,
    make_drug,
    db,
):
    other_clinic = _make_second_clinic(
        db,
        active_clinic,
    )

    foreign_drug = make_drug(
        other_clinic,
        is_active=True,
    )

    own_drug = make_drug(
        active_clinic,
        is_active=True,
    )

    with pytest.raises(
        ValidationError,
        match="does not belong to clinic",
    ):
        prescription_service.check_interactions(
            drug_ids=[
                foreign_drug.id,
                own_drug.id,
            ],
            clinic_id=active_clinic.id,
        )


# ============================================================================
# PRESCRIPTION LOOKUP
# ============================================================================


def test_get_prescription_success(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
    )

    result = prescription_service.get_prescription(
        prescription.id
    )

    assert result.id == prescription.id


def test_get_prescription_not_found(
    prescription_service,
    app,
):
    with pytest.raises(
        NotFoundError,
        match="Prescription 999999 not found",
    ):
        prescription_service.get_prescription(
            999999
        )


def test_get_prescription_is_historical_read(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.COMPLETED,
    )

    active_clinic.status = ClinicStatus.SUSPENDED

    result = prescription_service.get_prescription(
        prescription.id
    )

    assert result.id == prescription.id


# ============================================================================
# PRESCRIPTION LISTING / PAGINATION
# ============================================================================


def test_list_prescriptions_for_patient_returns_clinic_records(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    first = make_prescription(
        active_clinic,
        patient,
        staff,
    )

    second = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.COMPLETED,
    )

    result = (
        prescription_service.list_prescriptions_for_patient(
            patient_id=patient.id,
            clinic_id=active_clinic.id,
        )
    )

    assert set(
        prescription.id
        for prescription in result["items"]
    ) >= {
        first.id,
        second.id,
    }

    assert result["total"] == 2
    assert result["page"] == 1
    assert result["per_page"] == 50


def test_list_prescriptions_preloads_items(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
    db,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
    )

    db.session.expire_all()

    result = (
        prescription_service.list_prescriptions_for_patient(
            patient_id=patient.id,
            clinic_id=active_clinic.id,
        )
    )

    loaded = next(
        item
        for item in result["items"]
        if item.id == prescription.id
    )

    state = sa_inspect(loaded)

    assert "items" not in state.unloaded


def test_list_prescriptions_active_only(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    active = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
    )

    completed = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.COMPLETED,
    )

    result = (
        prescription_service.list_prescriptions_for_patient(
            patient_id=patient.id,
            clinic_id=active_clinic.id,
            active_only=True,
        )
    )

    ids = {
        prescription.id
        for prescription in result["items"]
    }

    assert active.id in ids
    assert completed.id not in ids
    assert result["total"] == 1


def test_list_prescriptions_allows_inactive_patient_history(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )
    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.COMPLETED,
    )

    patient.is_active = False

    result = prescription_service.list_prescriptions_for_patient(
        patient_id=patient.id,
        clinic_id=active_clinic.id,
    )

    ids = {item.id for item in result["items"]}
    assert prescription.id in ids


def test_list_prescriptions_rejects_other_clinic_patient(
    prescription_service,
    active_clinic,
    patient,
):
    patient.clinic_id = active_clinic.id + 999
    patient.is_active = True

    with pytest.raises(
        ValidationError,
        match="does not belong to clinic",
    ):
        prescription_service.list_prescriptions_for_patient(
            patient_id=patient.id,
            clinic_id=active_clinic.id,
        )


def test_list_prescriptions_supports_pagination(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescriptions = _make_prescriptions(
        make_prescription,
        active_clinic,
        patient,
        staff,
        5,
    )

    result = (
        prescription_service.list_prescriptions_for_patient(
            patient_id=patient.id,
            clinic_id=active_clinic.id,
            page=1,
            per_page=2,
        )
    )

    assert len(result["items"]) == 2
    assert result["total"] == 5
    assert result["page"] == 1
    assert result["per_page"] == 2

    assert set(
        item.id
        for item in result["items"]
    ).issubset(
        {
            prescription.id
            for prescription in prescriptions
        }
    )


def test_list_prescriptions_returns_last_partial_page(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    _make_prescriptions(
        make_prescription,
        active_clinic,
        patient,
        staff,
        5,
    )

    result = (
        prescription_service.list_prescriptions_for_patient(
            patient_id=patient.id,
            clinic_id=active_clinic.id,
            page=3,
            per_page=2,
        )
    )

    assert len(result["items"]) == 1
    assert result["total"] == 5
    assert result["page"] == 3
    assert result["per_page"] == 2


def test_list_prescriptions_returns_empty_page_after_last_page(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    _make_prescriptions(
        make_prescription,
        active_clinic,
        patient,
        staff,
        3,
    )

    result = (
        prescription_service.list_prescriptions_for_patient(
            patient_id=patient.id,
            clinic_id=active_clinic.id,
            page=5,
            per_page=2,
        )
    )

    assert result["items"] == []
    assert result["total"] == 3
    assert result["page"] == 5
    assert result["per_page"] == 2


def test_list_prescriptions_has_deterministic_ordering(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
    db,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    first = make_prescription(
        active_clinic,
        patient,
        staff,
    )

    second = make_prescription(
        active_clinic,
        patient,
        staff,
    )

    same_issued_at = datetime.now(
        timezone.utc
    )

    first.issued_at = same_issued_at
    second.issued_at = same_issued_at

    db.session.flush()

    result = (
        prescription_service.list_prescriptions_for_patient(
            patient_id=patient.id,
            clinic_id=active_clinic.id,
            page=1,
            per_page=50,
        )
    )

    ids = [
        item.id
        for item in result["items"]
    ]

    assert ids.index(
        max(
            first.id,
            second.id,
        )
    ) < ids.index(
        min(
            first.id,
            second.id,
        )
    )


@pytest.mark.parametrize(
    "page",
    [
        0,
        -1,
        True,
        False,
    ],
)
def test_list_prescriptions_rejects_invalid_page(
    prescription_service,
    active_clinic,
    patient,
    page,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    with pytest.raises(
        ValidationError,
        match="Page must be a positive integer",
    ):
        prescription_service.list_prescriptions_for_patient(
            patient_id=patient.id,
            clinic_id=active_clinic.id,
            page=page,
            per_page=50,
        )


@pytest.mark.parametrize(
    "per_page",
    [
        0,
        -1,
        True,
        False,
        501,
    ],
)
def test_list_prescriptions_rejects_invalid_per_page(
    prescription_service,
    active_clinic,
    patient,
    per_page,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    expected = (
        "must not exceed"
        if per_page == 501
        else "must be a positive integer"
    )

    with pytest.raises(
        ValidationError,
        match=expected,
    ):
        prescription_service.list_prescriptions_for_patient(
            patient_id=patient.id,
            clinic_id=active_clinic.id,
            page=1,
            per_page=per_page,
        )


def test_list_prescriptions_can_read_completed_history(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    completed = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.COMPLETED,
    )

    active_clinic.status = ClinicStatus.SUSPENDED

    result = (
        prescription_service.list_prescriptions_for_patient(
            patient_id=patient.id,
            clinic_id=active_clinic.id,
            active_only=False,
        )
    )

    ids = {
        item.id
        for item in result["items"]
    }

    assert completed.id in ids


# ============================================================================
# PRESCRIPTION CREATION
# ============================================================================


def test_create_prescription_success(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    drug = make_drug(
        active_clinic,
        is_active=True,
    )

    prescription, warnings = (
        prescription_service.create_prescription(
            clinic_id=active_clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {
                    "drug_id": drug.id,
                    "dosage": "500 mg",
                    "frequency": "twice daily",
                    "duration": "7 days",
                    "quantity": 14,
                    "instructions": "After meals",
                }
            ],
            notes="Test prescription",
        )
    )

    assert prescription.id is not None
    assert prescription.clinic_id == active_clinic.id
    assert prescription.patient_id == patient.id
    assert prescription.prescribed_by_id == staff.id
    assert prescription.status == (
        PrescriptionStatus.ACTIVE
    )

    assert len(
        prescription.items
    ) == 1

    assert prescription.items[0].drug_id == drug.id
    assert prescription.items[0].quantity == 14
    assert warnings == []


def test_create_prescription_normalizes_blank_notes(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    drug = make_drug(
        active_clinic
    )

    prescription, _ = (
        prescription_service.create_prescription(
            clinic_id=active_clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {
                    "drug_id": drug.id,
                }
            ],
            notes="   ",
        )
    )

    assert prescription.notes is None


def test_create_prescription_with_expiry(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    drug = make_drug(
        active_clinic
    )

    expires_at = _future()

    prescription, _ = (
        prescription_service.create_prescription(
            clinic_id=active_clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {"drug_id": drug.id}
            ],
            expires_at=expires_at,
        )
    )

    assert prescription.expires_at.replace(
        tzinfo=timezone.utc
    ) == expires_at


def test_create_prescription_normalizes_naive_expiry(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    drug = make_drug(
        active_clinic
    )

    expires_at = _naive_future()

    prescription, _ = (
        prescription_service.create_prescription(
            clinic_id=active_clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {"drug_id": drug.id}
            ],
            expires_at=expires_at,
        )
    )

    normalized = prescription.expires_at

    assert normalized.tzinfo in (
        None,
        timezone.utc,
    )

    assert normalized.replace(
        tzinfo=timezone.utc
    ) == expires_at.replace(
        tzinfo=timezone.utc
    )


def test_create_prescription_with_consultation(
    prescription_service,
    active_clinic,
    patient,
    make_consultation,
    make_authenticated_staff,
    make_drug,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    consultation = make_consultation(
        active_clinic,
        patient,
        staff,
    )

    consultation.clinic_id = active_clinic.id
    consultation.patient_id = patient.id

    drug = make_drug(
        active_clinic
    )

    prescription, _ = (
        prescription_service.create_prescription(
            clinic_id=active_clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            consultation_id=consultation.id,
            items=[
                {"drug_id": drug.id}
            ],
        )
    )

    assert (
        prescription.consultation_id
        == consultation.id
    )


def test_create_prescription_returns_interaction_warnings(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    drug_a = make_drug(
        None
    )

    drug_b = make_drug(
        None
    )

    prescription_service.create_drug_interaction(
        drug_a_id=drug_a.id,
        drug_b_id=drug_b.id,
        severity=DrugInteractionSeverity.SEVERE,
        description="Dangerous interaction",
    )

    prescription, warnings = (
        prescription_service.create_prescription(
            clinic_id=active_clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {"drug_id": drug_a.id},
                {"drug_id": drug_b.id},
            ],
        )
    )

    assert prescription.id is not None
    assert len(warnings) == 1
    assert warnings[0]["severity"] == "severe"


def test_create_prescription_rejects_clinical_safety_block(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    monkeypatch,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    drug = make_drug(
        active_clinic,
        is_active=True,
    )

    safety_evaluation = Mock(
        blocked=True,
        known_interactions=(),
    )

    monkeypatch.setattr(
        "app.core.clinical_safety.services.medication_safety_service.evaluate_medication_safety",
        Mock(
            return_value=safety_evaluation,
        ),
    )

    with pytest.raises(
        ValidationError,
        match="Prescription blocked by clinical safety rules",
    ):
        prescription_service.create_prescription(
            clinic_id=active_clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {
                    "drug_id": drug.id,
                    "dosage": "100 mg",
                    "frequency": "once daily",
                    "duration": "5 days",
                    "quantity": 5,
                }
            ],
        )


def test_create_prescription_rejects_inactive_clinic(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    active_clinic.status = ClinicStatus.SUSPENDED

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    drug = make_drug(
        active_clinic
    )

    with pytest.raises(
        ValidationError,
        match="not active",
    ):
        prescription_service.create_prescription(
            clinic_id=active_clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {"drug_id": drug.id}
            ],
        )


def test_create_prescription_rejects_missing_clinic(
    prescription_service,
    patient,
    make_authenticated_staff,
    active_clinic,
    make_drug,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    drug = make_drug(
        active_clinic
    )

    with pytest.raises(
        NotFoundError,
        match="Clinic 999999 not found",
    ):
        prescription_service.create_prescription(
            clinic_id=999999,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {"drug_id": drug.id}
            ],
        )


def test_create_prescription_rejects_inactive_patient(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = False

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    drug = make_drug(
        active_clinic
    )

    with pytest.raises(
        ValidationError,
        match="inactive",
    ):
        prescription_service.create_prescription(
            clinic_id=active_clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {"drug_id": drug.id}
            ],
        )


def test_create_prescription_rejects_patient_from_other_clinic(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_clinic,
):
    other_clinic = make_clinic(
        name="Prescription Other Clinic",
    )
    patient.clinic_id = other_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    drug = make_drug(
        active_clinic
    )

    with pytest.raises(
        ValidationError,
        match="does not belong to clinic",
    ):
        prescription_service.create_prescription(
            clinic_id=active_clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {"drug_id": drug.id}
            ],
        )


def test_create_prescription_rejects_invalid_items(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    with pytest.raises(
        ValidationError,
        match="at least one item",
    ):
        prescription_service.create_prescription(
            clinic_id=active_clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[],
        )


def test_create_prescription_rejects_other_clinic_drug(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    db,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    other_clinic = _make_second_clinic(
        db,
        active_clinic,
    )

    foreign_drug = make_drug(
        other_clinic,
        is_active=True,
    )

    with pytest.raises(
        ValidationError,
        match="does not belong to clinic",
    ):
        prescription_service.create_prescription(
            clinic_id=active_clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {
                    "drug_id": foreign_drug.id,
                }
            ],
        )


# ============================================================================
# LIFECYCLE
# ============================================================================


def test_cancel_prescription_success(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
        notes="Original notes",
    )

    result = prescription_service.cancel_prescription(
        prescription_id=prescription.id,
        clinic_id=active_clinic.id,
        reason="Patient requested cancellation",
    )

    assert result.status == (
        PrescriptionStatus.CANCELLED
    )

    assert (
        "Cancelled: Patient requested cancellation"
        in result.notes
    )


def test_cancel_prescription_without_reason(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
        notes="Original notes",
    )

    result = prescription_service.cancel_prescription(
        prescription_id=prescription.id,
        clinic_id=active_clinic.id,
    )

    assert result.status == (
        PrescriptionStatus.CANCELLED
    )

    assert result.notes == (
        "Original notes"
    )


def test_cancel_prescription_normalizes_blank_reason(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
        notes="Original notes",
    )

    result = prescription_service.cancel_prescription(
        prescription_id=prescription.id,
        clinic_id=active_clinic.id,
        reason="   ",
    )

    assert result.status == (
        PrescriptionStatus.CANCELLED
    )

    assert result.notes == (
        "Original notes"
    )


@pytest.mark.parametrize(
    "status",
    [
        PrescriptionStatus.COMPLETED,
        PrescriptionStatus.CANCELLED,
        PrescriptionStatus.EXPIRED,
    ],
)
def test_cancel_prescription_rejects_non_active(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
    status,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
        status=status,
    )

    with pytest.raises(
        ConflictError,
        match="expected one of",
    ):
        prescription_service.cancel_prescription(
            prescription_id=prescription.id,
            clinic_id=active_clinic.id,
        )


def test_cancel_prescription_rejects_expired_active_prescription(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
        expires_at=_past(),
    )

    with pytest.raises(
        ConflictError,
        match="expired",
    ):
        prescription_service.cancel_prescription(
            prescription_id=prescription.id,
            clinic_id=active_clinic.id,
        )


def test_cancel_prescription_rejects_other_clinic(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
    db,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
    )

    other_clinic = _make_second_clinic(
        db,
        active_clinic,
    )

    with pytest.raises(
        ValidationError,
        match="does not belong to clinic",
    ):
        prescription_service.cancel_prescription(
            prescription_id=prescription.id,
            clinic_id=other_clinic.id,
        )


def test_cancel_prescription_rejects_missing_prescription(
    prescription_service,
    active_clinic,
):
    with pytest.raises(
        NotFoundError,
        match="Prescription 999999 not found",
    ):
        prescription_service.cancel_prescription(
            prescription_id=999999,
            clinic_id=active_clinic.id,
        )


def test_complete_prescription_success(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
    )

    result = prescription_service.complete_prescription(
        prescription_id=prescription.id,
        clinic_id=active_clinic.id,
    )

    assert result.status == (
        PrescriptionStatus.COMPLETED
    )


def test_complete_prescription_rejects_expired(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
        expires_at=_past(),
    )

    with pytest.raises(
        ConflictError,
        match="expired",
    ):
        prescription_service.complete_prescription(
            prescription_id=prescription.id,
            clinic_id=active_clinic.id,
        )


def test_complete_prescription_rejects_non_active(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.CANCELLED,
    )

    with pytest.raises(
        ConflictError,
        match="expected one of",
    ):
        prescription_service.complete_prescription(
            prescription_id=prescription.id,
            clinic_id=active_clinic.id,
        )


def test_complete_prescription_rejects_other_clinic(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
    db,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
    )

    other_clinic = _make_second_clinic(
        db,
        active_clinic,
    )

    with pytest.raises(
        ValidationError,
        match="does not belong to clinic",
    ):
        prescription_service.complete_prescription(
            prescription_id=prescription.id,
            clinic_id=other_clinic.id,
        )


def test_complete_prescription_rejects_missing_prescription(
    prescription_service,
    active_clinic,
):
    with pytest.raises(
        NotFoundError,
        match="Prescription 999999 not found",
    ):
        prescription_service.complete_prescription(
            prescription_id=999999,
            clinic_id=active_clinic.id,
        )


# ============================================================================
# AUTOMATED EXPIRATION
# ============================================================================


def test_expire_stale_prescriptions_expires_old_active_records(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    stale = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
        expires_at=_past(),
    )

    count = prescription_service.expire_stale_prescriptions()

    assert count >= 1
    assert stale.status == (
        PrescriptionStatus.EXPIRED
    )


def test_expire_stale_prescriptions_does_not_expire_future(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    future = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
        expires_at=_future(),
    )

    prescription_service.expire_stale_prescriptions()

    assert future.status == (
        PrescriptionStatus.ACTIVE
    )


@pytest.mark.parametrize(
    "status",
    [
        PrescriptionStatus.COMPLETED,
        PrescriptionStatus.CANCELLED,
        PrescriptionStatus.EXPIRED,
    ],
)
def test_expire_stale_prescriptions_ignores_non_active(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
    status,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
        status=status,
        expires_at=_past(),
    )

    prescription_service.expire_stale_prescriptions()

    assert prescription.status == status


def test_expire_stale_prescriptions_ignores_without_expiry(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
        expires_at=None,
    )

    prescription_service.expire_stale_prescriptions()

    assert prescription.status == (
        PrescriptionStatus.ACTIVE
    )


# ============================================================================
# AUDIT
# ============================================================================


def test_create_prescription_writes_audit(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    monkeypatch,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    drug = make_drug(
        active_clinic
    )

    audit = Mock()

    monkeypatch.setattr(
        service,
        "create_audit_log",
        audit,
    )

    prescription_service.create_prescription(
        clinic_id=active_clinic.id,
        patient_id=patient.id,
        prescribed_by_id=staff.id,
        items=[
            {"drug_id": drug.id}
        ],
    )

    audit.assert_called_once()

    kwargs = audit.call_args.kwargs

    assert kwargs["action"] == (
        AuditAction.CREATE
    )

    assert kwargs["entity_type"] == (
        "Prescription"
    )


def test_create_prescription_audit_contains_interaction_warnings(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    monkeypatch,
):
    patient.clinic_id = active_clinic.id
    patient.is_active = True

    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    drug_a = make_drug(None)
    drug_b = make_drug(None)

    prescription_service.create_drug_interaction(
        drug_a_id=drug_a.id,
        drug_b_id=drug_b.id,
        severity=DrugInteractionSeverity.SEVERE,
    )

    audit = Mock()

    monkeypatch.setattr(
        service,
        "create_audit_log",
        audit,
    )

    prescription_service.create_prescription(
        clinic_id=active_clinic.id,
        patient_id=patient.id,
        prescribed_by_id=staff.id,
        items=[
            {"drug_id": drug_a.id},
            {"drug_id": drug_b.id},
        ],
    )

    kwargs = audit.call_args.kwargs

    assert kwargs["action"] == (
        AuditAction.CREATE
    )

    assert kwargs["entity_type"] == (
        "Prescription"
    )

    assert len(
        kwargs["new_value"]["interaction_warnings"]
    ) == 1


def test_create_drug_interaction_writes_audit(
    prescription_service,
    make_drug,
    monkeypatch,
):
    drug_a = make_drug(
        None
    )

    drug_b = make_drug(
        None
    )

    audit = Mock()

    monkeypatch.setattr(
        service,
        "create_audit_log",
        audit,
    )

    interaction = (
        prescription_service.create_drug_interaction(
            drug_a_id=drug_a.id,
            drug_b_id=drug_b.id,
            severity=DrugInteractionSeverity.MILD,
        )
    )

    audit.assert_called_once()

    kwargs = audit.call_args.kwargs

    assert kwargs["action"] == (
        AuditAction.CREATE
    )

    assert kwargs["entity_type"] == (
        "DrugInteraction"
    )

    assert kwargs["entity_id"] == (
        interaction.id
    )


def test_cancel_prescription_writes_status_audit(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
    monkeypatch,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
    )

    audit = Mock()

    monkeypatch.setattr(
        service,
        "create_audit_log",
        audit,
    )

    prescription_service.cancel_prescription(
        prescription_id=prescription.id,
        clinic_id=active_clinic.id,
        reason="No longer needed",
    )

    audit.assert_called_once()

    kwargs = audit.call_args.kwargs

    assert kwargs["action"] == (
        AuditAction.STATUS_CHANGE
    )

    assert kwargs["entity_type"] == (
        "Prescription"
    )

    assert kwargs["entity_id"] == (
        prescription.id
    )


def test_complete_prescription_writes_status_audit(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
    monkeypatch,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
    )

    audit = Mock()

    monkeypatch.setattr(
        service,
        "create_audit_log",
        audit,
    )

    prescription_service.complete_prescription(
        prescription_id=prescription.id,
        clinic_id=active_clinic.id,
    )

    audit.assert_called_once()

    kwargs = audit.call_args.kwargs

    assert kwargs["action"] == (
        AuditAction.STATUS_CHANGE
    )

    assert kwargs["entity_type"] == (
        "Prescription"
    )

    assert kwargs["entity_id"] == (
        prescription.id
    )


def test_expire_stale_prescriptions_writes_status_audit(
    prescription_service,
    active_clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
    monkeypatch,
):
    staff, _ = _make_doctor(
        make_authenticated_staff,
        active_clinic,
    )

    prescription = make_prescription(
        active_clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
        expires_at=_past(),
    )

    audit = Mock()

    monkeypatch.setattr(
        service,
        "create_audit_log",
        audit,
    )

    prescription_service.expire_stale_prescriptions()

    matching_calls = [
        call
        for call in audit.call_args_list
        if call.kwargs.get(
            "entity_id"
        ) == prescription.id
    ]

    assert matching_calls

    kwargs = matching_calls[-1].kwargs

    assert kwargs["action"] == (
        AuditAction.STATUS_CHANGE
    )

    assert kwargs["entity_type"] == (
        "Prescription"
    )

    assert kwargs["new_value"]["status"] == (
        PrescriptionStatus.EXPIRED.value
    )