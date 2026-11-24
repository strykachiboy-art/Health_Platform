"""CE1-S10 only: exact-source, read-only cross-module references.

The single S10 file is selected; previously authored Surgery suites are NOT rerun.
Fixture imports are explicit, including the S03->S09 dependency chain.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.enums.billing_enums import InvoiceStatus
from app.core.enums.emar_enums import BarcodeVerificationResult, MedicationAdministrationStatus
from app.core.enums.lab_enums import LabOrderStatus, LabResultFlag
from app.core.enums.pharmacy_enums import DispenseStatus
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.billing.models.billing_model import Invoice
from app.modules.emar.models.emar_model import MedicationAdministration, MedicationAdministrationEvent
from app.modules.lab.models.lab_model import LabOrder
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.nursing.models.admission_care_assignment_model import AdmissionCareAssignment
from app.modules.pharmacy.models.pharmacy_model import DispenseItem, DispenseRecord
from app.modules.surgery.models.surgical_clinical_reference_model import SurgicalClinicalReference
from app.modules.surgery.services import surgical_clinical_reference_service as svc
from app.modules.surgery.services import operative_report_service as reports
from app.tests.modules.surgery.test_surgical_authorization import scenario
from app.tests.modules.surgery.test_surgical_safety_checklist import ready
from app.tests.modules.surgery.test_anesthesia_foundation import anesthesia_ready
from app.tests.modules.surgery.test_intraoperative_evidence_foundation import ready_s06
from app.tests.modules.surgery.test_pacu_preparation_handoff import workspace as s08_workspace
from app.tests.modules.surgery.test_operative_report_signatures import (
    ready_s09, _report, _sign,
)


@pytest.fixture
def ready_s10(ready_s09, monkeypatch):
    ctx=ready_s09
    monkeypatch.setattr(svc,'create_audit_log', lambda **kw: ctx['events'].append(kw))
    ctx['report']=_report(ctx)
    _sign(ctx,ctx['report'])
    _sign(ctx,ctx['report'],'anesthetist')
    return ctx


def _ref(ctx,kind,source_id,**kw):
    p=dict(report_id=ctx['report'].id,clinic_id=ctx['clinic'].id,
        actor_staff_id=ctx['doctor'].id,source_kind=kind,source_id=source_id,
        record_note='Cross-module source reference only; not clearance or execution')
    p.update(kw)
    return svc.record_surgical_clinical_reference(**p)


def _invoice(ctx,make_invoice,**kw):
    return make_invoice(ctx['clinic'],ctx['patient'],**kw)


def _lab(ctx,make_lab_order,make_lab_test,**kw):
    test=make_lab_test(ctx['clinic'])
    order=make_lab_order(ctx['clinic'],ctx['patient'],ctx['doctor'],[test])
    t=order.created_at
    if t.tzinfo is None:
        t=t.replace(tzinfo=timezone.utc)
    order.status=LabOrderStatus.COMPLETED
    order.collected_by_id=ctx['nurse'].id
    order.sample_collected_at=t+timedelta(seconds=1)
    order.processed_by_id=ctx['doctor'].id
    order.processed_at=t+timedelta(seconds=2)
    order.verified_by_id=ctx['doctor'].id
    order.verified_at=t+timedelta(seconds=3)
    order.completed_at=t+timedelta(seconds=4)
    order.items[0].result_value='4.1'
    order.items[0].flag=LabResultFlag.NORMAL
    order.items[0].resulted_at=t+timedelta(seconds=3)
    for key,val in kw.items():
        setattr(order,key,val)
    db.session.flush()
    return order


def _pharmacy(ctx,make_prescription,make_prescription_item,make_drug,
              make_drug_batch,make_staff,*,complete=True):
    drug=make_drug(ctx['clinic'])
    pres=make_prescription(ctx['clinic'],ctx['patient'],ctx['doctor'])
    item=make_prescription_item(pres,drug)
    batch=make_drug_batch(ctx['clinic'],drug)
    pharmacist=make_staff(ctx['clinic'],role=Role.PHARMACIST)
    row=DispenseRecord(prescription_id=pres.id,dispensed_by_id=pharmacist.id,
        status=DispenseStatus.DISPENSED if complete else DispenseStatus.PENDING,
        dispensed_at=datetime.now(timezone.utc) if complete else None)
    db.session.add(row)
    db.session.flush()
    db.session.add(DispenseItem(dispense_record_id=row.id,batch_id=batch.id,
        prescription_item_id=item.id,quantity_dispensed=1))
    db.session.flush()
    return row,batch


def _emar(ctx,make_prescription,make_prescription_item,make_drug):
    drug=make_drug(ctx['clinic'])
    pres=make_prescription(ctx['clinic'],ctx['patient'],ctx['doctor'])
    item=make_prescription_item(pres,drug)
    now=datetime.now(timezone.utc)
    row=MedicationAdministration(clinic_id=ctx['clinic'].id,
        patient_id=ctx['patient'].id,admission_id=ctx['admission'].id,
        prescription_id=pres.id,prescription_item_id=item.id,
        status=MedicationAdministrationStatus.ADMINISTERED,
        administered_at=now,administered_by_staff_id=ctx['nurse'].id,
        verification_result=BarcodeVerificationResult.BARCODE_VERIFIED,
        patient_barcode='TEST_PATIENT_BARCODE',medication_barcode='TEST_DRUG_BARCODE',
        scan_timestamp=now,scan_device='s10-test-scanner',
        barcode_verified_by_staff_id=ctx['nurse'].id,source_order_version=1)
    db.session.add(row)
    db.session.flush()
    ev=MedicationAdministrationEvent(clinic_id=ctx['clinic'].id,
        patient_id=ctx['patient'].id,admission_id=ctx['admission'].id,
        medication_administration_id=row.id,prescription_id=pres.id,
        prescription_item_id=item.id,actor_staff_id=ctx['nurse'].id,
        scanned_patient_id=ctx['patient'].id,scanned_drug_id=drug.id,
        patient_barcode='TEST_PATIENT_BARCODE',medication_barcode='TEST_DRUG_BARCODE',
        scan_timestamp=now,scan_device='s10-test-scanner',
        verification_result=BarcodeVerificationResult.BARCODE_VERIFIED,
        patient_match=True,medication_match=True,dose_match=True,
        route_match=True,time_window_match=True,
        expected_drug_id=drug.id,expected_dose_value=Decimal('1.0'),
        expected_dose_unit='tablet',expected_route_code='PO',
        source_order_version=1,verification_policy_version=1,
        operation_identity='s10-local-test-barcode-one')
    db.session.add(ev)
    db.session.flush()
    row.barcode_verification_event_id=ev.id
    db.session.flush()
    return row,ev


def _audit(ctx,make_audit_log,**kw):
    args=dict(user=ctx['doctor'].user,clinic_id=ctx['clinic'].id,
        patient_id=ctx['patient'].id,entity_type='SurgicalOperativeReport',
        entity_id=ctx['report'].id)
    args.update(kw)
    return make_audit_log(**args)


def test_billing_reference_is_patient_scoped_and_does_not_charge_or_complete(ready_s10,make_invoice):
    ctx=ready_s10; inv=_invoice(ctx,make_invoice)
    row=_ref(ctx,'billing',inv.id)
    assert row.id and row.source_kind=='billing' and row.patient_id==ctx['patient'].id
    assert len(row.evidence_sha256)==len(row.source_sha256)==64
    assert svc.get_surgical_clinical_reference(reference_id=row.id,clinic_id=ctx['clinic'].id).id==row.id
    assert inv.amount_paid==0 and inv.status==InvoiceStatus.ISSUED
    assert ctx['case'].status=='planned' and ctx['order'].status=='active'
    assert ctx['admission'].status==AdmissionStatus.ADMITTED
    assert db.session.query(NursingHandoff).count()==0
    assert db.session.query(AdmissionCareAssignment).count()==0
    assert any(x['entity_type']=='SurgicalClinicalReference' for x in ctx['events'])


def test_billing_reference_does_not_require_payment(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice,status=InvoiceStatus.ISSUED)
    assert _ref(ready_s10,'billing',inv.id).id is not None


def test_paid_invoice_is_only_a_document_reference(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice,status=InvoiceStatus.PAID,amount_paid=Decimal('100.00'))
    _ref(ready_s10,'billing',inv.id)
    assert ready_s10['case'].status=='planned'


def test_draft_invoice_is_denied(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice,status=InvoiceStatus.DRAFT)
    with pytest.raises(ConflictError,match='non-draft'):
        _ref(ready_s10,'billing',inv.id)


def test_cancelled_invoice_is_denied(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice,status=InvoiceStatus.CANCELLED)
    with pytest.raises(ConflictError,match='non-cancelled'):
        _ref(ready_s10,'billing',inv.id)


def test_different_clinic_invoice_is_inaccessible(ready_s10,make_invoice,make_clinic):
    other=make_clinic(name='S10 alien billing clinic')
    inv=make_invoice(other,ready_s10['patient'])
    with pytest.raises(NotFoundError,match='invoice'):
        _ref(ready_s10,'billing',inv.id)


def test_different_patient_invoice_is_inaccessible(ready_s10,make_invoice,make_patient):
    person=make_patient(ready_s10['clinic'])
    inv=make_invoice(ready_s10['clinic'],person)
    with pytest.raises(NotFoundError,match='invoice'):
        _ref(ready_s10,'billing',inv.id)


def test_invoice_source_drift_is_detected(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice)
    row=_ref(ready_s10,'billing',inv.id)
    inv.amount_paid=Decimal('20.00')
    db.session.flush()
    with pytest.raises(ConflictError,match='source has changed'):
        svc.get_surgical_clinical_reference(reference_id=row.id,clinic_id=ready_s10['clinic'].id)


def test_reference_row_tamper_detected(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice)
    row=_ref(ready_s10,'billing',inv.id)
    row.record_note='tampered record'
    db.session.flush()
    with pytest.raises(ConflictError,match='fingerprint'):
        svc.get_surgical_clinical_reference(reference_id=row.id,clinic_id=ready_s10['clinic'].id)


def test_duplicate_reference_is_denied(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice)
    _ref(ready_s10,'billing',inv.id)
    with pytest.raises(ConflictError,match='already exists'):
        _ref(ready_s10,'billing',inv.id)
    assert db.session.query(SurgicalClinicalReference).count()==1


def test_list_references_checks_content_integrity(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice)
    row=_ref(ready_s10,'billing',inv.id)
    assert [x.id for x in svc.list_surgical_clinical_references(
        report_id=ready_s10['report'].id,clinic_id=ready_s10['clinic'].id)]==[row.id]


def test_foreign_clinic_get_and_list_fail(ready_s10,make_invoice,make_clinic):
    inv=_invoice(ready_s10,make_invoice)
    row=_ref(ready_s10,'billing',inv.id)
    other=make_clinic(name='S10 foreign viewer')
    with pytest.raises(NotFoundError):
        svc.get_surgical_clinical_reference(reference_id=row.id,clinic_id=other.id)
    with pytest.raises(NotFoundError):
        svc.list_surgical_clinical_references(report_id=ready_s10['report'].id,clinic_id=other.id)


def test_unassigned_doctor_cannot_record(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice)
    with pytest.raises(ConflictError,match='assigned privileged surgeon'):
        _ref(ready_s10,'billing',inv.id,actor_staff_id=ready_s10['reviewer'].id)


def test_nurse_cannot_impersonate_surgeon(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice)
    with pytest.raises(ValidationError,match='doctor'):
        _ref(ready_s10,'billing',inv.id,actor_staff_id=ready_s10['nurse'].id)


def test_blank_note_denied(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice)
    with pytest.raises(ValidationError):
        _ref(ready_s10,'billing',inv.id,record_note='  ')


def test_oversized_note_denied(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice)
    with pytest.raises(ValidationError):
        _ref(ready_s10,'billing',inv.id,record_note='X'*1201)


def test_unknown_kind_denied(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice)
    with pytest.raises(ValidationError,match='Unknown'):
        _ref(ready_s10,'transfusion',inv.id)


def test_missing_source_denied(ready_s10):
    with pytest.raises(NotFoundError):
        _ref(ready_s10,'billing',999999)


def test_invalid_source_identifier_denied(ready_s10):
    with pytest.raises(ValidationError):
        _ref(ready_s10,'billing',0)
    with pytest.raises(ValidationError):
        _ref(ready_s10,'billing',True)


def test_missing_report_denied(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice)
    with pytest.raises(NotFoundError):
        _ref(ready_s10,'billing',inv.id,report_id=999999)


def test_only_dual_signed_report_accepted(ready_s09,make_invoice,monkeypatch):
    ctx=ready_s09
    monkeypatch.setattr(svc,'create_audit_log',lambda **kw:ctx['events'].append(kw))
    row=_report(ctx)
    inv=make_invoice(ctx['clinic'],ctx['patient'])
    with pytest.raises(ConflictError,match='Dual-signed'):
        svc.record_surgical_clinical_reference(report_id=row.id,clinic_id=ctx['clinic'].id,
            actor_staff_id=ctx['doctor'].id,source_kind='billing',source_id=inv.id,
            record_note='Documentation only')
    _sign(ctx,row)
    with pytest.raises(ConflictError,match='Dual-signed'):
        svc.record_surgical_clinical_reference(report_id=row.id,clinic_id=ctx['clinic'].id,
            actor_staff_id=ctx['doctor'].id,source_kind='billing',source_id=inv.id,
            record_note='Documentation only')


def test_signed_report_content_tamper_blocks_read(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice)
    row=_ref(ready_s10,'billing',inv.id)
    ready_s10['report'].operative_findings='tampered surgical narrative'
    db.session.flush()
    with pytest.raises(ConflictError):
        svc.get_surgical_clinical_reference(reference_id=row.id,clinic_id=ready_s10['clinic'].id)


def test_unrelated_existing_audit_denied(ready_s10,make_audit_log):
    log=_audit(ready_s10,make_audit_log,entity_type='Patient',entity_id=ready_s10['patient'].id)
    with pytest.raises(ConflictError,match='Audit record'):
        _ref(ready_s10,'audit',log.id)


def test_surgical_audit_reference_success(ready_s10,make_audit_log):
    log=_audit(ready_s10,make_audit_log)
    row=_ref(ready_s10,'audit',log.id)
    assert row.id and row.source_kind=='audit'
    assert svc.get_surgical_clinical_reference(reference_id=row.id,clinic_id=ready_s10['clinic'].id).id==row.id


def test_lab_must_be_completed_and_verified(ready_s10,make_lab_order,make_lab_test):
    lab=_lab(ready_s10,make_lab_order,make_lab_test,status=LabOrderStatus.ORDERED)
    with pytest.raises(ConflictError,match='Completed, collected'):
        _ref(ready_s10,'lab',lab.id)


def test_completed_verified_lab_reference(ready_s10,make_lab_order,make_lab_test):
    lab=_lab(ready_s10,make_lab_order,make_lab_test)
    row=_ref(ready_s10,'lab',lab.id)
    assert row.source_kind=='lab'
    assert svc.get_surgical_clinical_reference(reference_id=row.id,clinic_id=ready_s10['clinic'].id).id==row.id


def test_lab_result_drift_denied(ready_s10,make_lab_order,make_lab_test):
    lab=_lab(ready_s10,make_lab_order,make_lab_test)
    row=_ref(ready_s10,'lab',lab.id)
    lab.items[0].result_value='changed later'
    db.session.flush()
    with pytest.raises(ConflictError,match='source has changed'):
        svc.get_surgical_clinical_reference(reference_id=row.id,clinic_id=ready_s10['clinic'].id)


def test_lab_wrong_patient_denied(ready_s10,make_lab_order,make_lab_test,make_patient):
    other=make_patient(ready_s10['clinic'])
    test=make_lab_test(ready_s10['clinic'])
    lab=make_lab_order(ready_s10['clinic'],other,ready_s10['doctor'],[test])
    with pytest.raises(NotFoundError,match='Lab order'):
        _ref(ready_s10,'lab',lab.id)


def test_pharmacy_complete_dispense_reference(ready_s10,make_prescription,
    make_prescription_item,make_drug,make_drug_batch,make_staff):
    row,_batch=_pharmacy(ready_s10,make_prescription,make_prescription_item,
        make_drug,make_drug_batch,make_staff)
    link=_ref(ready_s10,'pharmacy',row.id)
    assert link.source_kind=='pharmacy' and link.id


def test_pharmacy_pending_not_accepted(ready_s10,make_prescription,
    make_prescription_item,make_drug,make_drug_batch,make_staff):
    row,_batch=_pharmacy(ready_s10,make_prescription,make_prescription_item,
        make_drug,make_drug_batch,make_staff,complete=False)
    with pytest.raises(ConflictError,match='Completed authoritative pharmacy'):
        _ref(ready_s10,'pharmacy',row.id)


def test_pharmacy_out_of_clinic_batch_denied(ready_s10,make_prescription,
    make_prescription_item,make_drug,make_drug_batch,make_staff,make_clinic):
    row,batch=_pharmacy(ready_s10,make_prescription,make_prescription_item,
        make_drug,make_drug_batch,make_staff)
    foreign=make_clinic(name='S10 foreign pharmacy batch')
    batch.clinic_id=foreign.id
    db.session.flush()
    with pytest.raises(ConflictError,match='same-clinic positive batch'):
        _ref(ready_s10,'pharmacy',row.id)


def test_emar_verified_admin_reference(ready_s10,make_prescription,make_prescription_item,make_drug):
    row,ev=_emar(ready_s10,make_prescription,make_prescription_item,make_drug)
    link=_ref(ready_s10,'emar',row.id)
    assert link.source_kind=='emar' and link.source_id==row.id
    assert ready_s10['case'].status=='planned'


def test_emar_no_barcode_event_denied(ready_s10,make_prescription,make_prescription_item,make_drug):
    row,ev=_emar(ready_s10,make_prescription,make_prescription_item,make_drug)
    row.barcode_verification_event_id=None
    db.session.flush()
    with pytest.raises(ConflictError,match='barcode-verified'):
        _ref(ready_s10,'emar',row.id)


def test_emar_foreign_clinic_barcode_event_denied(ready_s10,make_prescription,make_prescription_item,make_drug,make_clinic):
    row,ev=_emar(ready_s10,make_prescription,make_prescription_item,make_drug)
    foreign=make_clinic(name='S10 foreign barcode event authority')
    ev.clinic_id=foreign.id
    db.session.flush()
    with pytest.raises(ConflictError,match='Matching successful'):
        _ref(ready_s10,'emar',row.id)


def test_emar_source_status_drift_denied(ready_s10,make_prescription,make_prescription_item,make_drug):
    row,ev=_emar(ready_s10,make_prescription,make_prescription_item,make_drug)
    link=_ref(ready_s10,'emar',row.id)
    # Resolve fixture identity before mutating a persisted eMAR record:
    # relationship loading otherwise autoflushes an incomplete HELD state.
    hold_actor_staff_id=ready_s10['nurse'].id
    row.status=MedicationAdministrationStatus.HELD
    row.held_at=datetime.now(timezone.utc)
    row.held_by_staff_id=hold_actor_staff_id
    row.hold_reason='Testing mutation only'
    db.session.flush()
    with pytest.raises(ConflictError):
        svc.get_surgical_clinical_reference(reference_id=link.id,clinic_id=ready_s10['clinic'].id)


def test_no_duplicate_non_surgery_state(ready_s10,make_invoice):
    inv=_invoice(ready_s10,make_invoice)
    original=(ready_s10['case'].status,ready_s10['order'].status,ready_s10['admission'].status,
              ready_s10['report'].status,inv.status)
    _ref(ready_s10,'billing',inv.id)
    assert original==(ready_s10['case'].status,ready_s10['order'].status,
        ready_s10['admission'].status,ready_s10['report'].status,inv.status)
    assert db.session.query(NursingHandoff).count()==0
    assert db.session.query(AdmissionCareAssignment).count()==0
