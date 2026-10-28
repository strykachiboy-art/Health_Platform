from __future__ import annotations

from flask_jwt_extended import decode_token

import pytest
from sqlalchemy import inspect as sa_inspect

from app.core.auth.user.services import clinic_context_service
from app.extensions import db
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import NotFoundError, ValidationError


def test_get_user_preloads_staff_for_profile_hot_path(
    clinic,
    make_staff,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    db.session.expire_all()

    user = clinic_context_service._get_user(
        staff.user_id
    )

    state = sa_inspect(user)

    assert "staff" not in state.unloaded
    assert user.staff is not None
    assert user.staff.id == staff.id


def test_resolve_effective_clinic_id_returns_assigned_clinic_for_normal_user(
    user,
    clinic,
):
    result = clinic_context_service.resolve_effective_clinic_id(
        user_id=user.id,
        jwt_payload={},
    )

    assert result == clinic.id


def test_resolve_effective_clinic_id_returns_none_for_super_admin_without_context(
    make_user,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    result = clinic_context_service.resolve_effective_clinic_id(
        user_id=user.id,
        jwt_payload={},
    )

    assert result is None


def test_resolve_effective_clinic_id_rejects_inactive_assigned_clinic(
    make_user,
    make_clinic,
):
    clinic = make_clinic(
        status=ClinicStatus.INACTIVE,
    )
    user = make_user(
        clinic=clinic,
    )

    with pytest.raises(
        ValidationError,
        match="Assigned clinic is not active",
    ):
        clinic_context_service.resolve_effective_clinic_id(
            user_id=user.id,
            jwt_payload={},
        )


def test_resolve_effective_clinic_id_returns_selected_active_clinic(
    make_user,
    make_clinic,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )
    clinic = make_clinic()

    result = clinic_context_service.resolve_effective_clinic_id(
        user_id=user.id,
        jwt_payload={
            "clinic_context_id": clinic.id,
        },
    )

    assert result == clinic.id


@pytest.mark.parametrize(
    "clinic_context_id",
    [
        0,
        -1,
        True,
        False,
        "1",
        [],
        {},
    ],
)
def test_resolve_effective_clinic_id_rejects_invalid_context(
    make_user,
    clinic_context_id,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    with pytest.raises(
        ValidationError,
        match="Invalid clinic context",
    ):
        clinic_context_service.resolve_effective_clinic_id(
            user_id=user.id,
            jwt_payload={
                "clinic_context_id": clinic_context_id,
            },
        )


@pytest.mark.parametrize(
    "status",
    [
        ClinicStatus.INACTIVE,
        ClinicStatus.SUSPENDED,
    ],
)
def test_resolve_effective_clinic_id_rejects_inactive_context(
    make_user,
    make_clinic,
    status,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )
    clinic = make_clinic(
        status=status,
    )

    with pytest.raises(
        ValidationError,
        match="Selected clinic is not active",
    ):
        clinic_context_service.resolve_effective_clinic_id(
            user_id=user.id,
            jwt_payload={
                "clinic_context_id": clinic.id,
            },
        )


def test_resolve_effective_clinic_id_rejects_non_mapping_payload(
    make_user,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    with pytest.raises(
        ValidationError,
        match="Invalid clinic context",
    ):
        clinic_context_service.resolve_effective_clinic_id(
            user_id=user.id,
            jwt_payload=None,
        )


def test_resolve_effective_clinic_id_rejects_missing_user(
    app,
):
    with pytest.raises(
        NotFoundError,
        match="User 999999 not found",
    ):
        clinic_context_service.resolve_effective_clinic_id(
            user_id=999999,
            jwt_payload={},
        )


def test_get_current_clinic_context_returns_assigned_for_normal_user(
    user,
    clinic,
):
    result = clinic_context_service.get_current_clinic_context(
        user_id=user.id,
        jwt_payload={},
    )

    assert result == {
        "clinic_id": clinic.id,
        "clinic_name": clinic.name,
        "source": "assigned",
    }


def test_get_current_clinic_context_returns_system_for_super_admin_without_context(
    make_user,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    result = clinic_context_service.get_current_clinic_context(
        user_id=user.id,
        jwt_payload={},
    )

    assert result == {
        "clinic_id": None,
        "clinic_name": None,
        "source": "system",
    }


def test_get_current_clinic_context_returns_selected_for_super_admin(
    make_user,
    make_clinic,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )
    clinic = make_clinic()

    result = clinic_context_service.get_current_clinic_context(
        user_id=user.id,
        jwt_payload={
            "clinic_context_id": clinic.id,
        },
    )

    assert result == {
        "clinic_id": clinic.id,
        "clinic_name": clinic.name,
        "source": "selected",
    }


def test_select_clinic_context_requires_super_admin(
    user,
    clinic,
):
    with pytest.raises(
        ValidationError,
        match="Only a super administrator can select a clinic context",
    ):
        clinic_context_service.select_clinic_context(
            user_id=user.id,
            clinic_id=clinic.id,
        )


def test_select_clinic_context_rejects_nonexistent_clinic(
    make_user,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    with pytest.raises(
        NotFoundError,
        match="Clinic 999999 not found",
    ):
        clinic_context_service.select_clinic_context(
            user_id=user.id,
            clinic_id=999999,
        )


@pytest.mark.parametrize(
    "status",
    [
        ClinicStatus.INACTIVE,
        ClinicStatus.SUSPENDED,
    ],
)
def test_select_clinic_context_rejects_inactive_clinic(
    make_user,
    make_clinic,
    status,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )
    clinic = make_clinic(
        status=status,
    )

    with pytest.raises(
        ValidationError,
        match="Selected clinic is not active",
    ):
        clinic_context_service.select_clinic_context(
            user_id=user.id,
            clinic_id=clinic.id,
        )


def test_select_clinic_context_returns_active_clinic(
    make_user,
    make_clinic,
    monkeypatch,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )
    clinic = make_clinic()

    monkeypatch.setattr(
        clinic_context_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    result = clinic_context_service.select_clinic_context(
        user_id=user.id,
        clinic_id=clinic.id,
    )

    assert result.id == clinic.id
    assert result.status is ClinicStatus.ACTIVE


def test_clear_clinic_context_preserves_selected_clinic_in_audit(
    make_user,
    make_clinic,
    monkeypatch,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )
    clinic = make_clinic()

    captured = {}

    monkeypatch.setattr(
        clinic_context_service,
        "create_audit_log",
        lambda **kwargs: captured.update(kwargs),
    )

    result = clinic_context_service.clear_clinic_context(
        user_id=user.id,
        clinic_id=clinic.id,
    )

    assert result is None
    assert captured["clinic_id"] == clinic.id
    assert captured["new_value"] == {
        "clinic_context_id": None,
    }


def test_clear_clinic_context_requires_super_admin(
    user,
):
    with pytest.raises(
        ValidationError,
        match="Only a super administrator can clear a clinic context",
    ):
        clinic_context_service.clear_clinic_context(
            user_id=user.id,
        )


def test_clear_clinic_context_succeeds_for_super_admin(
    make_user,
    monkeypatch,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    monkeypatch.setattr(
        clinic_context_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    result = clinic_context_service.clear_clinic_context(
        user_id=user.id,
    )

    assert result is None
