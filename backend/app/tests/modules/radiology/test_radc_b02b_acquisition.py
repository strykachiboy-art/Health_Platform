"""Synthetic SC only; real worker and disposable Orthanc round-trip mandatory."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import http.client
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
from threading import Barrier

import pytest
from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError
from app.extensions import db
from app.modules.radiology.models.ris_model import RadiologyInterpretationGrant, RadiologyStagedAcquisition, RadiologyStudyAssociation, RadiologyWorklistPublication
from app.modules.radiology.services import radc_acquisition_service as acq, radc_delivery_service as delivery, ris_service as ris
from app.modules.radiology.services.radc_orthanc_transport import ArchiveUnconfirmed, StagedOrthanc
from app.tests.modules.radiology.test_rad1_ris_foundation import setup_ris
from app.tests.modules.radiology.test_radc_b01_worklist import scheduled
from app.tests.modules.radiology.test_radc_b02a_delivery import published, fake


def _fixture_module():
    p=Path(__file__).resolve().parents[4]/'testing'/'radiology'/'rad2_dicom_fixture.py'
    spec=importlib.util.spec_from_file_location('radc_b02b_fixture',p)
    mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
    return mod


def fixture(p):
    m=_fixture_module(); s=m.make_study(run_code='RADC02B',subject='A'); raw=s.instance
    for tag,vr,old,new in [((0x0010,0x0020),'LO',s.patient_id,p['patient_identifier']),((0x0008,0x0050),'SH',s.accession,p['accession']),((0x0020,0x000d),'UI',s.study_uid,p['study_instance_uid'])]:
        a=m.element(*tag,vr,old); assert raw.count(a)==1; raw=raw.replace(a,m.element(*tag,vr,new),1)
    ns=m.element(0x0040,0x0031,'UT',p['accession_issuer'])
    seq=m.element(0x0008,0x0051,'SQ',struct.pack('<HHI',0xfffe,0xe000,len(ns))+ns)
    anchor=m.element(0x0008,0x0060,'CS','OT'); assert raw.count(anchor)==1; raw=raw.replace(anchor,seq+anchor,1)
    anchor=m.element(0x0010,0x0020,'LO',p['patient_identifier']); assert raw.count(anchor)==1
    raw=raw.replace(anchor,anchor+m.element(0x0010,0x0021,'LO',p['patient_issuer']),1)
    return raw,s.series_uid,s.sop_uid


def enable(s,app):
    app.config['RAD_C_ACQUISITION_STAGED_ENABLED']=True
    app.config['RAD_C_STAGED_WORKER']=json.loads(os.environ['RADC_STAGED_WORKER_CONFIG'])
    t=delivery.now()
    db.session.add(RadiologyInterpretationGrant(exam_id=s['args']['exam_id'],staff_id=s['doctor'].id,clinic_id=s['clinic'].id,
        modality=s['order'].modality,credential_reference='synthetic-approved',approved_at=t-timedelta(hours=1),expires_at=t+timedelta(hours=1)))
    db.session.commit()
    p=db.session.get(RadiologyWorklistPublication,s['delivery_args']['publication_id']).payload
    return p


@pytest.fixture
def staged(fake,app,monkeypatch):
    s,a=fake; delivery.dispatch(**s['delivery_args']);p=enable(s,app)
    raw,series,sop=fixture(p); data={'raw':raw,'inventory':('12345678-'*4+'12345678',series,sop)}
    monkeypatch.setattr(acq,'_inventory',lambda *_:data['inventory'])
    monkeypatch.setattr(acq,'_bytes',lambda *_:data['raw'])
    return s,p,data


def test_real_worker_and_exact_retrieval(staged):
    s,p,d=staged; args=s['delivery_args']; result=acq.validate(**args)
    assert result['state']=='staged_validated' and not result['archive_verified'] and not result['ohif_browser_authorized']
    assert db.session.query(RadiologyStudyAssociation).one().match_status=='unverified'
    assert acq.retrieve(**args)==d['raw']
    with pytest.raises(ConflictError):acq.validate(**args)
    assert db.session.query(RadiologyStagedAcquisition).count()==1


def test_wrong_issuer_quarantined(staged):
    s,p,d=staged; d['raw']=d['raw'].replace(p['patient_issuer'].encode(),b'X'*len(p['patient_issuer']))
    assert acq.validate(**s['delivery_args'])['state']=='quarantined'
    assert db.session.query(RadiologyStudyAssociation).one().match_status=='quarantined'
    with pytest.raises(NotFoundError):acq.retrieve(**s['delivery_args'])


def test_changed_file_denied(staged):
    s,p,d=staged; acq.validate(**s['delivery_args']);d['raw']=d['raw'][:-1]+bytes([d['raw'][-1]^1])
    with pytest.raises(NotFoundError):acq.retrieve(**s['delivery_args'])


def test_changed_inventory_denied(staged):
    s,p,d=staged; acq.validate(**s['delivery_args']);d['inventory']=(d['inventory'][0],'2.25.123456',d['inventory'][2])
    with pytest.raises(NotFoundError):acq.retrieve(**s['delivery_args'])


def test_revoked_grant_denied(staged):
    s,p,d=staged;acq.validate(**s['delivery_args']);db.session.query(RadiologyInterpretationGrant).one().revoked_at=delivery.now();db.session.commit()
    with pytest.raises(NotFoundError):acq.retrieve(**s['delivery_args'])


def test_ungranted_doctor_and_foreign_clinic_denied(staged,make_staff,make_clinic):
    s,p,d=staged; other=make_staff(s['clinic'],role=Role.DOCTOR);foreign=make_clinic(); staff=make_staff(foreign,role=Role.DOCTOR);db.session.commit()
    with pytest.raises(NotFoundError):acq.validate(**dict(s['delivery_args'],actor_staff_id=other.id))
    with pytest.raises(NotFoundError):acq.validate(**dict(s['delivery_args'],actor_staff_id=staff.id,clinic_id=foreign.id))
    assert db.session.query(RadiologyStagedAcquisition).count()==0


def test_worker_unavailable_no_false_quarantine(staged,app):
    s,p,d=staged;app.config['RAD_C_STAGED_WORKER']=None
    with pytest.raises(ArchiveUnconfirmed):acq.validate(**s['delivery_args'])
    assert db.session.query(RadiologyStudyAssociation).count()==0


def test_session_routes_and_no_cache(staged,client,auth_headers_for):
    s,p,d=staged; args=s['delivery_args']; path=f"/api/v1/radiology/exams/{args['exam_id']}/staged-study";body={'publication_id':args['publication_id']}
    assert client.post(path,json=body).status_code==401
    assert client.post(path,json=body,headers=auth_headers_for(s['nurse'].user)).status_code==403
    assert client.post(path,json=body|{'study_uid':'2.25.1'},headers=s['headers']).status_code==422
    res=client.post(path,json=body,headers=s['headers']);assert res.status_code==201,res.get_json();assert res.get_json()['data']['state']=='staged_validated'
    url=path+f"/{args['publication_id']}/instance";res=client.get(url,headers=s['headers']);assert res.status_code==200 and res.data==d['raw'];assert res.headers['Cache-Control']=='no-store'
    assert client.get(url+'?target=http://example.org',headers=s['headers']).status_code==422


def test_stale_publication_denied(staged):
    s,p,d=staged;ris.schedule_intent(**s['args'],expected_version=2,scheduled_at=s['when']+timedelta(days=1),station_code='XR02')
    with pytest.raises(ConflictError):acq.validate(**s['delivery_args'])


def test_real_disposable_orthanc_and_rust(published,app):
    s=published; cfg=json.loads(os.environ['RADC_DISPOSABLE_ORTHANC_CONFIG']);app.config['RAD_C_EXTERNAL_MWL_STAGED_ENABLED']=True;app.config['RAD_C_EXTERNAL_MWL_ARCHIVE']=cfg
    delivery.dispatch(**s['delivery_args']);p=enable(s,app);raw,_,_=fixture(p)
    archive=StagedOrthanc(**cfg);archive.check();conn=http.client.HTTPConnection('127.0.0.1',archive.port,timeout=8)
    try:
        conn.request('POST','/instances',body=raw,headers={'Authorization':archive._auth,'Content-Type':'application/dicom'});res=conn.getresponse();assert res.status==200;obj=json.loads(res.read());assert obj['Status']=='Success'
    finally:conn.close()
    try:
        assert acq.validate(**s['delivery_args'])['state']=='staged_validated'
        assert acq.retrieve(**s['delivery_args'])==raw
    finally:
        # Only this synthetic fixture, in the run-owned disposable archive.
        archive._request('DELETE','/instances/'+acq._oid(obj['ID']))
        delivery.withdraw(**s['delivery_args'])


def test_concurrent_acquisition_on_postgres(staged,app):
    if db.engine.dialect.name!='postgresql':pytest.skip('PostgreSQL required')
    s,p,d=staged;args=s['delivery_args'];db.session.remove();barrier=Barrier(2,timeout=15)
    def call():
        with app.app_context():
            barrier.wait()
            try:acq.validate(**args);return 'validated'
            except ConflictError:return 'conflict'
            finally:db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:call(),range(2)))
    assert sorted(results)==['conflict','validated'];assert db.session.query(RadiologyStagedAcquisition).count()==1


def test_rust_rejects_unsupported_modality(staged):
    s,p,d=staged;m=_fixture_module();old=m.element(0x0008,0x0060,'CS','OT');assert d['raw'].count(old)==1
    d['raw']=d['raw'].replace(old,m.element(0x0008,0x0060,'CS','CT'),1)
    assert acq.validate(**s['delivery_args'])['state']=='quarantined'


def test_wrong_accession_issuer_quarantined(staged):
    s,p,d=staged;m=_fixture_module();old=m.element(0x0040,0x0031,'UT',p['accession_issuer']);assert d['raw'].count(old)==1
    d['raw']=d['raw'].replace(old,m.element(0x0040,0x0031,'UT','Z'*len(p['accession_issuer'])),1)
    assert acq.validate(**s['delivery_args'])['state']=='quarantined'
