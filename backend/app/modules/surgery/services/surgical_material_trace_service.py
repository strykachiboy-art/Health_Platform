"""CE1-S07 passive, source-verified Surgery material trace links.

No stock movements, specimen collection, transfusion, implant placement,
clinical execution or operative completion are performed here. These are
append-only cross-module references, not proof of administration or implantation.
"""
from __future__ import annotations

import hmac
from datetime import timezone
from sqlalchemy import select
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.inventory_enums import StockMovementDirection, StockMovementType
from app.core.enums.lab_enums import LabOrderStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.clinical_orders.models.blood_product_order_model import BloodProductOrder
from app.modules.inventory.models.inventory_model import InventoryItem, InventoryBatch, StockMovement
from app.modules.lab.models.lab_model import LabOrder
from app.modules.staff.models.staff_model import Staff
from app.modules.surgery.models.surgical_material_trace_model import SurgicalMaterialTrace
from app.modules.surgery.services.intraoperative_evidence_service import (
    _active_team_member, _digest, _editable, _integrity, _journal,
)
from app.modules.surgery.services.surgical_case_service import _positive
from app.modules.surgery.services.theatre_booking_service import _required_text

_KINDS = frozenset({'specimen', 'implant', 'consumable', 'blood_product'})
_COLLECTED = {LabOrderStatus.SAMPLE_COLLECTED, LabOrderStatus.IN_PROGRESS, LabOrderStatus.COMPLETED}


def _time_iso(value):
    """Stable UTC wall-time serialization across SQLite and PostgreSQL drivers."""
    if value.tzinfo is not None and value.utcoffset() is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat()


def _source(kind: str, clinic_id: int, patient_id: int, journal_id: int,
            lab_order_id: int | None, stock_movement_id: int | None,
            blood_product_order_id: int | None):
    """Validate and snapshot the authoritative source without writing to it."""
    ids = (lab_order_id, stock_movement_id, blood_product_order_id)
    required = (kind == 'specimen', kind in ('implant', 'consumable'), kind == 'blood_product')
    if any((x is not None) != expected for x, expected in zip(ids, required)):
        raise ValidationError('Exactly the matching authoritative source ID is required')

    if kind == 'specimen':
        order = db.session.execute(select(LabOrder).where(
            LabOrder.id == _positive(lab_order_id, 'lab_order_id'),
            LabOrder.clinic_id == clinic_id, LabOrder.patient_id == patient_id,
        )).scalar_one_or_none()
        if order is None:
            raise NotFoundError('Surgical specimen LabOrder not found in patient clinic')
        if (order.status not in _COLLECTED or order.sample_collected_at is None
                or order.collected_by_id is None or not order.qr_code):
            raise ConflictError('Authoritative collected, labeled laboratory sample required')
        snapshot = {
            'source_type': 'LabOrder', 'id': order.id,
            'clinic_id': order.clinic_id, 'patient_id': order.patient_id,
            'qr_code': order.qr_code, 'collected_by_id': order.collected_by_id,
            'sample_collected_at': _time_iso(order.sample_collected_at),
        }
        return snapshot, order.qr_code, 1

    if kind in ('implant', 'consumable'):
        movement = db.session.execute(select(StockMovement).where(
            StockMovement.id == _positive(stock_movement_id, 'stock_movement_id'),
        )).scalar_one_or_none()
        if movement is None:
            raise NotFoundError('Authoritative stock movement not found')
        item = db.session.execute(select(InventoryItem).where(
            InventoryItem.id == movement.item_id,
            InventoryItem.clinic_id == clinic_id,
        )).scalar_one_or_none()
        if item is None:
            raise NotFoundError('Stock movement belongs to another clinic')
        performer = db.session.execute(select(Staff).where(
            Staff.id == movement.performed_by_id, Staff.clinic_id == clinic_id,
        )).scalar_one_or_none()
        if performer is None:
            raise ConflictError('Inventory movement performer does not belong to source clinic')
        if (movement.movement_type != StockMovementType.USAGE
                or movement.direction != StockMovementDirection.OUT
                or movement.reference_type != 'SurgicalEvidenceJournal'
                or movement.reference_id != journal_id or movement.quantity <= 0):
            raise ConflictError('Authoritative surgical stock usage movement required')
        batch = None
        if movement.batch_id is not None:
            batch = db.session.execute(select(InventoryBatch).where(
                InventoryBatch.id == movement.batch_id,
                InventoryBatch.item_id == item.id,
            )).scalar_one_or_none()
            if batch is None:
                raise ConflictError('Stock movement batch does not match inventory item')
        if kind == 'implant' and batch is None:
            raise ConflictError('Implant trace requires an authoritative inventory batch')
        label = batch.batch_number if batch is not None else (item.barcode or item.sku)
        if not label:
            raise ConflictError('Stock source lacks an authoritative identity label')
        snapshot = {
            'source_type': 'StockMovement', 'id': movement.id,
            'clinic_id': item.clinic_id, 'item_id': item.id,
            'batch_id': movement.batch_id, 'batch_number': batch.batch_number if batch else None,
            'item_barcode': item.barcode, 'quantity': movement.quantity,
            'movement_type': movement.movement_type.value,
            'direction': movement.direction.value,
            'reference_type': movement.reference_type, 'reference_id': movement.reference_id,
            'performed_by_id': movement.performed_by_id,
            'created_at': _time_iso(movement.created_at),
        }
        return snapshot, label, movement.quantity

    order = db.session.execute(select(BloodProductOrder).where(
        BloodProductOrder.id == _positive(blood_product_order_id, 'blood_product_order_id'),
        BloodProductOrder.clinic_id == clinic_id, BloodProductOrder.patient_id == patient_id,
    )).scalar_one_or_none()
    if order is None:
        raise NotFoundError('Blood product order not found in patient clinic')
    if (order.status != 'completed' or order.units_issued <= 0
            or order.compatibility_status not in ('compatible', 'emergency_release')
            or order.compatibility_verified_at is None
            or order.compatibility_verified_by_staff_id is None
            or order.fulfilled_by_staff_id is None or order.completed_at is None
            or (order.compatibility_status == 'compatible' and not order.crossmatch_reference)
            or (order.compatibility_status == 'emergency_release' and not order.emergency_release_reason)):
        raise ConflictError('Compatible, fulfilled blood product order required')
    snapshot = {
        'source_type': 'BloodProductOrder', 'id': order.id,
        'clinic_id': order.clinic_id, 'patient_id': order.patient_id,
        'admission_id': order.admission_id, 'version': order.version,
        'product_type': order.product_type, 'units_issued': order.units_issued,
        'status': order.status, 'compatibility_status': order.compatibility_status,
        'crossmatch_reference': order.crossmatch_reference,
        'emergency_release_reason': order.emergency_release_reason,
        'compatibility_verified_at': _time_iso(order.compatibility_verified_at),
        'fulfilled_by_staff_id': order.fulfilled_by_staff_id,
        'completed_at': _time_iso(order.completed_at),
    }
    return snapshot, f'BLOOD-ORDER-{order.id}', order.units_issued


def _evidence_snapshot(row: SurgicalMaterialTrace):
    return {
        'id': row.id, 'clinic_id': row.clinic_id, 'patient_id': row.patient_id,
        'journal_id': row.journal_id, 'trace_kind': row.trace_kind,
        'lab_order_id': row.lab_order_id, 'stock_movement_id': row.stock_movement_id,
        'blood_product_order_id': row.blood_product_order_id,
        'identity_label': row.identity_label, 'quantity': row.quantity,
        'record_note': row.record_note, 'recorded_by_staff_id': row.recorded_by_staff_id,
        'recorded_at': row.recorded_at.isoformat(), 'source_sha256': row.source_sha256,
    }


def _check_trace(row: SurgicalMaterialTrace):
    if not hmac.compare_digest(row.evidence_sha256, _digest(_evidence_snapshot(row))):
        raise ConflictError('Surgical material trace evidence fingerprint mismatch')
    source, label, quantity = _source(
        row.trace_kind, row.clinic_id, row.patient_id, row.journal_id,
        row.lab_order_id, row.stock_movement_id, row.blood_product_order_id,
    )
    if (label != row.identity_label or quantity != row.quantity
            or not hmac.compare_digest(_digest(source), row.source_sha256)):
        raise ConflictError('Surgical material authoritative source changed')


def _all_traces(journal_id: int, clinic_id: int):
    rows = list(db.session.execute(select(SurgicalMaterialTrace).where(
        SurgicalMaterialTrace.journal_id == journal_id,
        SurgicalMaterialTrace.clinic_id == clinic_id,
    ).order_by(SurgicalMaterialTrace.id)).scalars())
    for row in rows:
        _check_trace(row)
    return rows


@transactional
def record_surgical_material_trace(
    *, journal_id: int, clinic_id: int, actor_staff_id: int,
    expected_version: int, trace_kind: str, record_note: str,
    lab_order_id: int | None = None, stock_movement_id: int | None = None,
    blood_product_order_id: int | None = None,
) -> SurgicalMaterialTrace:
    clinic_id = _positive(clinic_id, 'clinic_id')
    journal = _journal(journal_id, clinic_id, lock=True)
    _editable(journal, expected_version)
    _all_traces(journal.id, clinic_id)
    actor = _active_team_member(actor_staff_id, clinic_id)
    if actor.id not in (journal.surgeon_staff_id, journal.anesthetist_staff_id, journal.counting_nurse_staff_id):
        raise ConflictError('Only the assigned surgical team may record trace evidence')
    if trace_kind not in _KINDS:
        raise ValidationError('Unknown surgical material trace kind')
    if not isinstance(record_note, str) or not record_note.strip():
        raise ValidationError('record_note is required')
    record_note = _required_text(record_note, 'record_note', 1200)
    source, label, quantity = _source(
        trace_kind, clinic_id, journal.patient_id, journal.id,
        lab_order_id, stock_movement_id, blood_product_order_id,
    )
    if trace_kind == 'blood_product':
        from app.modules.surgery.models.surgical_case_model import SurgicalCase
        case = db.session.execute(select(SurgicalCase).where(
            SurgicalCase.id == journal.surgical_case_id,
            SurgicalCase.clinic_id == clinic_id,
        )).scalar_one()
        order = db.session.execute(select(BloodProductOrder).where(
            BloodProductOrder.id == blood_product_order_id,
        )).scalar_one()
        if order.admission_id != case.admission_id:
            raise ConflictError('Blood product order admission differs from surgical admission')
    column = {
        'specimen': SurgicalMaterialTrace.lab_order_id,
        'implant': SurgicalMaterialTrace.stock_movement_id,
        'consumable': SurgicalMaterialTrace.stock_movement_id,
        'blood_product': SurgicalMaterialTrace.blood_product_order_id,
    }[trace_kind]
    authority_id = (lab_order_id if trace_kind == 'specimen' else
                    blood_product_order_id if trace_kind == 'blood_product' else stock_movement_id)
    if db.session.execute(select(SurgicalMaterialTrace.id).where(
        column == authority_id,
    )).scalar_one_or_none() is not None:
        raise ConflictError('Authoritative material source already linked to a surgical journal')
    row = SurgicalMaterialTrace(
        clinic_id=clinic_id, patient_id=journal.patient_id, journal_id=journal.id,
        trace_kind=trace_kind, lab_order_id=lab_order_id,
        stock_movement_id=stock_movement_id,
        blood_product_order_id=blood_product_order_id,
        quantity=quantity, identity_label=label, record_note=record_note,
        recorded_by_staff_id=actor.id, source_sha256=_digest(source),
        evidence_sha256='0' * 64,
    )
    db.session.add(row)
    db.session.flush()
    row.evidence_sha256 = _digest(_evidence_snapshot(row))
    journal.version += 1
    db.session.flush()
    create_audit_log(
        action=AuditAction.CREATE, entity_type='SurgicalMaterialTrace', entity_id=row.id,
        description='Linked existing authoritative material evidence; no clinical action performed',
        new_value={'journal_id': journal.id, 'trace_kind': trace_kind,
                   'source_sha256': row.source_sha256, 'evidence_sha256': row.evidence_sha256},
        clinic_id=clinic_id, patient_id=journal.patient_id, user_id=actor.user_id,
    )
    return row


def list_surgical_material_traces(*, journal_id: int, clinic_id: int):
    journal = _journal(journal_id, clinic_id)
    _integrity(journal)
    return _all_traces(journal.id, clinic_id)
