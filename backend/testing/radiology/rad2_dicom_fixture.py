"""RAD2 synthetic Secondary Capture DICOM generator and strict identity parser.

All values are fabricated for isolated PACS tests. Never use patient images.
Only explicit VR little-endian Part 10 is generated/accepted in RAD2.
"""
from __future__ import annotations

import hashlib
import struct
import uuid
from dataclasses import dataclass

SOP_CLASS = '1.2.840.10008.5.1.4.1.1.7'  # Secondary Capture
TRANSFER_SYNTAX = '1.2.840.10008.1.2.1'
IMPLEMENTATION_UID = '2.25.323700472866549312045881768529156045'
LONG_VR = {'OB','OD','OF','OL','OV','OW','SQ','UC','UN','UR','UT','UV','SV'}


def fresh_uid() -> str:
    return '2.25.' + str(uuid.uuid4().int)


def element(group: int, number: int, vr: str, value: bytes | str | int) -> bytes:
    if len(vr) != 2 or not (vr.isascii() and vr.isupper()):
        raise ValueError('Invalid DICOM VR')
    if isinstance(value, str):
        value = value.encode('ascii')
        if vr == 'UI':
            value += b'\x00' if len(value) % 2 else b''
        elif len(value) % 2:
            value += b' '
    elif isinstance(value, int):
        if vr == 'US': value = struct.pack('<H', value)
        elif vr == 'UL': value = struct.pack('<I', value)
        else: raise ValueError('Integer VR unsupported')
    elif len(value) % 2:
        value += b'\x00' if vr in ('OB', 'OW', 'UN') else b' '
    tag = struct.pack('<HH', group, number) + vr.encode('ascii')
    if vr in LONG_VR:
        return tag + b'\x00\x00' + struct.pack('<I', len(value)) + value
    if len(value) >= 65536: raise ValueError('DICOM short VR oversized')
    return tag + struct.pack('<H', len(value)) + value


@dataclass(frozen=True)
class SyntheticStudy:
    study_uid: str
    series_uid: str
    sop_uid: str
    patient_id: str
    accession: str
    instance: bytes
    pixel_sha256: str


def make_study(*, run_code: str, subject: str) -> SyntheticStudy:
    if len(run_code) > 20 or not run_code.isalnum() or subject not in ('A', 'B'):
        raise ValueError('Synthetic identity invalid')
    study, series, sop = fresh_uid(), fresh_uid(), fresh_uid()
    pid = f'RAD2_SYNTH_{run_code}_{subject}'
    accession = f'RAD2{run_code[:12]}{subject}'
    pixels = bytes([(x * (7 if subject == 'A' else 11) + 13) % 256 for x in range(64)])
    meta_elements = [
        element(0x0002,0x0001,'OB',b'\x00\x01'),
        element(0x0002,0x0002,'UI',SOP_CLASS),
        element(0x0002,0x0003,'UI',sop),
        element(0x0002,0x0010,'UI',TRANSFER_SYNTAX),
        element(0x0002,0x0012,'UI',IMPLEMENTATION_UID),
        element(0x0002,0x0013,'SH','CSP_RAD2_SYNTH'),
    ]
    meta = element(0x0002,0x0000,'UL',sum(map(len,meta_elements))) + b''.join(meta_elements)
    # DICOM data elements are encoded in ascending tag order.
    elements = [
        element(0x0008,0x0012,'DA','20300102'),
        element(0x0008,0x0013,'TM','123456'),
        element(0x0008,0x0016,'UI',SOP_CLASS),
        element(0x0008,0x0018,'UI',sop),
        element(0x0008,0x0020,'DA','20300102'),
        element(0x0008,0x0023,'DA','20300102'),
        element(0x0008,0x0030,'TM','123456'),
        element(0x0008,0x0033,'TM','123456'),
        element(0x0008,0x0050,'SH',accession),
        element(0x0008,0x0060,'CS','OT'),
        element(0x0008,0x0064,'CS','WSD'),
        element(0x0008,0x0070,'LO','CSP SYNTHETIC ONLY'),
        element(0x0008,0x0090,'PN',''),
        element(0x0010,0x0010,'PN','RAD2^SYNTHETIC'),
        element(0x0010,0x0020,'LO',pid),
        element(0x0010,0x0030,'DA',''),
        element(0x0010,0x0040,'CS',''),
        element(0x0020,0x000D,'UI',study),
        element(0x0020,0x000E,'UI',series),
        element(0x0020,0x0010,'SH','RAD2'),
        element(0x0020,0x0011,'IS','1'),
        element(0x0020,0x0013,'IS','1'),
        element(0x0028,0x0002,'US',1),
        element(0x0028,0x0004,'CS','MONOCHROME2'),
        element(0x0028,0x0010,'US',8),
        element(0x0028,0x0011,'US',8),
        element(0x0028,0x0100,'US',8),
        element(0x0028,0x0101,'US',8),
        element(0x0028,0x0102,'US',7),
        element(0x0028,0x0103,'US',0),
        element(0x7FE0,0x0010,'OB',pixels),
    ]
    instance = b'\x00' * 128 + b'DICM' + meta + b''.join(elements)
    result = SyntheticStudy(study,series,sop,pid,accession,instance,hashlib.sha256(pixels).hexdigest())
    assert_identity(instance,result)
    return result


def parse_tags(instance: bytes) -> dict[tuple[int, int], bytes]:
    if not isinstance(instance, bytes) or len(instance) < 160 or instance[128:132] != b'DICM':
        raise ValueError('Invalid DICOM Part 10 file')
    output = {}
    idx = 132
    while idx + 8 <= len(instance):
        group, number = struct.unpack_from('<HH',instance,idx)
        vr = instance[idx+4:idx+6].decode('ascii',errors='strict')
        if not vr.isupper() or len(vr)!=2: raise ValueError('Invalid VR')
        if vr in LONG_VR:
            if idx + 12 > len(instance): raise ValueError('Truncated long VR')
            length = struct.unpack_from('<I',instance,idx+8)[0]
            idx += 12
        else:
            length = struct.unpack_from('<H',instance,idx+6)[0]
            idx += 8
        if length == 0xFFFFFFFF or idx + length > len(instance):
            raise ValueError('Truncated/indefinite-length DICOM not supported')
        tag = (group,number)
        if tag in output: raise ValueError('Duplicate DICOM tag')
        output[tag]=instance[idx:idx+length]
        idx += length
    if idx != len(instance):raise ValueError('Unexpected trailing DICOM bytes')
    return output


def decoded(tags, tag):
    if tag not in tags: raise ValueError('Missing DICOM identity tag')
    return tags[tag].rstrip(b'\x00 ').decode('ascii',errors='strict')


def assert_identity(instance: bytes, expected: SyntheticStudy):
    tags = parse_tags(instance)
    expected_pairs = {
        (0x0008,0x0016): SOP_CLASS,
        (0x0008,0x0018): expected.sop_uid,
        (0x0002,0x0003): expected.sop_uid,
        (0x0002,0x0010): TRANSFER_SYNTAX,
        (0x0020,0x000D): expected.study_uid,
        (0x0020,0x000E): expected.series_uid,
        (0x0010,0x0020): expected.patient_id,
        (0x0008,0x0050): expected.accession,
    }
    for tag,value in expected_pairs.items():
        if decoded(tags,tag)!=value:raise ValueError('Study/patient/UID provenance mismatch')
    if hashlib.sha256(tags.get((0x7FE0,0x0010),b'')).hexdigest()!=expected.pixel_sha256:
        raise ValueError('Synthetic pixel data mismatch')
    return True
