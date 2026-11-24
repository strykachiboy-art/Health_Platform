#!/usr/bin/env python3
"""RAD3 isolated synthetic DIMSE MWL C-FIND + C-STORE against Orthanc 26.11.0.

Two Orthanc nodes are connected only by a newly created Docker bridge. No
clinical DB, live Orthanc, published DICOM socket, real patient or signed report.
The secondary node is a simulated DICOM modality actor, NOT real scanner proof.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from rad2_dicom_fixture import make_study, parse_tags, decoded, assert_identity
from rad2_orthanc_gate import (cmd, unused_port, auth_header,
                                http, http_json, qido, wado)

ROOT=Path(__file__).resolve().parents[2]
PROD=ROOT/'app/modules/radiology/services/rad3_mwl_contract.py'
spec=importlib.util.spec_from_file_location('rad3_mwl_contract_gate',PROD)
bridge=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=bridge
spec.loader.exec_module(bridge)

IMAGE='orthancteam/orthanc:26.11.0'
SCOPE='csp.rad3.scope'
RID='csp.rad3.run'
BASE=Path(__file__).resolve().parent


def say(k,v='PASS'):
    print('RAD3_'+k+'='+str(v),flush=True)


def ensure(yes,reason):
    if not yes:raise RuntimeError(reason)


def docker_data(parts,missing=False):
    proc=cmd(['docker',*parts],check=False,timeout=35)
    if proc.returncode:
        detail=(proc.stderr or b'').decode('utf-8','replace').lower()
        phrases=('no such object','no such container','no such volume',
                 'no such network','network not found')
        if missing and (any(q in detail for q in phrases) or
                        re.search(r'network .* not found',detail)):return None
        raise RuntimeError('DOCKER_INSPECT_FAILED_NOT_CONFIRMED_ABSENT:'+parts[0])
    try:return json.loads(proc.stdout.decode('utf-8'))
    except (UnicodeError,ValueError) as exc:
        raise RuntimeError('BAD_DOCKER_INSPECT_JSON') from exc


def dlabel(item,runid):
    labels=item.get('Labels') or item.get('Config',{}).get('Labels') or {}
    ensure(labels.get(SCOPE)=='synthetic' and labels.get(RID)==runid,'OWNERSHIP_LABEL_MISMATCH')


def find_resource(kind,name,runid):
    data=docker_data([kind,'inspect',name],missing=True)
    if data is None:return None
    ensure(isinstance(data,list) and len(data)==1,'AMBIGUOUS_DOCKER_RESOURCE')
    obj=data[0]
    dlabel(obj,runid)
    return obj


def clean_run(names,runid,image_id,created):
    """Only exact, labeled owned resources; bounded polling after removal."""
    failures=[]
    for name in (names['source'],names['pacs']):
        if name not in created:continue
        try:
            obj=find_resource('container',name,runid)
            if obj is not None:
                ensure(obj.get('Image')==image_id,'CLEANUP_WRONG_IMAGE')
                # Validate the intended disposable bridge and volume before touching.
                mounts=obj.get('Mounts') or []
                volume=names['source_volume'] if name==names['source'] else names['pacs_volume']
                ensure(any(m.get('Type')=='volume' and m.get('Name')==volume for m in mounts),
                       'CLEANUP_NOT_OWNED_STORAGE_MOUNT')
                stopped=cmd(['docker','stop','--time','5',name],check=False,timeout=45)
                if stopped.returncode and find_resource('container',name,runid) is not None:
                    raise RuntimeError('DISPOSABLE_STOP_FAILED:'+name)
                if find_resource('container',name,runid) is not None:
                    cmd(['docker','rm',name],timeout=45)
                for _ in range(50):
                    if find_resource('container',name,runid) is None:break
                    time.sleep(.25)
                else:raise RuntimeError('CONTAINER_NOT_REMOVED:'+name)
        except Exception as e:failures.append(type(e).__name__+':'+str(e))
    if not failures:say('TWO_OWNED_CONTAINERS_REMOVED_OR_ABSENT')
    for key in ('source_volume','pacs_volume'):
        name=names[key]
        if name not in created:continue
        try:
            obj=find_resource('volume',name,runid)
            if obj is not None:
                # Do not remove volume referenced by any remaining container.
                using=cmd(['docker','ps','-aq','--filter','volume='+name],timeout=25)
                ensure(not using.stdout.strip(),'VOLUME_IN_USE:'+name)
                cmd(['docker','volume','rm',name],timeout=45)
                for _ in range(50):
                    if find_resource('volume',name,runid) is None:break
                    time.sleep(.25)
                else:raise RuntimeError('VOLUME_NOT_REMOVED:'+name)
        except Exception as e:failures.append(type(e).__name__+':'+str(e))
    if not failures:say('TWO_OWNED_VOLUMES_REMOVED_OR_ABSENT')
    name=names['network']
    if name in created:
        try:
            obj=find_resource('network',name,runid)
            if obj is not None:
                ensure(not (obj.get('Containers') or {}),'NETWORK_STILL_IN_USE')
                cmd(['docker','network','rm',name],timeout=45)
                for _ in range(50):
                    if find_resource('network',name,runid) is None:break
                    time.sleep(.25)
                else:raise RuntimeError('NETWORK_NOT_REMOVED:'+name)
        except Exception as e:failures.append(type(e).__name__+':'+str(e))
    if failures:raise RuntimeError('DISPOSABLE_CLEANUP_REQUIRES_REVIEW:'+','.join(failures))
    say('OWNED_BRIDGE_NETWORK_REMOVED_OR_ABSENT')


def get_json(base,path,auth):
    return http_json(base,path,{'Authorization':auth})


def request_json(base,path,auth,body,*,method='POST',allowed=(200,201,202,204)):
    data=json.dumps(body,separators=(',',':')).encode('utf-8')
    _,raw,_=http(base,path,method=method,body=data,
       headers={'Authorization':auth,'Content-Type':'application/json'},allowed=allowed)
    if not raw:return {}
    try:return json.loads(raw)
    except ValueError as e:raise RuntimeError('NON_JSON_ORTHANC_RESPONSE:'+path) from e


def ready(base,auth,name):
    for _ in range(75):
        try:
            info=get_json(base,'/system',auth)
            if info.get('Name')==name:return
        except (OSError,RuntimeError):pass
        time.sleep(1)
    raise RuntimeError('ORTHANC_NOT_READY:'+name)


def to_dicom_accession(study,accession):
    """Replace one SH tag in RAD2 fixture without changing its UIDs or pixels."""
    ensure(isinstance(accession,str) and 1 <= len(accession) <= 16 and accession.isascii(),
           'DICOM_SH_ACCESSION_MAX_16_VIOLATED')
    old=study.instance
    marker=b'\x08\x00\x50\x00SH'
    ensure(old.count(marker)==1,'FIXTURE_ACCESSION_TAG_AMBIGUOUS')
    pos=old.index(marker)
    length=struct.unpack_from('<H',old,pos+6)[0]
    value=old[pos+8:pos+8+length].rstrip(b' \x00').decode('ascii')
    ensure(value==study.accession,'FIXTURE_ACCESSION_SOURCE_MISMATCH')
    new=accession.encode('ascii')
    if len(new)%2:new+=b' '
    binary=old[:pos+6]+struct.pack('<H',len(new))+new+old[pos+8+length:]
    next_study=replace(study,accession=accession,instance=binary)
    assert_identity(binary,next_study)
    return next_study


def intent_for(study,sequence,clinic,clinical_accession):
    return bridge.CanonicalRISIntent(
        clinic_id=clinic,imaging_order_id=sequence+4,
        exam_id=sequence+44,exam_version=2,order_status='active',exam_status='scheduled',
        issuer_namespace=f'CSP-CLINIC-{clinic}',accession_number=clinical_accession,
        patient_id=study.patient_id,study_instance_uid=study.study_uid,
        scheduled_step_id=sequence+77,scheduled_step_sequence=1,
        scheduled_step_status='intent',scheduled_at=datetime(2030,1,2,10+sequence,15,tzinfo=timezone.utc),
        modality='other',station_aet='RAD3PACS')


def ensure_worklist_row(row,study):
    # Orthanc returns worklist JSON in multiple representations; verify the
    # actual returned identity values, never merely the worklist count.
    flat=json.dumps(row,sort_keys=True)
    for wanted in (study.accession,study.patient_id,study.study_uid):
        ensure(wanted in flat,'DIMSE_C_FIND_IDENTITY_NOT_RETURNED')


def dimse_find(source,source_auth,accession):
    data=request_json(source,'/modalities/rad3pacs/find-worklist',source_auth,
                      {'Query':{'AccessionNumber':accession,
                                'PatientID':'', 'StudyInstanceUID':''}})
    ensure(isinstance(data,list),'DIMSE_C_FIND_RESPONSE_NOT_LIST')
    return data


def worklist_publish(pacs,auth,study,intent):
    dto=bridge.worklist_payload(intent)
    result=request_json(pacs,'/worklists/create',auth,dto)
    ensure(isinstance(result,dict) and isinstance(result.get('ID'),str),
           'WORKLIST_CREATE_NO_ID')
    wid=result['ID']
    ensure(re.fullmatch(r'[0-9a-fA-F-]{10,}',wid) is not None,'WORKLIST_ID_UNEXPECTED')
    stored=get_json(pacs,'/worklists/'+wid,auth)
    ensure_worklist_row(stored,study)
    return wid


def assert_private(container,volume,network,runid,image_id):
    obj=find_resource('container',container,runid)
    ensure(obj is not None and obj.get('Image')==image_id,'TEST_CONTAINER_MISSING_OR_WRONG_IMAGE')
    mounts=obj.get('Mounts') or []
    ensure(any(m.get('Type')=='volume' and m.get('Name')==volume and
            m.get('Destination')=='/var/lib/orthanc/db' for m in mounts),'TEST_VOLUME_MOUNT_MISMATCH')
    ports=(obj.get('HostConfig',{}).get('PortBindings') or {})
    ensure(not ports.get('4242/tcp'),'DICOM_SOCKET_PUBLISHED_TO_HOST')
    published=ports.get('8042/tcp') or []
    ensure(len(published)==1 and published[0].get('HostIp')=='127.0.0.1',
           'HTTP_NOT_LOOPBACK_ONLY')
    nets=(obj.get('NetworkSettings',{}).get('Networks') or {})
    ensure(set(nets)=={network},'TEST_CONTAINER_HAS_OTHER_NETWORKS')


def validate(pacs,pauth,source,sauth,names,runid,image_id,evidence):
    ready(pacs,pauth,'CSP-RAD3-SYNTHETIC-PACS')
    ready(source,sauth,'CSP-RAD3-SYNTHETIC-MODALITY')
    for who,volume in [(names['pacs'],names['pacs_volume']),
                       (names['source'],names['source_volume'])]:
        assert_private(who,volume,names['network'],runid,image_id)
    say('LOOPBACK_HTTP_ONLY_DICOM_BRIDGE_INTERNAL')
    plugins=get_json(pacs,'/plugins',pauth)
    ensure(any('worklist' in str(name).lower() for name in plugins), 'WORKLISTS_PLUGIN_NOT_LOADED')
    ensure(any('dicom' in str(name).lower() and 'web' in str(name).lower() for name in plugins),
           'DICOMWEB_PLUGIN_NOT_LOADED')
    say('WORKLISTS_AND_DICOMWEB_PLUGINS_ACTIVE')
    http(pacs,'/worklists/',allowed=(401,403))
    http(pacs,'/dicom-web/studies',allowed=(401,403))
    http(source,'/instances',allowed=(401,403))
    say('ANONYMOUS_API_ACCESS_DENIED_BOTH_NODES')
    ensure(get_json(pacs,'/instances',pauth)==[], 'PACS_TEST_STORAGE_NOT_EMPTY')
    base_a=make_study(run_code=runid,subject='A')
    base_b=make_study(run_code=runid,subject='B')
    s_a=intent_for(base_a,1,clinic=22,clinical_accession='RAD-'+secrets.token_hex(12).upper())
    s_b=intent_for(base_b,2,clinic=33,clinical_accession='RAD-'+secrets.token_hex(12).upper())
    a=to_dicom_accession(base_a,bridge.dicom_accession(s_a))
    b=to_dicom_accession(base_b,bridge.dicom_accession(s_b))
    ensure(a.accession!=b.accession,'SYNTHETIC_DICOM_SHORT_ACCESSION_COLLISION')
    ensure(len(a.accession)<=16 and len(b.accession)<=16,
           'DICOM_SH_ACCESSION_CONFORMANCE_FAILED')
    say('RAD1_28_CHAR_TO_SYNTHETIC_DICOM_16_CHAR_MAPPING_ONLY')
    ensure(s_a.issuer_namespace!=s_b.issuer_namespace and a.patient_id!=b.patient_id,
           'SYNTHETIC_SCOPE_NOT_DISTINCT')
    id_a=worklist_publish(pacs,pauth,a,s_a)
    id_b=worklist_publish(pacs,pauth,b,s_b)
    say('TWO_SYNTHETIC_RIS_INTENTS_PUBLISHED_AND_VERIFIED_BY_REST')
    rows_a=dimse_find(source,sauth,a.accession)
    rows_b=dimse_find(source,sauth,b.accession)
    ensure(len(rows_a)==1 and len(rows_b)==1,'DIMSE_C_FIND_NOT_ONE_EACH')
    ensure_worklist_row(rows_a[0],a)
    ensure_worklist_row(rows_b[0],b)
    ensure(dimse_find(source,sauth,'RAD-000000000000000000000000')==[],
           'UNKNOWN_ACCESSION_VISIBLE_IN_DIMSE')
    say('REAL_DIMSE_C_FIND_TWO_ACCESSIONS_AND_UNKNOWN_NEGATIVE')
    # Reschedule B in the plugin using documented PUT, no RIS state mutation.
    new_intent=replace(s_b,scheduled_at=datetime(2030,1,3,15,40,tzinfo=timezone.utc),
                       exam_version=3,scheduled_step_sequence=2,scheduled_step_id=s_b.scheduled_step_id+1)
    request_json(pacs,'/worklists/'+id_b,pauth,bridge.worklist_payload(new_intent),method='PUT')
    ensure_worklist_row(dimse_find(source,sauth,b.accession)[0],b)
    updated=get_json(pacs,'/worklists/'+id_b,pauth)
    ensure('20300103' in json.dumps(updated),'RESCHEDULED_DATE_NOT_REFLECTED')
    say('WORKLIST_RESCHEDULE_UPDATED_WITH_REREAD_AND_DIMSE')
    # C-STORE: local REST loads synthetic DICOM into modality archive, then
    # Orthanc SCU issues real DIMSE C-STORE over the dedicated bridge to PACS.
    _,raw,_=http(source,'/instances',method='POST',body=a.instance,
       headers={'Authorization':sauth,'Content-Type':'application/dicom'},allowed=(200,201))
    try:instance_id=json.loads(raw)['ID']
    except (ValueError,TypeError,KeyError) as e:raise RuntimeError('MODALITY_IMPORT_NO_ORTHANC_ID') from e
    request_json(source,'/modalities/rad3pacs/store',sauth,
                 {'Resources':[instance_id],'Synchronous':True,'Permissive':False,'Timeout':25})
    ensure(len(get_json(pacs,'/instances',pauth))==1,'DIMSE_C_STORE_NOT_ONE')
    got=wado(pacs,{'Authorization':pauth},a)
    assert_identity(got,a)
    say('REAL_DIMSE_C_STORE_AND_WADO_PIXEL_IDENTITY')
    tags=parse_tags(got)
    observed={
        'PatientID':decoded(tags,(0x0010,0x0020)),
        'AccessionNumber':decoded(tags,(0x0008,0x0050)),
        'StudyInstanceUID':decoded(tags,(0x0020,0x000D)),
        'Modality':decoded(tags,(0x0008,0x0060)),
        'SOPInstanceUID':decoded(tags,(0x0008,0x0018)),
    }
    assessment=bridge.reconcile_candidate(s_a,observed)
    ensure(assessment.match_status=='unverified' and not assessment.archive_verified,
           'ACQUISITION_RECONCILIATION_PROMOTED_CLINICAL_ARCHIVE')
    wrong=bridge.reconcile_candidate(s_b,observed)
    ensure(wrong.match_status=='quarantined' and not wrong.chart_released,
           'FOREIGN_PATIENT_CLINIC_RECONCILIATION_BYPASS')
    stale=bridge.reconcile_candidate(replace(s_a,exam_version=99),observed)
    ensure(stale.match_status=='quarantined','STALE_SCHEDULE_ACCEPTED')
    say('CANDIDATE_MATCH_UNVERIFIED_WRONG_PATIENT_AND_STALE_QUARANTINED')
    # Restart the PACS container; the worklist is in its SQLite-backed store.
    cmd(['docker','restart','--time','5',names['pacs']],timeout=90)
    ready(pacs,pauth,'CSP-RAD3-SYNTHETIC-PACS')
    assert_identity(wado(pacs,{'Authorization':pauth},a),a)
    ensure_worklist_row(get_json(pacs,'/worklists/'+id_a,pauth),a)
    ensure_worklist_row(dimse_find(source,sauth,a.accession)[0],a)
    say('RESTART_PERSISTED_STUDY_AND_WORKLIST_DIMSE')
    # Cancel both *after* persistence; absence must be corroborated by DIMSE.
    for wid,study in ((id_a,a),(id_b,b)):
        http(pacs,'/worklists/'+wid,method='DELETE',
             headers={'Authorization':pauth},allowed=(200,204))
        ensure(dimse_find(source,sauth,study.accession)==[],
               'CANCELLED_WORKLIST_STILL_VISIBLE_IN_DIMSE')
    say('CANCELLED_WORKLISTS_NOT_OFFERED_TO_MODALITY')
    # Only synthetic identity hashes in evidence; never real patient data.
    evidence['synthetic_acquisitions']=[{
        'study_uid':a.study_uid,'accession':a.accession,
        'dicom_sha256':hashlib.sha256(got).hexdigest(),
        'candidate_state':assessment.match_status,
        'foreign_candidate_state':wrong.match_status,
    }]
    evidence['clinical_authority']='NO_PATIENT_LINK_NO_ARCHIVE_VERIFICATION_NO_REPORT_SIGNATURE'
    evidence['real_scanner_evidence']=False
    evidence['true_ris_database_to_pacs_sync']=False
    evidence['simulated_modality']='SECOND_ISOLATED_ORTHANC_SCU'


def run(evidence_dir):
    runid=secrets.token_hex(5)
    names={
        'network':'csp-rad3-net-'+runid,
        'pacs':'csp-rad3-pacs-'+runid,
        'source':'csp-rad3-scu-'+runid,
        'pacs_volume':'csp-rad3-pacs-vol-'+runid,
        'source_volume':'csp-rad3-scu-vol-'+runid,
    }
    created=set()
    image=docker_data(['image','inspect',IMAGE])
    ensure(isinstance(image,list) and len(image)==1,'ORTHANC_IMAGE_NOT_LOCAL')
    image_id=image[0].get('Id')
    ensure(isinstance(image_id,str) and image_id.startswith('sha256:'),'ORTHANC_IMAGE_ID_INVALID')
    say('LOCAL_PINNED_IMAGE',IMAGE)
    say('PINNED_IMAGE_ID',image_id)
    ensure(cmd(['docker','info','--format','{{.ServerVersion}}'],check=False).returncode==0,
           'DOCKER_DAEMON_UNAVAILABLE')
    cfg=json.loads((BASE/'orthanc_rad3_policy.json').read_text(encoding='utf-8'))
    ensure(cfg['Worklists']['SaveInOrthancDatabase'] is True and
           cfg['Worklists']['DeleteWorklistsOnStableStudy'] is False,'WORKLIST_POLICY_UNSAFE')
    actor_pass=secrets.token_urlsafe(28)
    pacs_pass=secrets.token_urlsafe(28)
    pacs_user='rad3p'+secrets.token_hex(4)
    actor_user='rad3s'+secrets.token_hex(4)
    evidence={'phase':'RAD3','image_tag':IMAGE,'image_id':image_id,'run_id':runid,
              'status':'NOT_STARTED','report_contains_credentials':False}
    evidence_dir.mkdir(parents=True,exist_ok=True)
    error=None
    with tempfile.TemporaryDirectory(prefix='csp-rad3-config-') as tmp:
        pacs_cfg=dict(cfg)
        pacs_cfg['RegisteredUsers']={pacs_user:pacs_pass}
        pacs_cfg['DicomModalities']={'rad3source':['RAD3SRC',names['source'],4242]}
        actor_cfg={
            'Name':'CSP-RAD3-SYNTHETIC-MODALITY',
            'HttpServerEnabled':True,'HttpPort':8042,
            'AuthenticationEnabled':True,'RemoteAccessAllowed':True,
            'DicomServerEnabled':False,'DicomAet':'RAD3SRC',
            'StorageDirectory':'/var/lib/orthanc/db',
            'MaximumStorageSize':64,'OverwriteInstances':False,
            'RegisteredUsers':{actor_user:actor_pass},
            'DicomModalities':{'rad3pacs':['RAD3PACS',names['pacs'],4242]},
        }
        p_cfg=Path(tmp)/'rad3-pacs.json'
        a_cfg=Path(tmp)/'rad3-modality.json'
        p_cfg.write_text(json.dumps(pacs_cfg,sort_keys=True),encoding='utf-8')
        a_cfg.write_text(json.dumps(actor_cfg,sort_keys=True),encoding='utf-8')
        try:
            created.add(names['network'])
            cmd(['docker','network','create','--driver','bridge',
                 '--label',SCOPE+'=synthetic','--label',RID+'='+runid,names['network']])
            find_resource('network',names['network'],runid)
            for key in ('pacs_volume','source_volume'):
                name=names[key]
                created.add(name)
                cmd(['docker','volume','create','--label',SCOPE+'=synthetic',
                     '--label',RID+'='+runid,name])
                find_resource('volume',name,runid)
            say('OWNED_DISPOSABLE_NETWORK_AND_TWO_VOLUMES_CREATED')
            for container,volume,cfile,mem in ((names['pacs'],names['pacs_volume'],p_cfg,'640m'),
                                               (names['source'],names['source_volume'],a_cfg,'384m')):
                port=unused_port()
                evidence['loopback_'+('pacs' if container==names['pacs'] else 'modality')+'_port']=port
                conf_target='/etc/orthanc/'+('rad3-pacs.json' if container==names['pacs'] else 'rad3-modality.json')
                args=['docker','run','--detach','--pull=never','--name',container,
                    '--label',SCOPE+'=synthetic','--label',RID+'='+runid,
                    '--network',names['network'],'--publish','127.0.0.1:'+str(port)+':8042',
                    '--memory',mem,'--cpus','0.75',
                    '--mount','type=volume,source='+volume+',target=/var/lib/orthanc/db',
                    '--mount','type=bind,source='+str(cfile.resolve())+',target='+conf_target+',readonly',
                    IMAGE]
                if container==names['pacs']:
                    args[-1:-1]=['--env','DICOM_WEB_PLUGIN_ENABLED=true',
                                 '--env','WORKLISTS_PLUGIN_ENABLED=true']
                created.add(container)
                cmd(args,timeout=85)
            say('TWO_PRIVATE_ORTHANC_NODES_CREATED')
            pacs='http://127.0.0.1:'+str(evidence['loopback_pacs_port'])
            source='http://127.0.0.1:'+str(evidence['loopback_modality_port'])
            validate(pacs,auth_header(pacs_user,pacs_pass),source,
                     auth_header(actor_user,actor_pass),names,runid,image_id,evidence)
            evidence['status']='RAD3_SYNTHETIC_DIMSE_GATE_GREEN'
        except Exception as exc:
            error=exc
            evidence['status']='RED_REQUIRES_EXISTING_WORKTREE_RECOVERY'
            evidence['failure_class']=type(exc).__name__
            evidence['failure_reason']=str(exc)[:240]
        finally:
            try:clean_run(names,runid,image_id,created)
            except Exception as cleanup_exc:
                evidence['cleanup']='FAILED_REVIEW_RESOURCES_BY_LABEL'
                evidence['cleanup_class']=str(cleanup_exc)[:240]
                if error is None:error=cleanup_exc
            else:evidence['cleanup']='PASS'
            path=evidence_dir/('RAD3_SYNTHETIC_EVIDENCE_'+runid+'.json')
            path.write_text(json.dumps(evidence,indent=2,sort_keys=True)+'\n',encoding='utf-8')
            say('EVIDENCE_MANIFEST',str(path))
    if error is not None:raise error
    ensure(evidence['status']=='RAD3_SYNTHETIC_DIMSE_GATE_GREEN',
           'ORTHANC_DIMSE_GATE_NOT_GREEN')
    say('ORTHANC_REAL_DIMSE_SYNTHETIC_GATE','PASS')


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--evidence-dir',required=True,type=Path)
    arg=ap.parse_args()
    try:run(arg.evidence_dir)
    except Exception as e:
        say('STOP_REASON',type(e).__name__+':'+str(e))
        raise SystemExit(1)
