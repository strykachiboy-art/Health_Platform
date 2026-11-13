#!/usr/bin/env python3
"""RAD2 real Orthanc + DICOMweb disposable synthetic-only integration gate.

No Flask, database, Celery, redis, patient files, shared Docker services, or
source edits. Runs only inside a fresh, outer-installer-owned RAD2 worktree.
"""
from __future__ import annotations

import argparse
import base64
from email import policy as email_policy
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from rad2_dicom_fixture import SyntheticStudy, assert_identity, fresh_uid, make_study

HERE=Path(__file__).resolve().parent
IMAGE='orthancteam/orthanc:26.11.0'
SCOPE_LABEL='csp.rad2.scope'
RUN_LABEL='csp.rad2.run'


def say(name,value='PASS'):
    print(f'RAD2_{name}={value}',flush=True)


def require(ok, label):
    if not ok: raise RuntimeError(label)


def cmd(argv, *, check=True, timeout=45, capture=True):
    try:
        proc=subprocess.run(argv,check=False,timeout=timeout,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise RuntimeError('DOCKER_COMMAND_UNAVAILABLE_OR_TIMEOUT:'+str(argv[0])) from exc
    if check and proc.returncode:
        raise RuntimeError('DOCKER_COMMAND_FAILED:'+argv[1])
    return proc


def output(argv,**kw):
    return cmd(argv,**kw).stdout.decode('utf-8',errors='replace').strip()


def docker_json(argv, *, missing_ok=False):
    proc=cmd(['docker',*argv],check=False,timeout=25)
    if proc.returncode:
        detail=(proc.stderr or b'').decode('utf-8','replace').lower()
        # Only a real "not found" can be interpreted as absent.  A daemon
        # outage, TLS failure or denied permission must NEVER pass cleanup.
        if missing_ok and ('no such object' in detail or 'no such container' in detail
                           or 'no such volume' in detail):
            return None
        raise RuntimeError('DOCKER_INSPECT_FAILED_NOT_CONFIRMED_ABSENT')
    try:return json.loads(proc.stdout.decode('utf-8'))
    except (ValueError, UnicodeError) as exc:raise RuntimeError('BAD_DOCKER_JSON') from exc


def unused_port():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1',0))
        return s.getsockname()[1]


def ownership(container,volume,runid,image_id, *, require_binding=True):
    found=docker_json(['container','inspect',container],missing_ok=True)
    if found is None:return None
    require(isinstance(found,list) and len(found)==1,'CONTAINER_AMBIGUOUS')
    x=found[0]
    labels=x.get('Config',{}).get('Labels') or {}
    require(labels.get(SCOPE_LABEL)=='synthetic' and labels.get(RUN_LABEL)==runid,
            'CONTAINER_OWNER_LABEL_MISMATCH')
    require(x.get('Image')==image_id,'CONTAINER_IMAGE_CHANGED')
    mounts=x.get('Mounts') or []
    require(any(m.get('Type')=='volume' and m.get('Name')==volume and
                m.get('Destination')=='/var/lib/orthanc/db' for m in mounts),
            'CONTAINER_NOT_EXACT_DISPOSABLE_STORAGE_VOLUME')
    if require_binding:
        network=x.get('NetworkSettings',{}).get('Ports') or {}
        exposed=network.get('8042/tcp') or []
        require(len(exposed)==1 and exposed[0].get('HostIp')=='127.0.0.1',
                'CONTAINER_HTTP_NOT_BOUND_EXCLUSIVELY_TO_LOOPBACK')
        require(not network.get('4242/tcp'),'DICOM_LISTENER_EXPOSED_TO_HOST')
    return x


def volume_ownership(volume,runid):
    d=docker_json(['volume','inspect',volume],missing_ok=True)
    if d is None:return False
    require(isinstance(d,list) and len(d)==1,'VOLUME_AMBIGUOUS')
    labels=d[0].get('Labels') or {}
    require(labels.get(SCOPE_LABEL)=='synthetic' and labels.get(RUN_LABEL)==runid,
            'VOLUME_OWNER_LABEL_MISMATCH')
    return True


def cleanup(container,volume,runid,image_id, *, created_volume):
    """Only remove resources with both exact test labels. Poll after removal."""
    if ownership(container,volume,runid,image_id,require_binding=False) is not None:
        cmd(['docker','stop','--time','5',container],check=False,timeout=40)
        if ownership(container,volume,runid,image_id,require_binding=False) is not None:
            cmd(['docker','rm',container],timeout=35)
        for _ in range(40):
            if docker_json(['container','inspect',container],missing_ok=True) is None:break
            time.sleep(.25)
        else:raise RuntimeError('TEST_CONTAINER_NOT_REMOVED_MANUAL_CHECK:'+container)
    say('TEST_CONTAINER_REMOVED_OR_ABSENT')
    if created_volume and volume_ownership(volume,runid):
        # A new, separately-owned container must NEVER use this volume.
        using=output(['docker','ps','-aq','--filter','volume='+volume])
        require(not using,'TEST_VOLUME_IN_USE_DO_NOT_REMOVE:'+volume)
        cmd(['docker','volume','rm',volume],timeout=45)
        for _ in range(40):
            if not volume_ownership(volume,runid):break
            time.sleep(.25)
        else:raise RuntimeError('TEST_VOLUME_NOT_REMOVED_MANUAL_CHECK:'+volume)
    say('TEST_VOLUME_REMOVED_OR_ABSENT')


def auth_header(user, password):
    b64=base64.b64encode((user+':'+password).encode('ascii')).decode('ascii')
    return 'Basic '+b64


def http(base,path, *, headers=None, body=None, method=None, allowed=(200,)):
    request=urllib.request.Request(base+path,data=body,
         headers=headers or {},method=method or ('POST' if body is not None else 'GET'))
    try:
        with urllib.request.urlopen(request,timeout=15) as response:
            code=response.status
            data=response.read(8*1024*1024+1)
            content_type=response.headers.get('Content-Type','')
    except urllib.error.HTTPError as exc:
        code=exc.code
        data=exc.read(8*1024*1024+1)
        content_type=exc.headers.get('Content-Type','')
    require(len(data)<=8*1024*1024,'HTTP_RESPONSE_TOO_LARGE')
    require(code in allowed,'UNEXPECTED_HTTP_STATUS:'+path+':'+str(code))
    return code,data,content_type


def http_json(base,path,headers=None,*,allowed=(200,)):
    _,body,_=http(base,path,headers=headers,allowed=allowed)
    try:return json.loads(body.decode('utf-8'))
    except Exception as exc:raise RuntimeError('INVALID_DICOMWEB_JSON:'+path) from exc


def wait_ready(base,header):
    for _ in range(85):
        try:
            sysinfo=http_json(base,'/system',{'Authorization':header})
            if isinstance(sysinfo,dict) and sysinfo.get('Name')=='CSP-RAD2-SYNTHETIC-ISOLATED':
                return
        except (OSError,RuntimeError):pass
        time.sleep(1)
    raise RuntimeError('ORTHANC_STARTUP_OR_CONFIG_TIMEOUT')


def dicom_json_value(row,key):
    try:
        values=row[key]['Value']
        if len(values)!=1 or not isinstance(values[0],str):return None
        return values[0]
    except (KeyError,TypeError,IndexError):return None


def qido(base,auth,study: SyntheticStudy):
    query=urllib.parse.urlencode({'StudyInstanceUID':study.study_uid})
    rows=http_json(base,'/dicom-web/studies?'+query,headers=auth)
    require(isinstance(rows,list) and len(rows)==1,'QIDO_EXACT_STUDY_NOT_ONE')
    require(dicom_json_value(rows[0],'0020000D')==study.study_uid,'QIDO_STUDY_UID_MISMATCH')
    require(dicom_json_value(rows[0],'00100020')==study.patient_id,'QIDO_PATIENT_MISMATCH')
    return rows[0]


def wado(base,auth,study: SyntheticStudy):
    path='/dicom-web/studies/'+study.study_uid+'/series/'+study.series_uid+'/instances/'+study.sop_uid
    hdr=dict(auth)
    hdr['Accept']='multipart/related; type="application/dicom"'
    _,body,ctype=http(base,path,headers=hdr)
    if ctype.lower().startswith('application/dicom'):
        extracted=body
    elif ctype.lower().startswith('multipart/related'):
        envelope=(b'MIME-Version: 1.0\r\nContent-Type: '+ctype.encode('ascii')+
                  b'\r\n\r\n'+body)
        msg=BytesParser(policy=email_policy.default).parsebytes(envelope)
        require(msg.is_multipart(),'WADO_MULTIPART_PARSER_FAILED')
        parts=list(msg.iter_parts())
        require(len(parts)==1,'WADO_UNEXPECTED_INSTANCE_COUNT')
        require(parts[0].get_content_type()=='application/dicom',
                'WADO_MIME_NOT_DICOM')
        extracted=parts[0].get_payload(decode=True)
    else:
        raise RuntimeError('WADO_UNSUPPORTED_CONTENT_TYPE')
    require(isinstance(extracted,bytes),'WADO_MISSING_BINARY')
    assert_identity(extracted,study)
    return extracted


def stow(base,auth,study: SyntheticStudy):
    boundary='CSPRAD2'+secrets.token_hex(12)
    payload=(b'--'+boundary.encode()+b'\r\nContent-Type: application/dicom\r\n\r\n'+
             study.instance+b'\r\n--'+boundary.encode()+b'--\r\n')
    headers=dict(auth)
    headers['Content-Type']='multipart/related; type="application/dicom"; boundary='+boundary
    headers['Accept']='application/dicom+json'
    http(base,'/dicom-web/studies',headers=headers,method='POST',body=payload,allowed=(200,202))
    qido(base,auth,study)


def validate(base,header,runid,container,volume,image_id, evidence):
    auth={'Authorization':header}
    wait_ready(base,header)
    say('ORTHANC_AUTHENTICATED_READY')
    plugins=http_json(base,'/plugins',headers=auth)
    require(isinstance(plugins,list) and 'dicom-web' in plugins,'DICOMWEB_PLUGIN_NOT_LOADED')
    say('DICOMWEB_PLUGIN_ACTIVE')
    status,_,_=http(base,'/system',allowed=(401,403))
    require(status in (401,403),'ANONYMOUS_NATIVE_API_WAS_ALLOWED')
    status,_,_=http(base,'/dicom-web/studies',allowed=(401,403))
    require(status in (401,403),'ANONYMOUS_DICOMWEB_WAS_ALLOWED')
    say('ANONYMOUS_NATIVE_AND_DICOMWEB_DENIED')
    assert http_json(base,'/instances',headers=auth)==[], 'Unexpected preexisting instances'
    say('DISPOSABLE_STORAGE_INITIAL_EMPTY')
    a=make_study(run_code=runid,subject='A')
    b=make_study(run_code=runid,subject='B')
    require(a.patient_id!=b.patient_id and a.study_uid!=b.study_uid,
            'SYNTHETIC_IDENTITIES_NOT_DISTINCT')
    stow(base,auth,a);stow(base,auth,b)
    say('STOW_RS_TWO_SYNTHETIC_INSTANCES_AND_QIDO_MATCH')
    native=http_json(base,'/instances',headers=auth)
    require(isinstance(native,list) and len(native)==2,'NATIVE_ORTHANC_INSTANCE_COUNT_NOT_TWO')
    for study in (a,b):qido(base,auth,study)
    data_a=wado(base,auth,a)
    data_b=wado(base,auth,b)
    say('WADO_RS_DICOM_IDENTITY_AND_PIXEL_HASHES')
    # Checking mismatched identity is explicitly application-side verification,
    # not authorization within Orthanc (which is NOT multi-tenant aware).
    try: assert_identity(data_a,b)
    except ValueError:pass
    else:raise RuntimeError('WRONG_PATIENT_DICOM_NOT_QUARANTINED')
    wrong=urllib.parse.urlencode({'PatientID':b.patient_id,'StudyInstanceUID':a.study_uid})
    r=http_json(base,'/dicom-web/studies?'+wrong,headers=auth)
    require(isinstance(r,list) and not r,'QIDO_WRONG_PATIENT_FILTER_LEAK')
    missing=fresh_uid()
    missingrows=http_json(base,'/dicom-web/studies?StudyInstanceUID='+missing,headers=auth)
    require(missingrows==[],'UNKNOWN_STUDY_RETURNED')
    http(base,'/dicom-web/studies/'+missing,headers=auth,allowed=(404,))
    say('WRONG_PATIENT_AND_UNKNOWN_STUDY_FAIL_CLOSED')
    # The plugin may return 200 with per-instance failures. Inspect the store,
    # not only HTTP status, to guarantee malformed DICOM was not ingested.
    boundary='CSPRAD2'+secrets.token_hex(8)
    bad=(b'--'+boundary.encode()+b'\r\nContent-Type: application/dicom\r\n\r\n'+
        b'NOT A DICOM FILE'+b'\r\n--'+boundary.encode()+b'--\r\n')
    _,_,_=http(base,'/dicom-web/studies',method='POST',body=bad,
       headers={**auth,'Content-Type':'multipart/related; type="application/dicom"; boundary='+boundary},
       allowed=(200,202,400,409,415,422,500))
    require(len(http_json(base,'/instances',headers=auth))==2,'MALFORMED_DICOM_WAS_INGESTED')
    say('MALFORMED_DICOM_DID_NOT_CREATE_INSTANCE')
    # Restart the SAME container using the SAME disposable storage volume.
    ownership(container,volume,runid,image_id)
    cmd(['docker','restart','--time','5',container],timeout=75)
    wait_ready(base,header)
    require(len(http_json(base,'/instances',headers=auth))==2,
            'DURABILITY_POST_RESTART_INSTANCE_COUNT_CHANGED')
    assert_identity(wado(base,auth,a),a)
    assert_identity(wado(base,auth,b),b)
    say('RESTART_VOLUME_DURABILITY_WADO_RS_RETRIEVAL')
    evidence['synthetic_studies']=[{
        'study_uid':s.study_uid,'series_uid':s.series_uid,
        'sop_uid':s.sop_uid,'synthetic_patient_id':s.patient_id,
        'pixel_sha256':s.pixel_sha256,
        'exported_dicom_sha256':hashlib.sha256(raw).hexdigest(),
    } for s,raw in [(a,data_a),(b,data_b)]]
    evidence['backup_scope']='SYNTHETIC_DICOM_EXPORT_ONLY_NOT_FULL_PACS_BACKUP'
    evidence['tenant_authorization']='NOT_PROVIDED_BY_ORTHANC_RISK_REQUIRES_FLASK_PROXY'
    evidence['image_store_status']='DISPOSABLE_SYNTHETIC_ONLY_NO_RIS_ARCHIVE_CONFIRMATION'
    say('SYNTHETIC_EXPORT_DIGESTS_RECORDED_NO_FULL_BACKUP_CLAIM')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--evidence-dir',type=Path,required=True)
    args=parser.parse_args()
    runid=secrets.token_hex(5)
    container='csp-rad2-orthanc-'+runid
    volume='csp-rad2-vol-'+runid
    user='rad2'+secrets.token_hex(4)
    password=secrets.token_urlsafe(28)
    evidence={'gate':'RAD2','run_id':runid,'image_tag':IMAGE,'status':'NOT_RUN'}
    image=docker_json(['image','inspect',IMAGE])
    require(isinstance(image,list) and len(image)==1,'ORTHANC_IMAGE_NOT_LOCAL')
    image_id=image[0].get('Id')
    require(isinstance(image_id,str) and image_id.startswith('sha256:'),'INVALID_LOCAL_IMAGE_ID')
    evidence['local_image_id']=image_id
    evidence['repo_digests']=image[0].get('RepoDigests') or []
    require(cmd(['docker','info','--format','{{.ServerVersion}}'],check=False).returncode==0,
            'DOCKER_DAEMON_NOT_AVAILABLE')
    say('SOURCE_IMAGE','orthancteam/orthanc:26.11.0')
    say('TEST_IMAGE_ID',image_id)
    # volume creation is the first owned resource; cleanup finally never touches
    # other volumes or named containers.
    created_volume=False
    args.evidence_dir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='csp-rad2-config-') as work:
        config=json.loads((HERE/'orthanc_policy.json').read_text(encoding='utf-8'))
        require('RegisteredUsers' not in config,'SOURCE_POLICY_EMBEDS_CREDENTIALS')
        config['RegisteredUsers']={user:password}
        cfg=Path(work)/'rad2-isolated.json'
        cfg.write_text(json.dumps(config,sort_keys=True),encoding='utf-8')
        try:
            cmd(['docker','volume','create','--label',SCOPE_LABEL+'=synthetic',
                '--label',RUN_LABEL+'='+runid,volume])
            created_volume=True
            require(volume_ownership(volume,runid),'NEW_TEST_VOLUME_OWNERSHIP_FAILED')
            say('DISPOSABLE_VOLUME_CREATED')
            port=unused_port()
            # Published HTTP binds to loopback. DICOM port is not published and
            # the Orthanc DICOM server is disabled in config.
            args_run=['docker','run','--detach','--pull=never','--name',container,
                '--label',SCOPE_LABEL+'=synthetic','--label',RUN_LABEL+'='+runid,
                '--publish',f'127.0.0.1:{port}:8042',
                '--memory','768m','--cpus','1',
                '--mount','type=volume,source='+volume+',target=/var/lib/orthanc/db',
                '--mount','type=bind,source='+str(cfg.resolve())+',target=/etc/orthanc/rad2-isolated.json,readonly',
                '--env','DICOM_WEB_PLUGIN_ENABLED=true', IMAGE]
            cmd(args_run,timeout=90)
            ownership(container,volume,runid,image_id)
            say('DISPOSABLE_CONTAINER_LOOPBACK_AUTH_AND_NO_DICOM_SOCKET')
            evidence['host_http_port_loopback_only']=port
            evidence['container_name']=container
            evidence['volume_name']=volume
            base='http://127.0.0.1:'+str(port)
            validate(base,auth_header(user,password),runid,container,volume,image_id,evidence)
            evidence['status']='RAD2_SYNTHETIC_GATE_GREEN'
        finally:
            cleanup(container,volume,runid,image_id,created_volume=created_volume)
            evidence['disposable_docker_cleaned']=True
            evidence['credentials_written_to_evidence']=False
            report=args.evidence_dir/('RAD2_SYNTHETIC_EVIDENCE_'+runid+'.json')
            report.write_text(json.dumps(evidence,indent=2,sort_keys=True)+'\n',encoding='utf-8')
            say('EVIDENCE_MANIFEST',str(report))
    if evidence['status']!='RAD2_SYNTHETIC_GATE_GREEN':
        raise RuntimeError('ORTHANC_SYNTHETIC_GATE_NOT_GREEN')
    say('ORTHANC_DICOMWEB_PERSISTENCE_GATE','PASS')
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as e:
        say('GATE_STOP_REASON',f'{type(e).__name__}:{e}')
        raise SystemExit(1)
