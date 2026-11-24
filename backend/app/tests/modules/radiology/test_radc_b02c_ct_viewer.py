"""Staged synthetic CT; real Rust and disposable Orthanc mandatory."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import http.client
import json
import os
import struct
from threading import Barrier

import pytest
from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError
from app.extensions import db
from app.modules.radiology.models.ris_model import RadiologyInterpretationGrant, RadiologyStagedCTStudy, RadiologyStudyAssociation
from app.modules.radiology.services import radc_ct_service as ct, radc_acquisition_service as acq, radc_worklist_service as local, radc_delivery_service as delivery, ris_service as ris
from app.modules.radiology.services.radc_orthanc_transport import ArchiveUnconfirmed, StagedOrthanc
from app.tests.modules.radiology.test_rad1_ris_foundation import setup_ris
from app.tests.modules.radiology.test_radc_b02a_delivery import FakeArchive
from app.tests.modules.radiology.test_radc_b02b_acquisition import fixture, enable, _fixture_module


@pytest.fixture
def ct_published(setup_ris,app):
    s=setup_ris;app.config['RAD_C_WORKLIST_STAGED_ENABLED']=True
    s['order'].modality='ct';db.session.commit()
    exam=ris.establish_exam(imaging_order_id=s['order'].id,clinic_id=s['clinic'].id,actor_staff_id=s['doctor'].id)
    s['args']=dict(exam_id=exam['exam_id'],clinic_id=s['clinic'].id,actor_staff_id=s['doctor'].id)
    s['when']=datetime(2030,11,9,11,tzinfo=timezone.utc)
    ris.schedule_intent(**s['args'],expected_version=1,scheduled_at=s['when'],station_code='CT01')
    p=local.publish(**s['args'],expected_version=2)
    s['delivery_args']=dict(s['args'],publication_id=p['publication_id'])
    return s


def make_ct(p):
    m=_fixture_module(); raw,series,sop=fixture(p); frame=m.fresh_uid();sops=[sop,m.fresh_uid()]
    def one(index):
        tags=acq._elements(raw,132)
        def put(k,vr,value):
            e=m.element(*k,vr,value);tags[k]=acq._elements(e)[k]
        for k,vr,v in [((2,2),'UI',ct.CT),((2,3),'UI',sops[index]),((8,22),'UI',ct.CT),((8,24),'UI',sops[index]),((8,96),'CS','CT'),((32,19),'IS',str(index+1)),((32,50),'DS',f'0\\0\\{index*2}'),((32,55),'DS','1\\0\\0\\0\\1\\0'),((32,82),'UI',frame),((40,48),'DS','0.5\\0.5'),((40,256),'US',16),((40,257),'US',16),((40,258),'US',15),((40,259),'US',1),((40,4178),'DS','-1024'),((40,4179),'DS','1'),((24,80),'DS','2'),((0x7fe0,0x10),'OW',struct.pack('<64h',*[i+index for i in range(64)]))]:put(k,vr,v)
        # Recalculate File Meta Information Group Length after SOP class change.
        meta=b''.join(m.element(*k,vr.decode(),v) for k,(vr,v) in sorted(tags.items()) if k[0]==2 and k[1]!=0)
        body=b''.join(m.element(*k,vr.decode(),v) for k,(vr,v) in sorted(tags.items()) if k[0]!=2)
        return b'\0'*128+b'DICM'+m.element(2,0,'UL',len(meta))+meta+body
    return [(one(i),series,sops[i]) for i in range(2)]


def enable_ct(s,app):
    p=enable(s,app);app.config['RAD_C_CT_VIEWER_STAGED_ENABLED']=True
    return p


@pytest.fixture
def study(ct_published,app,monkeypatch):
    s=ct_published;a=FakeArchive();monkeypatch.setattr(delivery,'_archive',lambda:a)
    delivery.dispatch(**s['delivery_args']);p=enable_ct(s,app)
    data={'inventory':[], 'files':{}}
    for i,(raw,series,sop) in enumerate(make_ct(p)):
        iid=f'{i+1:08x}-12345678-12345678-12345678-12345678';data['inventory'].append((iid,series,sop));data['files'][iid]=raw
    monkeypatch.setattr(ct,'_inventory',lambda *_:list(data['inventory']))
    monkeypatch.setattr(acq,'_bytes',lambda archive,iid:data['files'][iid])
    return s,p,data


def args(s):return s['delivery_args']
def root(p):return 'studies/'+p['study_instance_uid']
def read(s,target,query=()):return ct.view(**args(s),target=target,query=list(query))


def test_two_slices_real_rust_and_manifest(study):
    s,p,d=study;r=ct.validate(**args(s));assert r['state']=='staged_validated' and r['instance_count']==2
    assert not r['archive_verified'] and not r['diagnostic_report_signed'] and not r['ohif_browser_authorized']
    row=db.session.query(RadiologyStagedCTStudy).one();assert row.manifest_sha256==local._digest(row.manifest)
    assert db.session.query(RadiologyStudyAssociation).one().match_status=='unverified'
    with pytest.raises(ConflictError):ct.validate(**args(s))


def test_scoped_qido_metadata_and_frames(study):
    s,p,d=study;ct.validate(**args(s));uid=p['study_instance_uid'];iid,series,sop=d['inventory'][0]
    raw,typ=read(s,'descriptor');desc=json.loads(raw);assert typ=='application/json' and desc['study_instance_uid']==uid and desc['instance_count']==2 and desc['authorization']=='bearer_per_request' and not desc['browser_handoff_authorized']
    raw,typ=read(s,'studies',[('StudyInstanceUID',uid)]);assert typ=='application/dicom+json';r=json.loads(raw);assert len(r)==1 and r[0]['0020000D']['Value']==[uid] and r[0]['00201208']['Value']==[2]
    raw,_=read(s,root(p)+'/series');assert len(json.loads(raw))==1
    path=root(p)+'/series/'+series+'/instances';raw,_=read(s,path);assert len(json.loads(raw))==2
    raw,_=read(s,root(p)+'/metadata');r=json.loads(raw);assert len(r)==2 and all(x['00280100']['Value']==[16] and '7FE00010' not in x for x in r)
    raw,typ=read(s,path+'/'+sop);assert typ.startswith('multipart/related; type="application/dicom"') and d['files'][iid] in raw
    raw,typ=read(s,path+'/'+sop+'/frames/1');assert 'application/octet-stream' in typ and acq._elements(d['files'][iid],132)[(0x7fe0,0x10)][1] in raw


def test_cross_study_series_instance_and_query_denied(study):
    s,p,d=study;ct.validate(**args(s));_,series,sop=d['inventory'][0];base=root(p)+'/series/'+series+'/instances/'+sop
    for target,query in [('studies',[]),('studies',[('StudyInstanceUID','2.25.1')]),('studies',[('StudyInstanceUID',p['study_instance_uid']),('StudyInstanceUID',p['study_instance_uid'])]),(root(p)+'/series/2.25.1/metadata',[]),(root(p)+'/series/'+series+'/instances/2.25.1',[]),(base+'/frames/2',[]),('http://127.0.0.1/system',[]),(base,[('url','http://127.0.0.1')])]:
        with pytest.raises(NotFoundError):read(s,target,query)


def test_changed_pixel_denies_whole_study(study):
    s,p,d=study;ct.validate(**args(s));iid=d['inventory'][1][0];raw=d['files'][iid];d['files'][iid]=raw[:-1]+bytes([raw[-1]^1])
    with pytest.raises(NotFoundError):read(s,root(p)+'/metadata')


def test_removed_instance_denied(study):
    s,p,d=study;ct.validate(**args(s));d['inventory'].pop()
    with pytest.raises(NotFoundError):read(s,root(p)+'/metadata')


def test_wrong_issuer_quarantine(study):
    s,p,d=study;iid=d['inventory'][1][0];d['files'][iid]=d['files'][iid].replace(p['patient_issuer'].encode(),b'Z'*len(p['patient_issuer']))
    assert ct.validate(**args(s))['state']=='quarantined'
    assert db.session.query(RadiologyStudyAssociation).one().match_status=='quarantined'
    with pytest.raises(NotFoundError):read(s,root(p)+'/metadata')


def test_duplicate_slice_position_quarantine(study):
    s,p,d=study;iid=d['inventory'][1][0];m=_fixture_module();old=m.element(32,50,'DS','0\\0\\2');assert d['files'][iid].count(old)==1;d['files'][iid]=d['files'][iid].replace(old,m.element(32,50,'DS','0\\0\\0'))
    assert ct.validate(**args(s))['state']=='quarantined'


def test_revoked_grant_denies_view(study):
    s,p,d=study;ct.validate(**args(s));db.session.query(RadiologyInterpretationGrant).one().revoked_at=delivery.now();db.session.commit()
    with pytest.raises(NotFoundError):read(s,root(p)+'/metadata')


def test_http_role_and_cache_scope(study,client,auth_headers_for,make_staff,make_clinic):
    s,p,d=study;a=args(s);url=f"/api/v1/radiology/exams/{a['exam_id']}/staged-ct";body={'publication_id':a['publication_id']}
    assert client.post(url,json=body).status_code==401
    assert client.post(url,json=body,headers=auth_headers_for(s['nurse'].user)).status_code==403
    assert client.post(url,json=body|{'target':'forged'},headers=s['headers']).status_code==422
    res=client.post(url,json=body,headers=s['headers']);assert res.status_code==201,res.get_json();assert res.get_json()['data']['instance_count']==2
    viewer=url+f"/{a['publication_id']}/viewer/"+root(p)+'/metadata'
    assert client.get(viewer).status_code==401
    other=make_staff(s['clinic'],role=Role.DOCTOR);foreign=make_staff(make_clinic(),role=Role.DOCTOR);db.session.commit()
    for person in (other,foreign):assert client.get(viewer,headers=auth_headers_for(person.user)).status_code==404
    res=client.get(viewer,headers=s['headers']);assert res.status_code==200 and res.headers['Cache-Control']=='no-store' and res.headers['X-Radiology-Scope']=='synthetic-nondiagnostic-only'
    assert client.post(viewer,headers=s['headers']).status_code==405


def test_feature_hold_and_worker_failure(study,app):
    s,p,d=study;app.config['RAD_C_CT_VIEWER_STAGED_ENABLED']=False
    with pytest.raises(NotFoundError):ct.validate(**args(s))
    app.config['RAD_C_CT_VIEWER_STAGED_ENABLED']=True;app.config['RAD_C_STAGED_WORKER']=None
    with pytest.raises(ArchiveUnconfirmed):ct.validate(**args(s))
    assert db.session.query(RadiologyStudyAssociation).count()==0


def test_stale_publication_denied(study):
    s,p,d=study;ct.validate(**args(s));ris.schedule_intent(**s['args'],expected_version=2,scheduled_at=s['when']+timedelta(days=1),station_code='CT02')
    with pytest.raises(ConflictError):read(s,root(p)+'/metadata')


def test_real_orthanc_two_slices(ct_published,app):
    s=ct_published;cfg=json.loads(os.environ['RADC_DISPOSABLE_ORTHANC_CONFIG']);app.config['RAD_C_EXTERNAL_MWL_STAGED_ENABLED']=True;app.config['RAD_C_EXTERNAL_MWL_ARCHIVE']=cfg
    delivery.dispatch(**args(s));p=enable_ct(s,app);archive=StagedOrthanc(**cfg);archive.check();ids=[]
    try:
        for raw,series,sop in make_ct(p):
            conn=http.client.HTTPConnection('127.0.0.1',archive.port,timeout=8)
            try:
                conn.request('POST','/instances',body=raw,headers={'Authorization':archive._auth,'Content-Type':'application/dicom'});res=conn.getresponse();assert res.status==200;obj=json.loads(res.read());assert obj['Status']=='Success';ids.append(acq._oid(obj['ID']))
            finally:conn.close()
        r=ct.validate(**args(s));assert r['state']=='staged_validated' and r['instance_count']==2,r
        raw,_=read(s,root(p)+'/metadata');assert len(json.loads(raw))==2
    finally:
        for iid in ids:archive._request('DELETE','/instances/'+iid)
        delivery.withdraw(**args(s))


def test_concurrent_ct_on_postgres(study,app):
    if db.engine.dialect.name!='postgresql':pytest.skip('PostgreSQL required')
    s,p,d=study;a=args(s);db.session.remove();barrier=Barrier(2,timeout=15)
    def call():
        with app.app_context():
            barrier.wait()
            try:ct.validate(**a);return 'validated'
            except ConflictError:return 'conflict'
            finally:db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:call(),range(2)))
    assert sorted(results)==['conflict','validated'] and db.session.query(RadiologyStagedCTStudy).count()==1
