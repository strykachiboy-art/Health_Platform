"""CE1-S07 ONLY: passive links to Lab, Inventory, Blood authoritative records.

Run THIS module only. Imported earlier fixture helpers are NOT earlier-suite reruns.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.exc import IntegrityError

from app.core.enums.inventory_enums import StockMovementDirection, StockMovementType
from app.core.enums.lab_enums import LabOrderStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.clinical_orders.models.blood_product_order_model import BloodProductOrder
from app.modules.inventory.models.inventory_model import InventoryBatch, StockMovement
from app.modules.surgery.models.surgical_material_trace_model import SurgicalMaterialTrace
from app.modules.surgery.services import surgical_material_trace_service as svc
from app.modules.surgery.services import intraoperative_evidence_service as evidence
from app.tests.modules.surgery.test_surgical_authorization import scenario
from app.tests.modules.surgery.test_surgical_safety_checklist import ready
from app.tests.modules.surgery.test_anesthesia_foundation import anesthesia_ready
from app.tests.modules.surgery.test_intraoperative_evidence_foundation import ready_s06, _journal, _event, _all_counts, _seal


def _utc():
    return datetime.now(timezone.utc)


@pytest.fixture
def workspace(ready_s06, monkeypatch):
    ctx = ready_s06
    monkeypatch.setattr(svc, 'create_audit_log', lambda **kw: ctx['events'].append(kw))
    ctx['journal'] = _journal(ctx)
    return ctx


def _trace(ctx, kind='specimen', **kw):
    values = dict(journal_id=ctx['journal'].id, clinic_id=ctx['clinic'].id,
                  actor_staff_id=ctx['nurse'].id,
                  expected_version=ctx['journal'].version,
                  trace_kind=kind, record_note='Passive evidence link; no clinical action')
    values.update(kw)
    return svc.record_surgical_material_trace(**values)


def _lab(ctx, make_lab_order, *, clinic=None, patient=None, staff=None,
         status=LabOrderStatus.SAMPLE_COLLECTED, collected=True, **kw):
    before = _utc() - timedelta(minutes=20)
    ordered_at = before
    values = dict(created_at=ordered_at)
    if collected:
        values.update(collected_by_id=(staff or ctx['nurse']).id,
                      sample_collected_at=before + timedelta(minutes=2))
    values.update(kw)
    return make_lab_order(clinic or ctx['clinic'], patient or ctx['patient'],
                          staff or ctx['doctor'], [], status=status, **values)


def _stock(ctx, make_inventory_item, *, kind='implant', clinic=None, batch=True,
           movement_type=StockMovementType.USAGE,
           direction=StockMovementDirection.OUT, journal_ref=True, quantity=2):
    target_clinic = clinic or ctx['clinic']
    item = make_inventory_item(target_clinic)
    lot = None
    if batch:
        lot = InventoryBatch(item_id=item.id, batch_number=f'S07-LOT-{item.id}',
                             quantity_on_hand=10, is_active=True)
        db.session.add(lot)
        db.session.flush()
    row = StockMovement(item_id=item.id, batch_id=lot.id if lot else None,
                        movement_type=movement_type, direction=direction,
                        quantity=quantity, performed_by_id=ctx['nurse'].id,
                        reference_type='SurgicalEvidenceJournal' if journal_ref else 'Other',
                        reference_id=ctx['journal'].id)
    db.session.add(row)
    db.session.flush()
    return row, item, lot


def _blood(ctx, *, patient=None, clinic=None, admission_id=None,
           status='completed', compatibility='compatible', units=1):
    now = _utc().replace(tzinfo=None)
    row = BloodProductOrder(
        clinic_id=(clinic or ctx['clinic']).id,
        patient_id=(patient or ctx['patient']).id,
        admission_id=admission_id or ctx['admission'].id,
        ordered_by_staff_id=ctx['doctor'].id,
        fulfilled_by_staff_id=ctx['reviewer'].id if status == 'completed' else None,
        compatibility_verified_by_staff_id=ctx['reviewer'].id if compatibility != 'pending' else None,
        product_type='packed_red_blood_cells', units_requested=2, units_issued=units,
        indication='S07 test-only surgical blood preparation',
        patient_blood_type_at_order='O+',
        patient_blood_type_verified='O+', product_blood_type='O+',
        compatibility_status=compatibility,
        crossmatch_reference='TEST-XMATCH-123' if compatibility == 'compatible' else None,
        emergency_release_reason='Test exception' if compatibility == 'emergency_release' else None,
        compatibility_verified_at=now if compatibility != 'pending' else None,
        status=status,
        fulfillment_note='Issued only, not administered' if status == 'completed' else None,
        completed_at=now if status == 'completed' else None,
        version=3,
    )
    db.session.add(row)
    db.session.flush()
    return row


def test_lab_specimen_links_collected_patient_order_without_collecting(workspace, make_lab_order):
    ctx = workspace
    lab = _lab(ctx, make_lab_order)
    trace = _trace(ctx, lab_order_id=lab.id)
    assert trace.trace_kind == 'specimen' and trace.lab_order_id == lab.id
    assert trace.identity_label == lab.qr_code and trace.quantity == 1
    assert len(trace.evidence_sha256) == 64 and len(trace.source_sha256) == 64
    assert ctx['journal'].status == 'draft' and ctx['case'].status == 'planned'
    assert len(svc.list_surgical_material_traces(journal_id=ctx['journal'].id, clinic_id=ctx['clinic'].id)) == 1
    assert any(x['entity_type'] == 'SurgicalMaterialTrace' for x in ctx['events'])


def test_lab_not_collected_denied(workspace, make_lab_order):
    lab = _lab(workspace, make_lab_order, status=LabOrderStatus.ORDERED, collected=False)
    with pytest.raises(ConflictError, match='collected'):
        _trace(workspace, lab_order_id=lab.id)


def test_lab_collected_timestamp_and_staff_required(workspace, make_lab_order):
    lab = _lab(workspace, make_lab_order, status=LabOrderStatus.ORDERED, collected=False)
    lab.status = LabOrderStatus.SAMPLE_COLLECTED
    db.session.flush()
    with pytest.raises(ConflictError, match='collected'):
        _trace(workspace, lab_order_id=lab.id)


def test_lab_qr_missing_denied(workspace, make_lab_order):
    lab = _lab(workspace, make_lab_order)
    lab.qr_code = None
    db.session.flush()
    with pytest.raises(ConflictError, match='labeled'):
        _trace(workspace, lab_order_id=lab.id)


def test_lab_wrong_patient_denied(workspace, make_lab_order, make_patient):
    other = make_patient(workspace['clinic'])
    lab = _lab(workspace, make_lab_order, patient=other)
    with pytest.raises(NotFoundError, match='patient clinic'):
        _trace(workspace, lab_order_id=lab.id)


def test_lab_foreign_clinic_denied(workspace, make_lab_order, make_clinic, make_patient, make_staff):
    other = make_clinic(name='S07 foreign lab')
    patient = make_patient(other)
    staff = make_staff(other, role=Role.DOCTOR)
    lab = _lab(workspace, make_lab_order, clinic=other, patient=patient, staff=staff)
    with pytest.raises(NotFoundError, match='patient clinic'):
        _trace(workspace, lab_order_id=lab.id)


def test_lab_duplicate_cannot_replay(workspace, make_lab_order):
    lab = _lab(workspace, make_lab_order)
    _trace(workspace, lab_order_id=lab.id)
    with pytest.raises(ConflictError, match='already linked'):
        _trace(workspace, lab_order_id=lab.id)


def test_lab_source_qr_tampering_detected(workspace, make_lab_order):
    lab = _lab(workspace, make_lab_order)
    _trace(workspace, lab_order_id=lab.id)
    lab.qr_code = 'CHANGED-BY-TAMPERING'
    db.session.flush()
    with pytest.raises(ConflictError, match='source changed'):
        svc.list_surgical_material_traces(journal_id=workspace['journal'].id, clinic_id=workspace['clinic'].id)


def test_lab_normal_status_progression_remains_valid(workspace, make_lab_order):
    lab = _lab(workspace, make_lab_order)
    _trace(workspace, lab_order_id=lab.id)
    lab.status = LabOrderStatus.IN_PROGRESS
    db.session.flush()
    assert len(svc.list_surgical_material_traces(journal_id=workspace['journal'].id, clinic_id=workspace['clinic'].id)) == 1


def test_inventory_implant_links_existing_outbound_usage(workspace, make_inventory_item):
    move, item, batch = _stock(workspace, make_inventory_item)
    trace = _trace(workspace, 'implant', stock_movement_id=move.id)
    assert trace.identity_label == batch.batch_number and trace.quantity == move.quantity
    assert item.quantity_on_hand == 100  # No stock deduction by Surgery trace service
    assert trace.stock_movement_id == move.id


def test_inventory_implant_requires_batch(workspace, make_inventory_item):
    move, _, _ = _stock(workspace, make_inventory_item, batch=False)
    with pytest.raises(ConflictError, match='inventory batch'):
        _trace(workspace, 'implant', stock_movement_id=move.id)


def test_inventory_consumable_allows_existing_unbatched_usage(workspace, make_inventory_item):
    move, item, _ = _stock(workspace, make_inventory_item, batch=False)
    row = _trace(workspace, 'consumable', stock_movement_id=move.id)
    assert row.identity_label == item.barcode and row.quantity == 2


def test_stock_movement_must_match_surgical_journal(workspace, make_inventory_item):
    move, _, _ = _stock(workspace, make_inventory_item, journal_ref=False)
    with pytest.raises(ConflictError, match='surgical stock usage'):
        _trace(workspace, 'implant', stock_movement_id=move.id)


def test_stock_movement_restock_not_usage(workspace, make_inventory_item):
    move, _, _ = _stock(workspace, make_inventory_item,
                         movement_type=StockMovementType.RESTOCK, direction=StockMovementDirection.IN)
    with pytest.raises(ConflictError, match='surgical stock usage'):
        _trace(workspace, 'consumable', stock_movement_id=move.id)


def test_stock_movement_foreign_clinic_denied(workspace, make_inventory_item, make_clinic):
    other = make_clinic(name='S07 foreign inventory')
    move, _, _ = _stock(workspace, make_inventory_item, clinic=other)
    with pytest.raises(NotFoundError, match='another clinic'):
        _trace(workspace, 'implant', stock_movement_id=move.id)


def test_stock_movement_source_tampering_detected(workspace, make_inventory_item):
    move, _, _ = _stock(workspace, make_inventory_item)
    _trace(workspace, 'implant', stock_movement_id=move.id)
    move.quantity += 1
    db.session.flush()
    with pytest.raises(ConflictError, match='source changed'):
        svc.list_surgical_material_traces(journal_id=workspace['journal'].id, clinic_id=workspace['clinic'].id)


def test_stock_movement_duplicate_across_kinds_blocked(workspace, make_inventory_item):
    move, _, _ = _stock(workspace, make_inventory_item)
    _trace(workspace, 'implant', stock_movement_id=move.id)
    with pytest.raises(ConflictError, match='already linked'):
        _trace(workspace, 'consumable', stock_movement_id=move.id)


def test_compatible_fulfilled_blood_order_linked_not_transfused(workspace):
    blood = _blood(workspace)
    trace = _trace(workspace, 'blood_product', blood_product_order_id=blood.id)
    assert trace.identity_label == f'BLOOD-ORDER-{blood.id}'
    assert trace.quantity == blood.units_issued
    assert trace.blood_product_order_id == blood.id
    assert workspace['case'].status == 'planned'


def test_emergency_release_with_evidence_allowed_as_issue_link(workspace):
    blood = _blood(workspace, compatibility='emergency_release')
    trace = _trace(workspace, 'blood_product', blood_product_order_id=blood.id)
    assert trace.quantity == 1 and blood.compatibility_status == 'emergency_release'


def test_pending_compatibility_cannot_link_blood(workspace):
    blood = _blood(workspace, status='active', compatibility='pending', units=0)
    with pytest.raises(ConflictError, match='fulfilled'):
        _trace(workspace, 'blood_product', blood_product_order_id=blood.id)


def test_blood_wrong_patient_denied(workspace, make_patient):
    blood = _blood(workspace, patient=make_patient(workspace['clinic']))
    with pytest.raises(NotFoundError, match='patient clinic'):
        _trace(workspace, 'blood_product', blood_product_order_id=blood.id)


def test_blood_foreign_clinic_denied(workspace, make_clinic, make_patient):
    other = make_clinic(name='S07 foreign blood source')
    blood = _blood(workspace, clinic=other, patient=make_patient(other))
    with pytest.raises(NotFoundError, match='patient clinic'):
        _trace(workspace, 'blood_product', blood_product_order_id=blood.id)


def test_blood_duplicate_source_blocked(workspace):
    blood = _blood(workspace)
    _trace(workspace, 'blood_product', blood_product_order_id=blood.id)
    with pytest.raises(ConflictError, match='already linked'):
        _trace(workspace, 'blood_product', blood_product_order_id=blood.id)


def test_blood_authoritative_source_tampering_detected(workspace):
    blood = _blood(workspace)
    _trace(workspace, 'blood_product', blood_product_order_id=blood.id)
    blood.crossmatch_reference = 'ALTERED-XMATCH'
    db.session.flush()
    with pytest.raises(ConflictError, match='source changed'):
        svc.list_surgical_material_traces(journal_id=workspace['journal'].id, clinic_id=workspace['clinic'].id)


def test_non_team_actor_denied(workspace, make_lab_order):
    lab = _lab(workspace, make_lab_order)
    with pytest.raises(ConflictError, match='assigned surgical team'):
        _trace(workspace, lab_order_id=lab.id, actor_staff_id=workspace['reviewer'].id)


def test_stale_journal_version_denied(workspace, make_lab_order):
    lab = _lab(workspace, make_lab_order)
    with pytest.raises(ConflictError, match='version changed'):
        _trace(workspace, lab_order_id=lab.id, expected_version=999)


def test_sealed_journal_rejects_new_traces(workspace, make_lab_order):
    lab = _lab(workspace, make_lab_order)
    _event(workspace, workspace['journal'])
    _all_counts(workspace, workspace['journal'])
    _seal(workspace, workspace['journal'])
    with pytest.raises(ConflictError, match='Sealed surgical documentation'):
        _trace(workspace, lab_order_id=lab.id)


def test_wrong_clinic_cannot_read_traces(workspace, make_lab_order, make_clinic):
    lab = _lab(workspace, make_lab_order)
    _trace(workspace, lab_order_id=lab.id)
    foreign = make_clinic(name='S07 foreign reader')
    with pytest.raises(NotFoundError):
        svc.list_surgical_material_traces(journal_id=workspace['journal'].id, clinic_id=foreign.id)


def test_trace_evidence_tampering_detected(workspace, make_lab_order):
    lab = _lab(workspace, make_lab_order)
    trace = _trace(workspace, lab_order_id=lab.id)
    trace.record_note = 'Tampered'
    db.session.flush()
    with pytest.raises(ConflictError, match='evidence fingerprint'):
        svc.list_surgical_material_traces(journal_id=workspace['journal'].id, clinic_id=workspace['clinic'].id)


def test_bad_kind_source_and_blank_note_denied(workspace, make_lab_order):
    lab = _lab(workspace, make_lab_order)
    with pytest.raises(ValidationError, match='Unknown'):
        _trace(workspace, 'transfusion', lab_order_id=lab.id)
    with pytest.raises(ValidationError, match='matching authoritative source'):
        _trace(workspace, 'implant', lab_order_id=lab.id)
    with pytest.raises(ValidationError, match='required'):
        _trace(workspace, 'specimen', lab_order_id=lab.id, record_note='  ')


def test_db_constraint_prevents_forged_trace_type(workspace, make_lab_order):
    lab = _lab(workspace, make_lab_order)
    trace = _trace(workspace, lab_order_id=lab.id)
    trace.trace_kind = 'blood_product'
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_no_public_routes_or_clinical_execution(workspace, app):
    # S11 permits exactly two authorized GET-only documentation endpoints.
    # No Surgery mutation or operative-execution routes may be registered.
    surgery_routes = {
        (rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
        if "/surgery" in rule.rule
    }
    assert surgery_routes == {
        ("/api/v1/dashboard/surgery", frozenset({"GET"})),
        ("/api/v1/dashboard/surgery/cases/<int:surgical_case_id>", frozenset({"GET"})),
    }
    assert workspace['case'].status == 'planned'
    assert workspace['order'].status == 'active'
    assert workspace['journal'].status == 'draft'
