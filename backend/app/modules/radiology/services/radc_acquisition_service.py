"""Staged single-instance SC acquisition using B01 identity, B02A archive and RAD4.
No diagnostic verification, arbitrary proxy, patient image access or production ingress.
"""
import hashlib
import http.client
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile

from flask import current_app
from sqlalchemy import select
from app.extensions import db
from app.core.enums.audit_enums import AuditAction
from app.core.exceptions import ConflictError, NotFoundError
from app.core.utils.decorators import transactional
from app.modules.radiology.models.ris_model import (
    RadiologyStagedAcquisition, RadiologyStudyAssociation, RadiologyWorklistPublication,
)
from app.modules.radiology.services import radc_delivery_service as delivery, radc_worklist_service as local, ris_service as ris
from app.modules.radiology.services.radc_orthanc_transport import ArchiveUnconfirmed

CAP = 4 * 1024 * 1024
OID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{8}){4}\Z')
UID = re.compile(r'(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))+\Z')
LONG = {b'OB', b'OD', b'OF', b'OL', b'OV', b'OW', b'SQ', b'UC', b'UN', b'UR', b'UT', b'UV', b'SV'}


class InvalidStudy(ValueError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _uid(value):
    if not isinstance(value, str) or len(value) > 64 or not UID.fullmatch(value):
        raise InvalidStudy('Invalid UID')
    return value


def _oid(value):
    if not isinstance(value, str) or not OID.fullmatch(value):
        raise InvalidStudy('Invalid archive resource')
    return value


def _elements(raw, start=0):
    out = {}; last = (-1, -1); pos = start
    while pos < len(raw):
        if len(raw)-pos < 8:
            raise InvalidStudy('Malformed DICOM header')
        key = struct.unpack_from('<HH', raw, pos); vr = raw[pos+4:pos+6]
        if key <= last or not re.fullmatch(rb'[A-Z]{2}', vr):
            raise InvalidStudy('Ambiguous DICOM elements')
        last = key
        if vr in LONG:
            if len(raw)-pos < 12 or raw[pos+6:pos+8] != b'\0\0':
                raise InvalidStudy('Malformed DICOM VR')
            n = struct.unpack_from('<I', raw, pos+8)[0]; pos += 12
        else:
            n = struct.unpack_from('<H', raw, pos+6)[0]; pos += 8
        if n % 2 or pos+n > len(raw):
            raise InvalidStudy('Unsupported DICOM length')
        out[key] = (vr, raw[pos:pos+n]); pos += n
    return out


def _text(tags, key, vr):
    try:
        actual, data = tags[key]
        if actual != vr: raise InvalidStudy('Unexpected VR')
        return data.rstrip(b'\0 ').decode('ascii')
    except (KeyError, UnicodeError) as exc:
        raise InvalidStudy('Required identity absent') from exc


def _manifest(raw, p, series, sop, *, pixel_vr=b'OB'):
    if not isinstance(raw, bytes) or not 160 <= len(raw) <= CAP or raw[128:132] != b'DICM':
        raise InvalidStudy('Unsupported file')
    t = _elements(raw, 132)
    expected = {
        (0x0010,0x0010):(b'PN','RAD2^SYNTHETIC'),
        (0x0010,0x0020):(b'LO',p['patient_identifier']),
        (0x0010,0x0021):(b'LO',p['patient_issuer']),
        (0x0008,0x0050):(b'SH',p['accession']),
        (0x0020,0x000d):(b'UI',p['study_instance_uid']),
        (0x0020,0x000e):(b'UI',series),
        (0x0008,0x0018):(b'UI',sop),
    }
    if any(_text(t,k,vr)!=v for k,(vr,v) in expected.items()):
        raise InvalidStudy('Canonical identity mismatch')
    # Exact single defined-length item with Local Namespace Entity ID (UT).
    vr, seq = t.get((0x0008,0x0051),(None,b''))
    if vr != b'SQ' or len(seq)<8 or seq[:4]!=b'\xfe\xff\x00\xe0' or struct.unpack_from('<I',seq,4)[0]!=len(seq)-8:
        raise InvalidStudy('Accession issuer sequence unavailable')
    issuer = _elements(seq,8)
    if set(issuer)!={(0x0040,0x0031)} or _text(issuer,(0x0040,0x0031),b'UT')!=p['accession_issuer']:
        raise InvalidStudy('Accession issuer mismatch')
    if any(v[0]==b'SQ' and k!=(0x0008,0x0051) for k,v in t.items()):
        raise InvalidStudy('Unsupported nested sequence')
    pixel = t.get((0x7fe0,0x0010))
    if pixel is None or pixel[0]!=pixel_vr: raise InvalidStudy('Unsupported pixels')
    return sha(raw), sha(pixel[1])


def _worker(raw, p, series, sop, hashes, *, ct=False):
    cfg = current_app.config.get('RAD_C_STAGED_WORKER')
    if not isinstance(cfg,dict) or set(cfg)!={'path','sha256'}:
        raise ArchiveUnconfirmed('Pinned worker unavailable')
    path=Path(cfg['path'])
    if not path.is_absolute() or not path.is_file() or path.is_symlink() or sha(path.read_bytes())!=cfg['sha256']:
        raise ArchiveUnconfirmed('Pinned worker mismatch')
    with tempfile.TemporaryDirectory(prefix='radc-staged-') as folder:
        f=Path(folder)/'instance.dcm'; f.write_bytes(raw)
        try:
            cp=subprocess.run([str(path),'inspect-staged-ct' if ct else 'inspect-staged',str(f),p['patient_identifier'],p['accession'],p['study_instance_uid'],series,sop,*hashes,p['patient_issuer']],capture_output=True,timeout=30,check=False)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise ArchiveUnconfirmed('Worker unavailable') from exc
        if cp.returncode==4: raise InvalidStudy('Worker rejected instance')
        if cp.returncode or len(cp.stdout)>8192: raise ArchiveUnconfirmed('Worker execution unconfirmed')
        try: result=json.loads(cp.stdout)
        except ValueError as exc: raise ArchiveUnconfirmed('Worker receipt invalid') from exc
    if not isinstance(result,dict) or any(result.get(k)!=v for k,v in {
        'status':'validated_staged_ct_only' if ct else 'validated_staged_sc_only','file_sha256':hashes[0],'pixel_sha256':hashes[1],
        'dicom_rs_parser_used':True,'archive_verified':False,'clinical_association':False,
        'diagnostic_report_signed':False,'chart_released':False,'preview_generated':False,
    }.items()): raise ArchiveUnconfirmed('Worker receipt mismatch')


def _main_tags(value):
    if not isinstance(value,dict) or not isinstance(value.get('MainDicomTags'),dict):
        raise InvalidStudy('Malformed archive resource')
    return value['MainDicomTags']


def _inventory(archive,p):
    archive.check()
    study_uid=_uid(p['study_instance_uid'])
    ids=archive._request('POST','/tools/find',{'Level':'Study','Query':{'StudyInstanceUID':study_uid},'Limit':2})
    if not isinstance(ids,list) or not ids: raise ArchiveUnconfirmed('No study available')
    if len(ids)!=1: raise InvalidStudy('Ambiguous study')
    sid=_oid(ids[0]); study=archive._request('GET','/studies/'+sid)
    if not isinstance(study,dict) or study.get('ID')!=sid or _main_tags(study).get('StudyInstanceUID')!=study_uid:
        raise InvalidStudy('Study mismatch')
    series_ids=study.get('Series')
    if not isinstance(series_ids,list) or len(series_ids)!=1: raise InvalidStudy('Only single-series staged SC supported')
    rid=_oid(series_ids[0]); series=archive._request('GET','/series/'+rid)
    if not isinstance(series,dict) or series.get('ID')!=rid or series.get('ParentStudy')!=sid: raise InvalidStudy('Series mismatch')
    series_uid=_uid(_main_tags(series).get('SeriesInstanceUID'))
    instances=series.get('Instances')
    if not isinstance(instances,list) or len(instances)!=1: raise InvalidStudy('Only single-instance staged SC supported')
    iid=_oid(instances[0]); item=archive._request('GET','/instances/'+iid)
    if not isinstance(item,dict) or item.get('ID')!=iid or item.get('ParentSeries')!=rid: raise InvalidStudy('Instance mismatch')
    sop=_uid(_main_tags(item).get('SOPInstanceUID'))
    return iid,series_uid,sop


def _bytes(archive,iid):
    archive.check(); iid=_oid(iid)
    conn=http.client.HTTPConnection('127.0.0.1',archive.port,timeout=8)
    try:
        conn.request('GET','/instances/'+iid+'/file',headers={'Authorization':archive._auth,'Accept':'application/dicom','Accept-Encoding':'identity'})
        res=conn.getresponse()
        if res.status!=200: raise ArchiveUnconfirmed('Instance unavailable')
        data=res.read(CAP+1)
        if len(data)>CAP: raise InvalidStudy('Instance too large')
        return data
    except (OSError,http.client.HTTPException) as exc: raise ArchiveUnconfirmed('Instance read unconfirmed') from exc
    finally: conn.close()


def _context(exam_id,clinic_id,actor_staff_id,publication_id):
    if current_app.config.get('RAD_C_ACQUISITION_STAGED_ENABLED') is not True:
        raise NotFoundError('Staged acquisition held')
    archive=delivery._archive()
    actor=local.require_interpreting_authority(exam_id=exam_id,clinic_id=clinic_id,actor_staff_id=actor_staff_id)
    exam,order,_=local._context(exam_id,clinic_id,actor_staff_id,lock=True,owner=False)
    # Reuse delivery integrity checks with the canonical ordering doctor, NOT a caller-supplied identity.
    _,_,_,pub,row=delivery._context(exam_id,clinic_id,order.ordered_by_staff_id,publication_id,archive)
    if row is None or row.state!='acknowledged' or not delivery._current(exam,order,pub):
        raise ConflictError('Current acknowledged publication required')
    delivery._confirm(archive.get(row.external_id),row.external_id,delivery._tags(pub))
    return archive,exam,actor,pub


def _receipt(exam,row):
    return {'exam_id':exam.id,'publication_id':row.publication_id,'state':row.state,
        'staged_instance_available':row.state=='staged_validated','archive_verified':False,
        'diagnostic_report_signed':False,'ohif_browser_authorized':False}


@transactional
def validate(**args):
    archive,exam,actor,pub=_context(**args)
    existing=db.session.execute(select(RadiologyStudyAssociation).where(RadiologyStudyAssociation.study_instance_uid==pub.payload['study_instance_uid'])).scalar_one_or_none()
    if existing is not None: raise ConflictError('Existing association requires reconciliation')
    state='staged_validated'; iid=series=sop=filehash=pixelhash=None
    try:
        iid,series,sop=_inventory(archive,pub.payload)
        raw=_bytes(archive,iid)
        filehash,pixelhash=_manifest(raw,pub.payload,series,sop)
        _worker(raw,pub.payload,series,sop,(filehash,pixelhash))
        if _inventory(archive,pub.payload)!=(iid,series,sop) or sha(_bytes(archive,iid))!=filehash:
            raise InvalidStudy('Archive changed during validation')
    except InvalidStudy:
        state='quarantined'; iid=series=sop=None
    assoc=RadiologyStudyAssociation(exam_id=exam.id,study_instance_uid=pub.payload['study_instance_uid'],
        match_status='unverified' if state=='staged_validated' else 'quarantined',source='radc_staged_sc')
    db.session.add(assoc); db.session.flush()
    row=RadiologyStagedAcquisition(publication_id=pub.id,association_id=assoc.id,archive_key=archive.archive_key,
        instance_id=iid,series_uid=series,sop_uid=sop,file_sha256=filehash,pixel_sha256=pixelhash,state=state,actor_staff_id=actor.id)
    db.session.add(row);db.session.flush()
    ris._audit(actor,exam.id,AuditAction.CREATE,'Staged SC validation decision',{'publication_id':pub.id,'state':state})
    return _receipt(exam,row)


@transactional
def retrieve(**args):
    archive,exam,actor,pub=_context(**args)
    row=db.session.get(RadiologyStagedAcquisition,pub.id)
    if row is None or row.state!='staged_validated' or row.archive_key!=archive.archive_key:
        raise NotFoundError('Staged instance unavailable')
    assoc=db.session.get(RadiologyStudyAssociation,row.association_id)
    if assoc is None or assoc.exam_id!=exam.id or assoc.match_status!='unverified' or assoc.source!='radc_staged_sc' or assoc.study_instance_uid!=pub.payload['study_instance_uid']:
        raise NotFoundError('Staged association unavailable')
    try:
        if _inventory(archive,pub.payload)!=(row.instance_id,row.series_uid,row.sop_uid): raise InvalidStudy('Inventory changed')
        raw=_bytes(archive,row.instance_id)
        if _manifest(raw,pub.payload,row.series_uid,row.sop_uid)!=(row.file_sha256,row.pixel_sha256): raise InvalidStudy('Content changed')
    except InvalidStudy as exc: raise NotFoundError('Staged instance unavailable') from exc
    ris._audit(actor,exam.id,AuditAction.VIEW,'Staged SC instance retrieval',{'publication_id':pub.id})
    return raw
