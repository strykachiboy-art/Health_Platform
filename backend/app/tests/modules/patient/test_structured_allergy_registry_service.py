from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    ValidationError,
)
from app.modules.patient.models.patient_model import (
    PatientAllergy,
)
import app.modules.patient.services.patient_service as service


@pytest.fixture()
def allergy_service(
    monkeypatch,
):
    audit_mock = Mock()

    monkeypatch.setattr(
        service,
        "create_audit_log",
        audit_mock,
    )

    return service, audit_mock


def _active_patient(
    clinic,
    patient,
):
    clinic.status = ClinicStatus.ACTIVE
    patient.clinic_id = clinic.id
    patient.is_active = True


def _staff(
    make_authenticated_staff,
    clinic,
):
    staff, _ = make_authenticated_staff(
        clinic,
        Role.DOCTOR,
    )
    return staff


def test_create_allergy_persists_server_controlled_defaults(
    allergy_service,
    clinic,
    patient,
    make_authenticated_staff,
):
    svc, audit_mock = allergy_service

    _active_patient(
        clinic,
        patient,
    )

    staff = _staff(
        make_authenticated_staff,
        clinic,
    )

    record = svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "  Penicillin  ",
            "allergen_code": "  RXNORM:7980  ",
            "allergen_type": "drug",
            "reaction": "  urticaria  ",
            "severity": "severe",
            "notes": "  history verified  ",
        },
        recorded_by_id=staff.id,
        actor_id=staff.user_id,
    )

    assert record.id is not None
    assert record.clinic_id == clinic.id
    assert record.patient_id == patient.id
    assert record.allergen_name == "Penicillin"
    assert record.allergen_code == "RXNORM:7980"
    assert record.allergen_type == "drug"
    assert record.reaction == "urticaria"
    assert record.severity == "severe"
    assert record.clinical_status == "active"
    assert record.verification_status == "unconfirmed"
    assert record.recorded_by_id == staff.id
    assert record.recorded_at is not None
    assert record.notes == "history verified"
    assert audit_mock.call_count == 1


def test_create_rejects_cross_clinic_patient(
    allergy_service,
    clinic,
    patient,
):
    svc, _ = allergy_service

    _active_patient(
        clinic,
        patient,
    )

    with pytest.raises(
        ValidationError,
        match="does not belong to clinic",
    ):
        svc.create_patient_allergy(
            clinic_id=clinic.id + 999,
            patient_id=patient.id,
            data={
                "allergen_name": "Penicillin",
            },
        )


def test_create_rejects_recorded_by_from_other_clinic(
    allergy_service,
    db,
    clinic,
    patient,
    make_clinic,
    make_authenticated_staff,
):
    svc, _ = allergy_service

    _active_patient(
        clinic,
        patient,
    )

    other_clinic = make_clinic()
    other_clinic.status = ClinicStatus.ACTIVE

    other_staff = _staff(
        make_authenticated_staff,
        other_clinic,
    )

    db.session.flush()

    with pytest.raises(
        ValidationError,
        match="same clinic",
    ):
        svc.create_patient_allergy(
            clinic_id=clinic.id,
            patient_id=patient.id,
            data={
                "allergen_name": "Penicillin",
            },
            recorded_by_id=other_staff.id,
        )


def test_duplicate_active_allergy_by_code_is_blocked_case_insensitively(
    allergy_service,
    clinic,
    patient,
):
    svc, _ = allergy_service

    _active_patient(
        clinic,
        patient,
    )

    svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "Penicillin",
            "allergen_code": "RXNORM:7980",
            "allergen_type": "drug",
        },
    )

    with pytest.raises(
        ConflictError,
        match="already exists",
    ):
        svc.create_patient_allergy(
            clinic_id=clinic.id,
            patient_id=patient.id,
            data={
                "allergen_name": "Penicillin G",
                "allergen_code": "rxnorm:7980",
                "allergen_type": "drug",
            },
        )


def test_duplicate_active_allergy_by_name_is_blocked_without_code(
    allergy_service,
    clinic,
    patient,
):
    svc, _ = allergy_service

    _active_patient(
        clinic,
        patient,
    )

    svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "  Peanut  ",
            "allergen_type": "food",
        },
    )

    with pytest.raises(
        ConflictError,
        match="already exists",
    ):
        svc.create_patient_allergy(
            clinic_id=clinic.id,
            patient_id=patient.id,
            data={
                "allergen_name": "peanut",
                "allergen_type": "food",
            },
        )


def test_resolved_allergy_does_not_block_new_active_record(
    allergy_service,
    clinic,
    patient,
):
    svc, _ = allergy_service

    _active_patient(
        clinic,
        patient,
    )

    old = svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "Latex",
            "allergen_type": "latex",
        },
    )

    svc.transition_patient_allergy_status(
        clinic_id=clinic.id,
        patient_id=patient.id,
        allergy_id=old.id,
        clinical_status="resolved",
        reason="No longer clinically active",
    )

    new = svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "Latex",
            "allergen_type": "latex",
        },
    )

    assert new.id != old.id
    assert new.clinical_status == "active"


def test_refuted_allergy_becomes_inactive_and_does_not_block_new_record(
    allergy_service,
    clinic,
    patient,
):
    svc, _ = allergy_service

    _active_patient(
        clinic,
        patient,
    )

    old = svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "Shellfish",
            "allergen_type": "food",
        },
    )

    transitioned = (
        svc.transition_patient_allergy_status(
            clinic_id=clinic.id,
            patient_id=patient.id,
            allergy_id=old.id,
            verification_status="refuted",
            reason="Challenge was negative",
        )
    )

    assert transitioned.verification_status == "refuted"
    assert transitioned.clinical_status == "inactive"

    new = svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "Shellfish",
            "allergen_type": "food",
        },
    )

    assert new.id != old.id


def test_update_rejects_identity_collision(
    allergy_service,
    clinic,
    patient,
):
    svc, _ = allergy_service

    _active_patient(
        clinic,
        patient,
    )

    first = svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "Penicillin",
            "allergen_code": "RXNORM:7980",
            "allergen_type": "drug",
        },
    )

    second = svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "Sulfonamide",
            "allergen_code": "RXNORM:10185",
            "allergen_type": "drug",
        },
    )

    assert first.id != second.id

    with pytest.raises(
        ConflictError,
        match="already exists",
    ):
        svc.update_patient_allergy(
            clinic_id=clinic.id,
            patient_id=patient.id,
            allergy_id=second.id,
            data={
                "allergen_code": "rxnorm:7980",
            },
        )


def test_update_audits_changed_fields(
    allergy_service,
    clinic,
    patient,
):
    svc, audit_mock = allergy_service

    _active_patient(
        clinic,
        patient,
    )

    record = svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "Penicillin",
        },
    )

    audit_mock.reset_mock()

    updated = svc.update_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        allergy_id=record.id,
        data={
            "reaction": "Anaphylaxis",
            "severity": "life_threatening",
        },
        actor_id=11,
    )

    assert updated.reaction == "Anaphylaxis"
    assert updated.severity == "life_threatening"
    assert audit_mock.call_count == 1

    new_value = (
        audit_mock
        .call_args
        .kwargs["new_value"]
    )

    assert (
        "changed_fields"
        in new_value
    )
    assert (
        new_value["changed_fields"]["severity"]["new"]
        == "life_threatening"
    )


def test_resolve_transition_is_audited(
    allergy_service,
    clinic,
    patient,
):
    svc, audit_mock = allergy_service

    _active_patient(
        clinic,
        patient,
    )

    record = svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "Pollen",
            "allergen_type": "environmental",
        },
    )

    audit_mock.reset_mock()

    result = svc.transition_patient_allergy_status(
        clinic_id=clinic.id,
        patient_id=patient.id,
        allergy_id=record.id,
        clinical_status="resolved",
        reason="Symptoms resolved",
        actor_id=17,
    )

    assert result.clinical_status == "resolved"
    assert audit_mock.call_count == 1

    audit_payload = (
        audit_mock
        .call_args
        .kwargs["new_value"]
    )

    assert (
        audit_payload["reason"]
        == "Symptoms resolved"
    )


def test_status_transition_rejects_noop(
    allergy_service,
    clinic,
    patient,
):
    svc, _ = allergy_service

    _active_patient(
        clinic,
        patient,
    )

    record = svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "Pollen",
        },
    )

    with pytest.raises(
        ConflictError,
        match="does not change state",
    ):
        svc.transition_patient_allergy_status(
            clinic_id=clinic.id,
            patient_id=patient.id,
            allergy_id=record.id,
            clinical_status="active",
            reason="No actual change",
        )


def test_list_active_non_refuted_filters_history(
    allergy_service,
    clinic,
    patient,
):
    svc, _ = allergy_service

    _active_patient(
        clinic,
        patient,
    )

    active = svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "Penicillin",
            "allergen_type": "drug",
        },
    )

    resolved = svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "Latex",
            "allergen_type": "latex",
        },
    )

    svc.transition_patient_allergy_status(
        clinic_id=clinic.id,
        patient_id=patient.id,
        allergy_id=resolved.id,
        clinical_status="resolved",
        reason="Resolved",
    )

    refuted = svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "Egg",
            "allergen_type": "food",
        },
    )

    svc.transition_patient_allergy_status(
        clinic_id=clinic.id,
        patient_id=patient.id,
        allergy_id=refuted.id,
        verification_status="refuted",
        reason="Negative challenge",
    )

    page = svc.list_patient_allergies(
        clinic_id=clinic.id,
        patient_id=patient.id,
        active_only=True,
        include_refuted=False,
        page=1,
        per_page=50,
    )

    ids = [
        item.id
        for item in page.items
    ]

    assert ids == [
        active.id
    ]


def test_get_allergy_is_tenant_scoped(
    allergy_service,
    clinic,
    patient,
):
    svc, _ = allergy_service

    _active_patient(
        clinic,
        patient,
    )

    record = svc.create_patient_allergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        data={
            "allergen_name": "Penicillin",
        },
    )

    with pytest.raises(
        ValidationError,
        match="does not belong to clinic",
    ):
        svc.get_patient_allergy(
            clinic_id=clinic.id + 999,
            patient_id=patient.id,
            allergy_id=record.id,
        )


def test_hard_delete_service_is_intentionally_absent():
    assert not hasattr(
        service,
        "delete_patient_allergy",
    )

    assert not hasattr(
        PatientAllergy,
        "deleted_at",
    )
