"""Pure, synthetic-only security tests; do not import Flask or touch the app DB."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import types
import unittest

HERE=Path(__file__).resolve()
ROOT=HERE.parents[4]  # backend
SRC=ROOT/'app/modules/printing/services'
for name, path in [('rad6local',SRC)]:
    pkg=types.ModuleType(name)
    pkg.__path__=[str(path)]
    sys.modules.setdefault(name,pkg)
policy=importlib.import_module('rad6local.print_policy')
render=importlib.import_module('rad6local.print_renderer')

DATA={
'invoice':dict(invoice_number='INV-TEST-01',patient_id=11,status='ISSUED',currency='NGN',total_amount='100.00',amount_paid='0.00'),
'appointment':dict(patient_id=11,assigned_staff_id=3,status='SCHEDULED',scheduled_start='2030-01-01 10:00',scheduled_end='2030-01-01 10:30'),
'lab_order':dict(patient_id=11,status='PENDING',created_at='2030-01-01',sample_collected_at='NOT COLLECTED'),
'radiology_draft':dict(imaging_order_id=40,accession_number='RAD-0123456789ABCDEF01234567',report_revision=1,report_text='TEST ONLY working note.'),
}

class Rad6PrintContract(unittest.TestCase):
    def document(self, kind='radiology_draft', source=None):
        return policy.build_document(kind=kind,clinic_id=1,record_id=9,
            source_version=1,source=source if source is not None else DATA[kind])

    def test_four_shared_source_types(self):
        self.assertEqual(set(policy.supported_kinds()),set(DATA))

    def test_allow_invoice_admin(self):
        policy.authorize(kind='invoice',actor_clinic_id=1,record_clinic_id=1,actor_role='ADMIN')

    def test_allow_invoice_accountant(self):
        policy.authorize(kind='invoice',actor_clinic_id=1,record_clinic_id=1,actor_role='ACCOUNTANT')

    def test_deny_invoice_doctor(self):
        with self.assertRaises(policy.PrintDenied):
            policy.authorize(kind='invoice',actor_clinic_id=1,record_clinic_id=1,actor_role='DOCTOR')

    def test_cross_clinic_denied_all_four(self):
        for kind in DATA:
            with self.subTest(kind=kind),self.assertRaises(policy.PrintDenied):
                policy.authorize(kind=kind,actor_clinic_id=2,record_clinic_id=1,actor_role='ADMIN')

    def test_patient_and_super_admin_denied(self):
        for role in ('PATIENT','SUPER_ADMIN','UNKNOWN'):
            with self.subTest(role=role),self.assertRaises(policy.PrintDenied):
                policy.authorize(kind='invoice',actor_clinic_id=1,record_clinic_id=1,actor_role=role)

    def test_radiology_doctor_must_own_order(self):
        policy.authorize(kind='radiology_draft',actor_clinic_id=1,record_clinic_id=1,
            actor_role='DOCTOR',actor_staff_id=4,owner_staff_id=4)
        with self.assertRaises(policy.PrintDenied):
            policy.authorize(kind='radiology_draft',actor_clinic_id=1,record_clinic_id=1,
                actor_role='DOCTOR',actor_staff_id=5,owner_staff_id=4)

    def test_appointment_doctor_must_be_assigned(self):
        with self.assertRaises(policy.PrintDenied):
            policy.authorize(kind='appointment',actor_clinic_id=1,record_clinic_id=1,
                actor_role='DOCTOR',actor_staff_id=5,owner_staff_id=4)

    def test_appointment_receptionist(self):
        policy.authorize(kind='appointment',actor_clinic_id=1,record_clinic_id=1,
            actor_role='RECEPTIONIST')

    def test_lab_technician_lab_order_only(self):
        policy.authorize(kind='lab_order',actor_clinic_id=1,record_clinic_id=1,
            actor_role='LAB_TECHNICIAN')
        with self.assertRaises(policy.PrintDenied):
            policy.authorize(kind='radiology_draft',actor_clinic_id=1,record_clinic_id=1,
                actor_role='LAB_TECHNICIAN')

    def test_boolean_identity_denied(self):
        with self.assertRaises(policy.PrintDenied):
            policy.authorize(kind='invoice',actor_clinic_id=True,record_clinic_id=1,
                actor_role='ADMIN')

    def test_unsupported_document_denied(self):
        for kind in ('prescription','discharge_summary','report_final_signed','admin_report','ohif_image'):
            with self.subTest(kind=kind),self.assertRaises(policy.PrintDenied):
                self.document(kind,source={})

    def test_draft_never_signed_or_released(self):
        d=self.document()
        self.assertFalse(d.is_signed);self.assertFalse(d.released_to_chart)
        self.assertIn('UNSIGNED',d.watermark)
        self.assertIn('NOT FOR DIAGNOSIS',d.watermark)

    def test_lab_never_claims_result(self):
        self.assertIn('NO VERIFIED RESULT',self.document('lab_order').watermark)

    def test_billing_never_claims_paid_receipt(self):
        self.assertIn('NOT A PAYMENT RECEIPT',self.document('invoice').watermark)

    def test_deterministic_snapshot_digest(self):
        self.assertEqual(self.document().source_digest,self.document().source_digest)

    def test_revision_changes_snapshot_digest(self):
        d=self.document()
        other=policy.build_document(kind='radiology_draft',clinic_id=1,record_id=9,source_version=2,source=DATA['radiology_draft'])
        self.assertNotEqual(d.source_digest,other.source_digest)

    def test_extra_sensitive_field_denied(self):
        src=dict(DATA['invoice']);src['patient_name']='EXTRA'
        with self.assertRaises(policy.PrintDenied):self.document('invoice',src)

    def test_missing_required_field_denied(self):
        src=dict(DATA['invoice']);del src['invoice_number']
        with self.assertRaises(policy.PrintDenied):self.document('invoice',src)

    def test_control_character_denied(self):
        src=dict(DATA['radiology_draft']);src['report_text']='trusted\x00untrusted'
        with self.assertRaises(policy.PrintDenied):self.document(source=src)

    def test_bidi_override_denied(self):
        src=dict(DATA['radiology_draft']);src['report_text']='DRAFT\u202eFINAL'
        with self.assertRaises(policy.PrintDenied):self.document(source=src)

    def test_bounded_text(self):
        src=dict(DATA['radiology_draft']);src['report_text']='a'*12001
        with self.assertRaises(policy.PrintDenied):self.document(source=src)

    def test_pdf_magic_and_markers(self):
        for kind in DATA:
            with self.subTest(kind=kind):
                pdf=render.render_pdf(self.document(kind))
                self.assertTrue(pdf.startswith(b'%PDF-'))
                self.assertGreater(len(pdf),1500)
                self.assertLess(len(pdf),250000)
                self.assertIn(self.document(kind).watermark.encode('ascii'),pdf)
                self.assertIn(b'CLINIC SYSTEM PRO',pdf)

    def test_pdf_text_is_markup_escaped(self):
        src=dict(DATA['radiology_draft']);src['report_text']='<b>NOT REAL</b> & transparent'
        pdf=render.render_pdf(self.document(source=src))
        self.assertTrue(pdf.startswith(b'%PDF-'))
        self.assertIn(b'( <b>NOT REAL</b> & transparent) Tj',pdf)  # literal displayed; not interpreted as markup

    def test_reject_claimed_signed_pdf(self):
        from dataclasses import replace
        with self.assertRaises(policy.PrintDenied):render.render_pdf(replace(self.document(),is_signed=True))

    def test_reject_claimed_released_pdf(self):
        from dataclasses import replace
        with self.assertRaises(policy.PrintDenied):render.render_pdf(replace(self.document(),released_to_chart=True))

    def test_no_unsafe_pdf_attachment_or_storage(self):
        path=ROOT/'app/modules/printing/routes/printing_routes.py'
        route=path.read_text('utf8')
        self.assertIn("RAD6_PRINTING_ENABLED',False",route)
        self.assertIn("db.session.commit()",route)
        self.assertIn("no-store",route)
        self.assertIn("as_attachment=True",route)
        self.assertIn("@role_required(*PRINT_ROLES)",route)
        self.assertNotIn('auto_print',route)

    def test_source_adapters_bind_original_models(self):
        src=(SRC/'source_adapters.py').read_text('utf8')
        for path in ('billing_model import Invoice','appointment_model import Appointment',
                     'lab_model import LabOrder','ris_model import (RadiologyExam',
                     'imaging_order_model import ImagingOrder','version.content_sha256','_row(ImagingOrder'):
            self.assertIn(path,src)

    def test_no_clinical_final_or_file_stores(self):
        src=(SRC/'source_adapters.py').read_text('utf8')
        route=(ROOT/'app/modules/printing/routes/printing_routes.py').read_text('utf8')
        self.assertNotIn('FINAL_SIGNED',src)
        self.assertNotIn('critical_result',src)
        self.assertNotIn('open(',route)
        self.assertNotIn('FileStorage',route)
        self.assertNotIn('celery',route)

if __name__=='__main__':unittest.main()
