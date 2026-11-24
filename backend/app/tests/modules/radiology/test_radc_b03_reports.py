"""Staged reporting, not clinical sign-off. Reuse verified B02C synthetic CT fixtures."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.radiology.models.ris_model import RadiologyStagedReport, RadiologyStagedReportRelease, RadiologyInterpretationGrant, RadiologyReportVersion
from app.modules.radiology.services import radc_report_service as report, radc_ct_service as ct, ris_service as ris, radc_delivery_service as delivery
from app.modules.clinical_records.models.clinical_record_amendment_model import ClinicalRecordAmendment
from app.modules.printing.services.print_policy import supported_kinds,PrintDenied
from app.modules.printing.services.source_adapters import snapshot
from app.tests.modules.radiology.test_rad1_ris_foundation import setup_ris
from app.tests.modules.radiology.test_radc_b02c_ct_viewer import ct_published,study


@pytest.fixture
def ready(study,app,make_user,auth_headers_for):
    s,p,d=study;app.config['RAD_C_REPORT_STAGED_ENABLED']=True
    user=make_user(s['clinic'],role=Role.PATIENT);s['patient'].user_id=user.id;db.session.commit()
    s['recipient']=dict(user_id=user.id,clinic_id=s['clinic'].id,exam_id=s['args']['exam_id']);s['patient_headers']=auth_headers_for(user)
    assert ct.validate(**s['delivery_args'])['state']=='staged_validated'
    s['draft']=ris.create_draft(**s['args'],content='SYNTHETIC test report version one')
    return s,p,d


def args(s):return s['delivery_args']
def freeze(s):
    p=report.preview(**args(s));return report.finalize(**args(s),expected_version=p['draft_revision'],preview_sha256=p['preview_sha256'])
def publish(s):return report.publish(**args(s),preview_sha256=report.release_preview(**args(s))['preview_sha256'])
def root(s):return f"/api/v1/radiology/exams/{s['args']['exam_id']}/staged-report/{args(s)['publication_id']}"


def test_freeze_and_original_draft_immutable(ready):
    s,_,_=ready;r=freeze(s);assert r['staged_finalized'] and not r['diagnostic_report_signed'] and not r['released_to_chart'] and not r['staged_patient_released']
    assert db.session.query(RadiologyStagedReport).count()==1
    with pytest.raises(ConflictError):ris.revise_draft(report_id=s['draft']['report_id'],clinic_id=s['clinic'].id,actor_staff_id=s['doctor'].id,expected_version=1,content='SYNTHETIC replacement')
    with pytest.raises(ConflictError):ris.get_draft_history(**s['args'])
    with pytest.raises(PrintDenied):snapshot(kind='radiology_draft',record_id=s['args']['exam_id'],clinic_id=s['clinic'].id,actor_role='DOCTOR',actor_staff_id=s['doctor'].id)
    with pytest.raises(ConflictError):freeze(s)
    assert db.session.query(RadiologyReportVersion).count()==1


def test_stale_review_denied(ready):
    s,_,_=ready;p=report.preview(**args(s));ris.revise_draft(report_id=s['draft']['report_id'],clinic_id=s['clinic'].id,actor_staff_id=s['doctor'].id,expected_version=1,content='SYNTHETIC new draft')
    with pytest.raises(ConflictError):report.finalize(**args(s),expected_version=1,preview_sha256=p['preview_sha256'])
    with pytest.raises(ConflictError):report.finalize(**args(s),expected_version=2,preview_sha256=p['preview_sha256'])
    assert db.session.query(RadiologyStagedReport).count()==0


def test_changed_archive_denied(ready):
    s,p,d=ready;iid=d['inventory'][0][0];raw=d['files'][iid];d['files'][iid]=raw[:-1]+bytes([raw[-1]^1])
    with pytest.raises(NotFoundError):freeze(s)
    assert db.session.query(RadiologyStagedReport).count()==0


def test_revoked_and_non_specialist_denied(ready,make_staff):
    s,_,_=ready;other=make_staff(s['clinic'],role=Role.DOCTOR,specialty='Radiology');db.session.commit()
    with pytest.raises(NotFoundError):report.preview(**dict(args(s),actor_staff_id=other.id))
    db.session.rollback();db.session.query(RadiologyInterpretationGrant).one().revoked_at=delivery.now();db.session.commit()
    with pytest.raises(NotFoundError):freeze(s)


def test_default_and_non_testing_held(ready,app):
    s,_,_=ready;app.config['RAD_C_REPORT_STAGED_ENABLED']=False
    with pytest.raises(NotFoundError):freeze(s)
    app.config['RAD_C_REPORT_STAGED_ENABLED']=True;app.config['TESTING']=False
    try:
        with pytest.raises(NotFoundError):freeze(s)
    finally:app.config['TESTING']=True


def test_release_separate_and_recipient_only(ready,make_user,make_patient):
    s,_,_=ready;freeze(s);r=s['recipient']
    with pytest.raises(NotFoundError):report.patient_detail(**r)
    assert report.patient_list(user_id=r['user_id'],clinic_id=r['clinic_id'])['items']==[]
    assert publish(s)['staged_patient_released'];assert report.patient_detail(**r)['record_version']==1
    assert len(report.patient_list(user_id=r['user_id'],clinic_id=r['clinic_id'])['items'])==1
    other=make_user(s['clinic'],role=Role.PATIENT);make_patient(s['clinic'],user_id=other.id);db.session.commit()
    with pytest.raises(NotFoundError):report.patient_detail(**dict(r,user_id=other.id))
    assert db.session.query(RadiologyStagedReportRelease).count()==1


def test_release_digest_and_duplicate_notification(ready):
    s,_,_=ready;freeze(s);p=report.release_preview(**args(s))
    with pytest.raises(ConflictError):report.publish(**args(s),preview_sha256='0'*64)
    report.publish(**args(s),preview_sha256=p['preview_sha256'])
    with pytest.raises(ConflictError):report.publish(**args(s),preview_sha256=p['preview_sha256'])
    from app.core.notifications.models.notification_models import Notification
    rows=db.session.query(Notification).filter_by(reference_type='staged_radiology_report').all()
    assert len(rows)==1 and rows[0].user_id==s['recipient']['user_id'] and 'SYNTHETIC test report' not in rows[0].message


def test_withdraw_then_shared_amendment_and_republish(ready):
    s,_,_=ready;freeze(s);publish(s)
    with pytest.raises(ConflictError):report.amend(**args(s),expected_version=1,report_text='SYNTHETIC corrected report',reason='Fixture correction')
    report.withdraw(**args(s),expected_version=1)
    with pytest.raises(NotFoundError):report.patient_detail(**s['recipient'])
    with pytest.raises(ConflictError):publish(s)
    a=report.amend(**args(s),expected_version=1,report_text='SYNTHETIC corrected report',reason='Fixture correction');assert a['record_version']==2
    assert db.session.query(ClinicalRecordAmendment).one().source_record_type=='staged_radiology_report'
    with pytest.raises(ConflictError):report.amend(**args(s),expected_version=1,report_text='SYNTHETIC stale',reason='Fixture correction')
    publish(s);assert report.patient_detail(**s['recipient'])['report_text']=='SYNTHETIC corrected report'
    assert db.session.query(RadiologyReportVersion).one().content=='SYNTHETIC test report version one'
    assert db.session.query(RadiologyStagedReportRelease).count()==2


def test_safety_withdraw_without_archive_or_grant(ready):
    s,_,d=ready;freeze(s);publish(s);d['inventory'].clear();db.session.query(RadiologyInterpretationGrant).one().revoked_at=delivery.now();db.session.commit()
    assert not report.withdraw(**args(s),expected_version=1)['staged_patient_released']
    with pytest.raises(NotFoundError):report.patient_detail(**s['recipient'])


def test_tampered_snapshot_denied(ready):
    s,_,_=ready;freeze(s);publish(s);row=db.session.query(RadiologyStagedReport).one();row.snapshot=dict(row.snapshot,report_text='SYNTHETIC tampered');db.session.commit()
    with pytest.raises(ConflictError):report.patient_detail(**s['recipient'])


def test_patient_pdf_held_and_watermarked(ready,app,client):
    s,_,_=ready;freeze(s);publish(s);app.config['RAD6_PRINTING_ENABLED']=False
    with pytest.raises(PrintDenied):report.patient_pdf(**s['recipient'])
    app.config['RAD6_PRINTING_ENABLED']=True
    data,doc=report.patient_pdf(**s['recipient']);assert data.startswith(b'%PDF-') and 'SYNTHETIC TEST COPY' in doc.watermark and not doc.is_signed and not doc.released_to_chart
    assert 'staged_radiology_copy' not in supported_kinds()
    url=f"/api/v1/radiology/me/staged-reports/{s['args']['exam_id']}/pdf";r=client.get(url,headers=s['patient_headers']);assert r.status_code==200 and r.headers['X-Radiology-Scope']=='synthetic-nondiagnostic-only' and r.headers['Cache-Control']=='no-store'


def test_http_role_and_strict_review(ready,client):
    s,_,_=ready;base=root(s);assert client.get(base+'/preview').status_code==401
    assert client.get(base+'/preview',headers=s['patient_headers']).status_code==403
    res=client.get(base+'/preview',headers=s['headers']);assert res.status_code==200,res.get_json();p=res.get_json()['data']
    body=dict(expected_version=p['draft_revision'],preview_sha256=p['preview_sha256']);assert client.post(base+'/freeze',headers=s['headers'],json=dict(body,diagnostic_report_signed=True)).status_code==422
    r=client.post(base+'/freeze',headers=s['headers'],json=body);assert r.status_code==200,r.get_json();assert r.get_json()['diagnostic_report_signed'] is False


def test_concurrent_freeze_on_postgres(ready,app):
    if db.engine.dialect.name!='postgresql':pytest.skip('PostgreSQL locking required')
    s,_,_=ready;a=dict(args(s));p=report.preview(**a);db.session.remove();barrier=Barrier(2,timeout=15)
    def attempt():
        with app.app_context():
            barrier.wait()
            try:report.finalize(**a,expected_version=p['draft_revision'],preview_sha256=p['preview_sha256']);return 'frozen'
            except ConflictError:return 'conflict'
            finally:db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:attempt(),range(2)))
    assert sorted(results)==['conflict','frozen'];assert db.session.query(RadiologyStagedReport).count()==1
