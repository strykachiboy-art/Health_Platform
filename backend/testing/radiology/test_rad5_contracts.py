"""Pure RAD5 synthetic study-scope security regression (no Docker required)."""
from __future__ import annotations

import pathlib
import unittest

from rad5_proxy_contract import ScopeDenied, allowed_dicomweb_target, is_uid, map_peer_access

A='2.25.123456789012345678901234567890123456'
B='2.25.223456789012345678901234567890123456'
S='2.25.3333333333333333333333'
I='2.25.4444444444444444444444'


class Rad5Contracts(unittest.TestCase):
    def test_qido_requires_explicit_exact_uid(self):
        self.assertEqual(allowed_dicomweb_target('/dicom-web/studies?StudyInstanceUID='+A,A),
                         '/dicom-web/studies?StudyInstanceUID='+A)
        for p in ('/dicom-web/studies','/dicom-web/studies?PatientID=A',
                  '/dicom-web/studies?StudyInstanceUID='+B,
                  '/dicom-web/studies?StudyInstanceUID='+A+'&StudyInstanceUID='+B):
            with self.subTest(p=p),self.assertRaises(ScopeDenied): allowed_dicomweb_target(p,A)

    def test_nested_study_paths(self):
        for p in (f'/dicom-web/studies/{A}/metadata',f'/dicom-web/studies/{A}/series',
                  f'/dicom-web/studies/{A}/series/{S}',
                  f'/dicom-web/studies/{A}/series/{S}/instances/{I}',
                  f'/dicom-web/studies/{A}/series/{S}/instances/{I}/frames/1'):
            with self.subTest(p=p):self.assertEqual(allowed_dicomweb_target(p,A),p)

    def test_cross_study_denied(self):
        for p in (f'/dicom-web/studies/{B}/metadata',f'/dicom-web/studies/{B}/series/{S}/instances/{I}',
                  '/dicom-web/studies?StudyInstanceUID='+B):
            with self.subTest(p=p),self.assertRaises(ScopeDenied):allowed_dicomweb_target(p,A)

    def test_dicomweb_write_methods_have_no_gate(self):
        from rad5_gateway import TestHandler
        self.assertEqual(TestHandler.do_PUT, TestHandler.do_POST)
        self.assertEqual(TestHandler.do_DELETE, TestHandler.do_POST)

    def test_traversal_and_encoded_slash_denied(self):
        for p in (f'/dicom-web/studies/{A}/../studies/{B}',
                  f'/dicom-web/studies/{A}%2f../',
                  '//dicom-web/studies?StudyInstanceUID='+A,
                  f'/dicom-web/studies/{A}/%2e%2e',
                  f'/dicom-web/studies/{A}\\series'):
            with self.subTest(p=p),self.assertRaises(ScopeDenied):allowed_dicomweb_target(p,A)

    def test_upstream_admin_routes_not_dicomweb(self):
        for p in ('/instances','/system','/plugins','/dicom-web/instances/1.2.3'):
            with self.subTest(p=p),self.assertRaises(ScopeDenied):allowed_dicomweb_target(p,A)

    def test_uid_validation(self):
        for v in ('', '1.2.x', '1..2', '01.2', '1', '1.2.','1.2/3','2.25.'+'1'*70):
            self.assertFalse(is_uid(v))
        self.assertTrue(is_uid(A))

    def test_exact_synthetic_scope_only(self):
        self.assertEqual(map_peer_access('A',{'A':A,'B':B}),A)
        for v in ('PATIENT','patient','C','',None):
            with self.subTest(v=v), self.assertRaises(ScopeDenied):map_peer_access(v,{'A':A,'B':B})

    def test_synthetic_config_never_leaks_archive(self):
        p=pathlib.Path(__file__).with_name('ohif_rad5_config.js').read_text(encoding='utf8')
        self.assertIn("qidoRoot: '/dicom-web'",p)
        self.assertIn('showStudyList: false',p)
        self.assertNotIn('http://',p)
        self.assertNotIn('Bearer ',p)
        self.assertNotIn('token=',p)

    def test_qido_limit_bounded(self):
        allowed_dicomweb_target('/dicom-web/studies?StudyInstanceUID='+A+'&limit=1',A)
        with self.assertRaises(ScopeDenied):allowed_dicomweb_target('/dicom-web/studies?StudyInstanceUID='+A+'&limit=1000',A)

    def test_dicomweb_wado_query_denied(self):
        with self.assertRaises(ScopeDenied):allowed_dicomweb_target(f'/dicom-web/studies/{A}/metadata?url=http:evil',A)

    def test_no_production_flask_or_clinical_release(self):
        p=pathlib.Path(__file__).with_name('rad5_gateway.py').read_text(encoding='utf8')
        self.assertNotIn('create_app(',p)
        self.assertNotIn('register_blueprint(',p)
        self.assertNotIn('RadiologyStudyAssociation(',p)
        self.assertNotIn('SQLALCHEMY',p)


if __name__=='__main__':unittest.main()
