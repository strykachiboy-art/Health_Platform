"""RAD-C B01 staged local intent and authorization; no live PACS claims."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.radiology.models.ris_model import (
    RadiologyExam, RadiologyDicomIdentity, RadiologyInterpretationGrant,
    RadiologyWorklistPublication,
)
from app.modules.radiology.services import ris_service as ris
from app.modules.radiology.services import radc_worklist_service as clinical
from app.tests.modules.radiology.test_rad1_ris_foundation import setup_ris


@pytest.fixture
def scheduled(setup_ris, app):
    s = setup_ris
    app.config['RAD_C_WORKLIST_STAGED_ENABLED'] = True
    exam = ris.establish_exam(imaging_order_id=s['order'].id, clinic_id=s['clinic'].id,
                              actor_staff_id=s['doctor'].id)
    s['args'] = dict(exam_id=exam['exam_id'], clinic_id=s['clinic'].id,
                     actor_staff_id=s['doctor'].id)
    s['legacy'] = exam['accession_number']
    s['when'] = datetime(2030, 11, 9, 11, tzinfo=timezone.utc)
    ris.schedule_intent(**s['args'], expected_version=1, scheduled_at=s['when'], station_code='XR01')
    return s


def test_local_publication_is_durable_and_idempotent(scheduled):
    s = scheduled
    args = s['args']
    assert clinical.get_publication(**args)['local_state'] == 'not_published'
    first = clinical.publish(**args, expected_version=2)
    patient_id = s['patient'].id
    db.session.remove()
    assert clinical.get_publication(**args) == first
    assert clinical.publish(**args, expected_version=2) == first
    assert first['local_state'] == 'local_published'
    assert first['external_publication_confirmed'] is False
    assert first['archive_verified'] is False
    assert first['diagnostic_report_signed'] is False
    row = db.session.query(RadiologyDicomIdentity).one()
    assert len(row.dicom_accession) == 16
    assert row.patient_id == patient_id
    assert row.study_instance_uid.startswith('2.25.')
    assert db.session.get(RadiologyExam, args['exam_id']).accession_number == s['legacy']
    assert db.session.query(RadiologyWorklistPublication).count() == 1


def test_reschedule_and_cancel_keep_history(scheduled):
    s = scheduled; args = s['args']
    first = clinical.publish(**args, expected_version=2)
    identity = db.session.get(RadiologyDicomIdentity, args['exam_id']).study_instance_uid
    ris.schedule_intent(**args, expected_version=2, scheduled_at=s['when'] + timedelta(days=1), station_code='XR02')
    assert clinical.get_publication(**args)['local_state'] == 'superseded'
    second = clinical.publish(**args, expected_version=3)
    assert second['publication_id'] != first['publication_id']
    assert db.session.get(RadiologyDicomIdentity, args['exam_id']).study_instance_uid == identity
    ris.cancel_schedule_intent(**args, expected_version=3)
    assert clinical.get_publication(**args)['local_state'] == 'cancelled_or_inactive'
    with pytest.raises(ConflictError):
        clinical.publish(**args, expected_version=4)
    assert db.session.query(RadiologyWorklistPublication).count() == 2


def test_stale_version_does_not_allocate(scheduled):
    with pytest.raises(ConflictError):
        clinical.publish(**scheduled['args'], expected_version=1)
    assert db.session.query(RadiologyDicomIdentity).count() == 0


def test_invalid_station_does_not_allocate(scheduled):
    s = scheduled
    ris.schedule_intent(**s['args'], expected_version=2, scheduled_at=s['when'], station_code='bad station')
    with pytest.raises(ValidationError):
        clinical.publish(**s['args'], expected_version=3)
    assert db.session.query(RadiologyDicomIdentity).count() == 0


def test_disabled_and_non_testing_hold(scheduled, app):
    app.config['RAD_C_WORKLIST_STAGED_ENABLED'] = False
    with pytest.raises(NotFoundError):
        clinical.publish(**scheduled['args'], expected_version=2)
    app.config['RAD_C_WORKLIST_STAGED_ENABLED'] = True
    app.config['TESTING'] = False
    try:
        with pytest.raises(NotFoundError):
            clinical.publish(**scheduled['args'], expected_version=2)
    finally:
        app.config['TESTING'] = True
    assert db.session.query(RadiologyWorklistPublication).count() == 0


def test_wrong_doctor_and_clinic_denied(scheduled, make_clinic, make_staff):
    s = scheduled
    other = make_staff(s['clinic'], role=Role.DOCTOR)
    foreign_clinic = make_clinic(name='RAD-C foreign')
    foreign = make_staff(foreign_clinic, role=Role.DOCTOR)
    other_args = s['args'] | {'actor_staff_id': other.id}
    foreign_args = s['args'] | {'actor_staff_id': foreign.id, 'clinic_id': foreign_clinic.id}
    db.session.commit()
    for args in (other_args, foreign_args):
        with pytest.raises(NotFoundError):
            clinical.publish(**args, expected_version=2)
    assert db.session.query(RadiologyWorklistPublication).count() == 0


def test_http_contract_and_no_authority_from_client(scheduled, client, auth_headers_for):
    s = scheduled
    url = f"/api/v1/radiology/exams/{s['args']['exam_id']}/clinical-worklist"
    assert client.get(url).status_code == 401
    assert client.post(url, json={'expected_version': 2}, headers=auth_headers_for(s['nurse'].user)).status_code == 403
    assert client.post(url, json={'expected_version': 2, 'patient_id': 99}, headers=s['headers']).status_code == 422
    assert client.post(url, json={'expected_version': 2}, headers={**s['headers'], 'Idempotency-Key': 'not-supported'}).status_code == 422
    response = client.post(url, json={'expected_version': 2}, headers=s['headers'])
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['data']['external_publication_confirmed'] is False
    assert response.headers['Cache-Control'] == 'no-store'
    assert client.get(url, headers=s['headers']).get_json()['data'] == response.get_json()['data']


def test_tampered_publication_is_not_current(scheduled):
    clinical.publish(**scheduled['args'], expected_version=2)
    row = db.session.query(RadiologyWorklistPublication).one()
    row.payload = dict(row.payload, patient_identifier='other')
    db.session.commit()
    with pytest.raises(ConflictError):
        clinical.get_publication(**scheduled['args'])
    with pytest.raises(ConflictError):
        clinical.publish(**scheduled['args'], expected_version=2)


def test_order_inactivation_holds_current_publication(scheduled):
    from app.modules.clinical_orders.services import imaging_order_service as imaging
    s = scheduled
    clinical.publish(**s['args'], expected_version=2)
    imaging.complete_imaging_order(s['order'].id, clinic_id=s['clinic'].id,
        actor_staff_id=s['doctor'].id, report_text='Synthetic legacy completion')
    assert clinical.get_publication(**s['args'])['local_state'] == 'cancelled_or_inactive'
    with pytest.raises(ConflictError):
        clinical.publish(**s['args'], expected_version=2)


def test_specialty_text_is_not_credential(scheduled):
    scheduled['doctor'].specialty = 'Radiologist'
    db.session.commit()
    with pytest.raises(NotFoundError):
        clinical.require_interpreting_authority(**scheduled['args'])


def _grant(s, actor, *, modality='xray', expired=False):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    g = RadiologyInterpretationGrant(exam_id=s['args']['exam_id'], staff_id=actor.id,
        clinic_id=s['clinic'].id, modality=modality, credential_reference='SYNTHETIC_APPROVAL_ONLY',
        approved_at=now-timedelta(days=2), expires_at=now+timedelta(days=-1 if expired else 1))
    db.session.add(g); db.session.commit()
    return g


def test_grant_required_and_revocation_immediate(scheduled, make_staff):
    s = scheduled
    specialist = make_staff(s['clinic'], role=Role.DOCTOR)
    grant = _grant(s, specialist)
    args = s['args'] | {'actor_staff_id': specialist.id}
    assert clinical.require_interpreting_authority(**args).id == specialist.id
    grant.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    with pytest.raises(NotFoundError):
        clinical.require_interpreting_authority(**args)


@pytest.mark.parametrize('variant', ['expired', 'modality', 'inactive', 'admin', 'super_admin'])
def test_grant_cannot_override_role_or_scope(scheduled, make_staff, variant):
    s = scheduled
    role = Role.ADMIN if variant == 'admin' else Role.SUPER_ADMIN if variant == 'super_admin' else Role.DOCTOR
    actor = make_staff(s['clinic'], role=role)
    _grant(s, actor, modality='ct' if variant == 'modality' else 'xray', expired=variant == 'expired')
    if variant == 'inactive':
        actor.status = StaffStatus.SUSPENDED; db.session.commit()
    with pytest.raises((NotFoundError, ValidationError)):
        clinical.require_interpreting_authority(**(s['args'] | {'actor_staff_id': actor.id}))


def test_failed_audit_rolls_back_identity_and_publication(scheduled, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('Synthetic audit failure')
    monkeypatch.setattr(ris, '_audit', fail)
    with pytest.raises(RuntimeError, match='Synthetic audit failure'):
        clinical.publish(**scheduled['args'], expected_version=2)
    assert db.session.query(RadiologyDicomIdentity).count() == 0
    assert db.session.query(RadiologyWorklistPublication).count() == 0


def test_concurrent_local_publication_on_postgres(scheduled, app):
    assert db.engine.dialect.name == 'postgresql'
    args = scheduled['args']
    db.session.remove()
    barrier = Barrier(2, timeout=20)
    def attempt():
        with app.app_context():
            barrier.wait()
            try:
                return clinical.publish(**args, expected_version=2)
            finally:
                db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert results[0] == results[1]
    assert db.session.query(RadiologyDicomIdentity).count() == 1
    assert db.session.query(RadiologyWorklistPublication).count() == 1
