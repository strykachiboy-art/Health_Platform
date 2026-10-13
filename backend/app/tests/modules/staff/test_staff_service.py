from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest

from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import (
    LeaveStatus,
    LeaveType,
    StaffStatus,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.extensions import db
from app.modules.staff.models.staff_model import (
    LeaveRequest,
    PayrollRecord,
)
from app.modules.staff.services import staff_service


def _make_leave(
    staff_id: int,
    *,
    leave_type: LeaveType = LeaveType.ANNUAL,
    status: LeaveStatus = LeaveStatus.PENDING,
    start_date: date | None = None,
    end_date: date | None = None,
    reason: str | None = "Annual leave",
    reviewed_by_user_id: int | None = None,
    reviewed_at=None,
):
    start_date = start_date or date.today() + timedelta(days=5)
    end_date = end_date or start_date + timedelta(days=2)

    leave = LeaveRequest(
        staff_id=staff_id,
        leave_type=leave_type,
        status=status,
        start_date=start_date,
        end_date=end_date,
        reason=reason,
        reviewed_by_user_id=reviewed_by_user_id,
        reviewed_at=reviewed_at,
    )

    db.session.add(leave)
    db.session.flush()

    return leave


def _make_payroll(
    staff_id: int,
    *,
    pay_period_start=date(2026, 9, 1),
    pay_period_end=date(2026, 9, 30),
    base_salary=Decimal("100000.00"),
    bonuses=Decimal("0"),
    deductions=Decimal("0"),
):
    record = PayrollRecord(
        staff_id=staff_id,
        pay_period_start=pay_period_start,
        pay_period_end=pay_period_end,
        base_salary=base_salary,
        bonuses=bonuses,
        deductions=deductions,
        net_pay=(
            base_salary
            + bonuses
            - deductions
        ),
    )

    db.session.add(record)
    db.session.flush()

    return record


def test_get_staff_returns_staff(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    result = staff_service.get_staff(
        staff_id=staff.id,
        clinic_id=clinic.id,
    )

    assert result.id == staff.id


def test_get_staff_enforces_clinic_isolation(
    clinic,
    make_clinic,
    make_staff,
):
    other_clinic = make_clinic()

    staff = make_staff(
        clinic=other_clinic,
        role=Role.DOCTOR,
    )

    with pytest.raises(NotFoundError):
        staff_service.get_staff(
            staff_id=staff.id,
            clinic_id=clinic.id,
        )


def test_get_staff_without_clinic_is_not_tenant_scoped(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    result = staff_service.get_staff(
        staff_id=staff.id,
    )

    assert result.id == staff.id


def test_get_staff_missing_raises_not_found(
    clinic,
):
    with pytest.raises(NotFoundError):
        staff_service.get_staff(
            staff_id=999999,
            clinic_id=clinic.id,
        )


def test_list_staff_returns_only_clinic_staff(
    clinic,
    make_clinic,
    make_staff,
):
    other_clinic = make_clinic()

    staff_a = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    staff_b = make_staff(
        clinic=other_clinic,
        role=Role.DOCTOR,
    )

    result = staff_service.list_staff(
        clinic_id=clinic.id,
    )

    ids = {staff.id for staff in result["items"]}

    assert staff_a.id in ids
    assert staff_b.id not in ids
    assert result["total"] == 1
    assert result["page"] == 1
    assert result["per_page"] == staff_service.DEFAULT_PER_PAGE


def test_list_staff_filters_by_status(
    clinic,
    make_staff,
):
    active = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )

    suspended = make_staff(
        clinic=clinic,
        role=Role.NURSE,
        status=StaffStatus.SUSPENDED,
    )

    result = staff_service.list_staff(
        clinic_id=clinic.id,
        status=StaffStatus.SUSPENDED,
    )

    ids = {staff.id for staff in result["items"]}

    assert suspended.id in ids
    assert active.id not in ids
    assert result["total"] == 1


def test_list_staff_searches_first_and_last_name(
    clinic,
    make_staff,
):
    first = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        first_name="Cardio",
        last_name="Doctor",
    )

    second = make_staff(
        clinic=clinic,
        role=Role.NURSE,
        first_name="Jane",
        last_name="Cardiology",
    )

    result = staff_service.list_staff(
        clinic_id=clinic.id,
        search="  cardiology  ",
    )

    ids = {staff.id for staff in result["items"]}

    assert second.id in ids
    assert first.id not in ids


def test_list_staff_blank_search_behaves_like_no_search(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    result = staff_service.list_staff(
        clinic_id=clinic.id,
        search="   ",
    )

    ids = {item.id for item in result["items"]}

    assert staff.id in ids


def test_list_staff_paginates(
    clinic,
    make_staff,
):
    staff_records = [
        make_staff(
            clinic=clinic,
            role=Role.DOCTOR,
            first_name=f"First{index}",
            last_name=f"Last{index}",
        )
        for index in range(5)
    ]

    page_one = staff_service.list_staff(
        clinic_id=clinic.id,
        page=1,
        per_page=2,
    )

    page_two = staff_service.list_staff(
        clinic_id=clinic.id,
        page=2,
        per_page=2,
    )

    page_three = staff_service.list_staff(
        clinic_id=clinic.id,
        page=3,
        per_page=2,
    )

    assert page_one["total"] == 5
    assert page_one["page"] == 1
    assert page_one["per_page"] == 2
    assert len(page_one["items"]) == 2

    assert page_two["total"] == 5
    assert page_two["page"] == 2
    assert page_two["per_page"] == 2
    assert len(page_two["items"]) == 2

    assert page_three["total"] == 5
    assert page_three["page"] == 3
    assert page_three["per_page"] == 2
    assert len(page_three["items"]) == 1

    returned_ids = {
        item.id
        for result in (
            page_one,
            page_two,
            page_three,
        )
        for item in result["items"]
    }

    assert returned_ids == {
        item.id
        for item in staff_records
    }


def test_list_staff_empty_page_returns_empty_items(
    clinic,
    make_staff,
):
    make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    result = staff_service.list_staff(
        clinic_id=clinic.id,
        page=2,
        per_page=1,
    )

    assert result["total"] == 1
    assert result["page"] == 2
    assert result["per_page"] == 1
    assert result["items"] == []


@pytest.mark.parametrize(
    ("page", "per_page"),
    [
        (0, 50),
        (-1, 50),
        (1, 0),
        (1, -1),
        (1, staff_service.MAX_PER_PAGE + 1),
    ],
)
def test_list_staff_rejects_invalid_pagination(
    clinic,
    page,
    per_page,
):
    with pytest.raises(ValidationError):
        staff_service.list_staff(
            clinic_id=clinic.id,
            page=page,
            per_page=per_page,
        )


def test_create_staff_success(
    clinic,
    make_user,
    monkeypatch,
):
    audit = Mock()

    monkeypatch.setattr(
        staff_service,
        "create_audit_log",
        audit,
    )

    user = make_user(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    result = staff_service.create_staff(
        clinic_id=clinic.id,
        first_name="  Jane  ",
        last_name="  Smith  ",
        user_id=user.id,
        specialty="Cardiology",
        phone="08000000000",
        email="jane@example.com",
        hired_at=date(2026, 9, 1),
    )

    assert result.id is not None
    assert result.clinic_id == clinic.id
    assert result.user_id == user.id
    assert result.first_name == "Jane"
    assert result.last_name == "Smith"
    assert result.specialty == "Cardiology"
    assert result.phone == "08000000000"
    assert result.email == "jane@example.com"
    assert result.hired_at == date(2026, 9, 1)
    assert result.status == StaffStatus.ACTIVE

    audit.assert_called_once()

    kwargs = audit.call_args.kwargs

    assert kwargs["action"] == AuditAction.CREATE
    assert kwargs["entity_type"] == "Staff"
    assert kwargs["entity_id"] == result.id


def test_create_staff_without_linked_user_succeeds(
    clinic,
):
    result = staff_service.create_staff(
        clinic_id=clinic.id,
        first_name="Jane",
        last_name="Smith",
    )

    assert result.clinic_id == clinic.id
    assert result.user_id is None
    assert result.status == StaffStatus.ACTIVE


def test_create_staff_rejects_blank_first_name(
    clinic,
):
    with pytest.raises(
        ValidationError,
        match="First name is required",
    ):
        staff_service.create_staff(
            clinic_id=clinic.id,
            first_name="   ",
            last_name="Smith",
        )


def test_create_staff_rejects_blank_last_name(
    clinic,
):
    with pytest.raises(
        ValidationError,
        match="Last name is required",
    ):
        staff_service.create_staff(
            clinic_id=clinic.id,
            first_name="Jane",
            last_name="   ",
        )


def test_create_staff_rejects_missing_clinic(
    app,
):
    with pytest.raises(NotFoundError):
        staff_service.create_staff(
            clinic_id=999999,
            first_name="Jane",
            last_name="Smith",
        )


def test_create_staff_rejects_inactive_clinic(
    suspended_clinic,
):
    with pytest.raises(
        ConflictError,
        match="not active",
    ):
        staff_service.create_staff(
            clinic_id=suspended_clinic.id,
            first_name="Jane",
            last_name="Smith",
        )


def test_create_staff_rejects_inactive_user(
    clinic,
    make_user,
):
    user = make_user(
        clinic=clinic,
        role=Role.DOCTOR,
        is_active=False,
    )

    with pytest.raises(
        ValidationError,
        match="User account is inactive",
    ):
        staff_service.create_staff(
            clinic_id=clinic.id,
            first_name="Jane",
            last_name="Smith",
            user_id=user.id,
        )


def test_create_staff_rejects_user_from_another_clinic(
    clinic,
    make_clinic,
    make_user,
):
    other_clinic = make_clinic()

    user = make_user(
        clinic=other_clinic,
        role=Role.DOCTOR,
    )

    with pytest.raises(NotFoundError):
        staff_service.create_staff(
            clinic_id=clinic.id,
            first_name="Jane",
            last_name="Smith",
            user_id=user.id,
        )


def test_create_staff_rejects_duplicate_user_link(
    clinic,
    make_staff,
):
    existing_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    with pytest.raises(
        ConflictError,
        match="already linked to staff",
    ):
        staff_service.create_staff(
            clinic_id=clinic.id,
            first_name="Jane",
            last_name="Smith",
            user_id=existing_staff.user_id,
        )


def test_update_staff_updates_allowed_fields(
    clinic,
    make_staff,
    monkeypatch,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        first_name="John",
        last_name="Doe",
    )

    audit = Mock()

    monkeypatch.setattr(
        staff_service,
        "create_audit_log",
        audit,
    )

    result = staff_service.update_staff(
        staff_id=staff.id,
        clinic_id=clinic.id,
        first_name="  Jane  ",
        last_name="  Smith  ",
        specialty="Cardiology",
    )

    assert result.first_name == "Jane"
    assert result.last_name == "Smith"
    assert result.specialty == "Cardiology"

    audit.assert_called_once()

    kwargs = audit.call_args.kwargs

    assert kwargs["action"] == AuditAction.UPDATE
    assert kwargs["entity_type"] == "Staff"
    assert kwargs["old_value"]["first_name"] == "John"
    assert kwargs["new_value"]["first_name"] == "Jane"


def test_update_staff_rejects_unknown_fields(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    with pytest.raises(
        ValidationError,
        match="Unknown staff field",
    ):
        staff_service.update_staff(
            staff_id=staff.id,
            clinic_id=clinic.id,
            role="admin",
        )


def test_update_staff_rejects_blank_first_name(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    with pytest.raises(
        ValidationError,
        match="First Name is required",
    ):
        staff_service.update_staff(
            staff_id=staff.id,
            clinic_id=clinic.id,
            first_name="   ",
        )


def test_update_staff_rejects_none_last_name(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    with pytest.raises(
        ValidationError,
        match="Last Name is required",
    ):
        staff_service.update_staff(
            staff_id=staff.id,
            clinic_id=clinic.id,
            last_name=None,
        )


def test_update_staff_same_values_produce_no_audit(
    clinic,
    make_staff,
    monkeypatch,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        first_name="John",
    )

    audit = Mock()

    monkeypatch.setattr(
        staff_service,
        "create_audit_log",
        audit,
    )

    result = staff_service.update_staff(
        staff_id=staff.id,
        clinic_id=clinic.id,
        first_name="John",
    )

    assert result.first_name == "John"
    audit.assert_not_called()


def test_update_staff_enforces_clinic_isolation(
    clinic,
    make_clinic,
    make_staff,
):
    other_clinic = make_clinic()

    staff = make_staff(
        clinic=other_clinic,
        role=Role.DOCTOR,
    )

    with pytest.raises(NotFoundError):
        staff_service.update_staff(
            staff_id=staff.id,
            clinic_id=clinic.id,
            first_name="Updated",
        )


def test_update_staff_rejects_inactive_clinic(
    suspended_clinic,
    make_staff,
):
    staff = make_staff(
        clinic=suspended_clinic,
        role=Role.DOCTOR,
    )

    with pytest.raises(ConflictError):
        staff_service.update_staff(
            staff_id=staff.id,
            clinic_id=suspended_clinic.id,
            first_name="Updated",
        )


def test_change_staff_status_changes_status(
    clinic,
    make_staff,
    monkeypatch,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )

    audit = Mock()

    monkeypatch.setattr(
        staff_service,
        "create_audit_log",
        audit,
    )

    result = staff_service.change_staff_status(
        staff_id=staff.id,
        clinic_id=clinic.id,
        new_status=StaffStatus.SUSPENDED,
    )

    assert result.status == StaffStatus.SUSPENDED

    audit.assert_called_once()

    kwargs = audit.call_args.kwargs

    assert kwargs["action"] == AuditAction.STATUS_CHANGE
    assert kwargs["old_value"]["status"] == StaffStatus.ACTIVE.value
    assert kwargs["new_value"]["status"] == StaffStatus.SUSPENDED.value


def test_change_staff_status_same_status_is_noop(
    clinic,
    make_staff,
    monkeypatch,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )

    audit = Mock()

    monkeypatch.setattr(
        staff_service,
        "create_audit_log",
        audit,
    )

    result = staff_service.change_staff_status(
        staff_id=staff.id,
        clinic_id=clinic.id,
        new_status=StaffStatus.ACTIVE,
    )

    assert result.status == StaffStatus.ACTIVE
    audit.assert_not_called()


@pytest.mark.parametrize(
    "new_status",
    [
        StaffStatus.SUSPENDED,
        StaffStatus.TERMINATED,
    ],
)
def test_change_staff_status_revokes_user_tokens_for_suspended_or_terminated_staff(
    clinic,
    make_staff,
    monkeypatch,
    new_status,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )
    revoke_tokens = Mock()

    monkeypatch.setattr(
        staff_service,
        "revoke_user_tokens",
        revoke_tokens,
    )
    monkeypatch.setattr(
        staff_service,
        "create_audit_log",
        Mock(),
    )

    result = staff_service.change_staff_status(
        staff_id=staff.id,
        clinic_id=clinic.id,
        new_status=new_status,
    )

    assert result.status == new_status
    revoke_tokens.assert_called_once_with(staff.user_id)


def test_change_staff_status_does_not_revoke_user_tokens_for_on_leave(
    clinic,
    make_staff,
    monkeypatch,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )
    revoke_tokens = Mock()

    monkeypatch.setattr(
        staff_service,
        "revoke_user_tokens",
        revoke_tokens,
    )
    monkeypatch.setattr(
        staff_service,
        "create_audit_log",
        Mock(),
    )

    result = staff_service.change_staff_status(
        staff_id=staff.id,
        clinic_id=clinic.id,
        new_status=StaffStatus.ON_LEAVE,
    )

    assert result.status == StaffStatus.ON_LEAVE
    revoke_tokens.assert_not_called()


def test_request_leave_success(
    clinic,
    make_staff,
    monkeypatch,
):
    user_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    audit = Mock()

    monkeypatch.setattr(
        staff_service,
        "create_audit_log",
        audit,
    )

    start = date(2026, 9, 10)
    end = date(2026, 9, 12)

    result = staff_service.request_leave(
        clinic_id=clinic.id,
        staff_id=user_staff.id,
        actor_user_id=user_staff.user_id,
        leave_type=LeaveType.ANNUAL,
        start_date=start,
        end_date=end,
        reason="  Annual leave  ",
    )

    assert result.staff_id == user_staff.id
    assert result.leave_type == LeaveType.ANNUAL
    assert result.status == LeaveStatus.PENDING
    assert result.start_date == start
    assert result.end_date == end
    assert result.reason == "Annual leave"

    audit.assert_called_once()

    kwargs = audit.call_args.kwargs

    assert kwargs["action"] == AuditAction.CREATE
    assert kwargs["entity_type"] == "LeaveRequest"


def test_request_leave_rejects_wrong_actor(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    other_staff = make_staff(
        clinic=clinic,
        role=Role.NURSE,
    )

    with pytest.raises(
        ValidationError,
        match="does not belong",
    ):
        staff_service.request_leave(
            clinic_id=clinic.id,
            staff_id=staff.id,
            actor_user_id=other_staff.user_id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 9, 10),
            end_date=date(2026, 9, 12),
        )


def test_request_leave_rejects_invalid_date_range(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    with pytest.raises(
        ValidationError,
        match="cannot be before",
    ):
        staff_service.request_leave(
            clinic_id=clinic.id,
            staff_id=staff.id,
            actor_user_id=staff.user_id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 9, 20),
            end_date=date(2026, 9, 10),
        )


@pytest.mark.parametrize(
    "status",
    [
        StaffStatus.SUSPENDED,
        StaffStatus.TERMINATED,
    ],
)
def test_request_leave_rejects_inactive_staff(
    clinic,
    make_staff,
    status,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=status,
    )

    with pytest.raises(
        ConflictError,
        match="cannot request leave",
    ):
        staff_service.request_leave(
            clinic_id=clinic.id,
            staff_id=staff.id,
            actor_user_id=staff.user_id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 9, 10),
            end_date=date(2026, 9, 12),
        )


def test_request_leave_rejects_overlapping_pending(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    _make_leave(
        staff.id,
        status=LeaveStatus.PENDING,
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 15),
    )

    with pytest.raises(
        ConflictError,
        match="pending",
    ):
        staff_service.request_leave(
            clinic_id=clinic.id,
            staff_id=staff.id,
            actor_user_id=staff.user_id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 9, 12),
            end_date=date(2026, 9, 18),
        )


def test_request_leave_rejects_overlapping_approved(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    _make_leave(
        staff.id,
        status=LeaveStatus.APPROVED,
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 15),
    )

    with pytest.raises(
        ConflictError,
        match="approved leave",
    ):
        staff_service.request_leave(
            clinic_id=clinic.id,
            staff_id=staff.id,
            actor_user_id=staff.user_id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 9, 12),
            end_date=date(2026, 9, 18),
        )


def test_get_leave_request_returns_clinic_owned_leave(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    leave = _make_leave(staff.id)

    result = staff_service.get_leave_request(
        leave_id=leave.id,
        clinic_id=clinic.id,
    )

    assert result.id == leave.id


def test_get_leave_request_hides_foreign_clinic_leave(
    clinic,
    make_clinic,
    make_staff,
):
    other_clinic = make_clinic()

    staff = make_staff(
        clinic=other_clinic,
        role=Role.DOCTOR,
    )

    leave = _make_leave(staff.id)

    with pytest.raises(NotFoundError):
        staff_service.get_leave_request(
            leave_id=leave.id,
            clinic_id=clinic.id,
        )


def test_list_leave_requests_filters_clinic(
    clinic,
    make_clinic,
    make_staff,
):
    other_clinic = make_clinic()

    local_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    foreign_staff = make_staff(
        clinic=other_clinic,
        role=Role.DOCTOR,
    )

    local_leave = _make_leave(local_staff.id)
    foreign_leave = _make_leave(foreign_staff.id)

    result = staff_service.list_leave_requests(
        clinic_id=clinic.id,
    )

    ids = {leave.id for leave in result["items"]}

    assert local_leave.id in ids
    assert foreign_leave.id not in ids
    assert result["total"] == 1
    assert result["page"] == 1
    assert result["per_page"] == staff_service.DEFAULT_PER_PAGE


def test_list_leave_requests_filters_staff_and_status(
    clinic,
    make_staff,
):
    staff_a = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    staff_b = make_staff(
        clinic=clinic,
        role=Role.NURSE,
    )

    matching = _make_leave(
        staff_a.id,
        status=LeaveStatus.PENDING,
    )

    _make_leave(
        staff_a.id,
        status=LeaveStatus.APPROVED,
    )

    _make_leave(
        staff_b.id,
        status=LeaveStatus.PENDING,
    )

    result = staff_service.list_leave_requests(
        clinic_id=clinic.id,
        staff_id=staff_a.id,
        status=LeaveStatus.PENDING,
    )

    ids = {leave.id for leave in result["items"]}

    assert ids == {matching.id}
    assert result["total"] == 1


def test_list_leave_requests_paginates(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    leaves = [
        _make_leave(
            staff.id,
            start_date=date(2026, 10, index + 1),
            end_date=date(2026, 10, index + 2),
        )
        for index in range(5)
    ]

    page_one = staff_service.list_leave_requests(
        clinic_id=clinic.id,
        page=1,
        per_page=2,
    )

    page_two = staff_service.list_leave_requests(
        clinic_id=clinic.id,
        page=2,
        per_page=2,
    )

    page_three = staff_service.list_leave_requests(
        clinic_id=clinic.id,
        page=3,
        per_page=2,
    )

    assert page_one["total"] == 5
    assert page_one["page"] == 1
    assert page_one["per_page"] == 2
    assert len(page_one["items"]) == 2

    assert page_two["total"] == 5
    assert page_two["page"] == 2
    assert page_two["per_page"] == 2
    assert len(page_two["items"]) == 2

    assert page_three["total"] == 5
    assert page_three["page"] == 3
    assert page_three["per_page"] == 2
    assert len(page_three["items"]) == 1

    returned_ids = {
        leave.id
        for result in (
            page_one,
            page_two,
            page_three,
        )
        for leave in result["items"]
    }

    assert returned_ids == {
        leave.id
        for leave in leaves
    }


def test_list_leave_requests_empty_page_returns_empty_items(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    _make_leave(staff.id)

    result = staff_service.list_leave_requests(
        clinic_id=clinic.id,
        page=2,
        per_page=1,
    )

    assert result["total"] == 1
    assert result["page"] == 2
    assert result["per_page"] == 1
    assert result["items"] == []


@pytest.mark.parametrize(
    ("page", "per_page"),
    [
        (0, 50),
        (-1, 50),
        (1, 0),
        (1, -1),
        (1, staff_service.MAX_PER_PAGE + 1),
    ],
)
def test_list_leave_requests_rejects_invalid_pagination(
    clinic,
    page,
    per_page,
):
    with pytest.raises(ValidationError):
        staff_service.list_leave_requests(
            clinic_id=clinic.id,
            page=page,
            per_page=per_page,
        )


def test_approve_leave_success(
    clinic,
    make_staff,
    monkeypatch,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    admin = make_staff(
        clinic=clinic,
        role=Role.ADMIN,
    )

    start = date.today()
    end = start + timedelta(days=2)

    leave = _make_leave(
        staff.id,
        status=LeaveStatus.PENDING,
        start_date=start,
        end_date=end,
    )

    audit = Mock()

    monkeypatch.setattr(
        staff_service,
        "create_audit_log",
        audit,
    )

    result = staff_service.approve_leave_request(
        leave_id=leave.id,
        clinic_id=clinic.id,
        reviewer_user_id=admin.user_id,
    )

    assert result.status == LeaveStatus.APPROVED
    assert result.reviewed_by_user_id == admin.user_id
    assert result.reviewed_at is not None

    db.session.refresh(staff)

    assert staff.status == StaffStatus.ON_LEAVE

    audit.assert_called_once()

    kwargs = audit.call_args.kwargs

    assert kwargs["action"] == AuditAction.STATUS_CHANGE
    assert kwargs["entity_type"] == "LeaveRequest"


def test_approve_leave_requires_pending_status(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    admin = make_staff(
        clinic=clinic,
        role=Role.ADMIN,
    )

    leave = _make_leave(
        staff.id,
        status=LeaveStatus.APPROVED,
    )

    with pytest.raises(
        ConflictError,
        match="already",
    ):
        staff_service.approve_leave_request(
            leave_id=leave.id,
            clinic_id=clinic.id,
            reviewer_user_id=admin.user_id,
        )


def test_approve_leave_requires_admin(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    reviewer = make_staff(
        clinic=clinic,
        role=Role.NURSE,
    )

    leave = _make_leave(staff.id)

    with pytest.raises(
        ValidationError,
        match="administrator",
    ):
        staff_service.approve_leave_request(
            leave_id=leave.id,
            clinic_id=clinic.id,
            reviewer_user_id=reviewer.user_id,
        )


def test_approve_leave_rejects_inactive_reviewer(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    admin = make_staff(
        clinic=clinic,
        role=Role.ADMIN,
        user_is_active=False,
    )

    leave = _make_leave(staff.id)

    with pytest.raises(
        ValidationError,
        match="inactive",
    ):
        staff_service.approve_leave_request(
            leave_id=leave.id,
            clinic_id=clinic.id,
            reviewer_user_id=admin.user_id,
        )


def test_approve_leave_rejects_overlapping_approved_leave(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    admin = make_staff(
        clinic=clinic,
        role=Role.ADMIN,
    )

    _make_leave(
        staff.id,
        status=LeaveStatus.APPROVED,
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 15),
    )

    pending = _make_leave(
        staff.id,
        status=LeaveStatus.PENDING,
        start_date=date(2026, 9, 12),
        end_date=date(2026, 9, 18),
    )

    with pytest.raises(
        ConflictError,
        match="approved leave",
    ):
        staff_service.approve_leave_request(
            leave_id=pending.id,
            clinic_id=clinic.id,
            reviewer_user_id=admin.user_id,
        )


def test_reject_leave_success_with_reason(
    clinic,
    make_staff,
    monkeypatch,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    admin = make_staff(
        clinic=clinic,
        role=Role.ADMIN,
    )

    leave = _make_leave(
        staff.id,
        reason="Annual leave",
    )

    audit = Mock()

    monkeypatch.setattr(
        staff_service,
        "create_audit_log",
        audit,
    )

    result = staff_service.reject_leave_request(
        leave_id=leave.id,
        clinic_id=clinic.id,
        reviewer_user_id=admin.user_id,
        reason="  Staffing shortage  ",
    )

    assert result.status == LeaveStatus.REJECTED
    assert result.reviewed_by_user_id == admin.user_id
    assert result.reviewed_at is not None
    assert "Annual leave" in result.reason
    assert "Rejection note: Staffing shortage" in result.reason

    audit.assert_called_once()


def test_reject_leave_allows_no_reason(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    admin = make_staff(
        clinic=clinic,
        role=Role.ADMIN,
    )

    leave = _make_leave(
        staff.id,
        reason="Annual leave",
    )

    result = staff_service.reject_leave_request(
        leave_id=leave.id,
        clinic_id=clinic.id,
        reviewer_user_id=admin.user_id,
        reason=None,
    )

    assert result.status == LeaveStatus.REJECTED
    assert result.reason == "Annual leave"


def test_reject_leave_rejects_blank_reason(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    admin = make_staff(
        clinic=clinic,
        role=Role.ADMIN,
    )

    leave = _make_leave(staff.id)

    with pytest.raises(
        ValidationError,
        match="Rejection reason cannot be empty",
    ):
        staff_service.reject_leave_request(
            leave_id=leave.id,
            clinic_id=clinic.id,
            reviewer_user_id=admin.user_id,
            reason="   ",
        )


def test_reject_leave_requires_admin(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    reviewer = make_staff(
        clinic=clinic,
        role=Role.RECEPTIONIST,
    )

    leave = _make_leave(staff.id)

    with pytest.raises(
        ValidationError,
        match="administrator",
    ):
        staff_service.reject_leave_request(
            leave_id=leave.id,
            clinic_id=clinic.id,
            reviewer_user_id=reviewer.user_id,
        )


def test_restore_staff_from_expired_leave(
    clinic,
    make_staff,
    monkeypatch,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ON_LEAVE,
    )

    _make_leave(
        staff.id,
        status=LeaveStatus.APPROVED,
        start_date=date.today() - timedelta(days=10),
        end_date=date.today() - timedelta(days=2),
    )

    audit = Mock()

    monkeypatch.setattr(
        staff_service,
        "create_audit_log",
        audit,
    )

    restored = staff_service.restore_staff_from_expired_leave()

    db.session.refresh(staff)

    assert restored >= 1
    assert staff.status == StaffStatus.ACTIVE

    audit.assert_called()


def test_restore_staff_keeps_current_leave_staff_on_leave(
    clinic,
    make_staff,
    monkeypatch,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ON_LEAVE,
    )

    _make_leave(
        staff.id,
        status=LeaveStatus.APPROVED,
        start_date=date.today() - timedelta(days=1),
        end_date=date.today() + timedelta(days=5),
    )

    audit = Mock()

    monkeypatch.setattr(
        staff_service,
        "create_audit_log",
        audit,
    )

    restored = staff_service.restore_staff_from_expired_leave()

    db.session.refresh(staff)

    assert restored == 0
    assert staff.status == StaffStatus.ON_LEAVE
    audit.assert_not_called()


def test_get_payroll_record_returns_record(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    record = _make_payroll(staff.id)

    result = staff_service.get_payroll_record(
        record_id=record.id,
        clinic_id=clinic.id,
    )

    assert result.id == record.id


def test_get_payroll_record_enforces_clinic(
    clinic,
    make_clinic,
    make_staff,
):
    other_clinic = make_clinic()

    staff = make_staff(
        clinic=other_clinic,
        role=Role.DOCTOR,
    )

    record = _make_payroll(staff.id)

    with pytest.raises(NotFoundError):
        staff_service.get_payroll_record(
            record_id=record.id,
            clinic_id=clinic.id,
        )


def test_list_payroll_returns_only_clinic_records(
    clinic,
    make_clinic,
    make_staff,
):
    other_clinic = make_clinic()

    local_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    foreign_staff = make_staff(
        clinic=other_clinic,
        role=Role.DOCTOR,
    )

    local_record = _make_payroll(local_staff.id)
    foreign_record = _make_payroll(foreign_staff.id)

    result = staff_service.list_payroll(
        clinic_id=clinic.id,
    )

    ids = {record.id for record in result["items"]}

    assert local_record.id in ids
    assert foreign_record.id not in ids
    assert result["total"] == 1
    assert result["page"] == 1
    assert result["per_page"] == staff_service.DEFAULT_PER_PAGE


def test_list_payroll_filters_by_staff(
    clinic,
    make_staff,
):
    staff_a = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    staff_b = make_staff(
        clinic=clinic,
        role=Role.NURSE,
    )

    matching = _make_payroll(staff_a.id)
    _make_payroll(staff_b.id)

    result = staff_service.list_payroll_for_staff(
        clinic_id=clinic.id,
        staff_id=staff_a.id,
    )

    ids = {
        record.id
        for record in result["items"]
    }

    assert ids == {matching.id}
    assert result["total"] == 1
    assert result["page"] == 1
    assert (
        result["per_page"]
        == staff_service.DEFAULT_PER_PAGE
    )


def test_list_payroll_for_staff_returns_staff_records(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    first = _make_payroll(
        staff.id,
        pay_period_start=date(2026, 8, 1),
        pay_period_end=date(2026, 8, 31),
    )

    second = _make_payroll(
        staff.id,
        pay_period_start=date(2026, 9, 1),
        pay_period_end=date(2026, 9, 30),
    )

    result = staff_service.list_payroll_for_staff(
        clinic_id=clinic.id,
        staff_id=staff.id,
    )

    ids = {record.id for record in result["items"]}

    assert ids == {first.id, second.id}
    assert result["total"] == 2
    assert result["page"] == 1
    assert result["per_page"] == staff_service.DEFAULT_PER_PAGE


def test_list_payroll_for_staff_requires_clinic_owned_staff(
    clinic,
    make_clinic,
    make_staff,
):
    other_clinic = make_clinic()

    foreign_staff = make_staff(
        clinic=other_clinic,
        role=Role.DOCTOR,
    )

    with pytest.raises(NotFoundError):
        staff_service.list_payroll_for_staff(
            clinic_id=clinic.id,
            staff_id=foreign_staff.id,
        )


def test_list_payroll_paginates(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    records = [
        _make_payroll(
            staff.id,
            pay_period_start=date(
                2026,
                index + 1,
                1,
            ),
            pay_period_end=date(
                2026,
                index + 1,
                28,
            ),
        )
        for index in range(1, 5)
    ]

    page_one = staff_service.list_payroll(
        clinic_id=clinic.id,
        page=1,
        per_page=2,
    )

    page_two = staff_service.list_payroll(
        clinic_id=clinic.id,
        page=2,
        per_page=2,
    )

    assert page_one["total"] == 4
    assert page_one["page"] == 1
    assert page_one["per_page"] == 2
    assert len(page_one["items"]) == 2

    assert page_two["total"] == 4
    assert page_two["page"] == 2
    assert page_two["per_page"] == 2
    assert len(page_two["items"]) == 2

    returned_ids = {
        record.id
        for result in (
            page_one,
            page_two,
        )
        for record in result["items"]
    }

    assert returned_ids == {
        record.id
        for record in records
    }


def test_list_payroll_for_staff_paginates(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    records = [
        _make_payroll(
            staff.id,
            pay_period_start=date(
                2026,
                index + 1,
                1,
            ),
            pay_period_end=date(
                2026,
                index + 1,
                28,
            ),
        )
        for index in range(1, 5)
    ]

    result = staff_service.list_payroll_for_staff(
        clinic_id=clinic.id,
        staff_id=staff.id,
        page=1,
        per_page=2,
    )

    assert result["total"] == 4
    assert result["page"] == 1
    assert result["per_page"] == 2
    assert len(result["items"]) == 2

    assert {
        record.id
        for record in result["items"]
    }.issubset(
        {record.id for record in records}
    )


def test_list_payroll_empty_page_returns_empty_items(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    _make_payroll(staff.id)

    result = staff_service.list_payroll(
        clinic_id=clinic.id,
        page=2,
        per_page=1,
    )

    assert result["total"] == 1
    assert result["page"] == 2
    assert result["per_page"] == 1
    assert result["items"] == []


@pytest.mark.parametrize(
    ("page", "per_page"),
    [
        (0, 50),
        (-1, 50),
        (1, 0),
        (1, -1),
        (1, staff_service.MAX_PER_PAGE + 1),
    ],
)
def test_list_payroll_rejects_invalid_pagination(
    clinic,
    page,
    per_page,
):
    with pytest.raises(ValidationError):
        staff_service.list_payroll(
            clinic_id=clinic.id,
            page=page,
            per_page=per_page,
        )


def test_create_payroll_success(
    clinic,
    make_staff,
    monkeypatch,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    audit = Mock()

    monkeypatch.setattr(
        staff_service,
        "create_audit_log",
        audit,
    )

    result = staff_service.create_payroll_record(
        clinic_id=clinic.id,
        staff_id=staff.id,
        pay_period_start=date(2026, 9, 1),
        pay_period_end=date(2026, 9, 30),
        base_salary=Decimal("100000"),
        bonuses=Decimal("5000"),
        deductions=Decimal("1000"),
    )

    assert result.staff_id == staff.id
    assert result.base_salary == Decimal("100000")
    assert result.bonuses == Decimal("5000")
    assert result.deductions == Decimal("1000")
    assert result.net_pay == Decimal("104000")

    audit.assert_called_once()

    kwargs = audit.call_args.kwargs

    assert kwargs["action"] == AuditAction.CREATE
    assert kwargs["entity_type"] == "PayrollRecord"


def test_create_payroll_rejects_invalid_period(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    with pytest.raises(
        ValidationError,
        match="cannot be before",
    ):
        staff_service.create_payroll_record(
            clinic_id=clinic.id,
            staff_id=staff.id,
            pay_period_start=date(2026, 9, 30),
            pay_period_end=date(2026, 9, 1),
            base_salary=Decimal("100000"),
        )


@pytest.mark.parametrize(
    (
        "field",
        "value",
        "message",
    ),
    [
        (
            "base_salary",
            Decimal("-1"),
            "Base salary cannot be negative",
        ),
        (
            "bonuses",
            Decimal("-1"),
            "Bonuses cannot be negative",
        ),
        (
            "deductions",
            Decimal("-1"),
            "Deductions cannot be negative",
        ),
    ],
)
def test_create_payroll_rejects_negative_values(
    clinic,
    make_staff,
    field,
    value,
    message,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    kwargs = {
        "clinic_id": clinic.id,
        "staff_id": staff.id,
        "pay_period_start": date(2026, 9, 1),
        "pay_period_end": date(2026, 9, 30),
        "base_salary": Decimal("100000"),
        "bonuses": Decimal("0"),
        "deductions": Decimal("0"),
    }

    kwargs[field] = value

    with pytest.raises(
        ValidationError,
        match=message,
    ):
        staff_service.create_payroll_record(
            **kwargs,
        )


def test_create_payroll_rejects_negative_net_pay(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    with pytest.raises(
        ValidationError,
        match="Net pay cannot be negative",
    ):
        staff_service.create_payroll_record(
            clinic_id=clinic.id,
            staff_id=staff.id,
            pay_period_start=date(2026, 9, 1),
            pay_period_end=date(2026, 9, 30),
            base_salary=Decimal("100000"),
            deductions=Decimal("100001"),
        )


def test_create_payroll_rejects_duplicate_period(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    _make_payroll(staff.id)

    with pytest.raises(
        ConflictError,
        match="already exists",
    ):
        staff_service.create_payroll_record(
            clinic_id=clinic.id,
            staff_id=staff.id,
            pay_period_start=date(2026, 9, 1),
            pay_period_end=date(2026, 9, 30),
            base_salary=Decimal("100000"),
        )


def test_generate_payroll_for_active_staff(
    clinic,
    make_staff,
    monkeypatch,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )

    audit = Mock()

    monkeypatch.setattr(
        staff_service,
        "create_audit_log",
        audit,
    )

    results = staff_service.generate_payroll_for_period(
        clinic_id=clinic.id,
        pay_period_start=date(2026, 9, 1),
        pay_period_end=date(2026, 9, 30),
        salary_lookup={
            staff.id: Decimal("125000"),
        },
    )

    assert len(results) == 1
    assert results[0].staff_id == staff.id
    assert results[0].base_salary == Decimal("125000")
    assert results[0].net_pay == Decimal("125000")

    audit.assert_called_once()


def test_generate_payroll_skips_staff_without_salary(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )

    results = staff_service.generate_payroll_for_period(
        clinic_id=clinic.id,
        pay_period_start=date(2026, 9, 1),
        pay_period_end=date(2026, 9, 30),
        salary_lookup={},
    )

    assert results == []


def test_generate_payroll_skips_inactive_staff(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.SUSPENDED,
    )

    results = staff_service.generate_payroll_for_period(
        clinic_id=clinic.id,
        pay_period_start=date(2026, 9, 1),
        pay_period_end=date(2026, 9, 30),
        salary_lookup={
            staff.id: Decimal("100000"),
        },
    )

    assert results == []


def test_generate_payroll_rejects_invalid_period(
    clinic,
):
    with pytest.raises(
        ValidationError,
        match="cannot be before",
    ):
        staff_service.generate_payroll_for_period(
            clinic_id=clinic.id,
            pay_period_start=date(2026, 9, 30),
            pay_period_end=date(2026, 9, 1),
            salary_lookup={},
        )


def test_generate_payroll_rejects_negative_salary(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )

    with pytest.raises(
        ValidationError,
        match="Base salary cannot be negative",
    ):
        staff_service.generate_payroll_for_period(
            clinic_id=clinic.id,
            pay_period_start=date(2026, 9, 1),
            pay_period_end=date(2026, 9, 30),
            salary_lookup={
                staff.id: Decimal("-100"),
            },
        )


def test_generate_payroll_skips_existing_records(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )

    existing = _make_payroll(staff.id)

    results = staff_service.generate_payroll_for_period(
        clinic_id=clinic.id,
        pay_period_start=existing.pay_period_start,
        pay_period_end=existing.pay_period_end,
        salary_lookup={
            staff.id: Decimal("150000"),
        },
    )

    assert results == []


def test_generate_payroll_only_generates_for_requested_clinic(
    clinic,
    make_clinic,
    make_staff,
):
    other_clinic = make_clinic()

    local_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )

    foreign_staff = make_staff(
        clinic=other_clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )

    results = staff_service.generate_payroll_for_period(
        clinic_id=clinic.id,
        pay_period_start=date(2026, 9, 1),
        pay_period_end=date(2026, 9, 30),
        salary_lookup={
            local_staff.id: Decimal("100000"),
            foreign_staff.id: Decimal("200000"),
        },
    )

    ids = {record.staff_id for record in results}

    assert local_staff.id in ids
    assert foreign_staff.id not in ids


def test_mark_payroll_paid_success(
    clinic,
    make_staff,
    monkeypatch,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    record = _make_payroll(staff.id)

    audit = Mock()

    monkeypatch.setattr(
        staff_service,
        "create_audit_log",
        audit,
    )

    result = staff_service.mark_payroll_paid(
        record_id=record.id,
        clinic_id=clinic.id,
    )

    assert result.paid_at is not None

    audit.assert_called_once()

    kwargs = audit.call_args.kwargs

    assert kwargs["action"] == AuditAction.PAYMENT
    assert kwargs["entity_type"] == "PayrollRecord"


def test_mark_payroll_paid_rejects_already_paid(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    record = _make_payroll(staff.id)
    record.paid_at = staff_service._utcnow()
    db.session.flush()

    with pytest.raises(
        ConflictError,
        match="already marked paid",
    ):
        staff_service.mark_payroll_paid(
            record_id=record.id,
            clinic_id=clinic.id,
        )


def test_mark_payroll_paid_enforces_clinic_isolation(
    clinic,
    make_clinic,
    make_staff,
):
    other_clinic = make_clinic()

    staff = make_staff(
        clinic=other_clinic,
        role=Role.DOCTOR,
    )

    record = _make_payroll(staff.id)

    with pytest.raises(NotFoundError):
        staff_service.mark_payroll_paid(
            record_id=record.id,
            clinic_id=clinic.id,
        )


def test_mark_payroll_paid_rejects_inactive_clinic(
    suspended_clinic,
    make_staff,
):
    staff = make_staff(
        clinic=suspended_clinic,
        role=Role.DOCTOR,
    )

    record = _make_payroll(staff.id)

    with pytest.raises(ConflictError):
        staff_service.mark_payroll_paid(
            record_id=record.id,
            clinic_id=suspended_clinic.id,
        )


def test_active_clinic_required_for_status_change(
    suspended_clinic,
    make_staff,
):
    staff = make_staff(
        clinic=suspended_clinic,
        role=Role.DOCTOR,
    )

    with pytest.raises(ConflictError):
        staff_service.change_staff_status(
            staff_id=staff.id,
            clinic_id=suspended_clinic.id,
            new_status=StaffStatus.SUSPENDED,
        )


def test_active_clinic_required_for_leave_request(
    suspended_clinic,
    make_staff,
):
    staff = make_staff(
        clinic=suspended_clinic,
        role=Role.DOCTOR,
    )

    with pytest.raises(ConflictError):
        staff_service.request_leave(
            clinic_id=suspended_clinic.id,
            staff_id=staff.id,
            actor_user_id=staff.user_id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 9, 10),
            end_date=date(2026, 9, 12),
        )


def test_active_clinic_required_for_payroll_creation(
    suspended_clinic,
    make_staff,
):
    staff = make_staff(
        clinic=suspended_clinic,
        role=Role.DOCTOR,
    )

    with pytest.raises(ConflictError):
        staff_service.create_payroll_record(
            clinic_id=suspended_clinic.id,
            staff_id=staff.id,
            pay_period_start=date(2026, 9, 1),
            pay_period_end=date(2026, 9, 30),
            base_salary=Decimal("100000"),
        )