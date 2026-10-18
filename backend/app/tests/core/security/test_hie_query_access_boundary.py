from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.enums.hie_enums import (
    HIEIntegrationStatus,
    HIEPurposeOfUse,
)
from app.core.exceptions import ValidationError
from app.modules.hie.services import hie_service


def _make_integration(
    clinic_id: int,
    integration_id: int = 1,
):
    return SimpleNamespace(
        id=integration_id,
        clinic_id=clinic_id,
        provider="test-hie-provider",
        status=HIEIntegrationStatus.ACTIVE,
        endpoint_url="https://fhir.partnerhealth.org/fhir/r4",
        organization_id="ORG-001",
        facility_id="FAC-001",
    )


def _prepare_hie_query(
    monkeypatch,
    clinic_id: int,
):
    integration = _make_integration(
        clinic_id=clinic_id,
    )

    provider = Mock()

    monkeypatch.setattr(
        hie_service,
        "_get_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_integration",
        Mock(
            return_value=integration,
        ),
    )

    monkeypatch.setattr(
        hie_service,
        "_validate_integration",
        Mock(),
    )

    monkeypatch.setattr(
        hie_service,
        "_get_provider",
        Mock(
            return_value=provider,
        ),
    )

    monkeypatch.setattr(
        hie_service,
        "_create_submission",
        Mock(
            return_value=100,
        ),
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

    return provider


def test_hie_patient_query_requires_requesting_user_id(
    monkeypatch,
):
    provider = _prepare_hie_query(
        monkeypatch,
        clinic_id=1,
    )

    provider.query_patient.return_value = {
        "status_code": 200,
        "records": [],
    }

    with pytest.raises(
        TypeError,
        match=(
            "missing 2 required keyword-only arguments"
        ),
    ):
        hie_service.query_patient(
            clinic_id=1,
            patient_identifier="EXTERNAL-PATIENT-999999",
        )

    provider.query_patient.assert_not_called()


def test_hie_clinical_query_requires_requesting_user_id(
    monkeypatch,
):
    provider = _prepare_hie_query(
        monkeypatch,
        clinic_id=1,
    )

    provider.query_clinical_data.return_value = {
        "status_code": 200,
        "records": [],
    }

    with pytest.raises(
        TypeError,
        match=(
            "missing 2 required keyword-only arguments"
        ),
    ):
        hie_service.query_clinical_data(
            clinic_id=1,
            patient_identifier="EXTERNAL-PATIENT-999999",
            filters={
                "resource_type": "Medication",
            },
        )

    provider.query_clinical_data.assert_not_called()


def test_hie_patient_query_rejects_invalid_requesting_user_id(
    monkeypatch,
):
    provider = _prepare_hie_query(
        monkeypatch,
        clinic_id=1,
    )

    with pytest.raises(
        ValidationError,
        match=(
            "Invalid requesting user ID"
        ),
    ):
        hie_service.query_patient(
            clinic_id=1,
            requesting_user_id=0,
            purpose_of_use=HIEPurposeOfUse.TREATMENT,
            patient_identifier="EXTERNAL-PATIENT-999999",
        )

    provider.query_patient.assert_not_called()


def test_hie_clinical_query_rejects_invalid_requesting_user_id(
    monkeypatch,
):
    provider = _prepare_hie_query(
        monkeypatch,
        clinic_id=1,
    )

    with pytest.raises(
        ValidationError,
        match=(
            "Invalid requesting user ID"
        ),
    ):
        hie_service.query_clinical_data(
            clinic_id=1,
            requesting_user_id=0,
            purpose_of_use=HIEPurposeOfUse.TREATMENT,
            patient_identifier="EXTERNAL-PATIENT-999999",
            filters={
                "resource_type": "Medication",
            },
        )

    provider.query_clinical_data.assert_not_called()


def test_hie_patient_query_requires_authorized_requester_context(
    monkeypatch,
):
    provider = _prepare_hie_query(
        monkeypatch,
        clinic_id=1,
    )

    requester = SimpleNamespace(
        id=10,
        clinic_id=1,
        is_active=True,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_hie_requesting_user",
        Mock(
            return_value=requester,
        ),
    )

    provider.query_patient.return_value = {
        "status_code": 200,
        "records": [],
    }

    result = hie_service.query_patient(
        clinic_id=1,
        requesting_user_id=10,
        purpose_of_use=HIEPurposeOfUse.TREATMENT,
        patient_identifier="EXTERNAL-PATIENT-999999",
    )

    assert result["status_code"] == 200

    provider.query_patient.assert_called_once_with(
        "EXTERNAL-PATIENT-999999",
    )

    hie_service._create_submission.assert_called_once()


def test_hie_clinical_query_requires_authorized_requester_context(
    monkeypatch,
):
    provider = _prepare_hie_query(
        monkeypatch,
        clinic_id=1,
    )

    requester = SimpleNamespace(
        id=10,
        clinic_id=1,
        is_active=True,
    )

    monkeypatch.setattr(
        hie_service,
        "_get_hie_requesting_user",
        Mock(
            return_value=requester,
        ),
    )

    provider.query_clinical_data.return_value = {
        "status_code": 200,
        "records": [
            {
                "id": "REMOTE-RECORD-1",
            },
        ],
    }

    filters = {
        "resource_type": "Medication",
    }

    result = hie_service.query_clinical_data(
        clinic_id=1,
        requesting_user_id=10,
        purpose_of_use=HIEPurposeOfUse.TREATMENT,
        patient_identifier="EXTERNAL-PATIENT-999999",
        filters=filters,
    )

    assert result["status_code"] == 200

    provider.query_clinical_data.assert_called_once_with(
        "EXTERNAL-PATIENT-999999",
        filters,
    )

    hie_service._create_submission.assert_called_once()
