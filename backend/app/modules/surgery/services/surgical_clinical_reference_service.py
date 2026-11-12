"""CE1-S10 audited, append-only links to authoritative cross-module evidence.

Never write to eMAR, Pharmacy, Lab, Billing, external audit records, source
orders or nursing flows. A reference is NOT clearance for any clinical action.
The mere presence/absence of a Billing reference cannot gate clinical care.
"""
from __future__ import annotations

import hmac
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.core.audit.models.audit_model import AuditLog
from app.core.audit.integrity import calculate_audit_evidence_sha256
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.billing_enums import InvoiceStatus
from app.core.enums.emar_enums import BarcodeVerificationResult, MedicationAdministrationStatus
from app.core.enums.lab_enums import LabOrderStatus
from app.core.enums.pharmacy_enums import DispenseStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.billing.models.billing_model import Invoice
from app.modules.emar.models.emar_model import MedicationAdministration, MedicationAdministrationEvent
from app.modules.lab.models.lab_model import LabOrder, LabOrderItem
from app.modules.pharmacy.models.pharmacy_model import DispenseItem, DispenseRecord, DrugBatch
from app.modules.prescription.models.prescription_model import Prescription, PrescriptionItem
from app.modules.surgery.models.surgical_clinical_reference_model import SurgicalClinicalReference
from app.modules.surgery.services import operative_report_service as reports
from app.modules.surgery.services import intraoperative_evidence_service as evidence
from app.modules.surgery.services.surgical_case_service import _doctor, _positive
from app.modules.surgery.services.theatre_booking_service import _required_text

_KINDS = frozenset({'emar','pharmacy','lab','billing','audit'})
_AUDIT_ENTITIES = frozenset({'SurgicalOperativeReport', 'SurgicalEvidenceJournal'})


def _value(item):
    if hasattr(item, 'value'):
        return item.value
    if isinstance(item, datetime):
        if item.tzinfo is not None and item.utcoffset() is not None:
            item = item.astimezone(timezone.utc).replace(tzinfo=None)
        return item.isoformat()
    if isinstance(item, Decimal):
        return str(item)
    return item


def _same_digest(left, right):
    return isinstance(left, str) and len(left) == 64 and hmac.compare_digest(left, right)


def _report(report_id, clinic_id, *, lock=False):
    row = reports._get(_positive(report_id, 'report_id'), _positive(clinic_id, 'clinic_id'), lock=lock)
    reports._integrity(row)
    if row.status != 'dual_signed':
        raise ConflictError('Dual-signed operative documentation required before cross-module references')
    reports._current(row)
    signatures = reports._signatures(row)
    digest = evidence._digest({'report_id': row.id, 'report_sha256': row.content_sha256,
        'signatures': [(x.id, x.signer_kind, x.evidence_sha256) for x in signatures]})
    return row, digest


def _source(kind: str, source_id: int, report):
    """Read existing, checked authority. Returned values form a drift-detectable snapshot."""
    clinic_id, patient_id, admission_id = report.clinic_id, report.patient_id, report.admission_id
    if kind not in _KINDS:
        raise ValidationError('Unknown surgical clinical reference kind')
    source_id = _positive(source_id, 'source_id')

    if kind == 'emar':
        row = db.session.execute(select(MedicationAdministration).where(
            MedicationAdministration.id == source_id, MedicationAdministration.clinic_id == clinic_id,
            MedicationAdministration.patient_id == patient_id,
            MedicationAdministration.admission_id == admission_id,
        )).scalar_one_or_none()
        if row is None:
            raise NotFoundError('Same-clinic, same-patient admission eMAR record not found')
        if (_value(row.status) != MedicationAdministrationStatus.ADMINISTERED.value
                or row.administered_at is None or row.administered_by_staff_id is None
                or _value(row.verification_result) != BarcodeVerificationResult.BARCODE_VERIFIED.value
                or row.barcode_verification_event_id is None):
            raise ConflictError('Authoritative administered barcode-verified eMAR required')
        ev = db.session.execute(select(MedicationAdministrationEvent).where(
            MedicationAdministrationEvent.id == row.barcode_verification_event_id,
            MedicationAdministrationEvent.clinic_id == clinic_id,
            MedicationAdministrationEvent.patient_id == patient_id,
            MedicationAdministrationEvent.admission_id == admission_id,
            MedicationAdministrationEvent.medication_administration_id == row.id,
        )).scalar_one_or_none()
        if (ev is None or _value(ev.verification_result) != 'barcode_verified'
                or not all((ev.patient_match, ev.medication_match, ev.dose_match,
                            ev.route_match, ev.time_window_match))
                or ev.failure_code is not None
                or ev.scanned_patient_id != patient_id
                or ev.scanned_drug_id != ev.expected_drug_id
                or ev.patient_barcode != row.patient_barcode
                or ev.medication_barcode != row.medication_barcode):
            raise ConflictError('Matching successful eMAR barcode event required')
        return dict(kind=kind, id=row.id, clinic_id=clinic_id, patient_id=patient_id,
            admission_id=admission_id, prescription_id=row.prescription_id,
            prescription_item_id=row.prescription_item_id, status=_value(row.status),
            administered_at=_value(row.administered_at), administered_by_staff_id=row.administered_by_staff_id,
            source_order_version=row.source_order_version, record_version=row.record_version,
            barcode_event_id=ev.id, operation_identity=ev.operation_identity,
            scan_timestamp=_value(ev.scan_timestamp), verification_result=_value(ev.verification_result))

    if kind == 'pharmacy':
        row = db.session.execute(select(DispenseRecord).where(DispenseRecord.id == source_id)).scalar_one_or_none()
        if row is None:
            raise NotFoundError('Pharmacy dispense record not found')
        pres = db.session.execute(select(Prescription).where(
            Prescription.id == row.prescription_id, Prescription.clinic_id == clinic_id,
            Prescription.patient_id == patient_id,
        )).scalar_one_or_none()
        if pres is None:
            raise NotFoundError('Pharmacy prescription is not assigned to surgical patient and clinic')
        items = list(db.session.execute(select(DispenseItem).where(
            DispenseItem.dispense_record_id == row.id).order_by(DispenseItem.id)).scalars())
        if (_value(row.status) != DispenseStatus.DISPENSED.value or row.dispensed_at is None
                or row.dispensed_by_id is None or not items):
            raise ConflictError('Completed authoritative pharmacy dispense with items required')
        contents = []
        for item in items:
            batch = db.session.execute(select(DrugBatch).where(
                DrugBatch.id == item.batch_id, DrugBatch.clinic_id == clinic_id,
            )).scalar_one_or_none()
            pres_item = db.session.execute(select(PrescriptionItem).where(
                PrescriptionItem.id == item.prescription_item_id,
                PrescriptionItem.prescription_id == pres.id,
            )).scalar_one_or_none() if item.prescription_item_id is not None else None
            if (batch is None or item.quantity_dispensed < 1 or pres_item is None
                    or pres_item.drug_id != batch.drug_id):
                raise ConflictError('Pharmacy dispense item must link same-clinic positive batch and prescription item')
            contents.append((item.id, item.batch_id, batch.drug_id, item.prescription_item_id, item.quantity_dispensed))
        return dict(kind=kind, id=row.id, clinic_id=clinic_id, patient_id=patient_id,
            prescription_id=row.prescription_id, prescription_status=_value(pres.status),
            status=_value(row.status), dispensed_at=_value(row.dispensed_at),
            dispensed_by_id=row.dispensed_by_id, items=contents)

    if kind == 'lab':
        row = db.session.execute(select(LabOrder).where(
            LabOrder.id == source_id, LabOrder.clinic_id == clinic_id,
            LabOrder.patient_id == patient_id,
        )).scalar_one_or_none()
        if row is None:
            raise NotFoundError('Same-clinic same-patient Lab order not found')
        if (_value(row.status) != LabOrderStatus.COMPLETED.value
                or row.sample_collected_at is None or row.collected_by_id is None
                or row.verified_at is None or row.verified_by_id is None
                or row.completed_at is None):
            raise ConflictError('Completed, collected and verified authoritative Lab order required')
        items = list(db.session.execute(select(LabOrderItem).where(
            LabOrderItem.order_id == row.id).order_by(LabOrderItem.id)).scalars())
        if not items or any(not i.result_value for i in items):
            raise ConflictError('Completed Lab order must have actual resulted test items')
        return dict(kind=kind, id=row.id, clinic_id=clinic_id, patient_id=patient_id,
            status=_value(row.status), qr_code=row.qr_code,
            sample_collected_at=_value(row.sample_collected_at), collected_by_id=row.collected_by_id,
            verified_at=_value(row.verified_at), verified_by_id=row.verified_by_id,
            completed_at=_value(row.completed_at),
            items=[(i.id,i.test_id,i.result_value,_value(i.flag)) for i in items])

    if kind == 'billing':
        row = db.session.execute(select(Invoice).where(
            Invoice.id == source_id, Invoice.clinic_id == clinic_id,
            Invoice.patient_id == patient_id,
        )).scalar_one_or_none()
        if row is None:
            raise NotFoundError('Same-clinic same-patient invoice not found')
        if _value(row.status) in (InvoiceStatus.DRAFT.value, InvoiceStatus.CANCELLED.value):
            raise ConflictError('Only existing non-draft non-cancelled invoice may be referenced')
        return dict(kind=kind, id=row.id, clinic_id=clinic_id, patient_id=patient_id,
            invoice_number=row.invoice_number, status=_value(row.status),
            total_amount=_value(row.total_amount), amount_paid=_value(row.amount_paid),
            currency=row.currency)

    row = db.session.execute(select(AuditLog).where(
        AuditLog.id == source_id, AuditLog.clinic_id == clinic_id,
        AuditLog.patient_id == patient_id,
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError('Same-clinic same-patient audit record not found')
    expected_id = report.id if row.entity_type == 'SurgicalOperativeReport' else report.journal_id
    if row.entity_type not in _AUDIT_ENTITIES or row.entity_id != expected_id:
        raise ConflictError('Audit record must refer to current surgical report or journal')
    if not _same_digest(row.evidence_sha256, calculate_audit_evidence_sha256(row)):
        raise ConflictError('Authoritative audit fingerprint mismatch')
    return dict(kind=kind, id=row.id, clinic_id=clinic_id, patient_id=patient_id,
        entity_type=row.entity_type, entity_id=row.entity_id,
        action=_value(row.action), created_at=_value(row.created_at),
        evidence_sha256=row.evidence_sha256)


def _snapshot(row: SurgicalClinicalReference):
    return dict(id=row.id, clinic_id=row.clinic_id, patient_id=row.patient_id,
        admission_id=row.admission_id, surgical_case_id=row.surgical_case_id,
        report_id=row.report_id, source_kind=row.source_kind, source_id=row.source_id,
        source_sha256=row.source_sha256, report_sha256=row.report_sha256,
        signatures_sha256=row.signatures_sha256, record_note=row.record_note,
        recorded_by_staff_id=row.recorded_by_staff_id, recorded_at=_value(row.recorded_at))


def _integrity(row):
    if not _same_digest(row.evidence_sha256, evidence._digest(_snapshot(row))):
        raise ConflictError('Cross-module surgical reference fingerprint mismatch')
    report, signature_digest = _report(row.report_id, row.clinic_id)
    if (row.patient_id != report.patient_id or row.admission_id != report.admission_id
            or row.surgical_case_id != report.surgical_case_id
            or not _same_digest(row.report_sha256, report.content_sha256)
            or not _same_digest(row.signatures_sha256, signature_digest)):
        raise ConflictError('Surgical document or signature evidence changed')
    src = _source(row.source_kind, row.source_id, report)
    if not _same_digest(row.source_sha256, evidence._digest(src)):
        raise ConflictError('Cross-module authoritative source has changed')
    return report


@transactional
def record_surgical_clinical_reference(*, report_id:int, clinic_id:int,
    actor_staff_id:int, source_kind:str, source_id:int, record_note:str) -> SurgicalClinicalReference:
    report, signature_digest = _report(report_id, clinic_id, lock=True)
    surgeon = _doctor(actor_staff_id, clinic_id)
    if surgeon.id != report.surgeon_staff_id:
        raise ConflictError('Only assigned privileged surgeon may link external clinical evidence')
    if source_kind not in _KINDS:
        raise ValidationError('Unknown surgical clinical reference kind')
    note = _required_text(record_note, 'record_note', 1200)
    source_id = _positive(source_id, 'source_id')
    if db.session.execute(select(SurgicalClinicalReference.id).where(
        SurgicalClinicalReference.report_id == report.id,
        SurgicalClinicalReference.source_kind == source_kind,
        SurgicalClinicalReference.source_id == source_id,
    )).scalar_one_or_none() is not None:
        raise ConflictError('Surgical clinical reference already exists')
    snapshot = _source(source_kind, source_id, report)
    row = SurgicalClinicalReference(
        clinic_id=report.clinic_id, patient_id=report.patient_id,
        admission_id=report.admission_id, surgical_case_id=report.surgical_case_id,
        report_id=report.id, source_kind=source_kind, source_id=source_id,
        source_sha256=evidence._digest(snapshot), report_sha256=report.content_sha256,
        signatures_sha256=signature_digest, record_note=note,
        recorded_by_staff_id=surgeon.id, recorded_at=evidence._now(),
        evidence_sha256='0'*64,
    )
    db.session.add(row)
    db.session.flush()
    row.evidence_sha256 = evidence._digest(_snapshot(row))
    db.session.flush()
    create_audit_log(action=AuditAction.CREATE, entity_type='SurgicalClinicalReference',
        entity_id=row.id, clinic_id=report.clinic_id, patient_id=report.patient_id,
        user_id=surgeon.user_id, description='Referenced authoritative external record; no execution or billing state change',
        new_value={'report_id':report.id,'source_kind':source_kind,
                   'source_id':source_id,'evidence_sha256':row.evidence_sha256})
    return row


def get_surgical_clinical_reference(*,reference_id:int,clinic_id:int):
    row=db.session.execute(select(SurgicalClinicalReference).where(
        SurgicalClinicalReference.id==_positive(reference_id,'reference_id'),
        SurgicalClinicalReference.clinic_id==_positive(clinic_id,'clinic_id'))).scalar_one_or_none()
    if row is None:
        raise NotFoundError('Surgical clinical reference not found in clinic')
    _integrity(row)
    return row


def list_surgical_clinical_references(*,report_id:int,clinic_id:int):
    _report(report_id,clinic_id)
    rows=list(db.session.execute(select(SurgicalClinicalReference).where(
        SurgicalClinicalReference.report_id==_positive(report_id,'report_id'),
        SurgicalClinicalReference.clinic_id==_positive(clinic_id,'clinic_id')).order_by(
            SurgicalClinicalReference.id)).scalars())
    for row in rows:
        _integrity(row)
    return rows
