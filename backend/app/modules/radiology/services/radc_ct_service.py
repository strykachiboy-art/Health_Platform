"""Staged CT inventory and bounded DICOMweb reads for an authenticated viewer.
No arbitrary upstream forwarding, browser credential handoff or diagnostic approval.
"""
import json
import math
import re
import struct
import uuid

from flask import current_app
from sqlalchemy import select
from app.extensions import db
from app.core.enums.audit_enums import AuditAction
from app.core.exceptions import ConflictError, NotFoundError
from app.core.utils.decorators import transactional
from app.modules.radiology.models.ris_model import RadiologyStudyAssociation, RadiologyStagedCTStudy
from app.modules.radiology.services import radc_acquisition_service as acq, radc_worklist_service as local, ris_service as ris
from app.modules.radiology.services.radc_orthanc_transport import ArchiveUnconfirmed

CT='1.2.840.10008.5.1.4.1.1.2'
TS='1.2.840.10008.1.2.1'
MAX_INSTANCES=16
MAX_TOTAL=16*1024*1024
InvalidStudy=acq.InvalidStudy


def _context(**args):
    if not current_app.testing or current_app.config.get('RAD_C_CT_VIEWER_STAGED_ENABLED') is not True:
        raise NotFoundError('Staged CT viewer held')
    archive,exam,actor,pub=acq._context(**args)
    if pub.payload.get('modality')!='CT': raise ConflictError('CT scheduling required')
    return archive,exam,actor,pub


def _inventory(archive,p):
    archive.check(); uid=acq._uid(p['study_instance_uid'])
    ids=archive._request('POST','/tools/find',{'Level':'Study','Query':{'StudyInstanceUID':uid},'Limit':2})
    if not isinstance(ids,list) or not ids: raise ArchiveUnconfirmed('No CT study available')
    if len(ids)!=1: raise InvalidStudy('Ambiguous study')
    sid=acq._oid(ids[0]); study=archive._request('GET','/studies/'+sid)
    if not isinstance(study,dict) or study.get('ID')!=sid or acq._main_tags(study).get('StudyInstanceUID')!=uid:
        raise InvalidStudy('Study identity mismatch')
    rs=study.get('Series')
    if not isinstance(rs,list) or not 1<=len(rs)<=4 or len(set(map(acq._oid,rs)))!=len(rs): raise InvalidStudy('Unsupported series inventory')
    out=[]; series_seen=set(); sop_seen=set(); iid_seen=set()
    for rid in rs:
        row=archive._request('GET','/series/'+rid)
        if not isinstance(row,dict) or row.get('ID')!=rid or row.get('ParentStudy')!=sid: raise InvalidStudy('Series identity mismatch')
        series=acq._uid(acq._main_tags(row).get('SeriesInstanceUID'))
        if series in series_seen: raise InvalidStudy('Duplicate series UID')
        series_seen.add(series); ii=row.get('Instances')
        if not isinstance(ii,list) or not 1<=len(ii)<=MAX_INSTANCES or len(out)+len(ii)>MAX_INSTANCES: raise InvalidStudy('Unsupported instance inventory')
        for iid in ii:
            iid=acq._oid(iid); item=archive._request('GET','/instances/'+iid)
            if not isinstance(item,dict) or item.get('ID')!=iid or item.get('ParentSeries')!=rid: raise InvalidStudy('Instance identity mismatch')
            sop=acq._uid(acq._main_tags(item).get('SOPInstanceUID'))
            if iid in iid_seen or sop in sop_seen: raise InvalidStudy('Duplicate instance identity')
            iid_seen.add(iid); sop_seen.add(sop); out.append((iid,series,sop))
    return sorted(out)


def _us(t,k):
    vr,data=t.get(k,(None,b''))
    if vr!=b'US' or len(data)!=2: raise InvalidStudy('Invalid image dimensions')
    return struct.unpack('<H',data)[0]


def _ds(t,k,n):
    try:
        values=[float(x) for x in acq._text(t,k,b'DS').split('\\')]
    except ValueError as exc: raise InvalidStudy('Invalid CT geometry') from exc
    if len(values)!=n or not all(math.isfinite(x) and abs(x)<=1000000 for x in values): raise InvalidStudy('Invalid CT geometry')
    return values


def _inspect(raw,p,series,sop):
    hashes=acq._manifest(raw,p,series,sop,pixel_vr=b'OW')
    t=acq._elements(raw,132)
    for k,vr,value in [((2,2),b'UI',CT),((2,3),b'UI',sop),((2,16),b'UI',TS),((8,22),b'UI',CT),((8,96),b'CS','CT'),((40,4),b'CS','MONOCHROME2')]:
        if acq._text(t,k,vr)!=value: raise InvalidStudy('Unsupported CT format')
    for k,n in [((40,2),1),((40,256),16),((40,257),16),((40,258),15),((40,259),1)]:
        if _us(t,k)!=n: raise InvalidStudy('Unsupported CT pixel format')
    rows,cols=_us(t,(40,16)),_us(t,(40,17))
    if not 1<=rows<=512 or not 1<=cols<=512 or len(t[(0x7fe0,0x10)][1])!=rows*cols*2: raise InvalidStudy('Unsupported CT pixel length')
    if (40,8) in t and acq._text(t,(40,8),b'IS')!='1': raise InvalidStudy('Multi-frame CT held')
    orient=_ds(t,(32,55),6); position=_ds(t,(32,50),3); spacing=_ds(t,(40,48),2)
    # Deliberately axial, no oblique or nonuniform orientation claim.
    if orient!=[1,0,0,0,1,0] or min(spacing)<=0: raise InvalidStudy('Unsupported CT geometry')
    slope=_ds(t,(40,4179),1)[0]; intercept=_ds(t,(40,4178),1)[0]
    if slope<=0: raise InvalidStudy('Unsupported CT rescale')
    frame=acq._uid(acq._text(t,(32,82),b'UI'))
    return {'file_sha256':hashes[0],'pixel_sha256':hashes[1],'rows':rows,'columns':cols,'position':position,'spacing':spacing,'frame':frame,'slope':slope,'intercept':intercept},t


def _snapshot(archive,p,*,worker=False):
    inventory=_inventory(archive,p); out=[]; files={}; geometry={}; positions=set(); total=0
    for iid,series,sop in inventory:
        raw=acq._bytes(archive,iid);total+=len(raw)
        if total>MAX_TOTAL: raise InvalidStudy('CT study exceeds size bound')
        info,t=_inspect(raw,p,series,sop)
        shape=(info['rows'],info['columns'],info['spacing'],info['frame'],info['slope'],info['intercept'],info['position'][:2])
        z=(series,info['position'][2])
        if (series in geometry and geometry[series]!=shape) or z in positions: raise InvalidStudy('Inconsistent CT series geometry')
        geometry[series]=shape;positions.add(z)
        if worker: acq._worker(raw,p,series,sop,(info['file_sha256'],info['pixel_sha256']),ct=True)
        out.append(dict(info,instance_id=iid,series_uid=series,sop_uid=sop));files[sop]=(raw,t)
    if not out or _inventory(archive,p)!=inventory: raise InvalidStudy('Archive inventory changed')
    return out,files


@transactional
def validate(**args):
    archive,exam,actor,pub=_context(**args)
    existing=db.session.execute(select(RadiologyStudyAssociation).where(RadiologyStudyAssociation.study_instance_uid==pub.payload['study_instance_uid'])).scalar_one_or_none()
    if existing is not None: raise ConflictError('Existing association requires reconciliation')
    state='staged_validated';manifest=[];digest=None
    try:
        manifest,_=_snapshot(archive,pub.payload,worker=True)
        if _snapshot(archive,pub.payload)[0]!=manifest: raise InvalidStudy('Archive changed during verification')
        digest=local._digest(manifest)
    except InvalidStudy:
        manifest=[];state='quarantined'
    assoc=RadiologyStudyAssociation(exam_id=exam.id,study_instance_uid=pub.payload['study_instance_uid'],match_status='unverified' if state=='staged_validated' else 'quarantined',source='radc_staged_ct')
    db.session.add(assoc);db.session.flush()
    db.session.add(RadiologyStagedCTStudy(publication_id=pub.id,association_id=assoc.id,archive_key=archive.archive_key,state=state,manifest=manifest,manifest_sha256=digest,instance_count=len(manifest),actor_staff_id=actor.id))
    db.session.flush();ris._audit(actor,exam.id,AuditAction.CREATE,'Staged CT validation decision',{'publication_id':pub.id,'state':state,'instance_count':len(manifest)})
    return {'exam_id':exam.id,'publication_id':pub.id,'state':state,'instance_count':len(manifest),'study_instance_uid':pub.payload['study_instance_uid'] if manifest else None,'archive_verified':False,'diagnostic_report_signed':False,'ohif_browser_authorized':False}


# Explicit bounded DICOM JSON metadata; no URLs from Orthanc, no private tags,
# no embedded pixels, no free-text patient attributes other than synthetic name.
_META={(8,22),(8,24),(8,32),(8,48),(8,80),(8,96),(16,16),(16,32),(16,33),(32,13),(32,14),(32,16),(32,17),(32,19),(32,50),(32,55),(32,82),(40,2),(40,4),(40,8),(40,16),(40,17),(40,48),(40,256),(40,257),(40,258),(40,259),(40,4176),(40,4177),(40,4178),(40,4179),(24,80)}


def _metadata(t):
    result={}
    for k in sorted(_META & set(t)):
        vr,data=t[k]; name=vr.decode('ascii')
        if vr==b'US': values=[_us(t,k)]
        elif vr==b'DS': values=_ds(t,k,len(acq._text(t,k,b'DS').split('\\')))
        elif vr==b'IS':
            try: values=[int(v) for v in acq._text(t,k,b'IS').split('\\')]
            except ValueError as exc: raise InvalidStudy('Invalid numeric metadata') from exc
        elif vr==b'PN': values=[{'Alphabetic':acq._text(t,k,vr)}]
        elif vr in (b'UI',b'SH',b'LO',b'CS',b'DA',b'TM'): values=acq._text(t,k,vr).split('\\')
        else: raise InvalidStudy('Unsupported metadata VR')
        result[f'{k[0]:04X}{k[1]:04X}']={'vr':name,'Value':values}
    return result


def _json(rows): return json.dumps(rows,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode('ascii'),'application/dicom+json'


def _multipart(raw,ctype):
    boundary='radc-'+uuid.uuid4().hex
    head=f'--{boundary}\r\nContent-Type: {ctype}\r\n\r\n'.encode('ascii')
    return head+raw+f'\r\n--{boundary}--\r\n'.encode('ascii'),f'multipart/related; type="{ctype.split(";")[0]}"; boundary={boundary}'


def _target(target,query,uid,manifest):
    if not isinstance(target,str) or len(target)>500 or not re.fullmatch(r'[A-Za-z0-9./]+',target) or '//' in target: raise NotFoundError('Viewer resource unavailable')
    parts=target.split('/')
    if parts==['descriptor'] and not query: return 'descriptor',manifest
    if parts==['studies']:
        names=[k for k,v in query]; params=dict(query)
        if names.count('StudyInstanceUID')!=1 or params.get('StudyInstanceUID')!=uid or any(k not in {'StudyInstanceUID','includefield','limit','offset'} for k in names): raise NotFoundError('Scoped study query required')
        if any(names.count(k)>1 for k in names if k!='includefield') or any(k=='includefield' and not re.fullmatch(r'all|[0-9A-Fa-f]{8}',v) for k,v in query) or params.get('offset','0')!='0' or params.get('limit','16') not in {str(i) for i in range(1,101)}: raise NotFoundError('Unsupported scoped query')
        return 'studies',manifest
    if query or parts[:2]!=['studies',uid]: raise NotFoundError('Viewer study unavailable')
    tail=parts[2:]
    if tail==['metadata']: return 'metadata',manifest
    if tail==['series']: return 'series',manifest
    if len(tail)<2 or tail[0]!='series': raise NotFoundError('Viewer resource unavailable')
    selected=[m for m in manifest if m['series_uid']==tail[1]]
    if not selected: raise NotFoundError('Viewer series unavailable')
    rest=tail[2:]
    if rest==['metadata']: return 'metadata',selected
    if rest==['instances']: return 'instances',selected
    if len(rest)<2 or rest[0]!='instances': raise NotFoundError('Viewer resource unavailable')
    selected=[m for m in selected if m['sop_uid']==rest[1]]
    if len(selected)!=1: raise NotFoundError('Viewer instance unavailable')
    if len(rest)==2: return 'instance',selected
    if rest[2:]==['metadata']: return 'metadata',selected
    if rest[2:]==['frames','1']: return 'frame',selected
    raise NotFoundError('Viewer resource unavailable')


@transactional
def view(*,target,query,**args):
    archive,exam,actor,pub=_context(**args)
    row=db.session.get(RadiologyStagedCTStudy,pub.id)
    if row is None or row.state!='staged_validated' or row.archive_key!=archive.archive_key or not isinstance(row.manifest,list) or len(row.manifest)!=row.instance_count or local._digest(row.manifest)!=row.manifest_sha256: raise NotFoundError('Staged CT inventory unavailable')
    uid=pub.payload['study_instance_uid'];assoc=db.session.get(RadiologyStudyAssociation,row.association_id)
    if assoc is None or assoc.exam_id!=exam.id or assoc.match_status!='unverified' or assoc.source!='radc_staged_ct' or assoc.study_instance_uid!=uid: raise NotFoundError('Staged CT association unavailable')
    kind,selected=_target(target,query,uid,row.manifest)
    try:
        manifest,files=_snapshot(archive,pub.payload)
        if manifest!=row.manifest: raise InvalidStudy('CT archive content changed')
        if kind=='descriptor':
            data=json.dumps({'study_instance_uid':uid,'instance_count':len(manifest),
                'dicomweb_root':f'/api/v1/radiology/exams/{exam.id}/staged-ct/{pub.id}/viewer',
                'authorization':'bearer_per_request','browser_handoff_authorized':False,
                'diagnostic_use_authorized':False},separators=(',',':')).encode('ascii'),'application/json'
        elif kind in ('instance','frame'):
            raw,t=files[selected[0]['sop_uid']]
            data=_multipart(raw,'application/dicom') if kind=='instance' else _multipart(t[(0x7fe0,0x10)][1],'application/octet-stream; transfer-syntax='+TS)
        else:
            rows=[_metadata(files[m['sop_uid']][1]) for m in selected]
            if kind=='studies':
                keys={'00080020','00080030','00080050','00100010','00100020','0020000D','00200010'}
                study={k:v for k,v in rows[0].items() if k in keys}
                study.update({'00080061':{'vr':'CS','Value':['CT']},'00201206':{'vr':'IS','Value':[len({m['series_uid'] for m in manifest})]},'00201208':{'vr':'IS','Value':[len(manifest)]}});rows=[study]
            elif kind=='series':
                keys={'00080060','0020000D','0020000E','00200011'}; unique={}
                for m,r in zip(selected,rows):
                    key=m['series_uid'];unique.setdefault(key,{k:v for k,v in r.items() if k in keys})
                for key,r in unique.items():r['00201209']={'vr':'IS','Value':[sum(m['series_uid']==key for m in manifest)]}
                rows=list(unique.values())
            data=_json(rows)
    except InvalidStudy as exc: raise NotFoundError('Staged CT inventory unavailable') from exc
    ris._audit(actor,exam.id,AuditAction.VIEW,'Staged CT viewer retrieval',{'publication_id':pub.id,'resource_kind':kind})
    return data
