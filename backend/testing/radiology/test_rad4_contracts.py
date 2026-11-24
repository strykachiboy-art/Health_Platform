"""RAD4 focused negative-policy and source-contract tests. No external services."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unittest
import tomllib

BASE = Path(__file__).resolve().parents[2]
CRATE = BASE / 'rust' / 'rad4_dicom_worker'
SRC = (CRATE / 'src' / 'main.rs').read_text(encoding='utf-8')
CARGO = tomllib.loads((CRATE / 'Cargo.toml').read_text(encoding='utf-8'))


class Rad4Contracts(unittest.TestCase):
    def test_pinned_rust_dependencies(self):
        self.assertEqual(CARGO['dependencies'], {
            'dicom-object': '=0.10.0',
            'sha2': '=0.10.9',
            'serde_json': '=1.0.145',
        })

    def test_bounded_one_shot_and_explicit_transfer_syntax(self):
        for text in ('MAX_BYTES: usize = 4 * 1024 * 1024',
                     '1.2.840.10008.1.2.1', 'open_file(src)',
                     'symlink_metadata', 'checked_wire(&bytes)',
                     'DUPLICATE_TAG', 'TRUNCATED_DICOM_VALUE'):
            self.assertIn(text, SRC)

    def test_synthetic_only_and_no_physical_archive_claims(self):
        for text in ('RAD2_SYNTH_', 'archive_verified": false',
                     'clinical_association": false', 'diagnostic_report_signed": false',
                     'chart_released": false', 'preview_generated": preview_generated'):
            self.assertIn(text, SRC)

    def test_no_parallel_celery_or_network_or_db(self):
        for pattern in ('reqwest', 'tokio', 'std::net', 'sqlx', 'diesel',
                        'redis::', 'celery', 'postgres', 'http::',
                        'unsafe {', 'std::process::Command'):
            self.assertNotIn(pattern, SRC)

    def test_pinned_dicom_accession_limit(self):
        self.assertIn('1..=16', SRC)
        self.assertIn('RAD2_SYNTH_', SRC)
        self.assertIn('DICOM_FILE_META_MISMATCH', SRC)
        self.assertIn('NON-DIAGNOSTIC PREVIEW - NOT FOR CLINICAL USE', SRC)
        self.assertIn('create_new(true)', SRC)

    def test_bounded_dicom_rs_element_and_sha_provenance(self):
        for text in ('get_text(&obj, "StudyInstanceUID")', 'sha2::{Digest, Sha256}',
                     'PIXEL_DIGEST_MISMATCH', 'FILE_DIGEST_MISMATCH'):
            # source pairs may use a loop instead of direct one-off lookups
            if text.startswith('get_text(&obj, "StudyInstanceUID")'):
                self.assertIn('("StudyInstanceUID", &expected[2])', SRC)
            else:
                self.assertIn(text, SRC)


if __name__ == '__main__':
    unittest.main()
