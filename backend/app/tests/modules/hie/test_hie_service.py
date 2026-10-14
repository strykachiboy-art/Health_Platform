from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.enums.hie_enums import (
    HIEIntegrationStatus,
    HIEOperation,
    HIEPurposeOfUse,
    HIESubmissionStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import NotFoundError, ValidationError
from app.modules.hie.services import hie_service


def make_clinic(*, clinic_id=1):
    return SimpleNamespace(
        id=clinic_id,
        name=f"Clinic {clinic_id}",
    )


def make_patient(*, patient_id=1, clinic_id=1):
    return SimpleNamespace(
        id=patient_id,
        clinic_id=clinic_id,
    )


def make_staff(
    *,
    staff_id=1,
    user_id=1,
    clinic_id=1,
    status=StaffStatus.ACTIVE,
):
    return SimpleNamespace(
        id=staff_id,
        user_id=user_id,
        clinic_id=clinic_id,
        status=status,
    )


def make_user(
    *,
    user_id=1,
    clinic_id=1,
    role=Role.DOCTOR,
    is_active=True,
    staff=None,
):
    return SimpleNamespace(
        id=user_id,
        clinic_id=clinic_id,
        role=role,
        is_active=is_active,
        staff=staff,
    )


def make_integration(
    *,
    integration_id=1,
    clinic_id=1,
    provider="test-hie-provider",
    status=HIEIntegrationStatus.ACTIVE,
    endpoint_url="https://hie.example.com",
    organization_id="ORG-001",
    facility_id="FAC-001",
    last_sync_at=None,
):
    now = datetime.now(timezone.utc)

    return SimpleNamespace(
        id=integration_id,
        clinic_id=clinic_id,
        provider=provider,
        status=status,
        endpoint_url=endpoint_url,
        organization_id=organization_id,
        facility_id=facility_id,
        last_sync_at=last_sync_at,
        created_at=now,
        updated_at=now,
    )


def make_submission(
    *,
    submission_id=1,
    integration_id=1,
    clinic_id=1,
    patient_id=1,
    operation=HIEOperation.PATIENT_SUBMISSION,
    status=HIESubmissionStatus.PENDING,
    request_data=None,
    response_data=None,
    status_code=None,
    external_reference=None,
    error_message=None,
    retry_count=0,
    submitted_at=None,
):
    now = datetime.now(timezone.utc)

    return SimpleNamespace(
        id=submission_id,
        integration_id=integration_id,
        clinic_id=clinic_id,
        patient_id=patient_id,
        operation=operation,
        status=status,
        request_data=request_data,
        response_data=response_data,
        status_code=status_code,
        external_reference=external_reference,
        error_message=error_message,
        retry_count=retry_count,
        submitted_at=submitted_at,
        created_at=now,
        updated_at=now,
    )


class FakeSubmission:
    _next_id = 1

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.id = FakeSubmission._next_id
        FakeSubmission._next_id += 1


class FakeIntegration:
    _next_id = 1

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.id = FakeIntegration._next_id
        FakeIntegration._next_id += 1


@pytest.fixture
def no_transaction(monkeypatch):
    monkeypatch.setattr(
        hie_service.db.session,
        "commit",
        Mock(),
    )
    monkeypatch.setattr(
        hie_service.db.session,
        "rollback",
        Mock(),
    )


def test_get_clinic_rejects_none():
    with pytest.raises(
        ValidationError,
        match="Invalid clinic ID",
    ):
        hie_service._get_clinic(None)


def test_get_clinic_rejects_non_positive_id():
    with pytest.raises(
        ValidationError,
        match="Invalid clinic ID",
    ):
        hie_service._get_clinic(0)

    with pytest.raises(
        ValidationError,
        match="Invalid clinic ID",
    ):
        hie_service._get_clinic(-1)


def test_get_clinic_rejects_boolean_id():
    with pytest.raises(
        ValidationError,
        match="Invalid clinic ID",
    ):
        hie_service._get_clinic(True)


def test_get_clinic_returns_clinic(monkeypatch):
    clinic = make_clinic(clinic_id=4)

    getter = Mock(return_value=clinic)

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        getter,
    )

    result = hie_service._get_clinic(4)

    assert result is clinic
    getter.assert_called_once_with(
        hie_service.Clinic,
        4,
    )


def test_get_clinic_raises_not_found(monkeypatch):
    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=None),
    )

    with pytest.raises(
        NotFoundError,
        match="Clinic 99 not found",
    ):
        hie_service._get_clinic(99)


def test_get_patient_none_returns_none():
    assert hie_service._get_patient(1, None) is None


def test_get_patient_rejects_invalid_id():
    with pytest.raises(
        ValidationError,
        match="Invalid patient ID",
    ):
        hie_service._get_patient(1, 0)

    with pytest.raises(
        ValidationError,
        match="Invalid patient ID",
    ):
        hie_service._get_patient(1, -1)


def test_get_patient_rejects_boolean_id():
    with pytest.raises(
        ValidationError,
        match="Invalid patient ID",
    ):
        hie_service._get_patient(
            1,
            True,
        )


def test_get_patient_returns_patient(monkeypatch):
    patient = make_patient(
        patient_id=5,
        clinic_id=2,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=patient),
    )

    result = hie_service._get_patient(
        2,
        5,
    )

    assert result is patient


def test_get_patient_raises_not_found(monkeypatch):
    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=None),
    )

    with pytest.raises(
        NotFoundError,
        match="Patient 10 not found",
    ):
        hie_service._get_patient(1, 10)


def test_get_patient_rejects_wrong_clinic(monkeypatch):
    patient = make_patient(
        patient_id=5,
        clinic_id=2,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=patient),
    )

    with pytest.raises(
        ValidationError,
        match="Patient does not belong to the clinic",
    ):
        hie_service._get_patient(99, 5)


def test_get_integration_rejects_invalid_clinic():
    with pytest.raises(
        ValidationError,
        match="Invalid clinic ID",
    ):
        hie_service._get_integration(0)


def test_get_integration_rejects_boolean_clinic_id():
    with pytest.raises(
        ValidationError,
        match="Invalid clinic ID",
    ):
        hie_service._get_integration(True)


def test_get_integration_rejects_invalid_integration_id():
    with pytest.raises(
        ValidationError,
        match="Invalid HIE integration ID",
    ):
        hie_service._get_integration(1, 0)


def test_get_integration_rejects_boolean_integration_id():
    with pytest.raises(
        ValidationError,
        match="Invalid HIE integration ID",
    ):
        hie_service._get_integration(
            1,
            True,
        )


def test_get_integration_by_id(monkeypatch):
    integration = make_integration(
        integration_id=8,
        clinic_id=3,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=integration),
    )

    result = hie_service._get_integration(
        3,
        8,
    )

    assert result is integration


def test_get_integration_by_id_not_found(monkeypatch):
    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=None),
    )

    with pytest.raises(
        NotFoundError,
        match="HIE integration 8 not found",
    ):
        hie_service._get_integration(3, 8)


def test_get_integration_by_id_rejects_other_clinic(monkeypatch):
    integration = make_integration(
        integration_id=8,
        clinic_id=3,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=integration),
    )

    with pytest.raises(
        ValidationError,
        match="HIE integration does not belong to the clinic",
    ):
        hie_service._get_integration(99, 8)


def test_get_active_integration_returns_only_active_integration(
    monkeypatch,
):
    integration = make_integration(
        integration_id=12,
        clinic_id=4,
        provider="test-hie-provider",
        status=HIEIntegrationStatus.ACTIVE,
    )

    scalars = Mock()
    scalars.all.return_value = [
        integration,
    ]

    execute_result = Mock()
    execute_result.scalars.return_value = scalars

    monkeypatch.setattr(
        hie_service.db.session,
        "execute",
        Mock(return_value=execute_result),
    )

    result = hie_service._get_integration(
        clinic_id=4,
    )

    assert result is integration
    execute_result.scalars.assert_called_once_with()
    scalars.all.assert_called_once_with()


def test_get_integration_requires_active_integration(
    monkeypatch,
):
    scalars = Mock()
    scalars.all.return_value = []

    execute_result = Mock()
    execute_result.scalars.return_value = scalars

    monkeypatch.setattr(
        hie_service.db.session,
        "execute",
        Mock(return_value=execute_result),
    )

    with pytest.raises(
        ValidationError,
        match="No active HIE integration is configured",
    ):
        hie_service._get_integration(
            clinic_id=4,
        )


def test_get_integration_requires_explicit_id_when_multiple_are_active(
    monkeypatch,
):
    integrations = [
        make_integration(
            integration_id=12,
            clinic_id=4,
            provider="provider-a",
            status=HIEIntegrationStatus.ACTIVE,
        ),
        make_integration(
            integration_id=13,
            clinic_id=4,
            provider="provider-b",
            status=HIEIntegrationStatus.ACTIVE,
        ),
    ]

    scalars = Mock()
    scalars.all.return_value = integrations

    execute_result = Mock()
    execute_result.scalars.return_value = scalars

    monkeypatch.setattr(
        hie_service.db.session,
        "execute",
        Mock(return_value=execute_result),
    )

    with pytest.raises(
        ValidationError,
        match="Multiple active HIE integrations are configured",
    ):
        hie_service._get_integration(
            clinic_id=4,
        )


def test_validate_integration_requires_integration():
    with pytest.raises(
        ValidationError,
        match="HIE integration is required",
    ):
        hie_service._validate_integration(None)


def test_validate_integration_requires_active_status():
    integration = make_integration(
        status=HIEIntegrationStatus.PENDING,
    )

    with pytest.raises(
        ValidationError,
        match="HIE integration is not active",
    ):
        hie_service._validate_integration(integration)


def test_validate_integration_requires_provider():
    integration = make_integration(
        provider="   ",
    )

    with pytest.raises(
        ValidationError,
        match="HIE integration provider is not configured",
    ):
        hie_service._validate_integration(integration)


def test_validate_integration_accepts_provider_neutral_name():
    integration = make_integration(
        provider="TEST-HIE-PROVIDER",
    )

    hie_service._validate_integration(
        integration
    )


def test_get_provider_resolves_provider_through_registry(
    monkeypatch,
):
    provider = object()

    get_provider = Mock(
        return_value=provider,
    )

    monkeypatch.setattr(
        hie_service,
        "get_provider",
        get_provider,
    )

    integration = make_integration(
        provider="TEST-HIE-PROVIDER",
        endpoint_url="https://provider.example.com",
    )

    result = hie_service._get_provider(
        integration
    )

    assert result is provider

    get_provider.assert_called_once_with(
        "test-hie-provider",
        endpoint=integration.endpoint_url,
    )


def test_get_provider_rejects_unregistered_provider(
    monkeypatch,
):
    integration = make_integration(
        provider="unregistered-provider",
    )

    monkeypatch.setattr(
        hie_service,
        "get_provider",
        Mock(
            side_effect=ValidationError(
                "Unsupported HIE provider: unregistered-provider"
            ),
        ),
    )

    with pytest.raises(
        ValidationError,
        match="Unsupported HIE provider",
    ):
        hie_service._get_provider(
            integration
        )


def test_validate_patient_identifier_requires_value():
    with pytest.raises(
        ValidationError,
        match="Patient identifier is required",
    ):
        hie_service._validate_patient_identifier(None)


def test_validate_patient_identifier_rejects_blank():
    with pytest.raises(
        ValidationError,
        match="Patient identifier is required",
    ):
        hie_service._validate_patient_identifier("   ")


def test_validate_patient_identifier_rejects_non_string():
    with pytest.raises(
        ValidationError,
        match="Patient identifier must be a string",
    ):
        hie_service._validate_patient_identifier(123)


def test_validate_patient_identifier_strips_whitespace():
    assert (
        hie_service._validate_patient_identifier(
            "  MRN-123  "
        )
        == "MRN-123"
    )


def test_normalize_hie_purpose_accepts_enum():
    result = hie_service._normalize_hie_purpose(
        HIEPurposeOfUse.TREATMENT
    )

    assert result is HIEPurposeOfUse.TREATMENT


def test_normalize_hie_purpose_normalizes_string():
    result = hie_service._normalize_hie_purpose(
        "  TREATMENT  "
    )

    assert result is HIEPurposeOfUse.TREATMENT


@pytest.mark.parametrize(
    "purpose",
    [
        None,
        "",
        " ",
        "not-a-purpose",
        123,
        True,
    ],
)
def test_normalize_hie_purpose_rejects_invalid_value(
    purpose,
):
    with pytest.raises(
        ValidationError,
        match="Invalid HIE purpose of use",
    ):
        hie_service._normalize_hie_purpose(
            purpose
        )


def test_get_hie_requesting_user_requires_valid_clinic_id():
    with pytest.raises(
        ValidationError,
        match="Invalid clinic ID",
    ):
        hie_service._get_hie_requesting_user(
            clinic_id=0,
            requesting_user_id=1,
        )


def test_get_hie_requesting_user_requires_valid_user_id():
    with pytest.raises(
        ValidationError,
        match="Invalid requesting user ID",
    ):
        hie_service._get_hie_requesting_user(
            clinic_id=1,
            requesting_user_id=0,
        )


def test_get_hie_requesting_user_rejects_boolean_ids():
    with pytest.raises(
        ValidationError,
        match="Invalid clinic ID",
    ):
        hie_service._get_hie_requesting_user(
            clinic_id=True,
            requesting_user_id=1,
        )

    with pytest.raises(
        ValidationError,
        match="Invalid requesting user ID",
    ):
        hie_service._get_hie_requesting_user(
            clinic_id=1,
            requesting_user_id=True,
        )


def test_get_hie_requesting_user_requires_user(
    monkeypatch,
):
    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=None),
    )

    with pytest.raises(
        NotFoundError,
        match="Requesting user not found",
    ):
        hie_service._get_hie_requesting_user(
            clinic_id=1,
            requesting_user_id=10,
        )


def test_get_hie_requesting_user_rejects_inactive_user(
    monkeypatch,
):
    user = make_user(
        user_id=10,
        clinic_id=1,
        role=Role.DOCTOR,
        is_active=False,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=user),
    )

    with pytest.raises(
        NotFoundError,
        match="Requesting user not found",
    ):
        hie_service._get_hie_requesting_user(
            clinic_id=1,
            requesting_user_id=10,
        )


def test_get_hie_requesting_user_rejects_cross_clinic_user(
    monkeypatch,
):
    user = make_user(
        user_id=10,
        clinic_id=2,
        role=Role.DOCTOR,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=user),
    )

    with pytest.raises(
        NotFoundError,
        match="Requesting user not found",
    ):
        hie_service._get_hie_requesting_user(
            clinic_id=1,
            requesting_user_id=10,
        )


@pytest.mark.parametrize(
    "role",
    [
        Role.PATIENT,
        Role.RECEPTIONIST,
        Role.ACCOUNTANT,
        Role.AMBULANCE_DISPATCHER,
        Role.EMT,
        Role.DRIVER,
    ],
)
def test_get_hie_requesting_user_rejects_unauthorized_role(
    monkeypatch,
    role,
):
    user = make_user(
        user_id=10,
        clinic_id=1,
        role=role,
        staff=make_staff(
            staff_id=10,
            user_id=10,
            clinic_id=1,
        ),
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=user),
    )

    with pytest.raises(
        ValidationError,
        match="User is not authorized for HIE queries",
    ):
        hie_service._get_hie_requesting_user(
            clinic_id=1,
            requesting_user_id=10,
        )


def test_get_hie_requesting_user_requires_staff(
    monkeypatch,
):
    user = make_user(
        user_id=10,
        clinic_id=1,
        role=Role.DOCTOR,
        staff=None,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=user),
    )

    with pytest.raises(
        NotFoundError,
        match="Requesting user not found",
    ):
        hie_service._get_hie_requesting_user(
            clinic_id=1,
            requesting_user_id=10,
        )


def test_get_hie_requesting_user_rejects_wrong_staff_user(
    monkeypatch,
):
    staff = make_staff(
        staff_id=20,
        user_id=999,
        clinic_id=1,
        status=StaffStatus.ACTIVE,
    )

    user = make_user(
        user_id=10,
        clinic_id=1,
        role=Role.DOCTOR,
        staff=staff,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=user),
    )

    with pytest.raises(
        NotFoundError,
        match="Requesting user not found",
    ):
        hie_service._get_hie_requesting_user(
            clinic_id=1,
            requesting_user_id=10,
        )


def test_get_hie_requesting_user_rejects_wrong_staff_clinic(
    monkeypatch,
):
    staff = make_staff(
        staff_id=10,
        user_id=10,
        clinic_id=2,
        status=StaffStatus.ACTIVE,
    )

    user = make_user(
        user_id=10,
        clinic_id=1,
        role=Role.DOCTOR,
        staff=staff,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=user),
    )

    with pytest.raises(
        NotFoundError,
        match="Requesting user not found",
    ):
        hie_service._get_hie_requesting_user(
            clinic_id=1,
            requesting_user_id=10,
        )


def test_get_hie_requesting_user_rejects_inactive_staff(
    monkeypatch,
):
    staff = make_staff(
        staff_id=10,
        user_id=10,
        clinic_id=1,
        status=StaffStatus.SUSPENDED,
    )

    user = make_user(
        user_id=10,
        clinic_id=1,
        role=Role.DOCTOR,
        staff=staff,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=user),
    )

    with pytest.raises(
        ValidationError,
        match="Staff member is not active",
    ):
        hie_service._get_hie_requesting_user(
            clinic_id=1,
            requesting_user_id=10,
        )


def test_get_hie_requesting_user_accepts_active_authorized_user(
    monkeypatch,
):
    staff = make_staff(
        staff_id=10,
        user_id=10,
        clinic_id=1,
        status=StaffStatus.ACTIVE,
    )

    user = make_user(
        user_id=10,
        clinic_id=1,
        role=Role.DOCTOR,
        staff=staff,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=user),
    )

    result = hie_service._get_hie_requesting_user(
        clinic_id=1,
        requesting_user_id=10,
    )

    assert result is user


def test_authorize_hie_query_validates_requester_and_purpose(
    monkeypatch,
):
    requester = make_user(
        user_id=10,
        clinic_id=1,
        role=Role.DOCTOR,
        staff=make_staff(
            staff_id=10,
            user_id=10,
            clinic_id=1,
        ),
    )

    get_requester = Mock(
        return_value=requester,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_hie_requesting_user",
        get_requester,
    )

    result = hie_service._authorize_hie_query(
        clinic_id=1,
        requesting_user_id=10,
        purpose_of_use="TREATMENT",
    )

    assert result is HIEPurposeOfUse.TREATMENT

    get_requester.assert_called_once_with(
        clinic_id=1,
        requesting_user_id=10,
    )


def test_validate_request_payload_requires_dict():
    with pytest.raises(
        ValidationError,
        match="HIE request payload must be an object",
    ):
        hie_service._validate_request_payload(
            [],
        )


def test_validate_request_payload_accepts_dict():
    payload = {
        "patient": "Jane",
    }

    assert (
        hie_service._validate_request_payload(
            payload,
        )
        is payload
    )


def test_validate_submission_context_requires_integration():
    with pytest.raises(
        ValidationError,
        match="HIE integration is required",
    ):
        hie_service._validate_submission_context(
            integration=None,
            clinic_id=1,
            patient_id=None,
        )


def test_validate_submission_context_rejects_wrong_integration_clinic():
    integration = make_integration(
        clinic_id=2,
    )

    with pytest.raises(
        ValidationError,
        match="HIE integration does not belong to the clinic",
    ):
        hie_service._validate_submission_context(
            integration=integration,
            clinic_id=1,
            patient_id=None,
        )


def test_validate_submission_context_accepts_no_patient():
    integration = make_integration(
        clinic_id=1,
    )

    hie_service._validate_submission_context(
        integration=integration,
        clinic_id=1,
        patient_id=None,
    )


def test_validate_submission_context_accepts_valid_patient(
    monkeypatch,
):
    integration = make_integration(
        clinic_id=1,
    )

    patient = make_patient(
        patient_id=7,
        clinic_id=1,
    )

    get_patient = Mock(
        return_value=patient,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_patient",
        get_patient,
    )

    hie_service._validate_submission_context(
        integration=integration,
        clinic_id=1,
        patient_id=7,
    )

    get_patient.assert_called_once_with(
        1,
        7,
    )


def test_validate_submission_context_rejects_wrong_patient_clinic(
    monkeypatch,
):
    integration = make_integration(
        clinic_id=1,
    )

    patient = make_patient(
        patient_id=7,
        clinic_id=9,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=patient),
    )

    with pytest.raises(
        ValidationError,
        match="Patient does not belong to the clinic",
    ):
        hie_service._validate_submission_context(
            integration=integration,
            clinic_id=1,
            patient_id=7,
        )


def test_validate_pagination_accepts_defaults():
    assert hie_service._validate_pagination(
        1,
        20,
    ) == (1, 20)


def test_validate_pagination_accepts_maximum():
    assert hie_service._validate_pagination(
        1,
        100,
    ) == (1, 100)


@pytest.mark.parametrize(
    "page",
    [0, -1],
)
def test_validate_pagination_rejects_invalid_page(
    page,
):
    with pytest.raises(
        ValidationError,
        match="Page must be greater than or equal to 1",
    ):
        hie_service._validate_pagination(
            page,
            20,
        )


@pytest.mark.parametrize(
    "page",
    [None, "1", 1.5, True, False],
)
def test_validate_pagination_rejects_non_integer_page(
    page,
):
    with pytest.raises(
        ValidationError,
        match="Page must be an integer",
    ):
        hie_service._validate_pagination(
            page,
            20,
        )


@pytest.mark.parametrize(
    "per_page",
    [0, -1, 101],
)
def test_validate_pagination_rejects_invalid_per_page(
    per_page,
):
    with pytest.raises(
        ValidationError,
        match="per_page must be between 1 and 100",
    ):
        hie_service._validate_pagination(
            1,
            per_page,
        )


@pytest.mark.parametrize(
    "per_page",
    [None, "20", 20.5, True, False],
)
def test_validate_pagination_rejects_non_integer_per_page(
    per_page,
):
    with pytest.raises(
        ValidationError,
        match="per_page must be an integer",
    ):
        hie_service._validate_pagination(
            1,
            per_page,
        )


def test_create_submission_success(
    monkeypatch,
    no_transaction,
):
    integration = make_integration(
        integration_id=11,
        clinic_id=3,
    )

    added = []

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=integration),
    )

    monkeypatch.setattr(
        hie_service,
        "HIESubmission",
        FakeSubmission,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "add",
        lambda obj: added.append(obj),
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "flush",
        Mock(),
    )

    submission_id = hie_service._create_submission(
        integration_id=11,
        clinic_id=3,
        patient_id=None,
        operation=HIEOperation.PATIENT_QUERY,
        request_data={
            "patient_identifier": "MRN-100",
        },
    )

    assert submission_id == added[0].id
    assert added[0].integration_id == 11
    assert added[0].clinic_id == 3
    assert added[0].patient_id is None
    assert added[0].operation == HIEOperation.PATIENT_QUERY
    assert added[0].status == HIESubmissionStatus.PENDING
    assert added[0].request_data == {
        "patient_identifier": "MRN-100",
    }
    assert added[0].retry_count == 0


def test_create_submission_rejects_invalid_clinic(
    no_transaction,
):
    with pytest.raises(
        ValidationError,
        match="Invalid clinic ID",
    ):
        hie_service._create_submission(
            integration_id=1,
            clinic_id=0,
            patient_id=None,
            operation=HIEOperation.PATIENT_QUERY,
            request_data={},
        )


def test_create_submission_rejects_invalid_integration(
    no_transaction,
):
    with pytest.raises(
        ValidationError,
        match="Invalid HIE integration ID",
    ):
        hie_service._create_submission(
            integration_id=0,
            clinic_id=1,
            patient_id=None,
            operation=HIEOperation.PATIENT_QUERY,
            request_data={},
        )


def test_create_submission_rejects_missing_integration(
    monkeypatch,
    no_transaction,
):
    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=None),
    )

    with pytest.raises(
        NotFoundError,
        match="HIE integration 99 not found",
    ):
        hie_service._create_submission(
            integration_id=99,
            clinic_id=1,
            patient_id=None,
            operation=HIEOperation.PATIENT_QUERY,
            request_data={},
        )


def test_create_submission_rejects_invalid_operation(
    monkeypatch,
    no_transaction,
):
    integration = make_integration(
        integration_id=1,
        clinic_id=1,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=integration),
    )

    with pytest.raises(
        ValidationError,
        match="Invalid HIE operation",
    ):
        hie_service._create_submission(
            integration_id=1,
            clinic_id=1,
            patient_id=None,
            operation="patient_query",
            request_data={},
        )


def test_create_submission_rejects_non_dict_request_data(
    monkeypatch,
    no_transaction,
):
    integration = make_integration(
        integration_id=1,
        clinic_id=1,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=integration),
    )

    with pytest.raises(
        ValidationError,
        match="HIE request data must be an object",
    ):
        hie_service._create_submission(
            integration_id=1,
            clinic_id=1,
            patient_id=None,
            operation=HIEOperation.PATIENT_QUERY,
            request_data="invalid",
        )


def test_mark_submission_success(
    monkeypatch,
    no_transaction,
):
    submission = make_submission(
        submission_id=20,
        retry_count=2,
        error_message="old error",
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=submission),
    )

    response = {
        "status_code": 200,
        "external_reference": "EXT-123",
        "patient": {
            "id": "ABC",
        },
    }

    before = datetime.now(timezone.utc)

    hie_service._mark_submission_success(
        20,
        response,
    )

    assert submission.status == HIESubmissionStatus.SUCCESS
    assert submission.response_data == response
    assert submission.status_code == 200
    assert submission.external_reference == "EXT-123"
    assert submission.error_message is None
    assert submission.submitted_at is not None
    assert submission.submitted_at >= before


def test_mark_submission_success_rejects_invalid_id(
    no_transaction,
):
    with pytest.raises(
        ValidationError,
        match="Invalid HIE submission ID",
    ):
        hie_service._mark_submission_success(
            0,
            {},
        )


def test_mark_submission_success_requires_submission(
    monkeypatch,
    no_transaction,
):
    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=None),
    )

    with pytest.raises(
        NotFoundError,
        match="HIE submission 99 not found",
    ):
        hie_service._mark_submission_success(
            99,
            {},
        )


def test_mark_submission_success_requires_dict(
    monkeypatch,
    no_transaction,
):
    submission = make_submission(
        submission_id=9,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=submission),
    )

    with pytest.raises(
        ValidationError,
        match="HIE provider response must be an object",
    ):
        hie_service._mark_submission_success(
            9,
            [],
        )


def test_mark_submission_failure_does_not_persist_provider_error(
    monkeypatch,
    no_transaction,
):
    submission = make_submission(
        submission_id=20,
        retry_count=2,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=submission),
    )

    provider_error = RuntimeError(
        "Sensitive provider response: token=SECRET-123"
    )

    hie_service._mark_submission_failure(
        20,
        provider_error,
    )

    assert submission.status == HIESubmissionStatus.FAILED

    assert submission.error_message == (
        "HIE provider operation failed"
    )

    assert "SECRET-123" not in (
        submission.error_message or ""
    )

    assert submission.retry_count == 3
    assert submission.submitted_at is not None


def test_mark_submission_failure_handles_none_retry_count(
    monkeypatch,
    no_transaction,
):
    submission = make_submission(
        submission_id=20,
        retry_count=0,
    )

    submission.retry_count = None

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=submission),
    )

    hie_service._mark_submission_failure(
        20,
        RuntimeError("Failure"),
    )

    assert submission.retry_count == 1
    assert submission.error_message == (
        "HIE provider operation failed"
    )


def test_update_last_sync(
    monkeypatch,
    no_transaction,
):
    integration = make_integration(
        integration_id=4,
        last_sync_at=None,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=integration),
    )

    before = datetime.now(timezone.utc)

    hie_service._update_last_sync(4)

    assert integration.last_sync_at is not None
    assert integration.last_sync_at >= before


def test_update_last_sync_rejects_invalid_id(
    no_transaction,
):
    with pytest.raises(
        ValidationError,
        match="Invalid HIE integration ID",
    ):
        hie_service._update_last_sync(0)


def test_update_last_sync_requires_integration(
    monkeypatch,
    no_transaction,
):
    monkeypatch.setattr(
        hie_service.db.session,
        "get",
        Mock(return_value=None),
    )

    with pytest.raises(
        NotFoundError,
        match="HIE integration 99 not found",
    ):
        hie_service._update_last_sync(99)


def test_submit_patient_success(
    monkeypatch,
):
    integration = make_integration(
        integration_id=5,
        clinic_id=1,
        provider="test-hie-provider",
    )

    provider_response = {
        "status_code": 201,
        "external_reference": "PAT-001",
    }

    provider = Mock()
    provider.submit_patient.return_value = (
        provider_response
    )

    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_patient",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    monkeypatch.setattr(
        hie_service,
        "_validate_integration",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_create_submission",
        Mock(return_value=100),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_provider",
        Mock(return_value=provider),
    )

    monkeypatch.setattr(
        hie_service,
        "_mark_submission_success",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_update_last_sync",
        Mock(),
    )

    payload = {
        "first_name": "Jane",
        "last_name": "Doe",
    }

    result = hie_service.submit_patient(
        clinic_id=1,
        patient_id=7,
        payload=payload,
        integration_id=5,
    )

    assert result == provider_response

    hie_service._get_clinic.assert_called_once_with(
        1,
    )

    hie_service._get_patient.assert_called_once_with(
        1,
        7,
    )

    hie_service._create_submission.assert_called_once_with(
        integration_id=5,
        clinic_id=1,
        patient_id=7,
        operation=HIEOperation.PATIENT_SUBMISSION,
        request_data=payload,
    )

    provider.submit_patient.assert_called_once_with(
        payload,
    )

    hie_service._mark_submission_success.assert_called_once_with(
        100,
        provider_response,
    )

    hie_service._update_last_sync.assert_called_once_with(
        5,
    )


def test_submit_patient_marks_failure_and_reraises(
    monkeypatch,
):
    integration = make_integration(
        integration_id=5,
        clinic_id=1,
    )

    provider = Mock()

    provider.submit_patient.side_effect = (
        RuntimeError(
            "Provider failure",
        )
    )

    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_patient",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    monkeypatch.setattr(
        hie_service,
        "_validate_integration",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_create_submission",
        Mock(return_value=100),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_provider",
        Mock(return_value=provider),
    )

    mark_failure = Mock()

    monkeypatch.setattr(
        hie_service,
        "_mark_submission_failure",
        mark_failure,
    )

    update_sync = Mock()

    monkeypatch.setattr(
        hie_service,
        "_update_last_sync",
        update_sync,
    )

    with pytest.raises(
        RuntimeError,
        match="Provider failure",
    ):
        hie_service.submit_patient(
            clinic_id=1,
            patient_id=7,
            payload={},
            integration_id=5,
        )

    mark_failure.assert_called_once_with(
        100,
        provider.submit_patient.side_effect,
    )

    update_sync.assert_not_called()


def test_submit_clinical_data_success(
    monkeypatch,
):
    integration = make_integration(
        integration_id=6,
        clinic_id=2,
    )

    provider = Mock()

    provider.submit_clinical_data.return_value = {
        "status_code": 200,
        "external_reference": "CLIN-1",
    }

    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_patient",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    monkeypatch.setattr(
        hie_service,
        "_validate_integration",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_create_submission",
        Mock(return_value=200),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_provider",
        Mock(return_value=provider),
    )

    monkeypatch.setattr(
        hie_service,
        "_mark_submission_success",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_update_last_sync",
        Mock(),
    )

    payload = {
        "diagnosis": "Malaria",
    }

    result = hie_service.submit_clinical_data(
        clinic_id=2,
        patient_id=10,
        payload=payload,
        integration_id=6,
    )

    assert result["status_code"] == 200

    provider.submit_clinical_data.assert_called_once_with(
        payload,
    )

    hie_service._create_submission.assert_called_once_with(
        integration_id=6,
        clinic_id=2,
        patient_id=10,
        operation=HIEOperation.CLINICAL_DATA_SUBMISSION,
        request_data=payload,
    )


def test_submit_clinical_document_success(
    monkeypatch,
):
    integration = make_integration(
        integration_id=7,
        clinic_id=3,
    )

    provider = Mock()

    provider.submit_clinical_document.return_value = {
        "status_code": 201,
        "external_reference": "DOC-1",
    }

    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_patient",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    monkeypatch.setattr(
        hie_service,
        "_validate_integration",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_create_submission",
        Mock(return_value=300),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_provider",
        Mock(return_value=provider),
    )

    monkeypatch.setattr(
        hie_service,
        "_mark_submission_success",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_update_last_sync",
        Mock(),
    )

    payload = {
        "document_type": "consultation",
        "content": "Clinical note",
    }

    result = hie_service.submit_clinical_document(
        clinic_id=3,
        patient_id=11,
        payload=payload,
        integration_id=7,
    )

    assert result["external_reference"] == "DOC-1"

    provider.submit_clinical_document.assert_called_once_with(
        payload,
    )


def test_query_patient_success(
    monkeypatch,
):
    integration = make_integration(
        integration_id=8,
        clinic_id=4,
        provider="test-hie-provider",
    )

    provider = Mock()

    provider.query_patient.return_value = {
        "status_code": 200,
        "patient": {
            "identifier": "MRN-1",
        },
    }

    requester = make_user(
        user_id=77,
        clinic_id=4,
        role=Role.DOCTOR,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_hie_requesting_user",
        Mock(return_value=requester),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    monkeypatch.setattr(
        hie_service,
        "_validate_integration",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_provider",
        Mock(return_value=provider),
    )

    monkeypatch.setattr(
        hie_service,
        "_create_submission",
        Mock(return_value=400),
    )

    monkeypatch.setattr(
        hie_service,
        "_mark_submission_success",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_update_last_sync",
        Mock(),
    )

    result = hie_service.query_patient(
        clinic_id=4,
        requesting_user_id=77,
        purpose_of_use=HIEPurposeOfUse.TREATMENT,
        patient_identifier="  MRN-1  ",
        integration_id=8,
    )

    assert result["status_code"] == 200

    provider.query_patient.assert_called_once_with(
        "MRN-1",
    )

    hie_service._get_hie_requesting_user.assert_called_once_with(
        clinic_id=4,
        requesting_user_id=77,
    )

    hie_service._create_submission.assert_called_once_with(
        integration_id=8,
        clinic_id=4,
        patient_id=None,
        operation=HIEOperation.PATIENT_QUERY,
        request_data={
            "patient_identifier": "MRN-1",
            "purpose_of_use": "treatment",
            "requesting_user_id": 77,
        },
    )


def test_query_patient_requires_requesting_user_context(
    monkeypatch,
):
    with pytest.raises(
        TypeError,
    ):
        hie_service.query_patient(
            clinic_id=1,
            purpose_of_use=HIEPurposeOfUse.TREATMENT,
            patient_identifier="MRN-1",
        )


def test_query_patient_rejects_invalid_requester(
    monkeypatch,
):
    provider = Mock()

    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_hie_requesting_user",
        Mock(
            side_effect=ValidationError(
                "User is not authorized for HIE queries",
            ),
        ),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_provider",
        provider,
    )

    with pytest.raises(
        ValidationError,
        match="User is not authorized for HIE queries",
    ):
        hie_service.query_patient(
            clinic_id=1,
            requesting_user_id=77,
            purpose_of_use=HIEPurposeOfUse.TREATMENT,
            patient_identifier="MRN-1",
        )

    provider.assert_not_called()


def test_query_patient_requires_identifier(
    monkeypatch,
):
    integration = make_integration(
        integration_id=8,
        clinic_id=1,
    )

    requester = make_user(
        user_id=77,
        clinic_id=1,
        role=Role.DOCTOR,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_hie_requesting_user",
        Mock(return_value=requester),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    with pytest.raises(
        ValidationError,
        match="Patient identifier is required",
    ):
        hie_service.query_patient(
            clinic_id=1,
            requesting_user_id=77,
            purpose_of_use=HIEPurposeOfUse.TREATMENT,
            patient_identifier="   ",
            integration_id=8,
        )


def test_query_patient_rejects_invalid_purpose(
    monkeypatch,
):
    requester = make_user(
        user_id=77,
        clinic_id=1,
        role=Role.DOCTOR,
    )

    provider = Mock()

    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_hie_requesting_user",
        Mock(return_value=requester),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_provider",
        provider,
    )

    with pytest.raises(
        ValidationError,
        match="Invalid HIE purpose of use",
    ):
        hie_service.query_patient(
            clinic_id=1,
            requesting_user_id=77,
            purpose_of_use="invalid-purpose",
            patient_identifier="MRN-1",
        )

    provider.assert_not_called()


def test_query_clinical_data_success(
    monkeypatch,
):
    integration = make_integration(
        integration_id=9,
        clinic_id=5,
        provider="test-hie-provider",
    )

    provider = Mock()

    provider.query_clinical_data.return_value = {
        "status_code": 200,
        "records": [],
    }

    requester = make_user(
        user_id=88,
        clinic_id=5,
        role=Role.NURSE,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_hie_requesting_user",
        Mock(return_value=requester),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    monkeypatch.setattr(
        hie_service,
        "_validate_integration",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_provider",
        Mock(return_value=provider),
    )

    monkeypatch.setattr(
        hie_service,
        "_create_submission",
        Mock(return_value=500),
    )

    monkeypatch.setattr(
        hie_service,
        "_mark_submission_success",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_update_last_sync",
        Mock(),
    )

    filters = {
        "from": "2026-01-01",
        "to": "2026-01-31",
    }

    result = hie_service.query_clinical_data(
        clinic_id=5,
        requesting_user_id=88,
        purpose_of_use=HIEPurposeOfUse.TREATMENT,
        patient_identifier="MRN-20",
        filters=filters,
        integration_id=9,
    )

    assert result["status_code"] == 200

    provider.query_clinical_data.assert_called_once_with(
        "MRN-20",
        filters,
    )

    hie_service._get_hie_requesting_user.assert_called_once_with(
        clinic_id=5,
        requesting_user_id=88,
    )

    hie_service._create_submission.assert_called_once_with(
        integration_id=9,
        clinic_id=5,
        patient_id=None,
        operation=HIEOperation.CLINICAL_DATA_QUERY,
        request_data={
            "patient_identifier": "MRN-20",
            "filters": filters,
            "purpose_of_use": "treatment",
            "requesting_user_id": 88,
        },
    )


def test_query_clinical_data_requires_requesting_user_context():
    with pytest.raises(
        TypeError,
    ):
        hie_service.query_clinical_data(
            clinic_id=1,
            purpose_of_use=HIEPurposeOfUse.TREATMENT,
            patient_identifier="MRN-1",
        )


def test_query_clinical_data_rejects_non_dict_filters(
    monkeypatch,
):
    requester = make_user(
        user_id=77,
        clinic_id=1,
        role=Role.DOCTOR,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_hie_requesting_user",
        Mock(return_value=requester),
    )

    with pytest.raises(
        ValidationError,
        match="Clinical data filters must be an object",
    ):
        hie_service.query_clinical_data(
            clinic_id=1,
            requesting_user_id=77,
            purpose_of_use=HIEPurposeOfUse.TREATMENT,
            patient_identifier="MRN-1",
            filters=[],
        )


def test_query_clinical_data_rejects_invalid_purpose(
    monkeypatch,
):
    requester = make_user(
        user_id=77,
        clinic_id=1,
        role=Role.DOCTOR,
    )

    provider = Mock()

    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_hie_requesting_user",
        Mock(return_value=requester),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_provider",
        provider,
    )

    with pytest.raises(
        ValidationError,
        match="Invalid HIE purpose of use",
    ):
        hie_service.query_clinical_data(
            clinic_id=1,
            requesting_user_id=77,
            purpose_of_use="invalid-purpose",
            patient_identifier="MRN-1",
        )

    provider.assert_not_called()


def test_get_hie_integration_success(
    monkeypatch,
):
    integration = make_integration(
        integration_id=15,
        clinic_id=6,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    result = hie_service.get_hie_integration(
        clinic_id=6,
        integration_id=15,
    )

    assert result is integration

    hie_service._get_clinic.assert_called_once_with(
        6,
    )

    hie_service._get_integration.assert_called_once_with(
        6,
        15,
    )


def test_create_hie_integration_success(
    monkeypatch,
    no_transaction,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "HIEIntegration",
        FakeIntegration,
    )

    added = []

    monkeypatch.setattr(
        hie_service.db.session,
        "add",
        lambda obj: added.append(obj),
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "flush",
        Mock(),
    )

    result = hie_service.create_hie_integration(
        clinic_id=8,
        provider=" TEST-HIE-PROVIDER ",
        endpoint_url="  https://hie.example.com  ",
        organization_id=" ORG-8 ",
        facility_id=" FAC-8 ",
    )

    assert result is added[0]
    assert result.clinic_id == 8
    assert result.provider == "test-hie-provider"
    assert result.status == HIEIntegrationStatus.PENDING
    assert result.endpoint_url == "https://hie.example.com"
    assert result.organization_id == "ORG-8"
    assert result.facility_id == "FAC-8"


def test_create_hie_integration_does_not_require_registered_provider(
    monkeypatch,
    no_transaction,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "HIEIntegration",
        FakeIntegration,
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "add",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service.db.session,
        "flush",
        Mock(),
    )

    result = hie_service.create_hie_integration(
        clinic_id=1,
        provider="future-provider",
    )

    assert result.provider == "future-provider"
    assert result.status == HIEIntegrationStatus.PENDING


def test_create_hie_integration_rejects_missing_provider(
    monkeypatch,
    no_transaction,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="Provider is required",
    ):
        hie_service.create_hie_integration(
            clinic_id=1,
            provider=None,
        )


def test_create_hie_integration_rejects_blank_provider(
    monkeypatch,
    no_transaction,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="Provider is required",
    ):
        hie_service.create_hie_integration(
            clinic_id=1,
            provider="   ",
        )


def test_create_hie_integration_rejects_non_string_provider(
    monkeypatch,
    no_transaction,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="Provider must be a string",
    ):
        hie_service.create_hie_integration(
            clinic_id=1,
            provider=123,
        )


def test_create_hie_integration_rejects_blank_organization_id(
    monkeypatch,
    no_transaction,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="Organization ID cannot be empty",
    ):
        hie_service.create_hie_integration(
            clinic_id=1,
            provider="test-hie-provider",
            organization_id="   ",
        )


def test_create_hie_integration_rejects_blank_facility_id(
    monkeypatch,
    no_transaction,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="Facility ID cannot be empty",
    ):
        hie_service.create_hie_integration(
            clinic_id=1,
            provider="test-hie-provider",
            facility_id="   ",
        )


def test_update_hie_integration_success(
    monkeypatch,
    no_transaction,
):
    integration = make_integration(
        integration_id=20,
        clinic_id=9,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )
    monkeypatch.setattr(
        hie_service,
        "is_provider_registered",
        Mock(return_value=True),
    )

    result = hie_service.update_hie_integration(
        clinic_id=9,
        integration_id=20,
        provider=" TEST-HIE-PROVIDER ",
        status=HIEIntegrationStatus.ACTIVE,
        endpoint_url=" https://new.example.com ",
        organization_id=" ORG-NEW ",
        facility_id=" FAC-NEW ",
    )

    assert result is integration
    assert result.provider == "test-hie-provider"
    assert result.status == HIEIntegrationStatus.ACTIVE
    assert result.endpoint_url == "https://new.example.com"
    assert result.organization_id == "ORG-NEW"
    assert result.facility_id == "FAC-NEW"


def test_update_hie_integration_rejects_activation_without_registered_provider(
    monkeypatch,
    no_transaction,
):
    integration = make_integration(
        integration_id=21,
        clinic_id=9,
        provider="future-provider",
        status=HIEIntegrationStatus.PENDING,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )
    monkeypatch.setattr(
        hie_service,
        "is_provider_registered",
        Mock(return_value=False),
    )

    with pytest.raises(
        ValidationError,
        match="HIE provider implementation is not registered",
    ):
        hie_service.update_hie_integration(
            clinic_id=9,
            integration_id=21,
            status=HIEIntegrationStatus.ACTIVE,
        )

    assert integration.status is HIEIntegrationStatus.PENDING


def test_update_hie_integration_rejects_unregistered_provider_change_when_active(
    monkeypatch,
    no_transaction,
):
    integration = make_integration(
        integration_id=22,
        clinic_id=9,
        provider="provider-a",
        status=HIEIntegrationStatus.ACTIVE,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )
    monkeypatch.setattr(
        hie_service,
        "is_provider_registered",
        Mock(return_value=False),
    )

    with pytest.raises(
        ValidationError,
        match="HIE provider implementation is not registered",
    ):
        hie_service.update_hie_integration(
            clinic_id=9,
            integration_id=22,
            provider="provider-b",
        )

    assert integration.provider == "provider-a"
    assert integration.status is HIEIntegrationStatus.ACTIVE


def test_update_hie_integration_accepts_future_provider_name(
    monkeypatch,
    no_transaction,
):
    integration = make_integration(
        integration_id=20,
        clinic_id=9,
        provider="provider-a",
        status=HIEIntegrationStatus.PENDING,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    result = hie_service.update_hie_integration(
        clinic_id=9,
        integration_id=20,
        provider=" Provider-B ",
    )

    assert result.provider == "provider-b"


def test_update_hie_integration_rejects_blank_provider(
    monkeypatch,
    no_transaction,
):
    integration = make_integration(
        integration_id=20,
        clinic_id=9,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    with pytest.raises(
        ValidationError,
        match="Provider cannot be empty",
    ):
        hie_service.update_hie_integration(
            clinic_id=9,
            integration_id=20,
            provider="   ",
        )


def test_update_hie_integration_rejects_non_string_provider(
    monkeypatch,
    no_transaction,
):
    integration = make_integration(
        integration_id=20,
        clinic_id=9,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    with pytest.raises(
        ValidationError,
        match="Provider must be a string",
    ):
        hie_service.update_hie_integration(
            clinic_id=9,
            integration_id=20,
            provider=123,
        )


def test_update_hie_integration_rejects_invalid_status(
    monkeypatch,
    no_transaction,
):
    integration = make_integration(
        integration_id=20,
        clinic_id=9,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    with pytest.raises(
        ValidationError,
        match="Invalid HIE integration status",
    ):
        hie_service.update_hie_integration(
            clinic_id=9,
            integration_id=20,
            status="active",
        )


def test_update_hie_integration_rejects_blank_endpoint(
    monkeypatch,
    no_transaction,
):
    integration = make_integration(
        integration_id=20,
        clinic_id=9,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    with pytest.raises(
        ValidationError,
        match="Endpoint URL cannot be empty",
    ):
        hie_service.update_hie_integration(
            clinic_id=9,
            integration_id=20,
            endpoint_url="   ",
        )


def test_update_hie_integration_rejects_blank_organization(
    monkeypatch,
    no_transaction,
):
    integration = make_integration(
        integration_id=20,
        clinic_id=9,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    with pytest.raises(
        ValidationError,
        match="Organization ID cannot be empty",
    ):
        hie_service.update_hie_integration(
            clinic_id=9,
            integration_id=20,
            organization_id="   ",
        )


def test_update_hie_integration_rejects_blank_facility(
    monkeypatch,
    no_transaction,
):
    integration = make_integration(
        integration_id=20,
        clinic_id=9,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(return_value=integration),
    )

    with pytest.raises(
        ValidationError,
        match="Facility ID cannot be empty",
    ):
        hie_service.update_hie_integration(
            clinic_id=9,
            integration_id=20,
            facility_id="   ",
        )


def test_list_hie_submissions_rejects_invalid_page(
    monkeypatch,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="Page must be greater than or equal to 1",
    ):
        hie_service.list_hie_submissions(
            clinic_id=1,
            page=0,
        )


def test_list_hie_submissions_rejects_non_integer_page(
    monkeypatch,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="Page must be an integer",
    ):
        hie_service.list_hie_submissions(
            clinic_id=1,
            page="1",
        )


def test_list_hie_submissions_rejects_invalid_per_page(
    monkeypatch,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="per_page must be between 1 and 100",
    ):
        hie_service.list_hie_submissions(
            clinic_id=1,
            per_page=101,
        )


def test_list_hie_submissions_rejects_non_integer_per_page(
    monkeypatch,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="per_page must be an integer",
    ):
        hie_service.list_hie_submissions(
            clinic_id=1,
            per_page="20",
        )


def test_list_hie_submissions_rejects_invalid_integration_id(
    monkeypatch,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="Invalid HIE integration ID",
    ):
        hie_service.list_hie_submissions(
            clinic_id=1,
            integration_id=0,
        )


def test_list_hie_submissions_rejects_invalid_patient_id(
    monkeypatch,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="Invalid patient ID",
    ):
        hie_service.list_hie_submissions(
            clinic_id=1,
            patient_id=0,
        )


def test_list_hie_submissions_rejects_invalid_operation(
    monkeypatch,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="Invalid HIE operation",
    ):
        hie_service.list_hie_submissions(
            clinic_id=1,
            operation="patient_query",
        )


def test_list_hie_submissions_rejects_invalid_status(
    monkeypatch,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    with pytest.raises(
        ValidationError,
        match="Invalid HIE submission status",
    ):
        hie_service.list_hie_submissions(
            clinic_id=1,
            status="success",
        )


def test_list_hie_submissions_validates_integration_ownership(
    monkeypatch,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    integration = make_integration(
        integration_id=20,
        clinic_id=1,
    )

    get_integration = Mock(
        return_value=integration,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        get_integration,
    )

    monkeypatch.setattr(
        hie_service.db,
        "paginate",
        Mock(),
    )

    hie_service.list_hie_submissions(
        clinic_id=1,
        integration_id=20,
    )

    get_integration.assert_called_once_with(
        1,
        20,
    )


def test_list_hie_submissions_validates_patient_ownership(
    monkeypatch,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    patient = make_patient(
        patient_id=30,
        clinic_id=1,
    )

    get_patient = Mock(
        return_value=patient,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_patient",
        get_patient,
    )

    monkeypatch.setattr(
        hie_service.db,
        "paginate",
        Mock(),
    )

    hie_service.list_hie_submissions(
        clinic_id=1,
        patient_id=30,
    )

    get_patient.assert_called_once_with(
        1,
        30,
    )


def test_list_hie_submissions_success(
    monkeypatch,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    integration = make_integration(
        integration_id=7,
        clinic_id=1,
    )

    patient = make_patient(
        patient_id=9,
        clinic_id=1,
    )

    get_integration = Mock(
        return_value=integration,
    )

    get_patient = Mock(
        return_value=patient,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        get_integration,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_patient",
        get_patient,
    )

    pagination = SimpleNamespace(
        items=[
            make_submission(
                submission_id=1,
                clinic_id=1,
            ),
            make_submission(
                submission_id=2,
                clinic_id=1,
            ),
        ],
        total=2,
        page=2,
        per_page=10,
        pages=1,
        has_next=False,
        has_prev=True,
    )

    paginate = Mock(
        return_value=pagination,
    )

    monkeypatch.setattr(
        hie_service.db,
        "paginate",
        paginate,
    )

    result = hie_service.list_hie_submissions(
        clinic_id=1,
        integration_id=7,
        patient_id=9,
        operation=HIEOperation.PATIENT_QUERY,
        status=HIESubmissionStatus.SUCCESS,
        page=2,
        per_page=10,
    )

    assert result is pagination

    paginate.assert_called_once()

    statement = paginate.call_args.args[0]

    where_criteria = list(
        statement._where_criteria
    )

    assert len(where_criteria) == 5

    assert any(
        criterion.left.name == "clinic_id"
        for criterion in where_criteria
    )

    assert any(
        criterion.left.name == "integration_id"
        for criterion in where_criteria
    )

    assert any(
        criterion.left.name == "patient_id"
        for criterion in where_criteria
    )

    assert any(
        criterion.left.name == "operation"
        for criterion in where_criteria
    )

    assert any(
        criterion.left.name == "status"
        for criterion in where_criteria
    )

    assert paginate.call_args.kwargs == {
        "page": 2,
        "per_page": 10,
        "error_out": False,
    }

    get_integration.assert_called_once_with(
        1,
        7,
    )

    get_patient.assert_called_once_with(
        1,
        9,
    )


def test_list_hie_submissions_uses_deterministic_ordering(
    monkeypatch,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    pagination = SimpleNamespace(
        items=[],
        total=0,
        page=1,
        per_page=20,
    )

    paginate = Mock(
        return_value=pagination,
    )

    monkeypatch.setattr(
        hie_service.db,
        "paginate",
        paginate,
    )

    result = hie_service.list_hie_submissions(
        clinic_id=1,
    )

    assert result is pagination

    paginate.assert_called_once()

    statement = paginate.call_args.args[0]

    order_by_clauses = list(
        statement._order_by_clauses
    )

    assert len(order_by_clauses) == 2

    first_order = order_by_clauses[0]
    second_order = order_by_clauses[1]

    assert first_order.element.name == "created_at"
    assert second_order.element.name == "id"

    assert str(first_order).lower().endswith(
        "desc"
    )

    assert str(second_order).lower().endswith(
        "desc"
    )


def test_list_hie_submissions_default_pagination(
    monkeypatch,
):
    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    pagination = SimpleNamespace(
        items=[],
        total=0,
        page=1,
        per_page=20,
    )

    paginate = Mock(
        return_value=pagination,
    )

    monkeypatch.setattr(
        hie_service.db,
        "paginate",
        paginate,
    )

    result = hie_service.list_hie_submissions(
        clinic_id=1,
    )

    assert result is pagination

    paginate.assert_called_once()

    assert paginate.call_args.kwargs == {
        "page": 1,
        "per_page": 20,
        "error_out": False,
    }