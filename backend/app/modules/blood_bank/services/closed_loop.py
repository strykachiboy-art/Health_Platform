"""Inventory-backed clinical order orchestration.

All functions called from transactional services intentionally share that
transaction.  No external messaging, background replay or clinical policy
activation is performed here.
"""
from datetime import datetime, timezone
from flask import current_app
from sqlalchemy import select
from app.extensions import db
from app.core.enums.role_enums import Role
from app.core.enums.audit_enums import AuditAction
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.modules.blood_bank import policy as p
from app.modules.blood_bank.models.blood_bank_model import BloodUnit, BloodDonation
from app.modules.blood_bank.models_extra import (
    BloodSupplier, BloodReleaseEvidence, BloodCompatibilityEvidence,
    BloodReactionEvidence, BloodRecallEvidence, BloodReturnEvidence,
)
from app.modules.blood_bank.services import blood_bank_service as donor
from app.modules.clinical_orders.models.blood_product_order_model import BloodProductOrder
from app.modules.clinical_orders.models.blood_product_issued_unit_model import BloodProductIssuedUnit
from app.modules.clinical_orders.models.blood_product_lab_followup_model import BloodProductLabFollowup
from app.modules.patient.models.patient_model import Patient
from app.modules.ward.models.ward_model import Admission, Bed, Ward
from app.core.enums.ward_enums import AdmissionStatus


def now():
    return datetime.now(timezone.utc)


def validate(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except p.PolicyError as exc:
        raise ValidationError(str(exc)) from exc


def enabled():
    return current_app.testing and current_app.config.get('BLOOD_BANK_INVENTORY_ENABLED') is True


def policy_for(clinic_id):
    if not enabled():
        raise ConflictError('Inventory-backed blood bank operation is disabled')
    policies = current_app.config.get('BLOOD_BANK_POLICIES')
    policy = policies.get(str(clinic_id)) if isinstance(policies, dict) else None
    validate(p.policy_version, policy)
    return policy


def authority(clinic_id, staff_id, key, roles):
    policy = policy_for(clinic_id)
    actor = donor._get_staff(staff_id, clinic_id, allowed_roles=roles,
                             role_message='Blood bank clinical authority is required')
    validate(p.require_actor, policy, key, actor.id)
    return actor, policy


def scoped(model, clinic_id, entity_id, *, lock=False):
    q = select(model).where(model.clinic_id == clinic_id, model.id == validate(p.positive_int, entity_id, 'id'))
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    obj = db.session.execute(q).scalar_one_or_none()
    if obj is None:
        raise NotFoundError('Blood bank record not found in clinic')
    return obj


def audit(actor, obj, description, new_value):
    db.session.flush()
    donor._audit(actor=actor, action=AuditAction.STATUS_CHANGE, entity_type=type(obj).__name__,
                 entity_id=obj.id, description=description, new_value=new_value)


def assert_context(order, *, lock=False):
    q = (select(Admission).join(Bed, Admission.bed_id == Bed.id)
         .join(Ward, Bed.ward_id == Ward.id)
         .where(Admission.id == order.admission_id, Admission.patient_id == order.patient_id,
                Ward.clinic_id == order.clinic_id))
    if lock:
        q = q.with_for_update(of=Admission)
    admission = db.session.execute(q).scalar_one_or_none()
    if admission is None or admission.status != AdmissionStatus.ADMITTED:
        raise ConflictError('An active matching patient admission is required')
    patient = scoped(Patient, order.clinic_id, order.patient_id)
    return admission, patient


def require_bedside_assignment(actor, order):
    from app.modules.nursing.models.admission_care_assignment_model import AdmissionCareAssignment
    kind = {Role.NURSE:'primary_nurse', Role.DOCTOR:'primary_doctor'}.get(actor.user.role)
    if kind is None:
        raise ConflictError('Bedside clinical assignment required')
    row = db.session.execute(select(AdmissionCareAssignment).where(
        AdmissionCareAssignment.clinic_id==order.clinic_id,
        AdmissionCareAssignment.patient_id==order.patient_id,
        AdmissionCareAssignment.admission_id==order.admission_id,
        AdmissionCareAssignment.staff_id==actor.id,
        AdmissionCareAssignment.assignment_kind==kind,
        AdmissionCareAssignment.ended_at.is_(None),
    )).scalar_one_or_none()
    if row is None:
        raise ConflictError('Current admission care assignment is required')
    return row


def assert_current_group(order, patient):
    group = getattr(patient.blood_type, 'value', patient.blood_type)
    if not order.patient_blood_type_verified or group != order.patient_blood_type_verified:
        raise ConflictError('Recipient blood group changed since order verification')
    return group


def assert_unit_usable(unit, policy):
    if unit.recall_blocked or unit.tti_screening_status != 'cleared' or p.db_utc(unit.expires_at) <= now():
        raise ConflictError('Unit is recalled, uncleared, or expired')
    release = db.session.execute(select(BloodReleaseEvidence).where(
        BloodReleaseEvidence.clinic_id == unit.clinic_id,
        BloodReleaseEvidence.blood_unit_id == unit.id,
        BloodReleaseEvidence.policy_version == validate(p.policy_version, policy),
    ).order_by(BloodReleaseEvidence.id.desc()).limit(1)).scalar_one_or_none()
    if release is None or not unit.released_by_staff_id or not unit.released_at:
        raise ConflictError('Current authorized component release evidence is required')
    validate(p.cleared_panel, policy, release.screening_results)
    from app.modules.blood_bank.services import lab_evidence
    lab_evidence.require_release(policy,unit,release.screening_results,release.blood_group_reference)
    if unit.source_type == 'external_blood_bank':
        supplier = scoped(BloodSupplier, unit.clinic_id, unit.external_supplier_id)
        if not supplier.active or supplier.supplier_code not in policy.get('approved_supplier_codes', ()):
            raise ConflictError('Supplier is no longer approved for use')
    return release


@transactional
def register_supplier(*, clinic_id, actor_staff_id, supplier_code, name, approval_reference):
    actor, policy = authority(clinic_id, actor_staff_id, 'supplier_admin_staff_ids', (Role.ADMIN, Role.LAB_TECHNICIAN))
    code = validate(p.identity, supplier_code, 'supplier_code', 80)
    if code not in policy.get('approved_supplier_codes', ()):
        raise ConflictError('Supplier is not on the approved server-side supplier list')
    if db.session.execute(select(BloodSupplier.id).where(BloodSupplier.clinic_id == clinic_id, BloodSupplier.supplier_code == code)).scalar_one_or_none() is not None:
        raise ConflictError('Supplier code already exists')
    supplier = BloodSupplier(clinic_id=clinic_id, supplier_code=code, name=validate(p.text, name, 'name', 200),
                             approval_reference=validate(p.text, approval_reference, 'supplier approval reference', 255),
                             active=True, created_by_staff_id=actor.id)
    db.session.add(supplier)
    audit(actor, supplier, 'Registered approved blood component supplier', {'supplier_code':code})
    return supplier


@transactional
def release_unit(unit_id, *, clinic_id, actor_staff_id, expected_version, screening_results,
                 release_reference, label_check_reference, storage_check_reference, blood_group_reference):
    actor, policy = authority(clinic_id, actor_staff_id, 'release_staff_ids', (Role.LAB_TECHNICIAN,))
    unit = scoped(BloodUnit, clinic_id, unit_id, lock=True)
    if unit.version != validate(p.positive_int, expected_version, 'expected_version'):
        raise ConflictError('Blood component version is stale')
    if unit.status != 'quarantined' or unit.recall_blocked or p.db_utc(unit.expires_at) <= now():
        raise ConflictError('Only eligible, unexpired quarantined units may be released')
    panel = validate(p.cleared_panel, policy, screening_results)
    from app.modules.blood_bank.services import lab_evidence
    lab_evidence.require_release(policy,unit,panel,blood_group_reference)
    if unit.source_type == 'in_house_donation':
        donation = scoped(BloodDonation, clinic_id, unit.donation_id, lock=True)
        if donation.tti_screening_status != 'cleared' or donation.tti_results != panel:
            raise ConflictError('Component evidence must match complete recorded donor screening')
        if donation.screened_by_staff_id == actor.id:
            raise ConflictError('Independent component release authorization is required')
    else:
        supplier = scoped(BloodSupplier, clinic_id, unit.external_supplier_id, lock=True)
        if not supplier.active or supplier.supplier_code not in policy.get('approved_supplier_codes', ()) or not unit.external_reference:
            raise ConflictError('Active approved supplier and receipt evidence required')
        if unit.received_or_created_by_staff_id == actor.id:
            raise ConflictError('Independent external component release authorization is required')
    unit.tti_screening_status = 'cleared'
    unit.status = 'available'
    unit.released_at = now()
    unit.released_by_staff_id = actor.id
    unit.version += 1
    evidence = BloodReleaseEvidence(clinic_id=clinic_id, blood_unit_id=unit.id, unit_version=unit.version,
        policy_version=validate(p.policy_version, policy), screening_results=panel, blood_group_reference=blood_group_reference,
        release_reference=validate(p.text, release_reference, 'release reference', 255),
        actor_staff_id=actor.id)
    # These are attested references, not automatic validation of the supplier's clinical records.
    evidence.label_check_reference = validate(p.text, label_check_reference, 'label check reference', 255)
    evidence.storage_check_reference = validate(p.text, storage_check_reference, 'storage check reference', 255)
    db.session.add(evidence)
    db.session.flush()
    audit(actor, unit, 'Authorized component release', {'release_evidence_id':evidence.id, 'version':unit.version})
    return unit


def assert_order(order, policy):
    if order.status not in {'pending','active','in_progress','on_hold'} or order.compatibility_status not in {'compatible','emergency_release'}:
        raise ConflictError('An active compatibility-verified order is required')
    admission, patient = assert_context(order, lock=True)
    group = assert_current_group(order, patient)
    emergency = order.compatibility_status == 'emergency_release'
    if emergency and (current_app.config.get('EMERGENCY_BLOOD_RELEASE_ENABLED') is not True or
            not order.emergency_release_requested or not order.emergency_requested_by_staff_id or
            not order.emergency_requested_at or not order.emergency_release_reason):
        raise ConflictError('Emergency release requires a signed request and enabled approved emergency policy')
    allowed = validate(p.candidate_groups, policy, order.product_type, group, emergency=emergency)
    return group, emergency, allowed


@transactional
def reserve_order_units(order_id, *, clinic_id, actor_staff_id, expected_order_version, units):
    actor, policy = authority(clinic_id, actor_staff_id, 'compatibility_staff_ids', (Role.LAB_TECHNICIAN,))
    order = scoped(BloodProductOrder, clinic_id, order_id, lock=True)
    if order.version != validate(p.positive_int, expected_order_version, 'expected_order_version'):
        raise ConflictError('Blood order version is stale')
    group, emergency, allowed = assert_order(order, policy)
    if order.units_issued and order.units_issued != len(order.issued_units):
        raise ConflictError('Legacy count-only order cannot be converted into component issuance')
    if not isinstance(units, list) or not units or len(units) > 100:
        raise ValidationError('One or more component reservations are required')
    by_id = {}
    for item in units:
        if not isinstance(item, dict) or set(item) != {'blood_unit_id','expected_version','evidence_reference','special_requirements_reference'}:
            raise ValidationError('Each reservation requires component/version and laboratory evidence references')
        uid = validate(p.positive_int, item['blood_unit_id'], 'blood_unit_id')
        if uid in by_id:
            raise ValidationError('Duplicate component')
        by_id[uid] = item
    existing = db.session.execute(select(BloodUnit.id).where(BloodUnit.clinic_id == clinic_id,
        BloodUnit.reserved_for_order_id == order.id, BloodUnit.status == 'reserved')).scalars().all()
    if existing:
        raise ConflictError('Release existing reservations before replacing them')
    if len(by_id) > order.units_requested - order.units_issued:
        raise ConflictError('Reservation exceeds outstanding requested units')
    locked = [scoped(BloodUnit, clinic_id, uid, lock=True) for uid in sorted(by_id)]
    for unit in locked:
        item = by_id[unit.id]
        if unit.version != validate(p.positive_int, item['expected_version'], 'expected_version') or unit.status != 'available':
            raise ConflictError('Component is unavailable or version is stale')
        if unit.product_type != order.product_type or unit.blood_type not in allowed or (order.product_blood_type and order.product_blood_type != unit.blood_type):
            raise ConflictError('Component does not match approved order compatibility policy')
        release = assert_unit_usable(unit, policy)
        from app.modules.blood_bank.services import lab_evidence
        lab_evidence.require_compatibility(policy,order,unit,item["evidence_reference"],item["special_requirements_reference"],emergency)
        unit.status = 'reserved'
        unit.reserved_for_order_id = order.id
        unit.reserved_at = now()
        unit.reserved_by_staff_id = actor.id
        unit.version += 1
        row = BloodCompatibilityEvidence(clinic_id=clinic_id, order_id=order.id, blood_unit_id=unit.id,
            patient_id=order.patient_id, admission_id=order.admission_id, actor_staff_id=actor.id,
            order_version=order.version, unit_version=unit.version, release_evidence_id=release.id,
            policy_version=validate(p.policy_version, policy), recipient_group=group, component_group=unit.blood_type,
            evidence_reference=validate(p.text, item['evidence_reference'], 'laboratory evidence reference', 255),
            special_requirements_reference=validate(p.text, item['special_requirements_reference'], 'special requirements assessment reference', 255), emergency=emergency)
        db.session.add(row)
        db.session.flush()
        audit(actor, unit, 'Reserved component with order-specific compatibility attestation', {'order_id':order.id,'compatibility_evidence_id':row.id,'version':unit.version})
    order.inventory_required = True
    db.session.flush()
    return locked


@transactional
def release_order_reservations(order_id, *, clinic_id, actor_staff_id, expected_order_version):
    actor, policy = authority(clinic_id, actor_staff_id, 'compatibility_staff_ids', (Role.LAB_TECHNICIAN,))
    order = scoped(BloodProductOrder, clinic_id, order_id, lock=True)
    if order.version != validate(p.positive_int, expected_order_version, 'expected_order_version'):
        raise ConflictError('Blood order version is stale')
    units = donor.release_reserved_units_for_order(order=order, actor=actor)
    order.version += 1
    db.session.flush()
    return units


def issue_inventory(order, actor, *, units_issued, issued_units, fulfillment_note):
    """Called inside canonical order fulfillment's existing transaction."""
    policy = policy_for(order.clinic_id)
    validate(p.require_actor, policy, 'issue_staff_ids', actor.id)
    group, emergency, allowed = assert_order(order, policy)
    if order.units_issued != len(order.issued_units):
        raise ConflictError('Legacy count-only issue cannot acquire fabricated component identities')
    if type(units_issued) is not int or units_issued <= 0 or not isinstance(issued_units,list) or len(issued_units) != units_issued:
        raise ValidationError('Actual component identifiers must account for every issued unit')
    if units_issued > order.units_requested - order.units_issued:
        raise ConflictError('Issuance exceeds outstanding requested quantity')
    labels = {}
    for item in issued_units:
        if not isinstance(item,dict) or set(item) != {'unit_identifier','product_blood_type'}:
            raise ValidationError('Each issue requires a component identifier and blood group')
        label = validate(p.identity, item['unit_identifier'], 'unit_identifier')
        if label in labels:
            raise ValidationError('Duplicate component identifier')
        labels[label] = getattr(item['product_blood_type'], 'value', item['product_blood_type'])
    units = db.session.execute(select(BloodUnit).where(BloodUnit.clinic_id == order.clinic_id,
        BloodUnit.reserved_for_order_id == order.id, BloodUnit.status == 'reserved')
        .order_by(BloodUnit.id).with_for_update()).scalars().all()
    if set(labels) != {x.unit_barcode for x in units}:
        raise ConflictError('Issue exactly the selected reservation set; release and re-reserve before changing it')
    for unit in units:
        release = assert_unit_usable(unit, policy)
        compat = db.session.execute(select(BloodCompatibilityEvidence).where(
            BloodCompatibilityEvidence.clinic_id == order.clinic_id,
            BloodCompatibilityEvidence.order_id == order.id,
            BloodCompatibilityEvidence.blood_unit_id == unit.id,
            BloodCompatibilityEvidence.unit_version == unit.version,
            BloodCompatibilityEvidence.order_version == order.version,
            BloodCompatibilityEvidence.release_evidence_id == release.id,
            BloodCompatibilityEvidence.policy_version == validate(p.policy_version,policy),
        )).scalar_one_or_none()
        if compat is None or compat.patient_id != order.patient_id or compat.admission_id != order.admission_id or compat.recipient_group != group or compat.component_group != unit.blood_type or compat.emergency != emergency:
            raise ConflictError('Current component-specific compatibility evidence required')
        from app.modules.blood_bank.services import lab_evidence
        lab_evidence.require_compatibility(policy,order,unit,compat.evidence_reference,compat.special_requirements_reference,emergency)
        if unit.product_type != order.product_type or unit.blood_type not in allowed or labels[unit.unit_barcode] != unit.blood_type:
            raise ConflictError('Issued component does not match order or recorded blood group')
        unit.status = 'issued'; unit.issued_to_order_id = order.id; unit.issued_at = now(); unit.issued_by_staff_id = actor.id
        unit.reserved_for_order_id = None; unit.reserved_at = None; unit.reserved_by_staff_id = None; unit.version += 1
        issue = BloodProductIssuedUnit(clinic_id=order.clinic_id, patient_id=order.patient_id,
            admission_id=order.admission_id, order_id=order.id, unit_identifier=unit.unit_barcode,
            product_blood_type=unit.blood_type, issued_by_staff_id=actor.id, issued_at=now(),
            blood_unit_id=unit.id, compatibility_evidence_id=compat.id)
        order.issued_units.append(issue)
        db.session.add(issue)
        db.session.flush()
        audit(actor, unit, 'Issued actual physical component', {'order_id':order.id,'issued_unit_id':issue.id,'version':unit.version})
        if emergency:
            db.session.add(BloodProductLabFollowup(clinic_id=order.clinic_id, patient_id=order.patient_id,
                admission_id=order.admission_id, order_id=order.id, issued_unit_id=issue.id,
                status='pending', created_at=issue.issued_at))
    order.inventory_required = True
    order.units_issued += len(units)
    order.fulfilled_by_staff_id = actor.id
    order.fulfillment_note = validate(p.text, fulfillment_note, 'fulfillment_note', 10000)
    order.version += 1
    if order.units_issued == order.units_requested:
        order.status = 'completed'; order.completed_at = now()
    else:
        order.status = 'in_progress'
    audit(actor, order, 'Recorded physical component issuance', {'units_issued':order.units_issued,'status':order.status,'version':order.version})
    return order


def assert_issued_context(order, unit, policy, *, lock_admission=False):
    admission, patient = assert_context(order, lock=lock_admission)
    group = assert_current_group(order, patient)
    release = assert_unit_usable(unit, policy)
    if order.status == 'cancelled' or unit.status != 'issued' or unit.issued_to_order_id != order.id or unit.product_type != order.product_type:
        raise ConflictError('Current issued component and active order context required')
    issue = db.session.execute(select(BloodProductIssuedUnit).where(
        BloodProductIssuedUnit.clinic_id == order.clinic_id, BloodProductIssuedUnit.order_id == order.id,
        BloodProductIssuedUnit.blood_unit_id == unit.id,
        BloodProductIssuedUnit.patient_id == order.patient_id, BloodProductIssuedUnit.admission_id == order.admission_id,
    )).scalar_one_or_none()
    if issue is None or issue.unit_identifier != unit.unit_barcode or issue.product_blood_type != unit.blood_type:
        raise ConflictError('Durable physical issue evidence does not match component')
    compat = scoped(BloodCompatibilityEvidence, order.clinic_id, issue.compatibility_evidence_id)
    if compat.blood_unit_id != unit.id or compat.order_id != order.id or compat.release_evidence_id != release.id or compat.recipient_group != group or compat.component_group != unit.blood_type or compat.policy_version != validate(p.policy_version, policy):
        raise ConflictError('Issue lacks current matching compatibility evidence')
    from app.modules.blood_bank.services import lab_evidence
    lab_evidence.require_compatibility(policy,order,unit,compat.evidence_reference,compat.special_requirements_reference,compat.emergency)
    allowed = validate(p.candidate_groups, policy, order.product_type, group, emergency=compat.emergency)
    if unit.blood_type not in allowed:
        raise ConflictError('Component is incompatible with current approved policy')
    return admission, issue


def recall_locked(unit, actor, reason, reaction=None):
    if unit.recall_blocked:
        return None
    previous = unit.status
    order_id = unit.issued_to_order_id or unit.reserved_for_order_id
    order = scoped(BloodProductOrder, unit.clinic_id, order_id) if order_id else None
    evidence = BloodRecallEvidence(clinic_id=unit.clinic_id, blood_unit_id=unit.id, actor_staff_id=actor.id,
        affected_order_id=order_id, affected_patient_id=order.patient_id if order else None,
        reaction_evidence_id=reaction.id if reaction else None, previous_status=previous, reason=reason)
    unit.recall_blocked=True; unit.version += 1
    if previous in {'quarantined','available','reserved','issued','returned_quarantine'}:
        unit.status='recalled'; unit.reserved_for_order_id=None; unit.reserved_at=None; unit.reserved_by_staff_id=None
    db.session.add(evidence)
    db.session.flush()
    audit(actor, unit, 'Blocked component from further clinical use', {'recall_evidence_id':evidence.id,'previous_status':previous,'affected_order_id':order_id})
    from app.modules.blood_bank.services import safety_acceptance
    safety_acceptance.on_recall(evidence,actor)
    return evidence


@transactional
def recall_components(*, clinic_id, actor_staff_id, reason, donation_id=None, supplier_id=None, din=None):
    actor, policy = authority(clinic_id, actor_staff_id, 'recall_staff_ids', (Role.LAB_TECHNICIAN,))
    reason = validate(p.text, reason, 'recall reason', 5000)
    if (donation_id is None) == (supplier_id is None):
        raise ValidationError('Specify either in-house donation or approved external supplier and DIN')
    q = select(BloodUnit).where(BloodUnit.clinic_id == clinic_id)
    if donation_id is not None:
        scoped(BloodDonation, clinic_id, donation_id)
        q=q.where(BloodUnit.donation_id == donation_id)
    else:
        scoped(BloodSupplier, clinic_id, supplier_id)
        q=q.where(BloodUnit.external_supplier_id == supplier_id, BloodUnit.din == validate(p.identity,din,'din',64))
    units = db.session.execute(q.order_by(BloodUnit.id).with_for_update()).scalars().all()
    if not units:
        raise NotFoundError('No matching components found for recall')
    return [e for u in units if (e:=recall_locked(u,actor,reason)) is not None]


@transactional
def return_issued_component(unit_id, *, clinic_id, actor_staff_id, expected_version, inspection_reference, reason):
    actor, policy = authority(clinic_id, actor_staff_id, 'release_staff_ids', (Role.LAB_TECHNICIAN,))
    unit = scoped(BloodUnit, clinic_id, unit_id, lock=True)
    if unit.version != validate(p.positive_int,expected_version,'expected_version') or unit.status != 'issued' or unit.recall_blocked:
        raise ConflictError('Only current issued unused components may be returned')
    # Retain issuance history and block reissue. No automatic return to available stock.
    row=BloodReturnEvidence(clinic_id=clinic_id,blood_unit_id=unit.id,order_id=unit.issued_to_order_id,actor_staff_id=actor.id,
        inspection_reference=validate(p.text,inspection_reference,'inspection reference',255),reason=validate(p.text,reason,'reason',5000))
    unit.status='returned_quarantine'; unit.version+=1
    db.session.add(row); db.session.flush(); audit(actor,unit,'Returned unused component to quarantine; reissue prohibited',{'return_evidence_id':row.id})
    return unit


@transactional
def deactivate_supplier(supplier_id, *, clinic_id, actor_staff_id, reason):
    actor, policy = authority(clinic_id, actor_staff_id, 'supplier_admin_staff_ids', (Role.ADMIN, Role.LAB_TECHNICIAN))
    supplier = scoped(BloodSupplier, clinic_id, supplier_id, lock=True)
    note = validate(p.text, reason, 'deactivation reason', 5000)
    if not supplier.active:
        raise ConflictError('Supplier is already inactive')
    supplier.active = False
    audit(actor, supplier, 'Deactivated supplier; current release eligibility blocked', {'reason':note})
    return supplier


@transactional
def close_partial_order(order_id, *, clinic_id, actor_staff_id, expected_order_version, reason):
    actor, policy = authority(clinic_id, actor_staff_id, 'partial_close_staff_ids', (Role.DOCTOR,))
    order = scoped(BloodProductOrder, clinic_id, order_id, lock=True)
    if order.version != validate(p.positive_int, expected_order_version, 'expected_order_version'):
        raise ConflictError('Order version is stale')
    if not order.inventory_required or order.status != 'in_progress' or not 0 < order.units_issued < order.units_requested:
        raise ConflictError('Only a partially issued inventory order can be closed')
    if len(order.issued_units) != order.units_issued or any(x.blood_unit_id is None for x in order.issued_units):
        raise ConflictError('Physical issue evidence is incomplete')
    note = validate(p.text, reason, 'partial order closure reason', 10000)
    # Preserve original requested quantity, issued units, and emergency follow-up.
    donor.release_reserved_units_for_order(order=order, actor=actor)
    order.status = 'completed'
    order.completed_at = now()
    order.fulfillment_note = (order.fulfillment_note or '')[:4500] + '\nPartial closure: ' + note[:4500]
    order.version += 1
    audit(actor, order, 'Closed remaining unissued quantity; physical issuance history retained',
          {'requested':order.units_requested, 'issued':order.units_issued, 'reason':note})
    return order
