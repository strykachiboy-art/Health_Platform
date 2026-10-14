from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.enums.lab_enums import (
    LabOrderStatus,
    LabResultFlag,
    SampleType,
)
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)


@pytest.fixture
def lab_service():
    import app.modules.lab.services.lab_service as service

    return service


@pytest.fixture(autouse=True)
def app_context(app):
    """
    Transactional Lab service methods require an active Flask
    application context.
    """
    with app.app_context():
        yield


def now():
    return datetime.now(timezone.utc)


def staff_obj(
    *,
    id=1,
    clinic_id=1,
    status=StaffStatus.ACTIVE,
):
    return SimpleNamespace(
        id=id,
        clinic_id=clinic_id,
        status=status,
    )


def patient_obj(*, id=1, clinic_id=1, is_active=True):
    return SimpleNamespace(
        id=id,
        clinic_id=clinic_id,
        is_active=is_active,
    )


def make_test_obj(
    *,
    id=1,
    clinic_id=1,
    name="CBC",
    code="CBC",
    is_active=True,
    reference_range=None,
    critical_low=None,
    critical_high=None,
    price=Decimal("25.00"),
    sample_type=SampleType.BLOOD,
):
    return SimpleNamespace(
        id=id,
        clinic_id=clinic_id,
        name=name,
        code=code,
        loinc_code=None,
        sample_type=sample_type,
        reference_range=reference_range,
        unit="mg/dL",
        price=price,
        critical_low=critical_low,
        critical_high=critical_high,
        is_active=is_active,
        created_at=now(),
        updated_at=now(),
    )


def order_obj(
    *,
    id=1,
    clinic_id=1,
    patient_id=1,
    consultation_id=None,
    ordered_by_id=1,
    status=LabOrderStatus.ORDERED,
    qr_code="LAB-ABC",
    collected_by_id=None,
    sample_collected_at=None,
    processed_by_id=None,
    processed_at=None,
    verified_by_id=None,
    verified_at=None,
    completed_at=None,
    equipment_reference_id=None,
    cancellation_reason=None,
    items=None,
):
    return SimpleNamespace(
        id=id,
        clinic_id=clinic_id,
        patient_id=patient_id,
        consultation_id=consultation_id,
        ordered_by_id=ordered_by_id,
        status=status,
        qr_code=qr_code,
        collected_by_id=collected_by_id,
        sample_collected_at=sample_collected_at,
        processed_by_id=processed_by_id,
        processed_at=processed_at,
        verified_by_id=verified_by_id,
        verified_at=verified_at,
        completed_at=completed_at,
        equipment_reference_id=equipment_reference_id,
        cancellation_reason=cancellation_reason,
        items=[] if items is None else items,
        created_at=now(),
        updated_at=now(),
    )


def order_item_obj(
    *,
    id=1,
    order_id=1,
    test=None,
    result_value=None,
    flag=None,
    result_notes=None,
    result_file_url=None,
    resulted_at=None,
):
    if test is None:
        test = make_test_obj()

    return SimpleNamespace(
        id=id,
        order_id=order_id,
        test_id=test.id,
        test=test,
        result_value=result_value,
        flag=flag,
        result_notes=result_notes,
        result_file_url=result_file_url,
        resulted_at=resulted_at,
    )


def pagination(
    items=None,
    *,
    total=None,
    page=1,
    per_page=50,
):
    """
    Lightweight pagination object matching the Flask-SQLAlchemy
    Pagination attributes consumed by the service tests.
    """
    items = list(items or [])

    if total is None:
        total = len(items)

    return SimpleNamespace(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


class FakeScalarsResult:
    def __init__(self, rows):
        self.rows = list(rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)


class FakeExecuteResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return FakeScalarsResult(self._rows)


# ============================================================================
# PAGINATION
# ============================================================================


def test_validate_positive_id_accepts_positive_integer(
    lab_service,
):
    assert (
        lab_service._validate_positive_id(
            10,
            "Lab ID",
        )
        == 10
    )


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        None,
        "1",
        1.5,
        True,
        False,
        [],
        {},
    ],
)
def test_validate_positive_id_rejects_invalid_values(
    lab_service,
    value,
):
    with pytest.raises(ValidationError):
        lab_service._validate_positive_id(
            value,
            "Lab ID",
        )


@pytest.mark.parametrize(
    ("page", "per_page"),
    [
        (0, 50),
        (-1, 50),
        (1, 0),
        (1, -1),
        (1, 501),
        (True, 50),
        (1, True),
        ("1", 50),
        (1, "50"),
    ],
)
def test_validate_pagination_rejects_invalid_values(
    lab_service,
    page,
    per_page,
):
    with pytest.raises(ValidationError):
        lab_service._validate_pagination(
            page,
            per_page,
        )


@pytest.mark.parametrize(
    ("page", "per_page"),
    [
        (1, 1),
        (1, 50),
        (2, 100),
        (1, 500),
    ],
)
def test_validate_pagination_accepts_valid_values(
    lab_service,
    page,
    per_page,
):
    assert (
        lab_service._validate_pagination(
            page,
            per_page,
        )
        == (page, per_page)
    )


def test_paginate_uses_db_paginate(
    lab_service,
    monkeypatch,
):
    expected = pagination(
        [make_test_obj()],
        total=1,
        page=1,
        per_page=50,
    )

    paginate = Mock(
        return_value=expected
    )

    monkeypatch.setattr(
        lab_service.db,
        "paginate",
        paginate,
    )

    result = lab_service._paginate(
        Mock(),
        page=1,
        per_page=50,
    )

    assert result is expected

    paginate.assert_called_once_with(
        paginate.call_args.args[0],
        page=1,
        per_page=50,
        error_out=False,
    )


def test_paginate_rejects_invalid_pagination(
    lab_service,
    monkeypatch,
):
    paginate = Mock()

    monkeypatch.setattr(
        lab_service.db,
        "paginate",
        paginate,
    )

    with pytest.raises(ValidationError):
        lab_service._paginate(
            Mock(),
            page=0,
            per_page=50,
        )

    paginate.assert_not_called()


def test_paginate_allows_empty_result(
    lab_service,
    monkeypatch,
):
    expected = pagination(
        [],
        total=0,
        page=1,
        per_page=50,
    )

    monkeypatch.setattr(
        lab_service.db,
        "paginate",
        Mock(return_value=expected),
    )

    result = lab_service._paginate(
        Mock(),
        page=1,
        per_page=50,
    )

    assert result.items == []
    assert result.total == 0
    assert result.page == 1
    assert result.per_page == 50


# ============================================================================
# SERIALIZATION
# ============================================================================


def test_serialize_value_handles_common_types(
    lab_service,
):
    value = {
        "decimal": Decimal("12.50"),
        "datetime": datetime(
            2026,
            1,
            1,
            tzinfo=timezone.utc,
        ),
        "enum": LabResultFlag.CRITICAL,
        "nested": [
            Decimal("1.25"),
            {"x": 2},
        ],
    }

    serialized = lab_service._serialize_value(
        value
    )

    assert serialized["decimal"] == "12.50"
    assert serialized["datetime"].startswith(
        "2026-01-01T"
    )
    assert (
        serialized["enum"]
        == LabResultFlag.CRITICAL.value
    )
    assert serialized["nested"][0] == "1.25"
    assert serialized["nested"][1]["x"] == 2


# ============================================================================
# INTERNAL LOOKUPS / TENANCY
# ============================================================================


def test_get_patient_uses_modern_select_and_scoped_clinic(
    lab_service,
    monkeypatch,
):
    patient = patient_obj(
        id=7,
        clinic_id=3,
    )

    execute = Mock(
        return_value=FakeExecuteResult(
            [patient]
        )
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        execute,
    )

    result = lab_service._get_patient(
        7,
        3,
    )

    assert result is patient
    execute.assert_called_once()

    statement = execute.call_args.args[0]

    assert statement is not None


def test_get_patient_rejects_invalid_patient_id(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="patient_id",
    ):
        lab_service._get_patient(
            0,
            1,
        )


def test_get_patient_rejects_invalid_clinic_id(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="clinic_id",
    ):
        lab_service._get_patient(
            1,
            0,
        )


def test_get_patient_not_found(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult([])
        ),
    )

    with pytest.raises(
        NotFoundError,
        match="Patient 99 not found",
    ):
        lab_service._get_patient(
            99,
            1,
        )


def test_get_staff_uses_modern_select_and_active_check(
    lab_service,
    monkeypatch,
):
    staff = staff_obj(
        id=11,
        clinic_id=4,
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult(
                [staff]
            )
        ),
    )

    result = lab_service._get_staff(
        11,
        4,
        require_active=True,
    )

    assert result is staff


def test_get_staff_rejects_invalid_staff_id(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="staff_id",
    ):
        lab_service._get_staff(
            0,
            1,
        )


def test_get_staff_rejects_invalid_clinic(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="clinic_id",
    ):
        lab_service._get_staff(
            1,
            -1,
        )


def test_get_staff_not_found(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult([])
        ),
    )

    with pytest.raises(
        NotFoundError,
        match="Staff 7 not found",
    ):
        lab_service._get_staff(
            7,
            1,
        )


def test_get_staff_rejects_inactive_staff(
    lab_service,
    monkeypatch,
):
    inactive = staff_obj(
        id=7,
        clinic_id=1,
        status=object(),
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult(
                [inactive]
            )
        ),
    )

    with pytest.raises(
        ValidationError,
        match="Staff 7 is not active",
    ):
        lab_service._get_staff(
            7,
            1,
            require_active=True,
        )


def test_get_consultation_uses_modern_select(
    lab_service,
    monkeypatch,
):
    consultation = SimpleNamespace(
        id=3,
        clinic_id=1,
        patient_id=8,
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult(
                [consultation]
            )
        ),
    )

    result = lab_service._get_consultation(
        3,
        1,
    )

    assert result is consultation


def test_get_consultation_rejects_invalid_id(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="consultation_id",
    ):
        lab_service._get_consultation(
            0,
            1,
        )


def test_get_consultation_not_found(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult([])
        ),
    )

    with pytest.raises(
        NotFoundError,
        match="Consultation 3 not found",
    ):
        lab_service._get_consultation(
            3,
            1,
        )


def test_get_lab_test_allows_global_test_for_clinic(
    lab_service,
    monkeypatch,
):
    test = make_test_obj(
        id=5,
        clinic_id=None,
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult(
                [test]
            )
        ),
    )

    assert (
        lab_service._get_lab_test(
            5,
            10,
        )
        is test
    )


def test_get_lab_test_without_clinic_allows_only_global(
    lab_service,
    monkeypatch,
):
    test = make_test_obj(
        id=5,
        clinic_id=None,
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult(
                [test]
            )
        ),
    )

    assert (
        lab_service._get_lab_test(5)
        is test
    )


def test_get_lab_test_rejects_invalid_test_id(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="test_id",
    ):
        lab_service._get_lab_test(
            0,
            1,
        )


def test_get_lab_test_rejects_invalid_clinic(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="positive integer",
    ):
        lab_service._get_lab_test(
            5,
            0,
        )


def test_get_lab_test_not_found(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult([])
        ),
    )

    with pytest.raises(
        NotFoundError,
        match="Lab test 5 not found",
    ):
        lab_service._get_lab_test(
            5,
            1,
        )


@pytest.mark.parametrize(
    "for_update",
    [False, True],
)
def test_get_lab_order_uses_modern_select(
    lab_service,
    monkeypatch,
    for_update,
):
    order = order_obj(
        id=2,
        clinic_id=3,
    )

    execute = Mock(
        return_value=FakeExecuteResult(
            [order]
        )
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        execute,
    )

    result = lab_service._get_lab_order(
        2,
        3,
        for_update=for_update,
    )

    assert result is order

    statement = execute.call_args.args[0]

    assert statement is not None


def test_get_lab_order_rejects_invalid_order_id(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="order_id",
    ):
        lab_service._get_lab_order(
            0,
            1,
        )


def test_get_lab_order_not_found(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult([])
        ),
    )

    with pytest.raises(
        NotFoundError,
        match="Lab order 9 not found",
    ):
        lab_service._get_lab_order(
            9,
            1,
        )


def test_get_lab_order_rejects_invalid_clinic(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="positive integer",
    ):
        lab_service._get_lab_order(
            1,
            0,
        )


def test_get_lab_order_item_scopes_through_parent_order(
    lab_service,
    monkeypatch,
):
    item = order_item_obj(
        id=12
    )

    execute = Mock(
        return_value=FakeExecuteResult(
            [item]
        )
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        execute,
    )

    result = lab_service._get_lab_order_item(
        12,
        7,
        for_update=True,
    )

    assert result is item

    statement = execute.call_args.args[0]

    assert statement is not None


def test_get_lab_order_item_rejects_invalid_item_id(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="order_item_id",
    ):
        lab_service._get_lab_order_item(
            0,
            1,
        )


def test_get_lab_order_item_not_found(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult([])
        ),
    )

    with pytest.raises(
        NotFoundError,
        match="Lab order item 12 not found",
    ):
        lab_service._get_lab_order_item(
            12,
            7,
        )


# ============================================================================
# VALIDATION HELPERS
# ============================================================================


def test_validate_patient_clinic_rejects_foreign_patient(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="does not belong to clinic 2",
    ):
        lab_service._validate_patient_clinic(
            patient_obj(
                id=1,
                clinic_id=1,
            ),
            2,
        )


def test_validate_staff_clinic_rejects_foreign_staff(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="does not belong to clinic 2",
    ):
        lab_service._validate_staff_clinic(
            staff_obj(
                id=2,
                clinic_id=1,
            ),
            2,
        )


def test_validate_consultation_rejects_foreign_clinic(
    lab_service,
):
    consultation = SimpleNamespace(
        id=4,
        clinic_id=1,
        patient_id=5,
    )

    with pytest.raises(
        ValidationError,
        match="does not belong to clinic 2",
    ):
        lab_service._validate_consultation(
            consultation,
            2,
            5,
        )


def test_validate_consultation_rejects_foreign_patient(
    lab_service,
):
    consultation = SimpleNamespace(
        id=4,
        clinic_id=2,
        patient_id=5,
    )

    with pytest.raises(
        ValidationError,
        match="does not belong to patient 7",
    ):
        lab_service._validate_consultation(
            consultation,
            2,
            7,
        )


def test_validate_actor_for_order(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        clinic_id=4
    )

    actor = staff_obj(
        id=9,
        clinic_id=4,
    )

    get_staff = Mock(
        return_value=actor
    )

    monkeypatch.setattr(
        lab_service,
        "_get_staff",
        get_staff,
    )

    result = lab_service._validate_actor_for_order(
        9,
        order,
    )

    assert result is actor

    get_staff.assert_called_once_with(
        9,
        4,
        require_active=True,
    )


def test_assert_status_accepts_allowed_status(
    lab_service,
):
    order = order_obj(
        status=LabOrderStatus.ORDERED
    )

    lab_service._assert_status(
        order,
        LabOrderStatus.ORDERED,
    )


def test_assert_status_rejects_wrong_status(
    lab_service,
):
    order = order_obj(
        status=LabOrderStatus.CANCELLED
    )

    with pytest.raises(
        ConflictError,
        match="expected one of",
    ):
        lab_service._assert_status(
            order,
            LabOrderStatus.ORDERED,
        )


@pytest.mark.parametrize(
    "actor_field,timestamp_field",
    [
        (
            "collected_by_id",
            "sample_collected_at",
        ),
        (
            "processed_by_id",
            "processed_at",
        ),
        (
            "verified_by_id",
            "verified_at",
        ),
    ],
)
def test_validate_actor_pair_rejects_mismatch(
    lab_service,
    actor_field,
    timestamp_field,
):
    order = order_obj()

    setattr(
        order,
        actor_field,
        1,
    )

    setattr(
        order,
        timestamp_field,
        None,
    )

    with pytest.raises(
        ValidationError,
        match="must either both be set",
    ):
        lab_service._validate_actor_pair(
            order,
            actor_field,
            timestamp_field,
        )


# ============================================================================
# LAB TEST CATALOG
# ============================================================================


def test_list_lab_tests_returns_pagination(
    lab_service,
    monkeypatch,
):
    rows = [
        make_test_obj(
            id=1,
            clinic_id=None,
            name="A",
        ),
        make_test_obj(
            id=2,
            clinic_id=3,
            name="B",
        ),
    ]

    expected = pagination(
        rows,
        total=2,
        page=1,
        per_page=50,
    )

    monkeypatch.setattr(
        lab_service,
        "_paginate",
        Mock(return_value=expected),
    )

    result = lab_service.list_lab_tests(
        clinic_id=3,
        active_only=True,
        page=1,
        per_page=50,
    )

    assert result is expected
    assert result.items == rows
    assert result.total == 2
    assert result.page == 1
    assert result.per_page == 50

    lab_service._paginate.assert_called_once()


def test_list_lab_tests_global_only_when_clinic_missing(
    lab_service,
    monkeypatch,
):
    expected = pagination(
        [
            make_test_obj(
                id=1,
                clinic_id=None,
            )
        ],
        total=1,
    )

    paginate = Mock(
        return_value=expected
    )

    monkeypatch.setattr(
        lab_service,
        "_paginate",
        paginate,
    )

    result = lab_service.list_lab_tests()

    assert result is expected
    paginate.assert_called_once()


def test_list_lab_tests_rejects_invalid_clinic(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="positive integer",
    ):
        lab_service.list_lab_tests(
            0
        )


@pytest.mark.parametrize(
    ("page", "per_page"),
    [
        (0, 50),
        (-1, 50),
        (1, 0),
        (1, 501),
    ],
)
def test_list_lab_tests_rejects_invalid_pagination(
    lab_service,
    monkeypatch,
    page,
    per_page,
):
    paginate = Mock()

    monkeypatch.setattr(
        lab_service,
        "_paginate",
        paginate,
    )

    with pytest.raises(ValidationError):
        lab_service.list_lab_tests(
            clinic_id=1,
            page=page,
            per_page=per_page,
        )

    paginate.assert_not_called()


def test_list_lab_tests_supports_custom_pagination(
    lab_service,
    monkeypatch,
):
    expected = pagination(
        [],
        total=0,
        page=3,
        per_page=25,
    )

    paginate = Mock(
        return_value=expected
    )

    monkeypatch.setattr(
        lab_service,
        "_paginate",
        paginate,
    )

    result = lab_service.list_lab_tests(
        clinic_id=1,
        page=3,
        per_page=25,
    )

    assert result is expected

    statement = paginate.call_args.args[0]

    assert statement is not None

    paginate.assert_called_once_with(
        statement,
        page=3,
        per_page=25,
    )


def test_create_lab_test_rejects_empty_name(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="name is required",
    ):
        lab_service.create_lab_test(
            "",
            clinic_id=1,
        )


def test_create_lab_test_rejects_unknown_field(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="Unknown lab test field",
    ):
        lab_service.create_lab_test(
            "CBC",
            clinic_id=1,
            unknown_field="x",
        )


def test_create_lab_test_rejects_invalid_clinic(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="positive integer",
    ):
        lab_service.create_lab_test(
            "CBC",
            clinic_id=0,
        )


def test_create_lab_test_checks_clinic_activity(
    lab_service,
    monkeypatch,
):
    ensure = Mock()

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        ensure,
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult([])
        ),
    )

    audit = Mock()

    monkeypatch.setattr(
        lab_service,
        "create_audit_log",
        audit,
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "add",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "flush",
        Mock(),
    )

    result = lab_service.create_lab_test(
        " CBC ",
        clinic_id=7,
        code="CBC",
    )

    ensure.assert_called_once_with(7)

    assert result.name == "CBC"
    assert result.code == "CBC"


def test_create_lab_test_rejects_duplicate_code(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    existing = make_test_obj(
        id=9,
        clinic_id=1,
        code="CBC",
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult(
                [existing]
            )
        ),
    )

    with pytest.raises(
        ConflictError,
        match="already exists",
    ):
        lab_service.create_lab_test(
            "CBC",
            clinic_id=1,
            code="CBC",
        )


@pytest.mark.parametrize(
    "low,high",
    [
        (
            Decimal("10"),
            Decimal("10"),
        ),
        (
            Decimal("20"),
            Decimal("10"),
        ),
    ],
)
def test_create_lab_test_rejects_invalid_critical_range(
    lab_service,
    low,
    high,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match=(
            "critical_low must be less than "
            "critical_high"
        ),
    ):
        lab_service.create_lab_test(
            "CBC",
            clinic_id=1,
            critical_low=low,
            critical_high=high,
        )


def test_create_lab_test_rejects_negative_price(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="price cannot be negative",
    ):
        lab_service.create_lab_test(
            "CBC",
            clinic_id=1,
            price=Decimal("-1"),
        )


def test_update_lab_test_merges_partial_critical_range(
    lab_service,
    monkeypatch,
):
    test = make_test_obj(
        id=5,
        clinic_id=1,
        critical_low=Decimal("5"),
        critical_high=Decimal("20"),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_test",
        Mock(return_value=test),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    audit = Mock()

    monkeypatch.setattr(
        lab_service,
        "create_audit_log",
        audit,
    )

    result = lab_service.update_lab_test(
        5,
        clinic_id=1,
        critical_low=Decimal("10"),
    )

    assert result.critical_low == Decimal("10")
    assert result.critical_high == Decimal("20")


def test_update_lab_test_rejects_final_invalid_critical_range(
    lab_service,
    monkeypatch,
):
    test = make_test_obj(
        id=5,
        clinic_id=1,
        critical_low=Decimal("10"),
        critical_high=Decimal("20"),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_test",
        Mock(return_value=test),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match=(
            "critical_low must be less than "
            "critical_high"
        ),
    ):
        lab_service.update_lab_test(
            5,
            clinic_id=1,
            critical_low=Decimal("25"),
        )


def test_update_lab_test_rejects_duplicate_code(
    lab_service,
    monkeypatch,
):
    test = make_test_obj(
        id=5,
        clinic_id=1,
        code="OLD",
    )

    existing = make_test_obj(
        id=6,
        clinic_id=1,
        code="NEW",
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_test",
        Mock(return_value=test),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult(
                [existing]
            )
        ),
    )

    with pytest.raises(
        ConflictError,
        match="already exists",
    ):
        lab_service.update_lab_test(
            5,
            clinic_id=1,
            code="NEW",
        )


def test_update_lab_test_name_is_editable(
    lab_service,
    monkeypatch,
):
    test = make_test_obj(
        id=5,
        clinic_id=1,
        name="Old",
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_test",
        Mock(return_value=test),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "create_audit_log",
        Mock(),
    )

    result = lab_service.update_lab_test(
        5,
        clinic_id=1,
        name=" New ",
    )

    assert result.name == "New"


# ============================================================================
# LAB ORDER CREATION
# ============================================================================


def test_list_orders_for_patient_returns_pagination(
    lab_service,
    monkeypatch,
):
    patient = patient_obj(
        id=7,
        clinic_id=2,
        is_active=False,
    )

    order = order_obj(
        id=10,
        clinic_id=2,
        patient_id=7,
    )

    get_patient = Mock(
        return_value=patient
    )

    monkeypatch.setattr(
        lab_service,
        "_get_patient",
        get_patient,
    )

    expected = pagination(
        [order],
        total=1,
        page=1,
        per_page=50,
    )

    paginate = Mock(
        return_value=expected
    )

    monkeypatch.setattr(
        lab_service,
        "_paginate",
        paginate,
    )

    result = lab_service.list_orders_for_patient(
        7,
        2,
        page=1,
        per_page=50,
    )

    assert result is expected
    assert result.items == [order]
    assert result.total == 1
    assert result.page == 1
    assert result.per_page == 50

    get_patient.assert_called_once_with(
        7,
        2,
    )

    paginate.assert_called_once()


def test_list_orders_for_patient_forwards_pagination(
    lab_service,
    monkeypatch,
):
    patient = patient_obj(
        id=7,
        clinic_id=2,
    )

    monkeypatch.setattr(
        lab_service,
        "_get_patient",
        Mock(return_value=patient),
    )

    expected = pagination(
        [],
        total=0,
        page=3,
        per_page=25,
    )

    paginate = Mock(
        return_value=expected
    )

    monkeypatch.setattr(
        lab_service,
        "_paginate",
        paginate,
    )

    result = lab_service.list_orders_for_patient(
        7,
        2,
        page=3,
        per_page=25,
    )

    assert result is expected

    paginate.assert_called_once_with(
        paginate.call_args.args[0],
        page=3,
        per_page=25,
    )


@pytest.mark.parametrize(
    ("page", "per_page"),
    [
        (0, 50),
        (-1, 50),
        (1, 0),
        (1, 501),
    ],
)
def test_list_orders_for_patient_rejects_invalid_pagination(
    lab_service,
    monkeypatch,
    page,
    per_page,
):
    monkeypatch.setattr(
        lab_service,
        "_get_patient",
        Mock(return_value=patient_obj()),
    )

    paginate = Mock()

    monkeypatch.setattr(
        lab_service,
        "_paginate",
        paginate,
    )

    with pytest.raises(
        ValidationError
    ):
        lab_service.list_orders_for_patient(
            1,
            1,
            page=page,
            per_page=per_page,
        )

    paginate.assert_not_called()


def test_list_orders_for_patient_rejects_invalid_patient(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="patient_id",
    ):
        lab_service.list_orders_for_patient(
            0,
            1,
        )


def test_list_orders_for_patient_rejects_invalid_clinic(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="clinic_id",
    ):
        lab_service.list_orders_for_patient(
            1,
            0,
        )


def test_create_lab_order_requires_tests(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="at least one test",
    ):
        lab_service.create_lab_order(
            clinic_id=1,
            patient_id=1,
            ordered_by_id=1,
            test_ids=[],
        )


def test_create_lab_order_rejects_duplicate_tests(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="Duplicate test IDs",
    ):
        lab_service.create_lab_order(
            clinic_id=1,
            patient_id=1,
            ordered_by_id=1,
            test_ids=[1, 1],
        )


def test_create_lab_order_rejects_non_positive_test_ids(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="positive integers",
    ):
        lab_service.create_lab_order(
            clinic_id=1,
            patient_id=1,
            ordered_by_id=1,
            test_ids=[1, 0],
        )


def test_create_lab_order_rejects_bool_test_id(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="positive integers",
    ):
        lab_service.create_lab_order(
            clinic_id=1,
            patient_id=1,
            ordered_by_id=1,
            test_ids=[True],
        )


def test_create_lab_order_rejects_missing_test(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_patient",
        Mock(
            return_value=patient_obj()
        ),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_staff",
        Mock(
            return_value=staff_obj()
        ),
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult([])
        ),
    )

    with pytest.raises(
        NotFoundError,
        match="Lab test",
    ):
        lab_service.create_lab_order(
            clinic_id=1,
            patient_id=1,
            ordered_by_id=1,
            test_ids=[99],
        )


def test_create_lab_order_rejects_foreign_clinic_test(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_patient",
        Mock(
            return_value=patient_obj()
        ),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_staff",
        Mock(
            return_value=staff_obj()
        ),
    )

    foreign_test = make_test_obj(
        id=9,
        clinic_id=2,
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult(
                [foreign_test]
            )
        ),
    )

    with pytest.raises(
        ValidationError,
        match="do not belong",
    ):
        lab_service.create_lab_order(
            clinic_id=1,
            patient_id=1,
            ordered_by_id=1,
            test_ids=[9],
        )


def test_create_lab_order_rejects_inactive_patient(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )
    monkeypatch.setattr(
        lab_service,
        "_get_patient",
        Mock(return_value=patient_obj(is_active=False)),
    )
    monkeypatch.setattr(
        lab_service,
        "_get_staff",
        Mock(return_value=staff_obj()),
    )

    with pytest.raises(ValidationError, match="inactive"):
        lab_service.create_lab_order(
            clinic_id=1,
            patient_id=1,
            ordered_by_id=1,
            test_ids=[9],
        )


def test_create_lab_order_rejects_inactive_test(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_patient",
        Mock(
            return_value=patient_obj()
        ),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_staff",
        Mock(
            return_value=staff_obj()
        ),
    )

    inactive_test = make_test_obj(
        id=9,
        clinic_id=1,
        is_active=False,
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult(
                [inactive_test]
            )
        ),
    )

    with pytest.raises(
        ValidationError,
        match="inactive",
    ):
        lab_service.create_lab_order(
            clinic_id=1,
            patient_id=1,
            ordered_by_id=1,
            test_ids=[9],
        )


def test_create_lab_order_validates_consultation(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_patient",
        Mock(
            return_value=patient_obj()
        ),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_staff",
        Mock(
            return_value=staff_obj()
        ),
    )

    consultation = SimpleNamespace(
        id=10,
        clinic_id=1,
        patient_id=1,
    )

    monkeypatch.setattr(
        lab_service,
        "_get_consultation",
        Mock(
            return_value=consultation
        ),
    )

    valid_test = make_test_obj(
        id=5,
        clinic_id=None,
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult(
                [valid_test]
            )
        ),
    )

    monkeypatch.setattr(
        lab_service,
        "_generate_unique_qr_code",
        Mock(
            return_value="LAB-123"
        ),
    )

    monkeypatch.setattr(
        lab_service,
        "create_audit_log",
        Mock(),
    )

    class FakeLabOrder:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.id = 100

    class FakeLabOrderItem:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    monkeypatch.setattr(
        lab_service,
        "LabOrder",
        FakeLabOrder,
    )

    monkeypatch.setattr(
        lab_service,
        "LabOrderItem",
        FakeLabOrderItem,
    )

    db_add = Mock()
    db_flush = Mock()

    monkeypatch.setattr(
        lab_service.db.session,
        "add",
        db_add,
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "flush",
        db_flush,
    )

    result = lab_service.create_lab_order(
        clinic_id=1,
        patient_id=1,
        ordered_by_id=1,
        test_ids=[5],
        consultation_id=10,
    )

    assert (
        result.status
        == LabOrderStatus.ORDERED
    )

    assert result.qr_code == "LAB-123"
    assert result.ordered_by_id == 1

    db_flush.assert_called_once()


def test_generate_unique_qr_code_retries_collision(
    lab_service,
    monkeypatch,
):
    generate = Mock(
        side_effect=[
            "LAB-1",
            "LAB-2",
        ]
    )

    monkeypatch.setattr(
        lab_service,
        "_generate_qr_code",
        generate,
    )

    first_existing = make_test_obj(
        id=1
    )

    execute = Mock(
        side_effect=[
            FakeExecuteResult(
                [first_existing]
            ),
            FakeExecuteResult([]),
        ]
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        execute,
    )

    assert (
        lab_service._generate_unique_qr_code()
        == "LAB-2"
    )

    assert generate.call_count == 2


def test_generate_unique_qr_code_raises_after_collisions(
    lab_service,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_service,
        "_generate_qr_code",
        Mock(
            return_value="LAB-X"
        ),
    )

    existing = make_test_obj(
        id=1
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult(
                [existing]
            )
        ),
    )

    with pytest.raises(
        ConflictError,
        match="Could not generate a unique QR code",
    ):
        lab_service._generate_unique_qr_code()


# ============================================================================
# SAMPLE COLLECTION
# ============================================================================


def test_collect_sample_success(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        id=1,
        clinic_id=4,
        status=LabOrderStatus.ORDERED,
        qr_code="LAB-123",
    )

    actor = staff_obj(
        id=9,
        clinic_id=4,
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_validate_actor_for_order",
        Mock(return_value=actor),
    )

    monkeypatch.setattr(
        lab_service,
        "create_audit_log",
        Mock(),
    )

    result = lab_service.collect_sample(
        order_id=1,
        collected_by_id=9,
        scanned_qr_code=" LAB-123 ",
        clinic_id=4,
    )

    assert (
        result.status
        == LabOrderStatus.SAMPLE_COLLECTED
    )

    assert result.collected_by_id == 9
    assert result.sample_collected_at is not None


def test_collect_sample_rejects_wrong_qr(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        clinic_id=1,
        status=LabOrderStatus.ORDERED,
        qr_code="LAB-123",
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_validate_actor_for_order",
        Mock(
            return_value=staff_obj()
        ),
    )

    with pytest.raises(
        ConflictError,
        match="does not match",
    ):
        lab_service.collect_sample(
            1,
            1,
            "LAB-WRONG",
            1,
        )


def test_collect_sample_rejects_empty_qr(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        clinic_id=1,
        status=LabOrderStatus.ORDERED,
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_validate_actor_for_order",
        Mock(
            return_value=staff_obj()
        ),
    )

    with pytest.raises(
        ValidationError,
        match="cannot be empty",
    ):
        lab_service.collect_sample(
            1,
            1,
            "   ",
            1,
        )


def test_collect_sample_rejects_already_collected(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        clinic_id=1,
        status=LabOrderStatus.ORDERED,
        collected_by_id=8,
        sample_collected_at=now(),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_validate_actor_for_order",
        Mock(
            return_value=staff_obj()
        ),
    )

    with pytest.raises(
        ConflictError,
        match="already",
    ):
        lab_service.collect_sample(
            1,
            1,
            None,
            1,
        )


# ============================================================================
# EQUIPMENT / PROCESSING
# ============================================================================


def test_link_equipment_success(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        clinic_id=1,
        status=LabOrderStatus.SAMPLE_COLLECTED,
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "create_audit_log",
        Mock(),
    )

    result = lab_service.link_equipment(
        1,
        " EQ-100 ",
        1,
    )

    assert (
        result.equipment_reference_id
        == "EQ-100"
    )

    assert (
        result.status
        == LabOrderStatus.IN_PROGRESS
    )


def test_link_equipment_rejects_empty_reference(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        clinic_id=1,
        status=LabOrderStatus.SAMPLE_COLLECTED,
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="reference ID is required",
    ):
        lab_service.link_equipment(
            1,
            " ",
            1,
        )


def test_link_equipment_rejects_too_long_reference(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        clinic_id=1,
        status=LabOrderStatus.SAMPLE_COLLECTED,
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="must not exceed",
    ):
        lab_service.link_equipment(
            1,
            "X" * 151,
            1,
        )


def test_process_sample_success(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        clinic_id=1,
        status=LabOrderStatus.SAMPLE_COLLECTED,
        collected_by_id=5,
        sample_collected_at=now(),
    )

    actor = staff_obj(
        id=8,
        clinic_id=1,
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_validate_actor_for_order",
        Mock(return_value=actor),
    )

    monkeypatch.setattr(
        lab_service,
        "create_audit_log",
        Mock(),
    )

    result = lab_service.process_sample(
        1,
        8,
        1,
        equipment_reference_id="EQ-1",
    )

    assert (
        result.status
        == LabOrderStatus.IN_PROGRESS
    )

    assert result.processed_by_id == 8
    assert result.processed_at is not None
    assert result.equipment_reference_id == "EQ-1"


def test_process_sample_rejects_duplicate_processing(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        clinic_id=1,
        status=LabOrderStatus.SAMPLE_COLLECTED,
        collected_by_id=5,
        sample_collected_at=now(),
        processed_by_id=6,
        processed_at=now(),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_validate_actor_for_order",
        Mock(
            return_value=staff_obj()
        ),
    )

    with pytest.raises(
        ConflictError,
        match="already been processed",
    ):
        lab_service.process_sample(
            1,
            1,
            1,
        )


def test_process_sample_requires_collection(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        clinic_id=1,
        status=LabOrderStatus.SAMPLE_COLLECTED,
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_validate_actor_for_order",
        Mock(
            return_value=staff_obj()
        ),
    )

    with pytest.raises(
        ValidationError,
        match="before collection",
    ):
        lab_service.process_sample(
            1,
            1,
            1,
        )


# ============================================================================
# CANCELLATION
# ============================================================================


@pytest.mark.parametrize(
    "status",
    [
        LabOrderStatus.COMPLETED,
        LabOrderStatus.CANCELLED,
    ],
)
def test_cancel_order_rejects_final_status(
    lab_service,
    monkeypatch,
    status,
):
    order = order_obj(
        clinic_id=1,
        status=status,
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_validate_actor_for_order",
        Mock(
            return_value=staff_obj()
        ),
    )

    with pytest.raises(
        ConflictError,
        match="Cannot cancel",
    ):
        lab_service.cancel_order(
            1,
            "No longer needed",
            1,
            1,
        )


def test_cancel_order_normalizes_reason(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        clinic_id=1,
        status=LabOrderStatus.ORDERED,
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_validate_actor_for_order",
        Mock(
            return_value=staff_obj()
        ),
    )

    monkeypatch.setattr(
        lab_service,
        "create_audit_log",
        Mock(),
    )

    result = lab_service.cancel_order(
        1,
        "  Patient request  ",
        1,
        1,
    )

    assert (
        result.status
        == LabOrderStatus.CANCELLED
    )

    assert (
        result.cancellation_reason
        == "Patient request"
    )


def test_cancel_order_rejects_empty_reason(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        clinic_id=1,
        status=LabOrderStatus.ORDERED,
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_validate_actor_for_order",
        Mock(
            return_value=staff_obj()
        ),
    )

    with pytest.raises(
        ValidationError,
        match="reason cannot be empty",
    ):
        lab_service.cancel_order(
            1,
            " ",
            1,
            1,
        )


def test_cancel_order_rejects_long_reason(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        clinic_id=1,
        status=LabOrderStatus.ORDERED,
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_validate_actor_for_order",
        Mock(
            return_value=staff_obj()
        ),
    )

    with pytest.raises(
        ValidationError,
        match="must not exceed",
    ):
        lab_service.cancel_order(
            1,
            "X" * 256,
            1,
            1,
        )


# ============================================================================
# RESULT FLAGGING
# ============================================================================


@pytest.mark.parametrize(
    "value,expected",
    [
        ("5", LabResultFlag.NORMAL),
        ("10", LabResultFlag.NORMAL),
        ("15", LabResultFlag.NORMAL),
        ("4.99", LabResultFlag.ABNORMAL),
        ("20.01", LabResultFlag.ABNORMAL),
    ],
)
def test_auto_flag_numeric_range(
    lab_service,
    value,
    expected,
):
    test = make_test_obj(
        reference_range="5 - 20",
    )

    assert (
        lab_service._auto_flag(
            test,
            value,
        )
        == expected
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        ("9.9", LabResultFlag.NORMAL),
        ("10", LabResultFlag.ABNORMAL),
        ("10.1", LabResultFlag.ABNORMAL),
    ],
)
def test_auto_flag_less_than_bound(
    lab_service,
    value,
    expected,
):
    test = make_test_obj(
        reference_range="< 10",
    )

    assert (
        lab_service._auto_flag(
            test,
            value,
        )
        == expected
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        ("5", LabResultFlag.NORMAL),
        ("5.1", LabResultFlag.NORMAL),
        ("4.9", LabResultFlag.ABNORMAL),
    ],
)
def test_auto_flag_greater_than_or_equal_bound(
    lab_service,
    value,
    expected,
):
    test = make_test_obj(
        reference_range=">= 5",
    )

    assert (
        lab_service._auto_flag(
            test,
            value,
        )
        == expected
    )


def test_auto_flag_critical_low_takes_precedence(
    lab_service,
):
    test = make_test_obj(
        reference_range="5 - 20",
        critical_low=Decimal("7"),
    )

    assert (
        lab_service._auto_flag(
            test,
            "6",
        )
        == LabResultFlag.CRITICAL
    )


def test_auto_flag_critical_high_takes_precedence(
    lab_service,
):
    test = make_test_obj(
        reference_range="5 - 20",
        critical_high=Decimal("18"),
    )

    assert (
        lab_service._auto_flag(
            test,
            "19",
        )
        == LabResultFlag.CRITICAL
    )


def test_auto_flag_non_numeric_is_unflagged(
    lab_service,
):
    test = make_test_obj(
        reference_range="5 - 20",
    )

    assert (
        lab_service._auto_flag(
            test,
            "positive",
        )
        is None
    )


def test_auto_flag_unsupported_range_is_unflagged(
    lab_service,
):
    test = make_test_obj(
        reference_range="5 to 20 mg/dL",
    )

    assert (
        lab_service._auto_flag(
            test,
            "10",
        )
        is None
    )


def test_auto_flag_inverted_range_is_unflagged(
    lab_service,
):
    test = make_test_obj(
        reference_range="20 - 5",
    )

    assert (
        lab_service._auto_flag(
            test,
            "10",
        )
        is None
    )


def test_resolve_result_flag_prefers_automatic_flag(
    lab_service,
):
    test = make_test_obj(
        reference_range="5 - 20",
    )

    resolved, automatic = (
        lab_service._resolve_result_flag(
            test=test,
            result_value="30",
            supplied_flag=LabResultFlag.NORMAL,
        )
    )

    assert resolved == LabResultFlag.ABNORMAL
    assert automatic is True


def test_resolve_result_flag_uses_supplied_flag_when_auto_unknown(
    lab_service,
):
    test = make_test_obj(
        reference_range="qualitative",
    )

    resolved, automatic = (
        lab_service._resolve_result_flag(
            test=test,
            result_value="positive",
            supplied_flag=LabResultFlag.CRITICAL,
        )
    )

    assert resolved == LabResultFlag.CRITICAL
    assert automatic is False


def test_resolve_result_flag_returns_none_when_no_flag_available(
    lab_service,
):
    test = make_test_obj(
        reference_range="qualitative",
    )

    resolved, automatic = (
        lab_service._resolve_result_flag(
            test=test,
            result_value="positive",
            supplied_flag=None,
        )
    )

    assert resolved is None
    assert automatic is False


# ============================================================================
# RESULT ENTRY
# ============================================================================


def test_enter_result_success_and_auto_flag(
    lab_service,
    monkeypatch,
):
    test = make_test_obj(
        id=7,
        reference_range="5 - 20",
    )

    item = order_item_obj(
        id=11,
        test=test,
    )

    order = order_obj(
        id=20,
        clinic_id=1,
        status=LabOrderStatus.IN_PROGRESS,
        sample_collected_at=now(),
        collected_by_id=2,
        processed_by_id=3,
        processed_at=now(),
    )

    item.order = order

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order_item",
        Mock(return_value=item),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "create_audit_log",
        Mock(),
    )

    result = lab_service.enter_result(
        order_item_id=11,
        result_value="25",
        result_notes=" Elevated ",
        result_file_url=" report.pdf ",
        flag=LabResultFlag.NORMAL,
        clinic_id=1,
    )

    assert result.result_value == "25"
    assert result.flag == LabResultFlag.ABNORMAL
    assert result.result_notes == "Elevated"
    assert result.result_file_url == "report.pdf"
    assert result.resulted_at is not None


def test_enter_result_rejects_empty_value(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="Result value is required",
    ):
        lab_service.enter_result(
            1,
            " ",
            None,
            None,
            None,
            1,
        )


def test_enter_result_rejects_too_long_value(
    lab_service,
):
    with pytest.raises(
        ValidationError,
        match="must not exceed 150",
    ):
        lab_service.enter_result(
            1,
            "X" * 151,
            None,
            None,
            None,
            1,
        )


def test_enter_result_rejects_after_verification(
    lab_service,
    monkeypatch,
):
    item = order_item_obj()

    item.order = order_obj(
        clinic_id=1,
        status=LabOrderStatus.IN_PROGRESS,
        sample_collected_at=now(),
        collected_by_id=2,
        processed_by_id=3,
        processed_at=now(),
        verified_by_id=4,
        verified_at=now(),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order_item",
        Mock(return_value=item),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    with pytest.raises(
        ConflictError,
        match="after verification",
    ):
        lab_service.enter_result(
            1,
            "10",
            None,
            None,
            None,
            1,
        )


def test_enter_result_requires_processing(
    lab_service,
    monkeypatch,
):
    item = order_item_obj()

    item.order = order_obj(
        clinic_id=1,
        status=LabOrderStatus.IN_PROGRESS,
        sample_collected_at=now(),
        collected_by_id=2,
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order_item",
        Mock(return_value=item),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="before the sample has been processed",
    ):
        lab_service.enter_result(
            1,
            "10",
            None,
            None,
            None,
            1,
        )


# ============================================================================
# RESULT VERIFICATION
# ============================================================================


def test_verify_results_success(
    lab_service,
    monkeypatch,
):
    item = order_item_obj(
        result_value="10",
        resulted_at=now(),
    )

    order = order_obj(
        id=5,
        clinic_id=1,
        status=LabOrderStatus.IN_PROGRESS,
        sample_collected_at=now(),
        collected_by_id=2,
        processed_by_id=3,
        processed_at=now(),
        items=[item],
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_validate_actor_for_order",
        Mock(
            return_value=staff_obj(
                id=9,
                clinic_id=1,
            )
        ),
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult(
                [item]
            )
        ),
    )

    monkeypatch.setattr(
        lab_service,
        "create_audit_log",
        Mock(),
    )

    result = lab_service.verify_results(
        5,
        9,
        1,
    )

    assert result.verified_by_id == 9
    assert result.verified_at is not None


def test_verify_results_rejects_missing_results(
    lab_service,
    monkeypatch,
):
    item = order_item_obj(
        result_value=None,
        resulted_at=None,
    )

    order = order_obj(
        id=5,
        clinic_id=1,
        status=LabOrderStatus.IN_PROGRESS,
        sample_collected_at=now(),
        collected_by_id=2,
        processed_by_id=3,
        processed_at=now(),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_validate_actor_for_order",
        Mock(
            return_value=staff_obj()
        ),
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult(
                [item]
            )
        ),
    )

    with pytest.raises(
        ConflictError,
        match=r"result\(s\) are missing",
    ):
        lab_service.verify_results(
            5,
            1,
            1,
        )


def test_verify_results_rejects_empty_order_items(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        id=5,
        clinic_id=1,
        status=LabOrderStatus.IN_PROGRESS,
        sample_collected_at=now(),
        collected_by_id=2,
        processed_by_id=3,
        processed_at=now(),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_validate_actor_for_order",
        Mock(
            return_value=staff_obj()
        ),
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult([])
        ),
    )

    with pytest.raises(
        ValidationError,
        match="no test items",
    ):
        lab_service.verify_results(
            5,
            1,
            1,
        )


def test_verify_results_rejects_already_verified(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        id=5,
        clinic_id=1,
        status=LabOrderStatus.IN_PROGRESS,
        sample_collected_at=now(),
        collected_by_id=2,
        processed_by_id=3,
        processed_at=now(),
        verified_by_id=4,
        verified_at=now(),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service,
        "_validate_actor_for_order",
        Mock(
            return_value=staff_obj()
        ),
    )

    with pytest.raises(
        ConflictError,
        match="already been verified",
    ):
        lab_service.verify_results(
            5,
            1,
            1,
        )


# ============================================================================
# COMPLETION
# ============================================================================


def test_complete_order_success(
    lab_service,
    monkeypatch,
):
    item = order_item_obj(
        result_value="10",
        resulted_at=now(),
    )

    order = order_obj(
        id=5,
        clinic_id=1,
        status=LabOrderStatus.IN_PROGRESS,
        sample_collected_at=now(),
        collected_by_id=2,
        processed_by_id=3,
        processed_at=now(),
        verified_by_id=4,
        verified_at=now(),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult(
                [item]
            )
        ),
    )

    monkeypatch.setattr(
        lab_service,
        "create_audit_log",
        Mock(),
    )

    result = lab_service.complete_order(
        5,
        1,
    )

    assert (
        result.status
        == LabOrderStatus.COMPLETED
    )

    assert result.completed_at is not None


def test_complete_order_requires_verification(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        id=5,
        clinic_id=1,
        status=LabOrderStatus.IN_PROGRESS,
        sample_collected_at=now(),
        collected_by_id=2,
        processed_by_id=3,
        processed_at=now(),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    with pytest.raises(
        ConflictError,
        match="before verification",
    ):
        lab_service.complete_order(
            5,
            1,
        )


def test_complete_order_rejects_missing_results(
    lab_service,
    monkeypatch,
):
    item = order_item_obj(
        result_value=None,
        resulted_at=None,
    )

    order = order_obj(
        id=5,
        clinic_id=1,
        status=LabOrderStatus.IN_PROGRESS,
        sample_collected_at=now(),
        collected_by_id=2,
        processed_by_id=3,
        processed_at=now(),
        verified_by_id=4,
        verified_at=now(),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult(
                [item]
            )
        ),
    )

    with pytest.raises(
        ConflictError,
        match=r"result\(s\) are missing",
    ):
        lab_service.complete_order(
            5,
            1,
        )


def test_complete_order_rejects_no_items(
    lab_service,
    monkeypatch,
):
    order = order_obj(
        id=5,
        clinic_id=1,
        status=LabOrderStatus.IN_PROGRESS,
        sample_collected_at=now(),
        collected_by_id=2,
        processed_by_id=3,
        processed_at=now(),
        verified_by_id=4,
        verified_at=now(),
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        Mock(return_value=order),
    )

    monkeypatch.setattr(
        lab_service,
        "ensure_clinic_active",
        Mock(),
    )

    monkeypatch.setattr(
        lab_service.db.session,
        "execute",
        Mock(
            return_value=FakeExecuteResult([])
        ),
    )

    with pytest.raises(
        ValidationError,
        match="no test items",
    ):
        lab_service.complete_order(
            5,
            1,
        )


# ============================================================================
# INTEGRITY
# ============================================================================


def test_validate_lab_order_integrity_accepts_ordered_state(
    lab_service,
):
    order = order_obj(
        status=LabOrderStatus.ORDERED,
    )

    lab_service.validate_lab_order_integrity(
        order
    )


def test_validate_lab_order_integrity_accepts_sample_collected(
    lab_service,
):
    order = order_obj(
        status=LabOrderStatus.SAMPLE_COLLECTED,
        collected_by_id=2,
        sample_collected_at=now(),
    )

    lab_service.validate_lab_order_integrity(
        order
    )


def test_validate_lab_order_integrity_accepts_in_progress(
    lab_service,
):
    order = order_obj(
        status=LabOrderStatus.IN_PROGRESS,
        collected_by_id=2,
        sample_collected_at=now(),
        processed_by_id=3,
        processed_at=now(),
    )

    lab_service.validate_lab_order_integrity(
        order
    )


def test_validate_lab_order_integrity_accepts_completed(
    lab_service,
):
    order = order_obj(
        status=LabOrderStatus.COMPLETED,
        collected_by_id=2,
        sample_collected_at=now(),
        processed_by_id=3,
        processed_at=now(),
        verified_by_id=4,
        verified_at=now(),
        completed_at=now(),
    )

    lab_service.validate_lab_order_integrity(
        order
    )


def test_validate_lab_order_integrity_rejects_ordered_with_collection(
    lab_service,
):
    order = order_obj(
        status=LabOrderStatus.ORDERED,
        collected_by_id=2,
        sample_collected_at=now(),
    )

    with pytest.raises(
        ValidationError,
        match="ORDERED order cannot have",
    ):
        lab_service.validate_lab_order_integrity(
            order
        )


def test_validate_lab_order_integrity_rejects_processing_without_collection(
    lab_service,
):
    order = order_obj(
        status=LabOrderStatus.IN_PROGRESS,
        processed_by_id=3,
        processed_at=now(),
    )

    with pytest.raises(
        ValidationError,
        match="sample collection",
    ):
        lab_service.validate_lab_order_integrity(
            order
        )


def test_validate_lab_order_integrity_rejects_verification_without_processing(
    lab_service,
):
    order = order_obj(
        status=LabOrderStatus.IN_PROGRESS,
        collected_by_id=2,
        sample_collected_at=now(),
        verified_by_id=4,
        verified_at=now(),
    )

    with pytest.raises(
        ValidationError,
        match="processing timestamp",
    ):
        lab_service.validate_lab_order_integrity(
            order
        )


def test_validate_lab_order_integrity_rejects_completion_without_verification(
    lab_service,
):
    order = order_obj(
        status=LabOrderStatus.COMPLETED,
        collected_by_id=2,
        sample_collected_at=now(),
        processed_by_id=3,
        processed_at=now(),
        completed_at=now(),
    )

    with pytest.raises(
        ValidationError,
        match="verification timestamp",
    ):
        lab_service.validate_lab_order_integrity(
            order
        )


def test_validate_lab_order_integrity_rejects_cancelled_with_completion(
    lab_service,
):
    order = order_obj(
        status=LabOrderStatus.CANCELLED,
        collected_by_id=2,
        sample_collected_at=now(),
        processed_by_id=3,
        processed_at=now(),
        verified_by_id=4,
        verified_at=now(),
        completed_at=now(),
    )

    with pytest.raises(
        ValidationError,
        match="CANCELLED order cannot have",
    ):
        lab_service.validate_lab_order_integrity(
            order
        )


def test_validate_lab_order_integrity_rejects_actor_without_timestamp(
    lab_service,
):
    order = order_obj(
        status=LabOrderStatus.ORDERED,
        collected_by_id=3,
        sample_collected_at=None,
    )

    with pytest.raises(
        ValidationError,
        match="must either both be set",
    ):
        lab_service.validate_lab_order_integrity(
            order
        )


# ============================================================================
# PUBLIC GETTERS
# ============================================================================


def test_get_lab_test_delegates_to_scoped_lookup(
    lab_service,
    monkeypatch,
):
    expected = make_test_obj(
        id=9,
        clinic_id=1,
    )

    helper = Mock(
        return_value=expected
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_test",
        helper,
    )

    result = lab_service.get_lab_test(
        9,
        1,
    )

    assert result is expected

    helper.assert_called_once_with(
        9,
        1,
    )


def test_get_lab_order_delegates_to_scoped_lookup(
    lab_service,
    monkeypatch,
):
    expected = order_obj(
        id=9,
        clinic_id=1,
    )

    helper = Mock(
        return_value=expected
    )

    monkeypatch.setattr(
        lab_service,
        "_get_lab_order",
        helper,
    )

    result = lab_service.get_lab_order(
        9,
        1,
    )

    assert result is expected

    helper.assert_called_once_with(
        9,
        1,
    )