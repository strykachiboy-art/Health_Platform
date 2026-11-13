"""RAD3 synthetic-only negative/failure tests. Pure Python; no clinical DB."""
from __future__ import annotations
from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import unittest

PROD=Path(__file__).resolve().parents[2]/'app/modules/radiology/services/rad3_mwl_contract.py'
spec=importlib.util.spec_from_file_location('rad3_mwl_contract', PROD)
bridge=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=bridge
spec.loader.exec_module(bridge)


def fixture():
    return bridge.CanonicalRISIntent(
        clinic_id=27, imaging_order_id=11, exam_id=10, exam_version=2,
        order_status='active', exam_status='scheduled',
        issuer_namespace='CSP-CLINIC-27',
        accession_number='RAD-'+'A'*24,
        patient_id='RAD2_SYNTH_TEST_A',
        study_instance_uid='2.25.99944543366',
        scheduled_step_id=33, scheduled_step_sequence=1,
        scheduled_step_status='intent',
        scheduled_at=datetime(2030, 1, 2, 11, 33, tzinfo=timezone.utc),
        modality='other', station_aet='RAD3PACS')


class Rad3Contracts(unittest.TestCase):
    def test_source_path_still_is_bound_pure_module(self):
        self.assertTrue(PROD.is_file())
        source=PROD.read_text(encoding='utf-8')
        for forbidden in ('db.session', 'requests.post(', 'docker run', 'report_signed=True', 'archive_verified=True'):
            self.assertNotIn(forbidden,source)

    def test_canonical_intent_yields_dicom_mwl_tag_mapping(self):
        payload=bridge.worklist_payload(fixture())
        tags=payload['Tags']
        self.assertEqual(tags['AccessionNumber'], bridge.dicom_accession(fixture()))
        self.assertEqual(len(tags['AccessionNumber']), 16)
        self.assertEqual(tags['StudyInstanceUID'], '2.25.99944543366')
        self.assertEqual(tags['PatientID'], 'RAD2_SYNTH_TEST_A')
        self.assertEqual(tags['ScheduledProcedureStepSequence'][0]['Modality'], 'OT')
        self.assertEqual(tags['ScheduledProcedureStepSequence'][0]['ScheduledProcedureStepStartDate'], '20300102')
        self.assertEqual(tags['ScheduledProcedureStepSequence'][0]['ScheduledStationAETitle'], 'RAD3PACS')

    def test_real_patient_disallowed_from_synthetic_phase(self):
        with self.assertRaises(bridge.ContractViolation):
            bridge.worklist_payload(replace(fixture(),patient_id='MRN-112'))

    def test_issuer_scoped_synthetic_dicom_short_accession(self):
        s=fixture()
        x=bridge.dicom_accession(s)
        other=replace(s,clinic_id=28,issuer_namespace='CSP-CLINIC-28')
        self.assertNotEqual(x,bridge.dicom_accession(other))
        self.assertEqual(x,bridge.dicom_accession(s))
        self.assertEqual(len(x),16)

    def test_wrong_clinic_issuer_denied(self):
        with self.assertRaises(bridge.ContractViolation):
            bridge.worklist_payload(replace(fixture(),issuer_namespace='CSP-CLINIC-3'))

    def test_noncanonical_accession_denied(self):
        for acc in ('RAD2TEST', 'RAD-'+('Z'*24), '11', 'RAD-'+('A'*23)):
            with self.subTest(acc=acc), self.assertRaises(bridge.ContractViolation):
                bridge.worklist_payload(replace(fixture(),accession_number=acc))

    def test_missing_or_stale_scheduling_denied(self):
        for changes in ({'exam_status':'planned'}, {'scheduled_step_status':'superseded'},
                        {'exam_version':3}, {'order_status':'completed'}):
            with self.subTest(changes=changes), self.assertRaises(bridge.ContractViolation):
                bridge.worklist_payload(replace(fixture(),**changes))

    def test_incorrect_aet_or_modality_denied(self):
        for changes in ({'station_aet':'A'*17}, {'modality':'telepathy'}):
            with self.subTest(changes=changes), self.assertRaises(bridge.ContractViolation):
                bridge.worklist_payload(replace(fixture(),**changes))

    def test_naive_time_denied(self):
        with self.assertRaises(bridge.ContractViolation):
            bridge.worklist_payload(replace(fixture(), scheduled_at=datetime(2030,1,2)))

    def test_mismatched_identity_and_missing_tags_quarantined(self):
        expected={'PatientID':'RAD2_SYNTH_TEST_A','AccessionNumber':bridge.dicom_accession(fixture()),
                  'StudyInstanceUID':'2.25.99944543366','Modality':'OT',
                  'SOPInstanceUID':'2.25.1234567'}
        good=bridge.reconcile_candidate(fixture(),expected)
        self.assertEqual(good.match_status,'unverified')
        self.assertFalse(good.archive_verified or good.chart_released or good.report_signed)
        for k in ('PatientID','AccessionNumber','StudyInstanceUID','Modality','SOPInstanceUID'):
            bad=dict(expected)
            bad[k]='OTHER'
            self.assertEqual(bridge.reconcile_candidate(fixture(),bad).match_status,'quarantined')
            missing=dict(expected);missing.pop(k)
            self.assertEqual(bridge.reconcile_candidate(fixture(),missing).match_status,'quarantined')

    def test_stale_snapshot_quarantined_even_when_dicom_matches(self):
        s=replace(fixture(),exam_version=99)
        self.assertEqual(bridge.reconcile_candidate(s,{}).match_status,'quarantined')

    def test_no_automatic_patient_or_archive_association(self):
        out=bridge.reconcile_candidate(fixture(),{})
        self.assertEqual(out.match_status,'quarantined')
        self.assertFalse(out.archive_verified)
        self.assertFalse(out.report_signed)
        self.assertFalse(out.chart_released)


if __name__ == '__main__':
    unittest.main()
