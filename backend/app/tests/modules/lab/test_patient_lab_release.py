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

# B02 tests append to the original B01 suite; all old assertions are preserved.
def correction_setup(s):
    finalize(s); release_valid(s)
    response = s['client'].post(f"/api/v1/lab/orders/{s['order'].id}/patient-release-withdrawal",json={'reason_code':'clinical_correction'},headers=s['doctor_headers'])
    assert response.status_code == 200, response.get_json()
    assert own_detail(s).status_code == 404


def correction_payload(s, value='6', version=1, flag='normal'):
    return {'expected_version':version,'changes':[{'item_id':s['item'].id,'result_value':value,'flag':flag}],'reason':'Synthetic verified correction'}


def correct(s, auth_headers_for, **kwargs):
    return s['client'].post(f"/api/v1/lab/orders/{s['order'].id}/patient-release-correction",json=correction_payload(s,**kwargs),headers=auth_headers_for(s['tech'].user))


def replacement_preview(s):
    return s['client'].get(f"/api/v1/lab/orders/{s['order'].id}/patient-replacement-preview",headers=s['doctor_headers'])


def replacement_publish(s,digest):
    return s['client'].post(f"/api/v1/lab/orders/{s['order'].id}/patient-replacement",json={'preview_sha256':digest},headers=s['doctor_headers'])


def test_b02_corrected_report_reuses_amendment_chain(scenario, db, auth_headers_for):
    from app.modules.clinical_records.models.clinical_record_amendment_model import ClinicalRecordAmendment
    from app.modules.lab.models.lab_model import LabReportPublication
    s=scenario; correction_setup(s)
    for version,value in [(2,'6'),(3,'7')]:
        res=correct(s,auth_headers_for,value=value,version=version-1)
        assert res.status_code==200,res.get_json()
        assert res.get_json()['data']['record_version']==version
        assert own_detail(s).status_code==404
        assert own_list(s).get_json()['data']['items']==[]
        assert correct(s,auth_headers_for,value='8',version=version-1).status_code==409
        res=replacement_preview(s); assert res.status_code==200,res.get_json()
        report=res.get_json()['data']; assert report['report']['record_version']==version
        assert report['report']['results'][0]['result_value']==value
        assert replacement_publish(s,'0'*64).status_code==409
        assert replacement_publish(s,report['preview_sha256']).status_code==200
        assert replacement_publish(s,report['preview_sha256']).status_code==409
        detail=own_detail(s); assert detail.status_code==200,detail.get_json()
        assert detail.get_json()['data']['record_version']==version
        assert detail.get_json()['data']['results'][0]['result_value']==value
        assert own_list(s).get_json()['data']['items'][0]['record_version']==version
        assert s['item'].result_value=='5' and s['order'].patient_release_snapshot['results'][0]['result_value']=='5'
        if version==2:
            response=s['client'].post(f"/api/v1/lab/orders/{s['order'].id}/patient-release-withdrawal",json={'reason_code':'clinical_correction'},headers=s['doctor_headers'])
            assert response.status_code==200,response.get_json()
            assert own_detail(s).status_code==404
    rows=db.session.execute(select(ClinicalRecordAmendment).where(ClinicalRecordAmendment.source_record_type=='lab_report').order_by(ClinicalRecordAmendment.new_version)).scalars().all()
    assert [r.new_version for r in rows]==[2,3] and rows[1].previous_amendment_id==rows[0].id
    pubs=db.session.execute(select(LabReportPublication).order_by(LabReportPublication.version)).scalars().all()
    assert len(pubs)==2 and pubs[0].withdrawn_at is not None and pubs[1].withdrawn_at is None
    assert pubs[0].amendment_id==rows[0].id and pubs[1].amendment_id==rows[1].id
    notes=db.session.execute(select(Notification).where(Notification.user_id==s['patient_user'].id)).scalars().all()
    assert len(notes)==3 and sum(n.title=='An updated record is available' for n in notes)==2
    assert all(n.reference_type=='released_lab_result' and n.reference_id==s['order'].id for n in notes)


def test_b02_wrong_roles_and_stale_versions(scenario, db, auth_headers_for, clinic, make_authenticated_staff):
    s=scenario; correction_setup(s)
    _, outsider=make_authenticated_staff(clinic,Role.DOCTOR); db.session.commit()
    route=f"/api/v1/lab/orders/{s['order'].id}"
    assert s['client'].post(route+'/patient-release-correction',json=correction_payload(s),headers=s['doctor_headers']).status_code==403
    assert correct(s,auth_headers_for,version=2).status_code==409
    assert correct(s,auth_headers_for).status_code==200
    assert s['client'].get(route+'/patient-replacement-preview',headers=outsider).status_code==404
    assert s['client'].get(route+'/patient-replacement-preview',headers=auth_headers_for(s['tech'].user)).status_code==403
    assert s['client'].post(route+'/patient-replacement',json={'preview_sha256':'0'*64},headers=s['patient_headers']).status_code==403
    assert own_detail(s).status_code==404


@pytest.mark.parametrize('value,flag,status',[('5','normal',422),('100','normal',409),('6','critical',422),('6','abnormal',409)])
def test_b02_no_change_or_unsafe_flag_held(scenario, auth_headers_for, value, flag, status):
    s=scenario; correction_setup(s)
    res=correct(s,auth_headers_for,value=value,flag=flag)
    assert res.status_code == status,res.get_json()
    assert own_detail(s).status_code==404


def test_b02_changed_catalogue_and_relinked_patient_held(scenario,db,auth_headers_for,clinic,make_user):
    s=scenario; correction_setup(s)
    s['test'].unit='changed'; db.session.commit()
    assert correct(s,auth_headers_for).status_code==409
    s['test'].unit='mg/dL'; db.session.commit()
    assert correct(s,auth_headers_for).status_code==200
    digest=replacement_preview(s).get_json()['data']['preview_sha256']
    new_user=make_user(clinic,role=Role.PATIENT); s['patient'].user_id=new_user.id; db.session.commit()
    assert replacement_publish(s,digest).status_code==409
    assert own_detail(s,auth_headers_for(new_user)).status_code==404


def test_b02_notification_rollback_and_hash_tamper(scenario, db, auth_headers_for, app, monkeypatch):
    from app.modules.lab.services import lab_replacement_service as replacements
    from app.modules.clinical_records.models.clinical_record_amendment_model import ClinicalRecordAmendment
    from app.modules.lab.models.lab_model import LabReportPublication
    s=scenario; correction_setup(s); assert correct(s,auth_headers_for).status_code==200
    digest=replacement_preview(s).get_json()['data']['preview_sha256']
    def broken(**kwargs): raise RuntimeError('Synthetic notification failure')
    with monkeypatch.context() as m:
        m.setattr(replacements,'create_notification_in_transaction',broken)
        app.config['PROPAGATE_EXCEPTIONS']=False
        assert replacement_publish(s,digest).status_code==500
    assert db.session.execute(select(LabReportPublication)).scalars().all()==[]
    assert own_detail(s).status_code==404
    assert replacement_publish(s,digest).status_code==200
    amendment=db.session.execute(select(ClinicalRecordAmendment).where(ClinicalRecordAmendment.source_record_type=='lab_report')).scalar_one()
    amendment.new_content_sha256='0'*64; db.session.commit()
    assert own_detail(s).status_code==404


def test_b02_concurrent_replacement_exactly_once_on_postgres(scenario,db,app,auth_headers_for):
    if db.engine.dialect.name!='postgresql': pytest.skip('PostgreSQL row locking required')
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.core.exceptions import ConflictError
    from app.modules.lab.services import lab_replacement_service as replacements
    from app.modules.lab.models.lab_model import LabReportPublication
    s=scenario; correction_setup(s); assert correct(s,auth_headers_for).status_code==200
    digest=replacement_preview(s).get_json()['data']['preview_sha256']
    ids=(s['order'].id,s['order'].clinic_id,s['doctor'].user_id,s['patient_user'].id)
    db.session.remove(); barrier=Barrier(2,timeout=15)
    def attempt():
        with app.app_context():
            barrier.wait()
            try:
                replacements.publish_replacement(order_id=ids[0],clinic_id=ids[1],doctor_user_id=ids[2],preview_sha256=digest)
                return 'published'
            except ConflictError: return 'conflict'
            finally: db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(lambda _:attempt(),range(2)))
    assert sorted(results)==['conflict','published']
    assert len(db.session.execute(select(LabReportPublication)).scalars().all())==1
    assert len(db.session.execute(select(Notification).where(Notification.user_id==ids[3])).scalars().all())==2


def test_b02_cross_clinic_and_critical_threshold_held(scenario, db, auth_headers_for, make_clinic, make_authenticated_staff):
    s=scenario; correction_setup(s)
    outside=make_clinic(); _, outsider=make_authenticated_staff(outside,Role.LAB_TECHNICIAN); db.session.commit()
    path=f"/api/v1/lab/orders/{s['order'].id}/patient-release-correction"
    assert s['client'].post(path,json=correction_payload(s),headers=outsider).status_code==404
    s['test'].critical_high=20; db.session.commit()
    assert correct(s,auth_headers_for,value='30',flag='abnormal').status_code==409
    assert own_detail(s).status_code==404
    s['test'].critical_high=None; db.session.commit()
    assert correct(s,auth_headers_for,value='30',flag='abnormal').status_code==200
    assert own_detail(s).status_code==404


@pytest.mark.parametrize('reason',['identity_review','other_safety_review'])
def test_b02_noncorrection_withdrawal_stays_held(scenario, auth_headers_for, reason):
    s=scenario; finalize(s); release_valid(s)
    response=s['client'].post(f"/api/v1/lab/orders/{s['order'].id}/patient-release-withdrawal",json={'reason_code':reason},headers=s['doctor_headers'])
    assert response.status_code==200
    assert correct(s,auth_headers_for).status_code==409
    assert own_detail(s).status_code==404

def b03_reject(s,digest,reason='incorrect_correction',headers=None):
    return s['client'].post(f"/api/v1/lab/orders/{s['order'].id}/patient-replacement-rejection",json={'preview_sha256':digest,'reason_code':reason},headers=headers or s['doctor_headers'])


def test_b03_rejected_amendment_preserved_and_superseded(scenario, db, auth_headers_for):
    from app.modules.clinical_records.models.clinical_record_amendment_model import ClinicalRecordAmendment
    from app.modules.lab.models.lab_model import LabReportPublication, LabReportCorrectionRejection
    s=scenario; correction_setup(s)
    assert correct(s,auth_headers_for,value='6').status_code==200
    old=replacement_preview(s).get_json()['data']['preview_sha256']
    assert b03_reject(s,'0'*64).status_code==409
    assert b03_reject(s,old,'bad_reason').status_code==422
    assert b03_reject(s,old).status_code==200
    assert b03_reject(s,old).status_code==409
    assert replacement_publish(s,old).status_code==409
    assert own_detail(s).status_code==404 and own_list(s).get_json()['data']['items']==[]
    status=s['client'].get(f"/api/v1/lab/orders/{s['order'].id}/patient-release",headers=auth_headers_for(s['tech'].user)).get_json()['data']
    assert status['correction_rejected'] is True and status['record_version']==2 and status['correction_pending'] is False
    assert correct(s,auth_headers_for,value='7',version=1).status_code==409
    assert correct(s,auth_headers_for,value='7',version=2).status_code==200
    new=replacement_preview(s).get_json()['data']
    assert new['report']['record_version']==3 and new['report']['results'][0]['result_value']=='7'
    assert replacement_publish(s,old).status_code==409
    assert replacement_publish(s,new['preview_sha256']).status_code==200
    assert own_detail(s).get_json()['data']['record_version']==3
    amendments=db.session.execute(select(ClinicalRecordAmendment).where(ClinicalRecordAmendment.source_record_type=='lab_report').order_by(ClinicalRecordAmendment.new_version)).scalars().all()
    rejection=db.session.execute(select(LabReportCorrectionRejection)).scalar_one()
    publication=db.session.execute(select(LabReportPublication)).scalar_one()
    assert [a.new_version for a in amendments]==[2,3]
    assert rejection.amendment_id==amendments[0].id and publication.amendment_id==amendments[1].id
    assert amendments[0].new_content['report']['results'][0]['result_value']=='6'
    assert s['item'].result_value=='5' and s['order'].patient_release_snapshot['results'][0]['result_value']=='5'
    assert len(db.session.execute(select(Notification).where(Notification.user_id==s['patient_user'].id)).scalars().all())==2


def test_b03_rejection_wrong_roles_and_wrong_doctor(scenario, db, auth_headers_for, clinic, make_authenticated_staff):
    s=scenario; correction_setup(s); assert correct(s,auth_headers_for).status_code==200
    _, unassigned=make_authenticated_staff(clinic,Role.DOCTOR); db.session.commit()
    digest=replacement_preview(s).get_json()['data']['preview_sha256']
    assert b03_reject(s,digest,headers=auth_headers_for(s['tech'].user)).status_code==403
    assert b03_reject(s,digest,headers=s['patient_headers']).status_code==403
    assert b03_reject(s,digest,headers=unassigned).status_code==404
    assert replacement_preview(s).status_code==200
    assert own_detail(s).status_code==404


def test_b03_concurrent_reject_publish_on_postgres(scenario,db,app,auth_headers_for):
    if db.engine.dialect.name!='postgresql': pytest.skip('PostgreSQL row locking required')
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.core.exceptions import ConflictError
    from app.modules.lab.services import lab_replacement_service as replacements
    from app.modules.lab.models.lab_model import LabReportPublication, LabReportCorrectionRejection
    s=scenario; correction_setup(s); assert correct(s,auth_headers_for).status_code==200
    digest=replacement_preview(s).get_json()['data']['preview_sha256']
    ids=(s['order'].id,s['order'].clinic_id,s['doctor'].user_id,s['patient_user'].id)
    db.session.remove(); barrier=Barrier(2,timeout=15)
    def attempt(reject):
        with app.app_context():
            barrier.wait()
            try:
                kwargs=dict(order_id=ids[0],clinic_id=ids[1],doctor_user_id=ids[2],preview_sha256=digest)
                if reject: replacements.reject_correction(**kwargs,reason_code='incorrect_correction'); return 'rejected'
                replacements.publish_replacement(**kwargs); return 'published'
            except ConflictError: return 'conflict'
            finally: db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(attempt,[True,False]))
    assert results.count('conflict')==1
    p=db.session.execute(select(LabReportPublication)).scalars().all()
    r=db.session.execute(select(LabReportCorrectionRejection)).scalars().all()
    assert len(p)+len(r)==1
    assert len(db.session.execute(select(Notification).where(Notification.user_id==ids[3])).scalars().all())==1+len(p)


def test_b03_critical_initial_release_requires_separate_attestation(scenario,db,auth_headers_for):
    s=scenario;s['test'].critical_high=4;db.session.commit();finalize(s)
    assert s['item'].flag is LabResultFlag.CRITICAL
    assert preview(s).status_code==409
    base=f"/api/v1/lab/orders/{s['order'].id}/patient-critical-release"
    res=s['client'].get(base+'-preview',headers=s['doctor_headers']);assert res.status_code==200,res.get_json()
    data=res.get_json()['data'];assert data['report']['results'][0]['flag']=='critical' and data['requires_direct_communication'] is True
    body={'preview_sha256':data['preview_sha256'],'communication_confirmed':True,'communication_method':'telephone'}
    assert publish(s,body['preview_sha256']).status_code==409
    assert s['client'].post(base,json={**body,'communication_confirmed':False},headers=s['doctor_headers']).status_code==422
    assert s['client'].post(base,json={**body,'communication_method':'notification'},headers=s['doctor_headers']).status_code==422
    assert s['client'].post(base,json=body,headers=auth_headers_for(s['tech'].user)).status_code==403
    assert s['client'].post(base,json={**body,'preview_sha256':'0'*64},headers=s['doctor_headers']).status_code==409
    assert own_detail(s).status_code==404
    assert s['client'].post(base,json=body,headers=s['doctor_headers']).status_code==200
    assert own_detail(s).get_json()['data']['results'][0]['flag']=='critical'
    assert s['order'].patient_release_critical_review['method']=='telephone'
    original_review=dict(s['order'].patient_release_critical_review)
    s['order'].patient_release_critical_review={**original_review,'preview_sha256':'0'*64};db.session.commit()
    assert own_detail(s).status_code==404
    s['order'].patient_release_critical_review=original_review;db.session.commit()
    assert own_detail(s).status_code==200
    assert s['client'].post(base,json=body,headers=s['doctor_headers']).status_code==409
    assert len(db.session.execute(select(Notification).where(Notification.user_id==s['patient_user'].id)).scalars().all())==1
    assert s['client'].post(f"/api/v1/lab/orders/{s['order'].id}/patient-release-withdrawal",json={'reason_code':'clinical_correction'},headers=s['doctor_headers']).status_code==200
    assert correct(s,auth_headers_for).status_code==409 and own_detail(s).status_code==404


def test_b03_normal_cannot_use_critical_release_path(scenario):
    s=scenario;finalize(s)
    base=f"/api/v1/lab/orders/{s['order'].id}/patient-critical-release"
    assert s['client'].get(base+'-preview',headers=s['doctor_headers']).status_code==409
    assert own_detail(s).status_code==404


def test_b03_released_pdf_current_owner_and_version(scenario,db,app,auth_headers_for,clinic,make_user,make_patient):
    s=scenario;finalize(s);release_valid(s)
    path=f"/api/v1/lab/me/released-results/{s['order'].id}/pdf"
    app.config['RAD6_PRINTING_ENABLED']=False
    assert s['client'].get(path,headers=s['patient_headers']).status_code==404
    app.config['RAD6_PRINTING_ENABLED']=True
    r=s['client'].get(path,headers=s['patient_headers']);assert r.status_code==200,r.get_json()
    assert r.data.startswith(b'%PDF-') and r.headers['X-Clinical-Release']=='patient-copy'
    assert 'v1.pdf' in r.headers['Content-Disposition'] and 'no-store' in r.headers['Cache-Control']
    old_digest=r.headers['X-Source-SHA256']
    outsider=make_user(clinic,role=Role.PATIENT);make_patient(clinic,user_id=outsider.id);db.session.commit()
    assert s['client'].get(path,headers=auth_headers_for(outsider)).status_code==404
    assert s['client'].get(path,headers=s['doctor_headers']).status_code==403
    assert s['client'].post(f"/api/v1/lab/orders/{s['order'].id}/patient-release-withdrawal",json={'reason_code':'clinical_correction'},headers=s['doctor_headers']).status_code==200
    assert s['client'].get(path,headers=s['patient_headers']).status_code==404
    assert correct(s,auth_headers_for).status_code==200
    assert replacement_publish(s,replacement_preview(s).get_json()['data']['preview_sha256']).status_code==200
    r=s['client'].get(path,headers=s['patient_headers']);assert r.status_code==200
    assert 'v2.pdf' in r.headers['Content-Disposition'] and r.headers['X-Source-SHA256']!=old_digest


def test_b03_pdf_unsupported_unit_held_not_truncated(scenario,db,app):
    s=scenario;s['test'].unit='\u03bcg/L';db.session.commit();finalize(s);release_valid(s)
    assert own_detail(s).get_json()['data']['results'][0]['unit']=='\u03bcg/L'
    app.config['RAD6_PRINTING_ENABLED']=True
    assert s['client'].get(f"/api/v1/lab/me/released-results/{s['order'].id}/pdf",headers=s['patient_headers']).status_code==404


def test_b03_supersession_does_not_inherit_rejected_values(scenario,db,auth_headers_for):
    s=scenario
    test=LabTest(clinic_id=s['order'].clinic_id,name='Second synthetic test',unit='mg/dL',reference_range='1 - 10')
    db.session.add(test);db.session.flush()
    item=LabOrderItem(order_id=s['order'].id,test_id=test.id);db.session.add(item);db.session.commit()
    enter_result(item.id,'5',None,None,None,s['order'].clinic_id)
    correction_setup(s)
    assert correct(s,auth_headers_for,value='6').status_code==200
    assert b03_reject(s,replacement_preview(s).get_json()['data']['preview_sha256']).status_code==200
    body={'expected_version':2,'changes':[{'item_id':item.id,'result_value':'7','flag':'normal'}],'reason':'New verified synthetic correction'}
    r=s['client'].post(f"/api/v1/lab/orders/{s['order'].id}/patient-release-correction",json=body,headers=auth_headers_for(s['tech'].user))
    assert r.status_code==200,r.get_json()
    report=replacement_preview(s).get_json()['data']
    assert [row['result_value'] for row in report['report']['results']]==['5','7']
    assert replacement_publish(s,report['preview_sha256']).status_code==200
    assert [row['result_value'] for row in own_detail(s).get_json()['data']['results']]==['5','7']
