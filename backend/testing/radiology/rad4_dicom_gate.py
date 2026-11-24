#!/usr/bin/env python3
"""RAD4 disposable Rust compiler + synthetic-only one-shot validator proof.

No existing Docker services, no application DB, no RIS/PACS mutation, no
network for validation. Only dependency-build container may use network.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import tomllib

BASE = Path(__file__).resolve().parents[2]
TESTING = Path(__file__).resolve().parent
CRATE = BASE / 'rust' / 'rad4_dicom_worker'
RAD3 = BASE / 'app' / 'modules' / 'radiology' / 'services' / 'rad3_mwl_contract.py'
IMAGE = 'rust:1.99.1-slim-bookworm'
SCOPE = 'csp.rad4.scope'
RID = 'csp.rad4.run'


def say(name, value='PASS'):
    print('RAD4_' + name + '=' + str(value), flush=True)


def ensure(cond, message):
    if not cond:
        raise RuntimeError(message)


def execute(argv, *, timeout=120, visible=False, required=True):
    try:
        completed = subprocess.run(argv, check=False, timeout=timeout,
            stdout=None if visible else subprocess.PIPE,
            stderr=None if visible else subprocess.PIPE)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('COMMAND_UNAVAILABLE_OR_TIMEOUT:' + str(argv[0])) from exc
    if required and completed.returncode:
        error = (completed.stderr or b'').decode('utf-8', 'replace')[:500]
        raise RuntimeError('COMMAND_FAILED:' + str(argv[:3]) + ':' + error)
    return completed


def out(argv, **kwargs):
    return execute(argv, **kwargs).stdout.decode('utf-8', 'replace').strip()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def mount(source, destination, readonly=False):
    value = str(Path(source).resolve())
    ensure(',' not in value and '\n' not in value, 'UNSAFE_DOCKER_MOUNT_PATH')
    return 'type=bind,source=' + value + ',target=' + destination + (',readonly' if readonly else '')


def docker_inspect(kind, name, missing=True):
    r = execute(['docker', kind, 'inspect', name], required=False, timeout=45)
    if r.returncode:
        txt = (r.stderr or b'').decode('utf-8', 'replace').lower()
        if missing and any(fragment in txt for fragment in (
            'no such object', 'no such container', 'no such volume')):
            return None
        raise RuntimeError('DOCKER_DAEMON_INSPECT_NOT_CONFIRMED_ABSENT:' + kind)
    obj = json.loads(r.stdout)
    ensure(isinstance(obj, list) and len(obj) == 1, 'AMBIGUOUS_DOCKER_INSPECT')
    return obj[0]


def owned(obj, run_id):
    labels = obj.get('Labels') or (obj.get('Config') or {}).get('Labels') or {}
    ensure(labels.get(SCOPE) == 'synthetic' and labels.get(RID) == run_id,
           'DOCKER_OWNERSHIP_MISMATCH')


def cleanup(name, volume, run_id, image_id):
    errors = []
    try:
        c = docker_inspect('container', name)
        if c is not None:
            owned(c, run_id)
            ensure(c.get('Image') == image_id, 'RAD4_CLEANUP_IMAGE_ID_MISMATCH')
            ensure(any(m.get('Name') == volume and m.get('Type') == 'volume'
                for m in c.get('Mounts', [])), 'RAD4_CLEANUP_STORAGE_NOT_OWNED')
            execute(['docker', 'stop', '--time', '7', name], required=False, timeout=40)
            for _ in range(48):
                if docker_inspect('container', name) is None: break
                time.sleep(.3)
            else:
                execute(['docker', 'rm', '--force', name], required=True, timeout=40)
            ensure(docker_inspect('container', name) is None, 'RAD4_CONTAINER_NOT_REMOVED')
    except Exception as exc:
        errors.append('container:' + str(exc))
    try:
        v = docker_inspect('volume', volume)
        if v is not None:
            owned(v, run_id)
            use = out(['docker', 'ps', '-aq', '--filter', 'volume=' + volume], timeout=35)
            ensure(use == '', 'RAD4_OWNED_VOLUME_IN_USE')
            execute(['docker', 'volume', 'rm', volume], timeout=45)
            for _ in range(45):
                if docker_inspect('volume', volume) is None: break
                time.sleep(.25)
            else: raise RuntimeError('RAD4_VOLUME_NOT_REMOVED')
    except Exception as exc:
        errors.append('volume:' + str(exc))
    if errors:
        raise RuntimeError('RAD4_DISPOSABLE_CLEANUP_REQUIRED:' + ';'.join(errors))
    say('DISPOSABLE_RUST_BUILD_CONTAINER_AND_VOLUME_REMOVED_OR_ABSENT')


def source_ris_intent(study, synthetic_patient, clinical_accession, clinic_id):
    spec = importlib.util.spec_from_file_location('rad4_ris_intent', RAD3)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    intent = module.CanonicalRISIntent(
        clinic_id=clinic_id, imaging_order_id=9 + clinic_id,
        exam_id=17 + clinic_id, exam_version=2, order_status='active',
        exam_status='scheduled', issuer_namespace=f'CSP-CLINIC-{clinic_id}',
        accession_number=clinical_accession, patient_id=synthetic_patient,
        study_instance_uid=study.study_uid, scheduled_step_id=23 + clinic_id,
        scheduled_step_sequence=1, scheduled_step_status='intent',
        scheduled_at=datetime(2030, 1, 2, 11, 25, tzinfo=timezone.utc),
        modality='other', station_aet='RAD4MODALITY',
    )
    mapping = module.worklist_payload(intent)
    short_acc = module.dicom_accession(intent)
    ensure(mapping['Tags']['AccessionNumber'] == short_acc,
           'RAD3_DICOM_ACCESSION_MAPPING_DRIFT')
    return short_acc


def replace_accession(study, new_acc):
    tag = b'\x08\x00\x50\x00SH'
    old = study.instance
    ensure(old.count(tag) == 1, 'AMBIGUOUS_ACCESSION_TAG')
    pos = old.index(tag)
    length = struct.unpack_from('<H', old, pos + 6)[0]
    ensure(old[pos+8:pos+8+length].rstrip(b' \x00').decode('ascii') == study.accession,
           'SOURCE_ACCESSION_UNTRUSTED')
    raw = new_acc.encode('ascii')
    if len(raw) % 2:
        raw += b' '
    instance = old[:pos+6] + struct.pack('<H', len(raw)) + raw + old[pos+8+length:]
    return replace(study, accession=new_acc, instance=instance)


def fixtures(directory):
    from rad2_dicom_fixture import make_study, assert_identity
    code = 'RAD4' + secrets.token_hex(4).upper()
    inputs = []
    for subject, clinic in (('A', 1), ('B', 2)):
        s = make_study(run_code=code, subject=subject)
        clinical = 'RAD-' + hashlib.sha256((code + subject).encode()).hexdigest()[:24].upper()
        accession = source_ris_intent(s, s.patient_id, clinical, clinic)
        s = replace_accession(s, accession)
        assert_identity(s.instance, s)
        f = directory / ('subject_' + subject.lower() + '.dcm')
        f.write_bytes(s.instance)
        expected = [s.patient_id, s.accession, s.study_uid, s.series_uid, s.sop_uid,
                    sha(s.instance), s.pixel_sha256]
        inputs.append((f, expected))
    # Invalid cases use fresh digest of their bytes so that low-level guards
    # are exercised without a conveniently wrong whole-file hash.
    path, expected = inputs[0]
    corrupted = bytearray(path.read_bytes())
    corrupted[-1] ^= 0x20
    tampered = directory / 'tampered_pixel.dcm'
    tampered.write_bytes(corrupted)
    p = expected.copy(); p[5] = sha(corrupted)
    invalid = [(tampered, p, 'tampered_pixel')]
    truncated = directory / 'truncated.dcm'
    data = path.read_bytes()[:-2]
    truncated.write_bytes(data)
    p = expected.copy(); p[5] = sha(data)
    invalid.append((truncated, p, 'truncated_dicom'))
    duplicate = directory / 'duplicate_tag.dcm'
    data = path.read_bytes() + b'\xe0\x7f\x10\x00OB\x00\x00\x00\x00\x00\x00'
    duplicate.write_bytes(data)
    p = expected.copy(); p[5] = sha(data)
    invalid.append((duplicate, p, 'duplicate_tag'))
    oversized = directory / 'oversized.dcm'
    oversized.write_bytes(path.read_bytes() + b'\x00' * (4*1024*1024))
    p = expected.copy(); p[5] = sha(oversized.read_bytes())
    invalid.append((oversized, p, 'oversized_file'))
    return inputs, invalid


def verify_lock(lock_path):
    lock = tomllib.loads(lock_path.read_text(encoding='utf-8'))
    found = {(r['name'], r['version']) for r in lock['package']}
    for item in (('dicom-object', '0.10.0'), ('sha2', '0.10.9'),
                 ('serde_json', '1.0.145'), ('rad4_dicom_worker', '0.1.0')):
        ensure(item in found, 'RUST_LOCKFILE_DEPENDENCY_DRIFT:' + item[0])
    say('GENERATED_CARGO_LOCK_PINNED_DIRECT_DEPENDENCIES')


def gate(evidence_dir):
    ensure(CRATE.is_dir(), 'RUST_CRATE_NOT_INSTALLED')
    if execute(['docker', 'info', '--format', '{{.ServerVersion}}'],
               required=False, timeout=35).returncode:
        raise RuntimeError('DOCKER_UNAVAILABLE')
    img = docker_inspect('image', IMAGE, missing=False)
    image_id = img['Id']
    rid = secrets.token_hex(6)
    name = 'csp-rad4-rust-build-' + rid
    volume = 'csp-rad4-rust-cargo-' + rid
    evidence_dir.mkdir(parents=True, exist_ok=False)
    scratch = Path(tempfile.mkdtemp(prefix='CSP_RAD4_Synthetic_'))
    prev_py = sys.path[:]
    sys.path.insert(0, str(TESTING))
    try:
        good, bad = fixtures(scratch)
    finally:
        sys.path[:] = prev_py
    created = False
    build = [
        'docker', 'run', '--rm', '--pull=never', '--name', name,
        '--label', SCOPE + '=synthetic', '--label', RID + '=' + rid,
        '--memory=1700m', '--cpus=2', '--pids-limit=256',
        '--cap-drop=ALL', '--security-opt=no-new-privileges',
        '--mount', mount(CRATE, '/source', True),
        '--mount', mount(evidence_dir, '/output'),
        '--mount', 'type=volume,source=' + volume + ',target=/work',
        '-e', 'CARGO_HOME=/work/cargo', '-e', 'CARGO_TARGET_DIR=/work/target',
        IMAGE, 'sh', '-ec',
        'mkdir -p /work/rad4; cp -R /source/. /work/rad4/; cd /work/rad4; '
        'rustc --version; cargo --version; cargo generate-lockfile; '
        'cargo test --locked; cargo build --locked; cp Cargo.lock /output/Cargo.lock'
    ]
    try:
        execute(['docker', 'volume', 'create', '--label', SCOPE + '=synthetic',
                 '--label', RID + '=' + rid, volume], timeout=40)
        created = True
        say('DISPOSABLE_RUST_BUILD_VOLUME_CREATED')
        say('RUST_DICOM_RS_CARGO_TEST_AND_BUILD', 'START')
        result = execute(build, timeout=1800, visible=True, required=False)
        ensure(result.returncode == 0, 'CARGO_TEST_OR_BUILD_FAILED_KEEP_WORKTREE_NO_COMMIT')
        say('RUST_DICOM_RS_CARGO_TEST_AND_BUILD')
        lock_path = evidence_dir / 'Cargo.lock'
        ensure(lock_path.is_file(), 'BUILD_DID_NOT_EXPORT_CARGO_LOCK')
        verify_lock(lock_path)
        say('RUST_ONE_SHOT_NETWORK_ISOLATED_VALIDATION', 'START')
        cases = []
        for f, expected in good:
            cases.append((f, expected, 'positive', True))
        for f, expected, label in bad:
            cases.append((f, expected, label, False))
        base_file, args = good[0]
        variants = [
            (0, 'RAD2_SYNTH_WRONG_A', 'wrong_patient'),
            (1, 'RWRONGACCESSION1', 'wrong_accession'),
            (2, '2.25.987654321', 'wrong_study_uid'),
            (3, '2.25.987654321', 'wrong_series_uid'),
            (4, '2.25.987654321', 'wrong_sop_uid'),
            (5, '0' * 64, 'wrong_file_digest'),
            (6, '0' * 64, 'wrong_pixel_digest'),
            (0, 'REAL_PATIENT_1', 'real_patient_denied'),
        ]
        for index, value, label in variants:
            p = args.copy(); p[index] = value
            cases.append((base_file, p, label, False))
        result_rows = []
        for index, (file, expected, label, success) in enumerate(cases):
            cmd = [
                'docker', 'run', '--rm', '--pull=never', '--network=none',
                '--read-only', '--memory=256m', '--cpus=1', '--pids-limit=48',
                '--cap-drop=ALL', '--security-opt=no-new-privileges',
                '--mount', 'type=volume,source=' + volume + ',target=/work,readonly',
                '--mount', mount(scratch, '/fixtures', True),
                '--mount', mount(evidence_dir, '/output'),
                IMAGE, '/work/target/debug/rad4_dicom_worker', 'inspect',
                '/fixtures/' + file.name, *expected,
            ]
            preview_path = evidence_dir / 'rad4_preview_a.pgm'
            if success and index == 0:
                cmd.append('/output/rad4_preview_a.pgm')
            r = execute(cmd, required=False, timeout=90)
            if success:
                ensure(r.returncode == 0, 'RUST_POSITIVE_REJECTED:' + label)
                obj = json.loads(r.stdout.decode('utf-8'))
                ensure(obj.get('status') == 'validated_synthetic_metadata_only' and
                       obj.get('file_sha256') == expected[5] and
                       obj.get('pixel_sha256') == expected[6] and
                       obj.get('dicom_rs_parser_used') is True and
                       obj.get('width') == 8 and obj.get('height') == 8 and
                       obj.get('archive_verified') is False and
                       obj.get('clinical_association') is False and
                       obj.get('diagnostic_report_signed') is False and
                       obj.get('chart_released') is False and
                       obj.get('preview_generated') is (index == 0),
                       'RUST_POSITIVE_UNSAFE_EVIDENCE:' + label)
                if index == 0:
                    data = preview_path.read_bytes()
                    marker = b'255\n'
                    ensure(data.startswith(b'P5\n# SYNTHETIC NON-DIAGNOSTIC')
                           and marker in data and len(data.split(marker, 1)[1]) == 64,
                           'PREVIEW_NOT_SAFE_BOUNDED_PGM')
                    ensure(sha(data.split(marker, 1)[1]) == expected[6],
                           'PREVIEW_PIXEL_DIGEST_MISMATCH')
                    say('NON_DIAGNOSTIC_SYNTHETIC_PREVIEW', 'PASS')
                result_rows.append({'test': label, 'accepted': True, 'file_sha256': expected[5]})
            else:
                ensure(r.returncode != 0 and r.stdout.strip() == b'' and
                       b'RAD4_DENIED=' in r.stderr,
                       'RUST_NEGATIVE_NOT_DENIED:' + label)
                result_rows.append({'test': label, 'accepted': False})
        say('TWO_SYNTHETIC_POSITIVE_AND_TWELVE_NEGATIVE_CASES', 'PASS')
        say('RUST_VALIDATION_WITH_NETWORK_DISABLED', 'PASS')
        manifest = {
            'status': 'RAD4_SYNTHETIC_DICOM_RS_VALIDATION_GREEN',
            'source_git_commit': 'fbf273d8e1f885a5d2d16ece0964cbdc24cfeedc',
            'rust_image': IMAGE,
            'image_id': image_id,
            'cargo_lock_sha256': sha(lock_path.read_bytes()),
            'tests': result_rows,
            'no_clinical_mutation': True,
            'network_disabled_for_file_processing': True,
            'real_patient_files_processed': False,
            'archive_verified': False,
            'clinical_association': False,
            'report_signed': False,
            'image_preview_generated': True,
            'worker_service_or_queue_started': False,
        }
    finally:
        try:
            if created:
                cleanup(name, volume, rid, image_id)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    manifest['cleanup'] = 'PASS'
    evidence_file = evidence_dir / ('RAD4_SYNTHETIC_EVIDENCE_' + rid + '.json')
    evidence_file.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    say('EVIDENCE_MANIFEST', str(evidence_file))
    say('RUST_GATE', 'PASS')
    return evidence_file, lock_path


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--evidence-dir', type=Path, required=True)
    args = ap.parse_args()
    try:
        gate(args.evidence_dir)
    except Exception as exc:
        say('RUST_GATE_STOP_REASON', type(exc).__name__ + ':' + str(exc))
        raise SystemExit(1)
