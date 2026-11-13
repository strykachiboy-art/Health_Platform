"""RAD1 focused safety contracts; not clinical-release certification."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.radiology.models.ris_model import (
    RadiologyExam, RadiologyReport, RadiologyReportVersion, RadiologyStudyAssociation,
)
from app.modules.radiology.services import ris_service as ris
from app.modules.clinical_orders.services import imaging_order_service as imaging
from app.tests.modules.nursing.test_nursing_service_invariants import (
    _db_now, _setup_admission,
)


@pytest.fixture
def setup_ris(clinic, make_patient, make_staff, monkeypatch, auth_headers_for):
    ctx = _setup_admission(clinic=clinic, make_patient=make_patient, make_staff=make_staff)
    patient, nurse, doctor, ward, bed, admission = ctx
    monkeypatch.setattr(ris, 'create_audit_log', lambda **kwargs: None)
    monkeypatch.setattr(imaging, 'create_audit_log', lambda **kwargs: None)
    order = imaging.create_imaging_order(
        admission_id=admission.id, clinic_id=clinic.id, actor_staff_id=doctor.id,
        study_name='Chest X-ray', modality='xray', body_site='chest',
        indication='Synthetic RAD1 test indication', priority='routine',
        scheduled_at=_db_now() + timedelta(days=1),
    )
    return {'clinic': clinic, 'patient': patient, 'doctor': doctor,
            'nurse': nurse, 'admission': admission, 'order': order,
            'headers': auth_headers_for(doctor.user)}


def _new(s):
    return ris.establish_exam(imaging_order_id=s['order'].id,
        clinic_id=s['clinic'].id, actor_staff_id=s['doctor'].id)


def _path():
    return '/api/v1/radiology/exams'


def test_app_registers_only_inert_rad1_routes(app):
    api = {(r.rule, verb) for r in app.url_map.iter_rules()
           if r.rule.startswith('/api/v1/radiology')
           for verb in r.methods if verb not in ('OPTIONS', 'HEAD')}
    assert (_path(), 'POST') in api
    assert ('/api/v1/radiology/exams/<int:exam_id>/worklist', 'GET') in api
    assert ('/api/v1/radiology/exams/<int:exam_id>/reports', 'POST') in api
    assert len(api) == 9, api
    assert not any(any(x in route.lower() for x in (
        'final', 'sign', 'archive', 'dicom', 'ingest', 'critical', 'execute', 'release'
    )) for route, _ in api)


def test_missing_auth_patient_or_nurse_cannot_establish(client, setup_ris, auth_headers_for):
    s = setup_ris
    body = {'imaging_order_id': s['order'].id}
    assert client.post(_path(), json=body).status_code == 401
    assert client.post(_path(), json=body, headers=auth_headers_for(s['nurse'].user)).status_code == 403


def test_exam_is_one_to_one_with_canonical_order(setup_ris):
    s = setup_ris
    value = _new(s)
    assert value['imaging_order_id'] == s['order'].id
    assert value['clinic_id'] == s['clinic'].id
    assert value['version'] == 1
    assert value['worklist_published'] is False
    assert value['archive_verified'] is False
    assert value['diagnostic_report_signed'] is False
    assert value['accession_number'].startswith('RAD-')
    assert str(s['order'].id) != value['accession_number']
    with pytest.raises(ConflictError):
        _new(s)
    assert db.session.query(RadiologyExam).count() == 1


def test_patient_clinic_admission_stay_owned_by_imaging_order(setup_ris):
    s = setup_ris
    _new(s)
    row = db.session.query(RadiologyExam).one()
    assert not hasattr(row, 'patient_id')
    assert not hasattr(row, 'admission_id')
    assert not hasattr(row, 'clinic_id')
    assert s['order'].patient_id == s['patient'].id
    assert s['order'].admission_id == s['admission'].id


def test_wrong_clinic_cannot_read_or_mutate(setup_ris, make_clinic, make_staff):
    s = setup_ris
    exam = _new(s)
    other = make_clinic(name='RAD1 foreign clinic')
    foreign = make_staff(other, role=Role.DOCTOR)
    with pytest.raises((NotFoundError, ConflictError)):
        ris.get_exam(exam_id=exam['exam_id'], clinic_id=other.id,
                     actor_staff_id=foreign.id)
    with pytest.raises(NotFoundError):
        ris.establish_exam(imaging_order_id=s['order'].id, clinic_id=other.id,
                           actor_staff_id=foreign.id)


def test_forged_clinic_context_and_extra_patient_rejected(client, setup_ris,
                                                           make_clinic, auth_headers_for):
    s = setup_ris
    foreign = make_clinic(name='RAD1 selected wrong clinic')
    body = {'imaging_order_id': s['order'].id}
    response = client.post(_path(), json=body,
        headers=auth_headers_for(s['doctor'].user, clinic_context_id=foreign.id))
    assert response.status_code in (403, 404, 422)
    assert db.session.query(RadiologyExam).count() == 0
    extra = body | {'patient_id': s['patient'].id, 'clinic_id': s['clinic'].id}
    assert client.post(_path(), json=extra, headers=s['headers']).status_code == 422
    assert db.session.query(RadiologyExam).count() == 0


def test_service_denies_non_ordering_doctor(setup_ris, make_staff):
    s = setup_ris
    other = make_staff(s['clinic'], role=Role.DOCTOR)
    with pytest.raises(NotFoundError):
        ris.establish_exam(imaging_order_id=s['order'].id,
            clinic_id=s['clinic'].id, actor_staff_id=other.id)


def test_suspended_doctor_denied_before_write(setup_ris):
    s = setup_ris
    s['doctor'].status = StaffStatus.SUSPENDED
    db.session.flush()
    with pytest.raises(ValidationError):
        _new(s)
    assert db.session.query(RadiologyExam).count() == 0


def test_booking_version_and_supersession(setup_ris):
    s = setup_ris
    exam = _new(s)
    when = datetime(2030, 11, 9, 11, tzinfo=timezone.utc)
    done = ris.schedule_intent(exam_id=exam['exam_id'], clinic_id=s['clinic'].id,
        actor_staff_id=s['doctor'].id, expected_version=1,
        scheduled_at=when, station_code='XR01')
    assert done['version'] == 2
    assert done['worklist_published'] is False
    with pytest.raises(ConflictError):
        ris.schedule_intent(exam_id=exam['exam_id'], clinic_id=s['clinic'].id,
            actor_staff_id=s['doctor'].id, expected_version=1,
            scheduled_at=when, station_code='XR01')
    new = ris.schedule_intent(exam_id=exam['exam_id'], clinic_id=s['clinic'].id,
        actor_staff_id=s['doctor'].id, expected_version=2,
        scheduled_at=when + timedelta(days=1), station_code='XR02')
    assert new['version'] == 3
    history = ris.get_worklist_intent(exam_id=exam['exam_id'],
        clinic_id=s['clinic'].id, actor_staff_id=s['doctor'].id)
    assert [x['status'] for x in history['steps']] == ['intent', 'superseded']
    assert history['mwl_published'] is False
    gone = ris.cancel_schedule_intent(exam_id=exam['exam_id'],
        clinic_id=s['clinic'].id, actor_staff_id=s['doctor'].id,
        expected_version=3)
    assert gone['status'] == 'planned'
    assert gone['version'] == 4
    assert not ris.get_worklist_intent(exam_id=exam['exam_id'],
        clinic_id=s['clinic'].id, actor_staff_id=s['doctor'].id)['mwl_published']


def test_report_working_note_is_private_append_only_and_not_signed(setup_ris):
    s = setup_ris
    exam = _new(s)
    first = ris.create_draft(exam_id=exam['exam_id'], clinic_id=s['clinic'].id,
        actor_staff_id=s['doctor'].id, content='SYNTHETIC UNRELEASED NOTE A')
    assert first['status'] == 'private_draft'
    assert first['diagnostic_report_signed'] is False
    with pytest.raises(ConflictError):
        ris.create_draft(exam_id=exam['exam_id'], clinic_id=s['clinic'].id,
            actor_staff_id=s['doctor'].id, content='Should not replace')
    second = ris.revise_draft(report_id=first['report_id'],
        clinic_id=s['clinic'].id, actor_staff_id=s['doctor'].id,
        expected_version=1, content='SYNTHETIC UNRELEASED NOTE B')
    assert second['version'] == 2
    with pytest.raises(ConflictError):
        ris.revise_draft(report_id=first['report_id'],
            clinic_id=s['clinic'].id, actor_staff_id=s['doctor'].id,
            expected_version=1, content='Stale note')
    history = ris.get_draft_history(exam_id=exam['exam_id'],
        clinic_id=s['clinic'].id, actor_staff_id=s['doctor'].id)
    assert [v['content'] for v in history['versions']] == [
        'SYNTHETIC UNRELEASED NOTE B', 'SYNTHETIC UNRELEASED NOTE A']
    assert history['released_to_chart'] is False
    assert history['diagnostic_report_signed'] is False
    assert db.session.query(RadiologyReportVersion).count() == 2


def test_existing_imaging_order_completion_does_not_sign_report(setup_ris):
    s = setup_ris
    exam = _new(s)
    imaging.complete_imaging_order(s['order'].id,
        clinic_id=s['clinic'].id, actor_staff_id=s['doctor'].id,
        report_text='Legacy unsigned completion text')
    summary = ris.get_exam(exam_id=exam['exam_id'],
        clinic_id=s['clinic'].id, actor_staff_id=s['doctor'].id)
    assert summary['order_status'] == 'completed'
    assert summary['diagnostic_report_signed'] is False
    assert db.session.query(RadiologyReport).count() == 0


def test_no_idempotency_key_and_no_signing_payload(client, setup_ris):
    s = setup_ris
    headers = {**s['headers'], 'Idempotency-Key': 'auto-replay-disallowed'}
    assert client.post(_path(), json={'imaging_order_id': s['order'].id},
                       headers=headers).status_code == 422
    assert client.post(_path(), json={
        'imaging_order_id': s['order'].id, 'final_signed': True,
    }, headers=s['headers']).status_code == 422
    assert db.session.query(RadiologyExam).count() == 0


def test_doctor_http_create_and_readback_metadata_only(client, setup_ris):
    s = setup_ris
    result = client.post(_path(), json={'imaging_order_id': s['order'].id},
                         headers=s['headers'])
    assert result.status_code == 201, result.get_json()
    envelope = result.get_json()
    assert envelope['archive_verified'] is False
    assert envelope['diagnostic_report_signed'] is False
    assert 'patient_id' not in envelope['data']
    target = envelope['data']['exam_id']
    get = client.get(_path() + f'/{target}', headers=s['headers'])
    assert get.status_code == 200, get.get_json()
    assert get.get_json()['data']['accession_number'] == envelope['data']['accession_number']
    assert get.headers.get('Cache-Control') == 'no-store'


def test_study_uid_is_global_unique_and_never_archive_verified(app, setup_ris):
    s = setup_ris
    exam = _new(s)
    association = RadiologyStudyAssociation(
        exam_id=exam['exam_id'], study_instance_uid='1.2.840.10008.1.2.3',
        source='synthetic_test_only', match_status='unverified',
    )
    db.session.add(association)
    db.session.commit()
    db.session.add(RadiologyStudyAssociation(
        exam_id=exam['exam_id'], study_instance_uid='1.2.840.10008.1.2.3',
        source='synthetic_test_only', match_status='quarantined',
    ))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
