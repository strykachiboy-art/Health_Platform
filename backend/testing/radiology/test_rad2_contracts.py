"""RAD2 offline contract checks. No Flask, DB, Docker, or network usage."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from rad2_dicom_fixture import assert_identity, make_study, parse_tags, element, fresh_uid

HERE = Path(__file__).resolve().parent


class Rad2Contracts(unittest.TestCase):
    def test_synthetic_part10_and_identity(self):
        a = make_study(run_code='ABC123', subject='A')
        self.assertEqual(a.instance[128:132], b'DICM')
        self.assertTrue(assert_identity(a.instance,a))
        self.assertEqual(len(parse_tags(a.instance)[(0x7FE0,0x0010)]),64)

    def test_other_patient_is_never_associated(self):
        a = make_study(run_code='ABC123',subject='A')
        b = make_study(run_code='ABC123',subject='B')
        self.assertNotEqual(a.study_uid,b.study_uid)
        self.assertNotEqual(a.patient_id,b.patient_id)
        with self.assertRaisesRegex(ValueError,'provenance mismatch'):
            assert_identity(a.instance,b)

    def test_tampered_pixel_data_rejected(self):
        a=make_study(run_code='ABC123',subject='A')
        tampered=bytearray(a.instance)
        tampered[-1]^=1
        with self.assertRaisesRegex(ValueError,'pixel data mismatch'):
            assert_identity(bytes(tampered),a)

    def test_truncated_dicom_rejected(self):
        a=make_study(run_code='ABC123',subject='A')
        with self.assertRaises(ValueError):
            assert_identity(a.instance[:-1],a)
        with self.assertRaises(ValueError):
            parse_tags(b'DICM')

    def test_duplicate_tag_rejected(self):
        a=make_study(run_code='ABC123',subject='A')
        with self.assertRaisesRegex(ValueError,'Duplicate DICOM tag'):
            parse_tags(a.instance+element(0x0010,0x0010,'PN','BAD^NAME'))

    def test_unique_uids(self):
        a,b=fresh_uid(),fresh_uid()
        self.assertTrue(a.startswith('2.25.'))
        self.assertNotEqual(a,b)
        self.assertLessEqual(len(a),64)

    def test_strict_synthetic_subject_input(self):
        with self.assertRaises(ValueError):make_study(run_code='REAL-PATIENT',subject='A')
        with self.assertRaises(ValueError):make_study(run_code='ABC',subject='X')

    def test_policy_no_unauthenticated_or_dicom_socket(self):
        p=json.loads((HERE/'orthanc_policy.json').read_text(encoding='utf-8'))
        self.assertIs(p['AuthenticationEnabled'],True)
        self.assertIs(p['DicomServerEnabled'],False)
        self.assertIs(p['DicomAlwaysAllowStore'],False)
        self.assertIs(p['OverwriteInstances'],False)
        self.assertEqual(p['StorageDirectory'],'/var/lib/orthanc/db')
        self.assertEqual(p['DicomWeb']['Root'],'/dicom-web/')
        self.assertLessEqual(p['MaximumStorageSize'],128)

    def test_private_networking_policy_in_runner(self):
        text=(HERE/'rad2_orthanc_gate.py').read_text(encoding='utf-8')
        self.assertIn("127.0.0.1:{port}:8042",text)
        self.assertNotIn("':4242'",text)
        self.assertIn('DICOM_WEB_PLUGIN_ENABLED=true',text)


if __name__=='__main__':unittest.main()
