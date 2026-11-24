from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.enums.lab_enums import LabOrderStatus, LabResultFlag
from app.core.enums.role_enums import Role
from app.core.notifications.models.notification_models import Notification
from app.modules.lab.models.lab_model import LabOrder, LabOrderItem, LabTest
from app.modules.lab.services import lab_release_service as release
from app.modules.lab.services.lab_service import enter_result, verify_results, complete_order


@pytest.fixture
def scenario(app, db, clinic, make_user, make_patient, make_authenticated_staff, auth_headers_for):
    app.config['PATIENT_LAB_RELEASE_ENABLED'] = True
    doctor, doctor_headers = make_authenticated_staff(clinic, Role.DOCTOR)
    tech, _ = make_authenticated_staff(clinic, Role.LAB_TECHNICIAN)
    patient_user = make_user(clinic, role=Role.PATIENT)
    patient = make_patient(clinic, user_id=patient_user.id)
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    test = LabTest(clinic_id=clinic.id, name='Synthetic test', loinc_code='2345-7', unit='mg/dL', reference_range='1 - 10')
    order = LabOrder(clinic_id=clinic.id, patient_id=patient.id, ordered_by_id=doctor.id, collected_by_id=tech.id, processed_by_id=tech.id, status=LabOrderStatus.IN_PROGRESS, created_at=now, sample_collected_at=now+timedelta(seconds=1), processed_at=now+timedelta(seconds=2))
    db.session.add_all([test, order]); db.session.flush()
    item = LabOrderItem(order_id=order.id, test_id=test.id)
    db.session.add(item); db.session.commit()
    return dict(client=app.test_client(), order=order, item=item, test=test, doctor=doctor, tech=tech, patient=patient, patient_user=patient_user, doctor_headers=doctor_headers, patient_headers=auth_headers_for(patient_user))


def finalize(s):
    enter_result(s['item'].id, '5', None, None, None, s['order'].clinic_id)
    verify_results(s['order'].id, s['tech'].id, s['order'].clinic_id)
    complete_order(s['order'].id, s['order'].clinic_id)


def preview(s):
    return s['client'].get(f"/api/v1/lab/orders/{s['order'].id}/patient-release-preview", headers=s['doctor_headers'])


def publish(s, digest):
    return s['client'].post(f"/api/v1/lab/orders/{s['order'].id}/patient-release", json={'preview_sha256':digest}, headers=s['doctor_headers'])


def own_list(s, headers=None):
    return s['client'].get('/api/v1/lab/me/released-results', headers=headers or s['patient_headers'])


def own_detail(s, headers=None):
    return s['client'].get(f"/api/v1/lab/me/released-results/{s['order'].id}", headers=headers or s['patient_headers'])


def release_valid(s):
    res = preview(s); assert res.status_code == 200, res.get_json()
    digest = res.get_json()['data']['preview_sha256']
    res = publish(s, digest); assert res.status_code == 200, res.get_json()
    return digest


def test_review_release_immutable_snapshot_and_one_notification(scenario, db):
    s = scenario
    assert own_list(s).get_json()['data']['items'] == []
    finalize(s)
    assert own_detail(s).status_code == 404
    # Catalog updates after result entry must not change original metadata.
    s['test'].name = 'Changed catalog name'; s['test'].unit = 'changed'; s['test'].reference_range = '100 - 200'; db.session.commit()
    data = preview(s).get_json()['data']
    assert data['report']['results'][0]['unit'] == 'mg/dL'
    assert data['report']['results'][0]['reference_range'] == '1 - 10'
    digest = release_valid(s)
    assert publish(s,digest).status_code == 409
    status = s['client'].get(f"/api/v1/lab/orders/{s['order'].id}/patient-release",headers=s['doctor_headers'])
    assert status.status_code == 200 and status.get_json()['data']['release_status'] == 'released'
    detail = own_detail(s)
    assert detail.status_code == 200
    assert detail.headers['Cache-Control'] == 'no-store'
    row = detail.get_json()['data']['results'][0]
    assert row == data['report']['results'][0]
    assert row['test_name'] == 'Synthetic test' and row['result_value'] == '5' and row['flag'] == 'normal'
    assert len(own_list(s).get_json()['data']['items']) == 1
    records = db.session.execute(select(Notification).where(Notification.user_id == s['patient_user'].id)).scalars().all()
    assert len(records) == 1
    assert records[0].title == 'A new record is available'
    assert records[0].message == 'Sign in to view your new clinical record.'
    assert records[0].reference_id == s['order'].id and records[0].reference_type == 'released_lab_result'
    assert row['loinc_code'] == '2345-7'


def test_disabled_and_unfinalized_are_denied(scenario, app):
    s = scenario
    assert preview(s).status_code == 409
    app.config['PATIENT_LAB_RELEASE_ENABLED'] = False
    assert own_list(s).status_code == 503
    assert preview(s).status_code == 503


@pytest.mark.parametrize('field,value,code', [('result_display_metadata',None,'LAB_RELEASE_METADATA_REQUIRED'), ('flag',LabResultFlag.CRITICAL,'LAB_RELEASE_REVIEW_REQUIRED'), ('flag',None,'LAB_RELEASE_REVIEW_REQUIRED'), ('result_notes','Synthetic review required','LAB_RELEASE_REVIEW_REQUIRED'), ('result_file_url','https://example.invalid/report','LAB_RELEASE_REVIEW_REQUIRED')])
def test_unsafe_or_historical_report_held(scenario, db, field, value, code):
    s = scenario; finalize(s)
    setattr(s['item'],field,value); db.session.commit()
    res = preview(s)
    assert res.status_code == 409 and res.get_json()['code'] == code
    assert own_detail(s).status_code == 404


def test_stale_preview_cannot_publish(scenario, db):
    s = scenario; finalize(s)
    digest = preview(s).get_json()['data']['preview_sha256']
    # Simulate stale/corrected persistent state, not a supported post-verification mutation.
    s['item'].result_value = '6'; db.session.commit()
    res = publish(s,digest)
    assert res.status_code == 409 and res.get_json()['code'] == 'LAB_RELEASE_PREVIEW_CHANGED'
    assert own_detail(s).status_code == 404


def test_patient_and_doctor_boundaries(scenario, db, clinic, make_clinic, make_user, make_patient, make_authenticated_staff, auth_headers_for):
    s = scenario; finalize(s)
    other = make_user(clinic, role=Role.PATIENT); make_patient(clinic,user_id=other.id)
    outsider_clinic = make_clinic(); outsider = make_user(outsider_clinic,role=Role.PATIENT); make_patient(outsider_clinic,user_id=outsider.id)
    _, unassigned = make_authenticated_staff(clinic, Role.DOCTOR)
    _, nurse = make_authenticated_staff(clinic, Role.NURSE)
    # Commit synthetic accounts before a denied transactional preview can roll back.
    db.session.commit()
    assert s['client'].get(f"/api/v1/lab/orders/{s['order'].id}/patient-release-preview", headers=unassigned).status_code == 404
    assert s['client'].get(f"/api/v1/lab/orders/{s['order'].id}/patient-release-preview", headers=nurse).status_code == 403
    release_valid(s)
    for user in (other, outsider):
        headers = auth_headers_for(user)
        assert own_detail(s, headers).status_code == 404
        assert own_list(s, headers).get_json()['data']['items'] == []
    assert s['client'].get('/api/v1/lab/me/released-results?patient_id=1', headers=s['patient_headers']).status_code == 422
    assert s['client'].get(f"/api/v1/lab/orders/{s['order'].id}",headers=s['patient_headers']).status_code == 403


def test_withdrawal_removes_patient_access_without_deleting_evidence(scenario, db):
    s = scenario; finalize(s); release_valid(s)
    res = s['client'].post(f"/api/v1/lab/orders/{s['order'].id}/patient-release-withdrawal",json={'reason_code':'clinical_correction'},headers=s['doctor_headers'])
    assert res.status_code == 200
    assert own_detail(s).status_code == 404 and own_list(s).get_json()['data']['items'] == []
    db.session.refresh(s['order'])
    assert s['order'].patient_release_snapshot is not None
    assert s['order'].patient_release_withdrawn_at is not None
    assert preview(s).status_code == 409


def test_notification_failure_rolls_back_release(scenario, db, monkeypatch, app):
    s = scenario; finalize(s)
    digest = preview(s).get_json()['data']['preview_sha256']
    def broken(**kwargs):
        raise RuntimeError('Synthetic notification failure')
    monkeypatch.setattr(release,'create_notification_in_transaction',broken)
    app.config['PROPAGATE_EXCEPTIONS'] = False
    assert publish(s,digest).status_code == 500
    db.session.refresh(s['order'])
    assert s['order'].patient_released_at is None
    assert s['order'].patient_release_snapshot is None
    assert own_detail(s).status_code == 404


def test_relinked_patient_cannot_inherit_release(scenario, db, clinic, make_user, auth_headers_for):
    s = scenario; finalize(s); release_valid(s)
    replacement = make_user(clinic, role=Role.PATIENT)
    s['patient'].user_id = replacement.id; db.session.commit()
    assert own_detail(s, auth_headers_for(replacement)).status_code == 404
    assert own_list(s, auth_headers_for(replacement)).get_json()['data']['items'] == []


def test_non_testing_profile_cannot_enable_incomplete_release(scenario, app):
    app.config['TESTING'] = False
    try:
        assert own_list(scenario).status_code == 503
    finally:
        app.config['TESTING'] = True


def test_concurrent_release_is_exactly_once_on_postgres(scenario, db, app):
    # SQLite unit/route suite has no row-lock claim; this is executed again
    # by the mandatory PostgreSQL service-test gate.
    if db.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL row locking is required')
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.core.exceptions import ConflictError
    s = scenario
    finalize(s)
    digest = preview(s).get_json()['data']['preview_sha256']
    order_id = s['order'].id
    clinic_id = s['order'].clinic_id
    doctor_user_id = s['doctor'].user_id
    recipient_id = s['patient_user'].id
    db.session.remove()
    barrier = Barrier(2, timeout=15)
    def attempt():
        with app.app_context():
            barrier.wait()
            try:
                release.release_report(order_id=order_id, clinic_id=clinic_id,
                    doctor_user_id=doctor_user_id, preview_sha256=digest)
                return 'released'
            except ConflictError:
                return 'conflict'
            finally:
                db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ['conflict', 'released']
    rows = db.session.execute(select(Notification).where(Notification.user_id == recipient_id)).scalars().all()
    assert len(rows) == 1
    assert db.session.get(LabOrder, order_id).patient_released_at is not None


def test_lab_technician_status_only(scenario, auth_headers_for):
    s = scenario; finalize(s)
    tech_headers = auth_headers_for(s['tech'].user)
    base = f"/api/v1/lab/orders/{s['order'].id}/patient-release"
    res = s['client'].get(base, headers=tech_headers)
    assert res.status_code == 200 and res.get_json()['data']['release_status'] == 'unreleased'
    assert s['client'].get(base+'-preview',headers=tech_headers).status_code == 403
    assert s['client'].post(base,json={'preview_sha256':'0'*64},headers=tech_headers).status_code == 403
    release_valid(s)
    res = s['client'].get(base, headers=tech_headers)
    assert res.status_code == 200 and res.get_json()['data']['release_status'] == 'released'
    assert 'results' not in res.get_json()['data']
