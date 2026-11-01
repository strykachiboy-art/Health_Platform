import pytest

from app.core.audit.models.audit_model import AuditLog
from app.core.audit.queries.patient_timeline import (
    count_patient_timeline,
    get_patient_timeline,
    get_patient_timeline_page,
)
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role


def make_log(
    *,
    clinic_id,
    patient_id,
    entity_type="Patient",
    entity_id=None,
    action=AuditAction.CREATE,
    user_id=None,
):
    if entity_id is None:
        entity_id = patient_id

    return AuditLog(
        clinic_id=clinic_id,
        patient_id=patient_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        user_id=user_id,
    )


def test_get_patient_timeline_returns_patient_events(
    db_session,
    clinic,
    make_patient,
):
    patient = make_patient(clinic)
    other_patient = make_patient(clinic)

    db_session.add(
        make_log(
            clinic_id=clinic.id,
            patient_id=patient.id,
        )
    )
    db_session.add(
        make_log(
            clinic_id=clinic.id,
            patient_id=patient.id,
            action=AuditAction.UPDATE,
        )
    )
    db_session.add(
        make_log(
            clinic_id=clinic.id,
            patient_id=other_patient.id,
        )
    )

    db_session.flush()

    result = get_patient_timeline(
        actor_role=Role.ADMIN,
        actor_clinic_id=clinic.id,
        patient_id=patient.id,
    )

    assert len(result) == 2
    assert all(
        log.entity_type == "Patient"
        for log in result
    )
    assert all(
        log.entity_id == patient.id
        for log in result
    )
    assert all(
        log.patient_id == patient.id
        for log in result
    )
    assert all(
        log.clinic_id == clinic.id
        for log in result
    )


def test_patient_timeline_is_tenant_scoped(
    db_session,
    make_clinic,
    make_patient,
):
    clinic_a = make_clinic()
    clinic_b = make_clinic()
    patient = make_patient(clinic_a)

    db_session.add(
        make_log(
            clinic_id=clinic_a.id,
            patient_id=patient.id,
        )
    )
    db_session.add(
        make_log(
            clinic_id=clinic_b.id,
            patient_id=patient.id,
        )
    )

    db_session.flush()

    result = get_patient_timeline(
        actor_role=Role.ADMIN,
        actor_clinic_id=clinic_a.id,
        patient_id=patient.id,
    )

    assert len(result) == 1
    assert result[0].clinic_id == clinic_a.id


def test_super_admin_can_read_patient_timeline_across_clinics(
    db_session,
    make_clinic,
    make_patient,
):
    clinic_a = make_clinic()
    clinic_b = make_clinic()
    patient = make_patient(clinic_a)

    db_session.add(
        make_log(
            clinic_id=clinic_a.id,
            patient_id=patient.id,
        )
    )
    db_session.add(
        make_log(
            clinic_id=clinic_b.id,
            patient_id=patient.id,
        )
    )

    db_session.flush()

    result = get_patient_timeline(
        actor_role=Role.SUPER_ADMIN,
        actor_clinic_id=None,
        patient_id=patient.id,
    )

    assert len(result) == 2


def test_patient_timeline_supports_action_filter(
    db_session,
    clinic,
    make_patient,
):
    patient = make_patient(clinic)

    db_session.add(
        make_log(
            clinic_id=clinic.id,
            patient_id=patient.id,
            action=AuditAction.CREATE,
        )
    )
    db_session.add(
        make_log(
            clinic_id=clinic.id,
            patient_id=patient.id,
            action=AuditAction.UPDATE,
        )
    )

    db_session.flush()

    result = get_patient_timeline(
        actor_role=Role.ADMIN,
        actor_clinic_id=clinic.id,
        patient_id=patient.id,
        action=AuditAction.UPDATE,
    )

    assert len(result) == 1
    assert result[0].action == AuditAction.UPDATE


def test_patient_timeline_page_preserves_pagination(
    db_session,
    clinic,
    make_patient,
):
    patient = make_patient(clinic)

    for index in range(5):
        db_session.add(
            make_log(
                clinic_id=clinic.id,
                patient_id=patient.id,
            )
        )

    db_session.flush()

    result = get_patient_timeline_page(
        actor_role=Role.ADMIN,
        actor_clinic_id=clinic.id,
        patient_id=patient.id,
        page=1,
        per_page=2,
    )

    assert result.total == 5
    assert result.page == 1
    assert result.per_page == 2
    assert len(result.items) == 2
    assert result.has_next is True


def test_count_patient_timeline_is_tenant_scoped(
    db_session,
    make_clinic,
    make_patient,
):
    clinic_a = make_clinic()
    clinic_b = make_clinic()
    patient = make_patient(clinic_a)

    db_session.add(
        make_log(
            clinic_id=clinic_a.id,
            patient_id=patient.id,
        )
    )
    db_session.add(
        make_log(
            clinic_id=clinic_a.id,
            patient_id=patient.id,
        )
    )
    db_session.add(
        make_log(
            clinic_id=clinic_b.id,
            patient_id=patient.id,
        )
    )

    db_session.flush()

    count = count_patient_timeline(
        actor_role=Role.ADMIN,
        actor_clinic_id=clinic_a.id,
        patient_id=patient.id,
    )

    assert count == 2


@pytest.mark.parametrize(
    "role",
    [
        Role.DOCTOR,
        Role.NURSE,
        Role.PATIENT,
        Role.RECEPTIONIST,
        Role.ACCOUNTANT,
    ],
)
def test_non_admin_cannot_read_patient_timeline(
    role,
):
    with pytest.raises(
        PermissionError,
        match="Insufficient audit permissions",
    ):
        get_patient_timeline(
            actor_role=role,
            actor_clinic_id=1,
            patient_id=100,
        )
        
def test_patient_timeline_reads_production_patient_audit_writer(
    db_session,
    clinic,
    user,
):
    from app.core.audit.queries.patient_timeline import (
        get_patient_timeline,
    )
    from app.modules.patient.services.patient_service import (
        create_patient,
    )

    patient = create_patient(
        clinic.id,
        {
            "first_name": "Timeline",
            "last_name": "Integration",
            "email": "timeline-integration@test.com",
        },
        actor_id=user.id,
    )

    result = get_patient_timeline(
        actor_role=Role.ADMIN,
        actor_clinic_id=clinic.id,
        patient_id=patient.id,
    )

    assert len(result) == 1
    assert result[0].entity_id == patient.id
    assert result[0].entity_type == "patient"
    assert result[0].user_id == user.id
    assert result[0].clinic_id == clinic.id
    assert result[0].action == AuditAction.CREATE