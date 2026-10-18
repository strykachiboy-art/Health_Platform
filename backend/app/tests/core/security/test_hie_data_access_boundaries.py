from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.enums.hie_enums import (
    HIEIntegrationStatus,
    HIEOperation,
    HIESubmissionStatus,
)
from app.core.exceptions import ValidationError
from app.extensions import db
from app.modules.hie.models.hie_model import (
    HIEIntegration,
    HIESubmission,
)
from app.modules.hie.services import hie_service


def _make_integration(
    clinic_id: int,
) -> HIEIntegration:
    integration = HIEIntegration(
        clinic_id=clinic_id,
        provider="malaffi",
        status=HIEIntegrationStatus.ACTIVE,
        endpoint_url="https://fhir.partnerhealth.org/fhir/r4",
        organization_id=f"ORG-{clinic_id}",
        facility_id=f"FAC-{clinic_id}",
    )

    db.session.add(integration)
    db.session.flush()

    return integration


def _make_submission(
    clinic_id: int,
    integration_id: int,
    patient_id: int | None,
) -> HIESubmission:
    submission = HIESubmission(
        clinic_id=clinic_id,
        integration_id=integration_id,
        patient_id=patient_id,
        operation=HIEOperation.PATIENT_QUERY,
        status=HIESubmissionStatus.SUCCESS,
        request_data={
            "patient_identifier": (
                f"MRN-{patient_id}"
                if patient_id is not None
                else "MRN-EXTERNAL"
            ),
        },
        response_data={
            "status_code": 200,
        },
        status_code=200,
        submitted_at=datetime.now(
            timezone.utc,
        ),
        retry_count=0,
    )

    db.session.add(submission)
    db.session.flush()

    return submission


def test_hie_integration_cross_clinic_access_is_rejected(
    clinic,
    make_clinic,
):
    clinic_b = make_clinic(
        name="HIE Clinic B",
    )

    integration_b = _make_integration(
        clinic_b.id,
    )

    db.session.commit()

    with pytest.raises(
        ValidationError,
        match="HIE integration does not belong to the clinic",
    ):
        hie_service.get_hie_integration(
            clinic_id=clinic.id,
            integration_id=integration_b.id,
        )


def test_hie_submission_listing_is_tenant_scoped(
    clinic,
    make_clinic,
):
    clinic_b = make_clinic(
        name="HIE Clinic B",
    )

    integration_a = _make_integration(
        clinic.id,
    )

    integration_b = _make_integration(
        clinic_b.id,
    )

    submission_a = _make_submission(
        clinic_id=clinic.id,
        integration_id=integration_a.id,
        patient_id=None,
    )

    submission_b = _make_submission(
        clinic_id=clinic_b.id,
        integration_id=integration_b.id,
        patient_id=None,
    )

    db.session.commit()

    pagination = hie_service.list_hie_submissions(
        clinic_id=clinic.id,
    )

    returned_ids = {
        submission.id
        for submission in pagination.items
    }

    assert submission_a.id in returned_ids
    assert submission_b.id not in returned_ids


def test_hie_integration_filter_cannot_cross_clinic(
    clinic,
    make_clinic,
):
    clinic_b = make_clinic(
        name="HIE Clinic B",
    )

    integration_b = _make_integration(
        clinic_b.id,
    )

    db.session.commit()

    with pytest.raises(
        ValidationError,
        match="HIE integration does not belong to the clinic",
    ):
        hie_service.list_hie_submissions(
            clinic_id=clinic.id,
            integration_id=integration_b.id,
        )