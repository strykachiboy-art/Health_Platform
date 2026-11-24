#!/usr/bin/env python3
"""RAD5 synthetic OHIF viewer + scoped DICOMweb proxy real Docker gate.

No backend app/database, no real patient material, no interactive service retained.
Uses two freshly owned test containers and a disposable Orthanc volume only.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from rad2_dicom_fixture import assert_identity, make_study
from rad5_gateway import TestGateway

ORTHANC_IMAGE='orthancteam/orthanc:26.11.0'
OHIF_IMAGE='ohif/app:v3.13.14'
LABEL='csp.rad5.scope'
RUN_LABEL='csp.rad5.run'
ROOT=Path(__file__).resolve().parent


def say(key,value='PASS'):
    print('RAD5_'+key+'='+str(value),flush=True)


def require(cond,reason):
    if not cond: raise RuntimeError(reason)


def cmd(argv,*,timeout=40,required=True):
    try:
        p=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise RuntimeError('DOCKER_COMMAND_FAILED_OR_TIMEOUT:'+argv[0]) from exc
    if required and p.returncode:
        raise RuntimeError('DOCKER_COMMAND_FAILED:'+str(argv[:3]))
    return p


def docker_json(args,*,missing_ok=False):
    p=cmd(['docker',*args],required=False,timeout=25)
    if p.returncode:
        message=p.stderr.decode('utf8','replace').lower()
        if missing_ok and any(x in message for x in ('no such object','no such container','no such volume')):
            return None
        raise RuntimeError('DOCKER_INSPECT_ERROR_NOT_PROVEN_ABSENT')
    try:return json.loads(p.stdout)
    except ValueError as exc:raise RuntimeError('MALFORMED_DOCKER_JSON') from exc


def loopback_port():
    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    s.bind(('127.0.0.1',0));p=s.getsockname()[1];s.close()
    return p


def image_id(img):
    p=cmd(['docker','image','inspect','--format','{{.Id}}',img],required=False)
    require(p.returncode==0,'PINNED_IMAGE_MISSING:'+img)
    v=p.stdout.decode().strip()
    require(v.startswith('sha256:'),'MALFORMED_IMAGE_ID')
    return v


def owned(name,kind,run,image_id_str=None):
    record=docker_json([kind,'inspect',name],missing_ok=True)
    if record is None:return None
    require(isinstance(record,list) and len(record)==1,'AMBIGUOUS_DOCKER_RESOURCE')
    row=record[0]; labels=(row.get('Config',{}).get('Labels') if kind=='container' else row.get('Labels')) or {}
    require(labels.get(LABEL)=='synthetic' and labels.get(RUN_LABEL)==run,'RESOURCE_OWNER_MISMATCH:'+name)
    if image_id_str:
        require(row.get('Image')==image_id_str,'RESOURCE_IMAGE_MISMATCH:'+name)
        ports=(row.get('NetworkSettings',{}).get('Ports') or {})
        for bindings in ports.values():
            for item in bindings or []:
                require(item.get('HostIp')=='127.0.0.1','RESOURCE_EXPOSED_OFF_LOOPBACK:'+name)
    return row


def cleanup(names,volume,run,images):
    # Never remove a resource if labels or image identity differs.
    for name,img in names:
        row=owned(name,'container',run,images[img])
        if row is not None:
            cmd(['docker','stop','--time','5',name],required=False,timeout=35)
            for _ in range(40):
                if owned(name,'container',run,images[img]) is None:break
                time.sleep(.25)
            else:
                # If --rm removal did not finish, remove only our verified container.
                owned(name,'container',run,images[img])
                cmd(['docker','rm','-f',name],timeout=35)
        for _ in range(40):
            if owned(name,'container',run,images[img]) is None:break
            time.sleep(.25)
        else:raise RuntimeError('OWNED_CONTAINER_CLEANUP_INCOMPLETE:'+name)
    say('TWO_OWNED_TEST_CONTAINERS_REMOVED_OR_ABSENT')
    if owned(volume,'volume',run) is not None:
        active=cmd(['docker','ps','-aq','--filter','volume='+volume]).stdout.strip()
        require(not active,'TEST_VOLUME_STILL_IN_USE_NO_REMOVE')
        cmd(['docker','volume','rm',volume],timeout=35)
    for _ in range(30):
        if owned(volume,'volume',run) is None:break
        time.sleep(.25)
    else:raise RuntimeError('OWNED_VOLUME_CLEANUP_INCOMPLETE')
    say('OWNED_ORTHANC_TEST_VOLUME_REMOVED_OR_ABSENT')


def orthanc_request(port,path,*,auth=None,body=None,method=None,ctype=None):
    h={'Accept':'application/dicom+json'}
    if auth:h['Authorization']=auth
    if ctype:h['Content-Type']=ctype
    r=urllib.request.Request('http://127.0.0.1:'+str(port)+path, data=body,
         headers=h, method=method or ('POST' if body is not None else 'GET'))
    try:
        with urllib.request.urlopen(r,timeout=12) as response:
            return response.status,response.read(18*1024*1024+1),response.headers.get('Content-Type','')
    except urllib.error.HTTPError as exc:
        return exc.code,exc.read(4096),exc.headers.get('Content-Type','')


def gateway_request(port,path,*,user=None,password=None,method='GET',host=None,accept=None):
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=18)
    h={'Host':host or ('127.0.0.1:'+str(port))}
    if user is not None:
        token=base64.b64encode((user+':'+password).encode()).decode()
        h['Authorization']='Basic '+token
    if accept:h['Accept']=accept
    try:
        conn.request(method,path,headers=h)
        res=conn.getresponse()
        status=res.status
        data=res.read(18*1024*1024+1)
        headers=dict(res.getheaders())
    finally:
        conn.close()
    require(len(data)<=18*1024*1024,'GATEWAY_RESPONSE_EXCEEDS_CAP')
    return status,data,headers


def basic(user,passwd):
    return 'Basic '+base64.b64encode((user+':'+passwd).encode()).decode()


def wait_orthanc(port,auth):
    for _ in range(90):
        try:
            code,data,_=orthanc_request(port,'/system',auth=auth)
            if code==200 and json.loads(data).get('Name')=='CSP-RAD5-SYNTHETIC-ONLY':return
        except (OSError,ValueError,TimeoutError):pass
        time.sleep(1)
    raise RuntimeError('ORTHANC_DID_NOT_START_WITH_EXPECTED_POLICY')


def wait_ohif(port):
    for _ in range(75):
        try:
            code,data,_=orthanc_request(port,'/')
            if code==200 and (b'<html' in data.lower() or b'<!doctype' in data.lower()):return
        except (OSError,ValueError,TimeoutError):pass
        time.sleep(1)
    raise RuntimeError('OHIF_DID_NOT_SERVE_HTML')


def stow(port,auth,study):
    boundary='RAD5'+secrets.token_hex(8)
    payload=(b'--'+boundary.encode()+b'\r\nContent-Type: application/dicom\r\n\r\n'+
             study.instance+b'\r\n--'+boundary.encode()+b'--\r\n')
    code,_,_=orthanc_request(port,'/dicom-web/studies',auth=auth,body=payload,method='POST',
          ctype='multipart/related; type="application/dicom"; boundary='+boundary)
    require(code in (200,202),'SYNTHETIC_STOW_FAILED')
    code,data,_=orthanc_request(port,'/dicom-web/studies?StudyInstanceUID='+study.study_uid,auth=auth)
    require(code==200 and len(json.loads(data))==1,'SYNTHETIC_STOW_NOT_QUERYABLE')


def start_orthanc(name,volume,config_path,port,run):
    cmd(['docker','run','-d','--rm','--pull=never','--name',name,'--label',LABEL+'=synthetic',
         '--label',RUN_LABEL+'='+run,'--memory','640m','--cpus','1',
         '-p',f'127.0.0.1:{port}:8042',
         '--mount',f'type=volume,source={volume},target=/var/lib/orthanc/db',
         '--mount',f'type=bind,source={config_path},target=/etc/orthanc/rad5-isolated.json,readonly',
         '--env','DICOM_WEB_PLUGIN_ENABLED=true', ORTHANC_IMAGE],timeout=75)


def start_ohif(name,config_path,port,run):
    cmd(['docker','run','-d','--rm','--pull=never','--name',name,'--label',LABEL+'=synthetic',
         '--label',RUN_LABEL+'='+run,'--memory','384m','--cpus','1',
         '-p',f'127.0.0.1:{port}:80',
         '--mount',f'type=bind,source={config_path},target=/usr/share/nginx/html/app-config.js,readonly',
         OHIF_IMAGE],timeout=75)


def validate(orthanc_port,ohif_port,run,admin,adminpw,auth):
    wait_orthanc(orthanc_port,auth)
    wait_ohif(ohif_port)
    say('ORTHANC_AND_PINNED_OHIF_CONTAINERS_READY')
    code,pl,_=orthanc_request(orthanc_port,'/plugins',auth=auth)
    require(code==200 and 'dicom-web' in json.loads(pl),'DICOMWEB_PLUGIN_ABSENT')
    code,_,_=orthanc_request(orthanc_port,'/system')
    require(code in (401,403),'ORTHANC_UNAUTHENTICATED_ACCESS_OPEN')
    say('ORTHANC_NATIVE_API_AUTH_REQUIRED_AND_DICOMWEB_ACTIVE')
    study_a=make_study(run_code=run,subject='A')
    study_b=make_study(run_code=run,subject='B')
    stow(orthanc_port,auth,study_a)
    stow(orthanc_port,auth,study_b)
    say('TWO_SYNTHETIC_STUDIES_IN_PRIVATE_PACS')
    secrets_map={'A':secrets.token_urlsafe(26),'B':secrets.token_urlsafe(26)}
    scopes={'A':study_a.study_uid,'B':study_b.study_uid}
    gateway=TestGateway(0,orthanc_port=orthanc_port,ohif_port=ohif_port,
                        orthanc_auth=auth,users=secrets_map,studies=scopes)
    gp=gateway.server_port
    worker=threading.Thread(target=gateway.serve_forever,daemon=True)
    worker.start()
    positive=0; negative=0
    try:
        def hit(subject,path,expected,method='GET',accept=None,host=None):
            nonlocal positive,negative
            user='rad5-'+subject if subject else None
            pw=secrets_map[subject] if subject else None
            code,data,headers=gateway_request(gp,path,user=user,password=pw,
                                                method=method,accept=accept,host=host)
            require(code==expected,'SCOPED_GATE_STATUS_MISMATCH:'+str(code)+':'+str(expected))
            if code==200:positive+=1
            else:negative+=1
            return data,headers
        h,heads=hit('A','/',200)
        require(b'<html' in h.lower() or b'<!doctype' in h.lower(),'NO_OHIF_HTML_VIA_PROXY')
        cfg,_=hit('A','/app-config.js',200)
        require(b"qidoRoot: '/dicom-web'" in cfg,'OHIF_SAME_ORIGIN_CONFIG_NOT_SERVED')
        require(b'orthanc:' not in cfg and b'Bearer ' not in cfg,'OHIF_CONFIG_LEAK')
        # OHIF's documented deep-link is sent without a JWT/token in the URL.
        page,_=hit('A','/viewer?StudyInstanceUIDs='+study_a.study_uid,200)
        require(len(page)>100,'OHIF_DEEP_LINK_NOT_SERVABLE')
        say('AUTHENTICATED_OHIF_STATIC_AND_STUDY_DEEP_LINK=PASS')
        hit(None,'/',401)
        hit(None,'/dicom-web/studies?StudyInstanceUID='+study_a.study_uid,401)
        bad_code,_,_=gateway_request(gp,'/dicom-web/studies?StudyInstanceUID='+study_a.study_uid,
                        user='rad5-A',password='wrong')
        require(bad_code==401,'INCORRECT_GATEWAY_PASSWORD_ALLOWED');negative+=1
        for path in ('/system','/instances','/plugins','/pacs','/dicom-web/studies',
                     '/dicom-web/studies?PatientID=synthetic',
                     '/dicom-web/studies?StudyInstanceUID='+study_b.study_uid,
                     '/dicom-web/studies/'+study_b.study_uid+'/metadata',
                     '/dicom-web/studies/'+study_b.study_uid+'/series/'+study_b.series_uid+'/instances/'+study_b.sop_uid,
                     '/dicom-web/studies/'+study_a.study_uid+'/../'+study_b.study_uid,
                     '/dicom-web/studies?StudyInstanceUID='+study_a.study_uid+'&StudyInstanceUID='+study_b.study_uid):
            hit('A',path,404)
        hit('A','/dicom-web/studies',405,method='POST')
        hit('A','/dicom-web/studies?StudyInstanceUID='+study_a.study_uid,404,host='evil.example')
        say('UNAUTHENTICATED_UNSCOPED_CROSS_STUDY_AND_WRITE_DENIAL=PASS')
        for subject,study in (('A',study_a),('B',study_b)):
            path='/dicom-web/studies?StudyInstanceUID='+study.study_uid
            body,_=hit(subject,path,200)
            rows=json.loads(body)
            require(isinstance(rows,list) and len(rows)==1 and
                    rows[0]['0020000D']['Value']==[study.study_uid], 'QIDO_STUDY_SCOPE_WRONG')
            relative=(f'/dicom-web/studies/{study.study_uid}/series/{study.series_uid}'
                      f'/instances/{study.sop_uid}')
            content,heads=hit(subject,relative,200,accept='multipart/related; type="application/dicom"')
            # Orthanc may return raw DICOM or a multipart envelope; use RAD2 robust parser.
            from email import policy as email_policy
            from email.parser import BytesParser
            ctype=next((v for k,v in heads.items() if k.lower()=='content-type'),'')
            if ctype.lower().startswith('multipart/related'):
                msg=BytesParser(policy=email_policy.default).parsebytes(
                    b'MIME-Version: 1.0\r\nContent-Type: '+ctype.encode()+b'\r\n\r\n'+content)
                parts=list(msg.iter_parts());require(len(parts)==1,'WADO_MULTIPART_COUNT')
                content=parts[0].get_payload(decode=True)
            require(ctype.lower().startswith(('multipart/related','application/dicom')),
                    'GATEWAY_INVALID_DICOM_CONTENT_TYPE')
            assert_identity(content,study)
        say('STUDY_SCOPED_QIDO_WADO_AND_PIXEL_DIGESTS=PASS')
        # Cross-account misuse must not serve another study, regardless of known SOP UID.
        for subject,foreign in (('A',study_b),('B',study_a)):
            hit(subject, f'/dicom-web/studies/{foreign.study_uid}/series/{foreign.series_uid}/instances/{foreign.sop_uid}',404)
        say('SWAPPED_STUDY_SERIES_INSTANCE_UID_DENIAL=PASS')
        return {
            'status':'RAD5_SYNTHETIC_OHIF_SCOPE_GATE_GREEN',
            'synthetic_only':True,'ohif_static_html_proved':True,
            'browser_rendering_verified':False,'diagnostic_use_authorized':False,
            'real_clinical_study_authorized':False,
            'clinical_release_enabled':False,'flask_app_mutated':False,
            'gateway_positive_http_checks':positive,'gateway_negative_http_checks':negative,
            'study_digests':[hashlib.sha256(x.instance).hexdigest() for x in (study_a,study_b)],
            'two_distinct_synthetic_studies':study_a.study_uid!=study_b.study_uid,
            'image_source':'official pinned tags',
        }
    finally:
        gateway.shutdown();worker.join(timeout=10);gateway.server_close()
        require(not worker.is_alive(),'GATEWAY_DID_NOT_SHUT_DOWN')
        say('EPHEMERAL_TEST_GATEWAY_STOPPED')


def main(evidence_dir):
    run=secrets.token_hex(5)
    names=[('csp-rad5-orthanc-'+run,ORTHANC_IMAGE),('csp-rad5-ohif-'+run,OHIF_IMAGE)]
    volume='csp-rad5-orthanc-db-'+run
    images={x:image_id(x) for x in (ORTHANC_IMAGE,OHIF_IMAGE)}
    say('PINNED_ORTHANC_IMAGE_ID',images[ORTHANC_IMAGE])
    say('PINNED_OHIF_IMAGE_ID',images[OHIF_IMAGE])
    orthoport=loopback_port();ohifport=loopback_port()
    require(orthoport!=ohifport,'PORT_COLLISION')
    admin='rad5admin';adminpw=secrets.token_urlsafe(27)
    auth=basic(admin,adminpw)
    # Windows Docker Desktop file bind is correct using the absolute host path.
    report=None
    created=False
    with tempfile.TemporaryDirectory(prefix='csp_rad5_') as temp:
        config=Path(temp)/'orthanc_rad5.json'
        config.write_text(json.dumps({
            'Name':'CSP-RAD5-SYNTHETIC-ONLY','HttpServerEnabled':True,'HttpPort':8042,
            'RemoteAccessAllowed':True,'AuthenticationEnabled':True,
            'RegisteredUsers':{admin:adminpw},'DicomServerEnabled':False,
            'DicomAlwaysAllowStore':False,'DicomAlwaysAllowFind':False,
            'DicomAlwaysAllowMove':False,'OverwriteInstances':False,
            'StorageDirectory':'/var/lib/orthanc/db','MaximumStorageSize':128,
            'DicomWeb':{'Enable':True,'Root':'/dicom-web/'}
        }),encoding='utf8')
        try:
            cmd(['docker','volume','create','--label',LABEL+'=synthetic',
                 '--label',RUN_LABEL+'='+run,volume])
            created=True
            say('OWNED_DISPOSABLE_ORTHANC_VOLUME_CREATED')
            start_orthanc(names[0][0],volume,str(config.resolve()),orthoport,run)
            start_ohif(names[1][0],str((ROOT/'ohif_rad5_config.js').resolve()),ohifport,run)
            for name,img in names:
                row=owned(name,'container',run,images[img])
                require(row is not None,'OWNED_TEST_CONTAINER_NOT_STARTED:'+name)
            say('TWO_TEST_CONTAINERS_LOOPBACK_ONLY_AND_OWNED')
            report=validate(orthoport,ohifport,run,admin,adminpw,auth)
        finally:
            if created:
                cleanup(names,volume,run,images)
    require(report is not None,'RAD5_GATE_DID_NOT_COMPLETE')
    report['cleanup']='PASS'
    report['orthanc_image_id']=images[ORTHANC_IMAGE]
    report['ohif_image_id']=images[OHIF_IMAGE]
    report['timestamp_utc']=datetime.now(timezone.utc).isoformat()
    evidence_dir.mkdir(parents=True,exist_ok=False)
    out=evidence_dir/('RAD5_SYNTHETIC_EVIDENCE_'+run+'.json')
    out.write_text(json.dumps(report,indent=2,sort_keys=True),encoding='utf8')
    say('EVIDENCE_MANIFEST',str(out))
    say('REAL_ORTHANC_OHIF_GATE')


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--evidence-dir',type=Path,required=True)
    args=p.parse_args()
    try:main(args.evidence_dir)
    except Exception as exc:
        say('STOP_REASON',type(exc).__name__+':'+str(exc))
        raise SystemExit(1)
