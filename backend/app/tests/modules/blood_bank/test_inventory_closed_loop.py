"""BB-01 synthetic fixture tests. No real clinical operations are authorized.

These tests must run against the fully reconstructed pinned repository; they
have not been executed in the authoring environment.
"""
from datetime import datetime,timedelta,timezone
import pytest
from flask import current_app
from sqlalchemy import select
from app.extensions import db
from app.core.enums.role_enums import Role
from app.core.enums.patient_enums import BloodType
from app.core.exceptions import ConflictError,ValidationError,NotFoundError
from app.modules.blood_bank.services import closed_loop as loop,blood_bank_service as bank,blood_barcode_transfusion_service as barcode
from app.modules.blood_bank.models_extra import BloodReactionEvidence,BloodRecallEvidence
from app.modules.blood_bank.models.blood_bank_model import BloodUnit
from app.modules.clinical_orders.services import blood_product_order_service as orders
from app.tests.modules.clinical_orders.test_blood_product_orders import _context,_create


def panel():
    return {m:{'result':'non_reactive','evidence_reference':'synthetic-'+m} for m in ('hiv','hbv','hcv','syphilis')}


@pytest.fixture
def bb(monkeypatch,clinic,make_patient,make_staff):
    c=_context(monkeypatch=monkeypatch,clinic=clinic,make_patient=make_patient,make_staff=make_staff)
    release=make_staff(clinic=clinic,role=Role.LAB_TECHNICIAN)
    c.update(release=release,clinic=clinic)
    c['verifier']=make_staff(clinic=clinic,role=Role.LAB_TECHNICIAN)
    lab=c['lab_tech'].id;nurse=c['nurse'].id;doc=c['doctor'].id
    current_app.config['BLOOD_BANK_INVENTORY_ENABLED']=True
    current_app.config['BLOOD_BANK_POLICIES']={str(clinic.id):{
        'approved':True,'version':'synthetic-test-only','required_tti_markers':['hiv','hbv','hcv','syphilis'],
        'supplier_admin_staff_ids':[lab],'approved_supplier_codes':['SUP-1'],'receipt_staff_ids':[lab],
        'release_staff_ids':[release.id],'screening_staff_ids':[lab],'intake_staff_ids':[nurse],
        'donor_intake_staff_ids':[nurse,lab],'deferral_staff_ids':[release.id], 'minimum_cooldown_days':90,
        'intake_measurement_ranges':{k:[1,300] for k in ('hemoglobin_g_dl','systolic_bp','diastolic_bp','pulse_bpm')},
        'compatibility_staff_ids':[lab],'issue_staff_ids':[lab],'bedside_staff_ids':[nurse,doc],
        'recall_staff_ids':[release.id],'donor_outreach_staff_ids':[lab],
        'lab_record_staff_ids':[lab], 'lab_verify_staff_ids':[c['verifier'].id], 'patient_release_staff_ids':[doc],
        'patient_lab_result_mapping':{'SYN-CROSS':{'synthetic compatible':'compatible'}},
        'investigation_staff_ids':[release.id,c['verifier'].id,lab], 'stock_alert_staff_ids':[lab], 'report_staff_ids':[lab],
        'lab_artifact_protocol':{
            'screening':{m:{'test_codes':['SYN-SCREEN'],'allowed_results':['non_reactive','reactive','indeterminate']} for m in ('hiv','hbv','hcv','syphilis')},
            'blood_group':{'abo_rh':{'test_codes':['SYN-GROUP'],'allowed_results':['O+','O-','A+']}},
            'compatibility':{'packed_red_blood_cells':{'test_codes':['SYN-CROSS'],'allowed_results':['compatible','incompatible']}},
            'special_requirements':{'packed_red_blood_cells':{'test_codes':['SYN-REQ'],'allowed_results':['requirements_reviewed','not_met']}},
            'confirmatory':{'hiv':{'test_codes':['SYN-CONFIRM'],'allowed_results':['non_reactive','reactive']}},
            'emergency_assessment':{'packed_red_blood_cells':{'test_codes':['SYN-EMERGENCY'],'allowed_results':['authorized','not_authorized']}},
        },
        'barcode_max_age_seconds':30,'component_compatibility':{'packed_red_blood_cells':{'O+':['O+']}},
        'emergency_product_types':['packed_red_blood_cells'],'emergency_compatibility':{'packed_red_blood_cells':{'O+':['O-'],'unknown':['O-']}},
    }}
    # Test fixtures are synthetic, not policy recommendations. Audit service is
    # not mocked for new blood bank paths.
    from app.modules.nursing.models.admission_care_assignment_model import AdmissionCareAssignment
    admin=make_staff(clinic=clinic,role=Role.ADMIN)
    for kind,key in [('primary_nurse','nurse'),('primary_doctor','doctor')]:
        db.session.add(AdmissionCareAssignment(clinic_id=clinic.id,patient_id=c['patient'].id,admission_id=c['admission'].id,staff_id=c[key].id,assignment_kind=kind,assigned_by_user_id=admin.user_id,assignment_reason='synthetic assignment fixture'))
    c['patient'].barcode='PATIENT-TEST-01';db.session.commit()
    return c


def ctx(c,key='lab_tech'):return {'clinic_id':c['clinic'].id,'actor_staff_id':c[key].id}


def supplier(c):
    return loop.register_supplier(**ctx(c),supplier_code='SUP-1',name='Synthetic supplier',approval_reference='fixture')


def receive(c,s,barcode_value='BB-UNIT-01',division='00',group=BloodType.O_POS,**overrides):
    t=datetime.now(timezone.utc)
    data=dict(**ctx(c),unit_barcode=barcode_value,din='DONATION-01',external_supplier_name=s.name,external_supplier_id=s.id,
        external_reference='synthetic-receipt',product_type='packed_red_blood_cells',product_identifier='PROD-1',division_identifier=division,
        blood_type=group,volume_ml=100,collected_at=t-timedelta(hours=1),expires_at=t+timedelta(hours=1))
    data.update(overrides)
    return bank.receive_external_blood_unit(**data)


def release(c,u,**overrides):
    data=dict(**ctx(c,'release'),expected_version=u.version,screening_results=verified_panel(c,u),blood_group_reference=group_reference(u),release_reference='synthetic-release',label_check_reference='fixture-label',storage_check_reference='fixture-storage')
    data.update(overrides);return loop.release_unit(u.id,**data)


def order(c):
    o=_create(c,c['clinic'],units_requested=1)
    return orders.verify_blood_product_compatibility(o.id,**ctx(c),crossmatch_reference='synthetic-crossmatch',product_blood_type=BloodType.O_POS)


def reserve(c,o,u,**overrides):
    data=dict(**ctx(c),expected_order_version=o.version,units=[verified_reservation(c,o,u)])
    data.update(overrides);return loop.reserve_order_units(o.id,**data)


def issue(c,o,u):
    return orders.fulfill_blood_product_order(o.id,**ctx(c),units_issued=1,fulfillment_note='synthetic inventory issue',issued_units=[{'unit_identifier':u.unit_barcode,'product_blood_type':u.blood_type}])


def scan(c,o,u,**overrides):
    data=dict(**ctx(c,'nurse'),patient_barcode=c['patient'].barcode,unit_barcode=u.unit_barcode,scan_timestamp=datetime.now(timezone.utc),scan_device='fixture',operation_identity='OP-01',blood_product_order_id=o.id)
    data.update(overrides);return barcode.verify_blood_transfusion_barcode(**data)


def start(c,o,u,e,**overrides):
    data=dict(**ctx(c,'nurse'),blood_product_order_id=o.id,blood_unit_id=u.id,barcode_verification_event_id=e.id,pre_vitals={'synthetic':True})
    data.update(overrides);return barcode.start_blood_transfusion(**data)


def issued(c):
    s=supplier(c);u=release(c,receive(c,s));o=order(c);reserve(c,o,u);issue(c,o,u);return s,o,u


def test_external_receipt_quarantine_and_full_panel(bb):
    s=supplier(bb);u=receive(bb,s)
    assert u.status=='quarantined' and u.tti_screening_status=='pending'
    with pytest.raises(ValidationError):receive(bb,s,'BB-UNIT-02','01',tti_screening_cleared=True)
    partial=panel();partial.pop('hiv')
    with pytest.raises(ValidationError):release(bb,u,screening_results=partial)
    assert u.status=='quarantined'
    with pytest.raises((ConflictError,ValidationError)):release(bb,u,actor_staff_id=bb['lab_tech'].id)
    assert release(bb,u).status=='available'


def test_split_component_identity(bb):
    s=supplier(bb);a=receive(bb,s);b=receive(bb,s,'BB-UNIT-02','01')
    assert a.din==b.din and a.division_identifier!=b.division_identifier
    with pytest.raises((ConflictError,ValidationError)):receive(bb,s,'BB-UNIT-03','01')


def test_physical_issue_barcode_start_and_completion(bb):
    _,o,u=issued(bb)
    assert o.units_issued==1 and o.issued_units[0].blood_unit_id==u.id and u.status=='issued'
    event=scan(bb,o,u);assert event.verification_result.value=='barcode_verified'
    row=start(bb,o,u,event)
    assert row.status=='in_progress' and u.status=='administration_started'
    with pytest.raises(ConflictError):start(bb,o,u,event)
    row=barcode.complete_blood_transfusion(row.id,**ctx(bb,'nurse'),post_vitals={'synthetic':True})
    assert row.status=='completed' and u.status=='transfused'


def test_count_only_issue_blocked_when_inventory_enabled(bb):
    o=order(bb)
    with pytest.raises(ValidationError):orders.fulfill_blood_product_order(o.id,**ctx(bb),units_issued=1,fulfillment_note='no physical evidence')
    assert o.units_issued==0


def test_wrong_patient_scan_and_stale_scan(bb):
    _,o,u=issued(bb)
    wrong=scan(bb,o,u,patient_barcode='UNKNOWN-PATIENT');assert wrong.verification_result.value=='not_barcode_verified'
    with pytest.raises(ConflictError):start(bb,o,u,wrong)
    stale=scan(bb,o,u,operation_identity='OP-02',scan_timestamp=datetime.now(timezone.utc)-timedelta(minutes=10))
    assert stale.verification_result.value=='not_barcode_verified'


def test_barcode_actor_and_unit_version_binding(bb):
    _,o,u=issued(bb);event=scan(bb,o,u)
    with pytest.raises(ConflictError):start(bb,o,u,event,actor_staff_id=bb['doctor'].id)
    u.version+=1;db.session.commit()
    with pytest.raises(ConflictError):start(bb,o,u,event)


def test_recalled_component_blocks_start(bb):
    s,o,u=issued(bb);event=scan(bb,o,u)
    rows=loop.recall_components(**ctx(bb,'release'),supplier_id=s.id,din=u.din,reason='fixture recall')
    assert rows[0].affected_patient_id==o.patient_id and u.recall_blocked
    with pytest.raises(ConflictError):start(bb,o,u,event)


def test_reaction_history_not_overwritten(bb):
    s=supplier(bb);u=release(bb,receive(bb,s));sibling=release(bb,receive(bb,s,'BB-UNIT-02','01'))
    o=order(bb);reserve(bb,o,u);issue(bb,o,u);e=scan(bb,o,u);t=start(bb,o,u,e)
    for note in ('initial','subsequent'):
        barcode.record_transfusion_reaction(t.id,**ctx(bb,'nurse'),reaction_type='other',reaction_severity='mild',reaction_notes=note)
    rows=db.session.execute(select(BloodReactionEvidence).where(BloodReactionEvidence.transfusion_id==t.id).order_by(BloodReactionEvidence.id)).scalars().all()
    assert [r.notes for r in rows]==['initial','subsequent'] and t.reaction_notes=='initial'
    assert sibling.recall_blocked and sibling.status=='recalled' and u.status=='administration_stopped'


def test_deactivated_supplier_blocks_existing_component_use(bb):
    s=supplier(bb);u=release(bb,receive(bb,s));o=order(bb)
    loop.deactivate_supplier(s.id,**ctx(bb),reason='synthetic supplier hold')
    assert not s.active
    with pytest.raises(ConflictError):reserve(bb,o,u)
    assert u.status=='available'
    assert bank.get_blood_stock_summary(clinic_id=bb['clinic'].id)['total_available']==0


def test_partial_close_preserves_physical_issue(bb):
    c=bb; current_app.config['BLOOD_BANK_POLICIES'][str(c['clinic'].id)]['partial_close_staff_ids']=[c['doctor'].id]
    s=supplier(c);u=release(c,receive(c,s));v=release(c,receive(c,s,'BB-UNIT-02','01'))
    o=_create(c,c['clinic'],units_requested=2)
    orders.verify_blood_product_compatibility(o.id,**ctx(c),crossmatch_reference='fixture',product_blood_type=BloodType.O_POS)
    reserve(c,o,u);issue(c,o,u);reserve(c,o,v)
    assert o.status=='in_progress' and o.units_issued==1
    loop.close_partial_order(o.id,**ctx(c,'doctor'),expected_order_version=o.version,reason='synthetic remainder cancelled')
    assert o.status=='completed' and o.units_requested==2 and o.units_issued==1
    assert o.issued_units[0].blood_unit_id==u.id and u.status=='issued' and v.status=='available'


def test_scoped_records_do_not_expose_other_clinic(bb,make_clinic):
    s=supplier(bb);u=receive(bb,s)
    other=make_clinic()
    with pytest.raises(NotFoundError):loop.scoped(BloodUnit,other.id,u.id)
    with pytest.raises(NotFoundError):loop.scoped(type(s),other.id,s.id)


def test_postgres_concurrent_reservation_has_one_winner(bb):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    if db.engine.dialect.name!='postgresql':
        pytest.skip('Requires dedicated disposable PostgreSQL')
    s=supplier(bb);u=release(bb,receive(bb,s));a=order(bb);b=order(bb)
    app=current_app._get_current_object();cid=bb['clinic'].id;actor=bb['lab_tech'].id
    unit_id=u.id;unit_version=u.version;requests=[(x.id,x.version,verified_reservation(bb,x,u)) for x in (a,b)]
    db.session.commit();db.session.remove();barrier=Barrier(2)
    def attempt(pair):
        with app.app_context():
            barrier.wait(timeout=20)
            try:
                loop.reserve_order_units(pair[0],clinic_id=cid,actor_staff_id=actor,expected_order_version=pair[1],units=[pair[2]])
                return 'reserved'
            except ConflictError:
                return 'blocked'
            finally:
                db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as ex:
        result=list(ex.map(attempt,requests))
    assert sorted(result)==['blocked','reserved']
    row=db.session.get(BloodUnit,unit_id)
    assert row.status=='reserved' and row.reserved_for_order_id in [a[0] for a in requests]


def test_authenticated_tenant_and_role_bound_inventory(bb,client,make_clinic,make_staff,make_user):
    from uuid import uuid4
    from app.modules.clinic.models.organization_model import HospitalOrganization,TenantMembership,ClinicMembership
    from app.core.auth.user.services.user_service import issue_auth_tokens
    app=current_app._get_current_object();s=supplier(bb);u=receive(bb,s)
    def grant(staff,clinic):
        db.session.add(TenantMembership(user_id=staff.user_id,organization_id=clinic.organization_id,role='member',is_active=True,status='active'))
        db.session.flush()
        db.session.add(ClinicMembership(user_id=staff.user_id,organization_id=clinic.organization_id,clinic_id=clinic.id,role=staff.user.role.value,is_active=True))
        db.session.commit()
        with app.test_request_context():
            tokens=issue_auth_tokens(staff.user,clinic_context_id=clinic.id)
        return {'Authorization':'Bearer '+tokens['access_token']}
    own=grant(bb['lab_tech'],bb['clinic']);endpoint='/api/v1/blood-bank/units/'+str(u.id)
    response=client.get(endpoint,headers=own)
    assert response.status_code==200,response.get_json()
    assert response.get_json()['unit_barcode']==u.unit_barcode
    # Other organization, genuine role and membership: never a forged trusted claim.
    org=HospitalOrganization(id=uuid4(),name='Synthetic separate organization');db.session.add(org);db.session.flush()
    other=make_clinic(organization_id=org.id);foreign=make_staff(clinic=other,role=Role.LAB_TECHNICIAN)
    current_app.config['BLOOD_BANK_POLICIES'][str(other.id)]=dict(current_app.config['BLOOD_BANK_POLICIES'][str(bb['clinic'].id)])
    foreign_headers=grant(foreign,other)
    response=client.get(endpoint,headers=foreign_headers)
    assert response.status_code==404,response.get_json()
    assert u.unit_barcode not in response.get_data(as_text=True)
    assert client.get(endpoint).status_code in (401,422)
    nurse=grant(bb['nurse'],bb['clinic'])
    response=client.get(endpoint,headers=nurse)
    assert response.status_code==403,response.get_json()


def test_same_clinic_unassigned_staff_cannot_authorize_barcode(bb,make_staff):
    _,o,u=issued(bb)
    other=make_staff(clinic=bb['clinic'],role=Role.NURSE);db.session.commit()
    current_app.config['BLOOD_BANK_POLICIES'][str(bb['clinic'].id)]['bedside_staff_ids'].append(other.id)
    event=scan(bb,o,u,actor_staff_id=other.id)
    assert event.verification_result.value=='not_barcode_verified'
    assert event.failure_code.value=='unauthorized_actor' and event.patient_id is None
    with pytest.raises(ConflictError):start(bb,o,u,event,actor_staff_id=other.id)


# R02 synthetic laboratory verification fixtures. Existing assertions retained.
from app.modules.blood_bank.services import lab_evidence as evidence, safety_acceptance as safety
from app.modules.blood_bank.models_acceptance import BloodLabArtifact,BloodLabVerification,BloodInvestigationEvent,BloodSafetyInbox


def record_lab(c,reference,kind,marker,code,result,*,verify=True,observed=None,**subject):
    row=db.session.execute(select(BloodLabArtifact).where(BloodLabArtifact.clinic_id==c['clinic'].id,BloodLabArtifact.reference==reference)).scalar_one_or_none()
    if row is None:
        row=evidence.record_artifact(**ctx(c),reference=reference,kind=kind,marker=marker,specimen_identifier='SPECIMEN-1',test_code=code,result=result,source_reference='synthetic source',observed_at=observed or datetime.now(timezone.utc),**subject)
    if verify and db.session.execute(select(BloodLabVerification.id).where(BloodLabVerification.artifact_id==row.id)).scalar_one_or_none() is None:
        evidence.verify_artifact(row.id,**ctx(c,'verifier'),verification_reference='independent synthetic review')
    return row


def verified_panel(c,u):
    subject={'donation_id':u.donation_id} if u.donation_id is not None else {'blood_unit_id':u.id}
    prefix='D'+str(u.donation_id) if u.donation_id is not None else 'U'+str(u.id)
    results={}
    for m in panel():
        row=record_lab(c,prefix+'-'+m,'screening',m,'SYN-SCREEN','non_reactive',**subject)
        results[m]={'result':'non_reactive','evidence_reference':row.reference}
    record_lab(c,prefix+'-group','blood_group','abo_rh','SYN-GROUP',u.blood_type,**subject)
    return results


def group_reference(u):
    return ('D'+str(u.donation_id) if u.donation_id is not None else 'U'+str(u.id))+'-group'


def patient_lab_result(c,o):
    from app.modules.lab.models.lab_model import LabOrder,LabOrderItem,LabTest
    from app.core.enums.lab_enums import LabOrderStatus,LabResultFlag,SampleType
    cid=c['clinic'].id;code='SYN-CROSS'
    test=db.session.execute(select(LabTest).where(LabTest.clinic_id==cid,LabTest.code==code)).scalar_one_or_none()
    if test is None:
        test=LabTest(clinic_id=cid,code=code,name='Synthetic crossmatch result',sample_type=SampleType.BLOOD);db.session.add(test);db.session.flush()
    now=datetime.now(timezone.utc)
    lab=LabOrder(clinic_id=cid,patient_id=o.patient_id,ordered_by_id=c['doctor'].id,status=LabOrderStatus.COMPLETED,created_at=now,sample_collected_at=now,collected_by_id=c['lab_tech'].id,processed_at=now,processed_by_id=c['lab_tech'].id,verified_at=now,verified_by_id=c['verifier'].id,completed_at=now)
    db.session.add(lab);db.session.flush()
    item=LabOrderItem(order_id=lab.id,test_id=test.id,result_value='synthetic compatible',flag=LabResultFlag.NORMAL,resulted_at=now)
    db.session.add(item);db.session.commit();return item


def verified_reservation(c,o,u):
    prefix=f'O{o.id}-U{u.id}'
    emergency=o.compatibility_status=='emergency_release'
    extra={}
    existing=db.session.execute(select(BloodLabArtifact).where(BloodLabArtifact.clinic_id==c['clinic'].id,BloodLabArtifact.reference==prefix+'-match')).scalar_one_or_none()
    if not emergency and existing is None:
        item=patient_lab_result(c,o)
        from app.modules.blood_bank import policy
        extra=dict(lab_order_item_id=item.id,observed=policy.db_utc(item.resulted_at))
    a=record_lab(c,prefix+'-match','emergency_assessment' if emergency else 'compatibility',o.product_type,'SYN-EMERGENCY' if emergency else 'SYN-CROSS','authorized' if emergency else 'compatible',blood_unit_id=u.id,order_id=o.id,**extra)
    b=record_lab(c,prefix+'-requirements','special_requirements',o.product_type,'SYN-REQ','requirements_reviewed',blood_unit_id=u.id,order_id=o.id)
    return {'blood_unit_id':u.id,'expected_version':u.version,'evidence_reference':a.reference,'special_requirements_reference':b.reference}


def test_r02_reference_only_screening_is_not_release(bb):
    s=supplier(bb);u=receive(bb,s)
    with pytest.raises(ConflictError):
        loop.release_unit(u.id,**ctx(bb,'release'),expected_version=u.version,screening_results=panel(),blood_group_reference='unverified-group',release_reference='unverified',label_check_reference='fixture',storage_check_reference='fixture')
    assert u.status=='quarantined'
    assert release(bb,u).status=='available'


def test_r02_independent_verification_and_subject_binding(bb):
    s=supplier(bb);u=receive(bb,s);v=receive(bb,s,'BB-UNIT-02','01')
    row=record_lab(bb,'unverified-hiv','screening','hiv','SYN-SCREEN','non_reactive',verify=False,blood_unit_id=u.id)
    pol=loop.policy_for(bb['clinic'].id)
    with pytest.raises(ConflictError):evidence.require_artifact(pol,u.clinic_id,row.reference,kind='screening',marker='hiv',result='non_reactive',blood_unit_id=u.id)
    pol['lab_verify_staff_ids'].append(bb['lab_tech'].id)
    with pytest.raises(ConflictError):evidence.verify_artifact(row.id,**ctx(bb),verification_reference='self verification')
    evidence.verify_artifact(row.id,**ctx(bb,'verifier'),verification_reference='independent synthetic review')
    with pytest.raises(ConflictError):evidence.require_artifact(pol,u.clinic_id,row.reference,kind='screening',marker='hiv',result='non_reactive',blood_unit_id=v.id)
    assert evidence.require_artifact(pol,u.clinic_id,row.reference,kind='screening',marker='hiv',result='non_reactive',blood_unit_id=u.id)[0].id==row.id


def test_r02_adverse_result_blocks_issued_unit_and_creates_alerts(bb):
    for key in ('nurse','doctor','release'):grant_header(bb,bb[key])
    _,o,u=issued(bb);event=scan(bb,o,u)
    record_lab(bb,'new-adverse','screening','hiv','SYN-SCREEN','reactive',verify=False,blood_unit_id=u.id)
    assert u.recall_blocked and u.status=='recalled'
    with pytest.raises(ConflictError):start(bb,o,u,event)
    recalls=db.session.execute(select(BloodRecallEvidence).where(BloodRecallEvidence.blood_unit_id==u.id)).scalars().all()
    assert len(recalls)==1
    rows=db.session.execute(select(BloodSafetyInbox).where(BloodSafetyInbox.recall_id==recalls[0].id)).scalars().all()
    assert {x.recipient_staff_id for x in rows}>={bb['nurse'].id,bb['doctor'].id,bb['release'].id}
    own=next(x for x in rows if x.recipient_staff_id==bb['nurse'].id)
    with pytest.raises(NotFoundError):safety.acknowledge(own.id,**ctx(bb,'doctor'))
    assert safety.acknowledge(own.id,**ctx(bb,'nurse')).acknowledged_at is not None


def test_r02_investigation_review_does_not_release_recalled_unit(bb):
    s,o,u=issued(bb)
    recall=loop.recall_components(**ctx(bb,'release'),supplier_id=s.id,din=u.din,reason='synthetic review')[0]
    row=record_lab(bb,'confirmation','confirmatory','hiv','SYN-CONFIRM','non_reactive',blood_unit_id=u.id)
    with pytest.raises(ConflictError):safety.record_investigation(**ctx(bb,'verifier'),recall_id=recall.id,operation_identity='REVIEW-EARLY',outcome='reviewed',notes='no evidence')
    safety.record_investigation(**ctx(bb),recall_id=recall.id,artifact_id=row.id,operation_identity='REVIEW-EVIDENCE',outcome='evidence_recorded',notes='verified result')
    with pytest.raises(ConflictError):safety.record_investigation(**ctx(bb,'release'),recall_id=recall.id,operation_identity='SELF-REVIEW',outcome='reviewed',notes='not independent')
    safety.record_investigation(**ctx(bb,'verifier'),recall_id=recall.id,operation_identity='REVIEW-DONE',outcome='reviewed',notes='independently reviewed, hold remains')
    assert u.recall_blocked and u.status=='recalled'


def test_r02_compatibility_requires_matching_verified_order_artifact(bb):
    s=supplier(bb);u=release(bb,receive(bb,s));o=order(bb);other=order(bb)
    bad=verified_reservation(bb,other,u)
    with pytest.raises(ConflictError):loop.reserve_order_units(o.id,**ctx(bb),expected_order_version=o.version,units=[bad])
    assert u.status=='available'
    assert reserve(bb,o,u)[0].status=='reserved'


def test_r02_partial_donor_screening_and_independent_release(bb):
    from decimal import Decimal
    d=bank.register_blood_donor(**ctx(bb,'nurse'),donor_code='DONOR-X',full_name='Synthetic donor',phone='000',blood_type=BloodType.O_POS,cooldown_days=90)
    assert d.consent_to_contact is False
    donation,units=bank.record_blood_donation(**ctx(bb,'nurse'),donor_id=d.id,donation_number='INHOUSE-X',volume_ml=100,hemoglobin_g_dl=Decimal('12'),systolic_bp=100,diastolic_bp=70,pulse_bpm=70,intake_passed=True,intake_notes='synthetic documented intake',components=[dict(unit_barcode='INHOUSE-UNIT-1',product_type='packed_red_blood_cells',product_identifier='PROD-1',division_identifier='00',volume_ml=100,expires_at=datetime.now(timezone.utc)+timedelta(hours=2))])
    u=units[0];row=record_lab(bb,'partial-hiv','screening','hiv','SYN-SCREEN','non_reactive',donation_id=donation.id)
    with pytest.raises(ValidationError):bank.screen_blood_donation(donation.id,**ctx(bb),tti_screening_status='cleared',tti_results={'hiv':{'result':'non_reactive','evidence_reference':row.reference}},confirmed_blood_type=BloodType.O_POS)
    assert u.status=='quarantined'
    full=verified_panel(bb,u)
    bank.screen_blood_donation(donation.id,**ctx(bb),tti_screening_status='cleared',tti_results=full,confirmed_blood_type=BloodType.O_POS,blood_group_reference=f'D{donation.id}-group')
    assert donation.tti_screening_status=='cleared' and u.status=='quarantined'
    assert release(bb,u).status=='available'


def test_r02_stock_alert_is_idempotent_and_report_is_scoped(bb):
    grant_header(bb,bb['lab_tech'])
    from app.modules.blood_bank.models.blood_bank_model import BloodStockThreshold
    db.session.add(BloodStockThreshold(clinic_id=bb['clinic'].id,product_type='packed_red_blood_cells',blood_type='O+',minimum_units=2,critical_units=1,updated_by_staff_id=bb['lab_tech'].id));db.session.commit()
    safety.evaluate_stock(**ctx(bb));safety.evaluate_stock(**ctx(bb))
    rows=db.session.execute(select(BloodSafetyInbox).where(BloodSafetyInbox.clinic_id==bb['clinic'].id,BloodSafetyInbox.kind=='stock_shortage')).scalars().all()
    assert len(rows)==1
    assert safety.report(bb['clinic'].id)['unacknowledged_alerts']==1


def test_r02_barcode_timeline_never_calls_issuance_administration(bb):
    from app.modules.patient.timeline.query import event_selects,page_statement
    _,o,u=issued(bb)
    def events():
        ss=event_selects(db.metadata.tables,clinic_id=o.clinic_id,patient_id=o.patient_id,organization_id=bb['clinic'].organization_id,staff_id=bb['nurse'].id,role='nurse',assigned_clinic=o.clinic_id,categories=('blood_transfusion',),dialect=db.engine.dialect.name,problem_enabled=False,lab_release_enabled=False,blood_enabled=True)
        q=page_statement(ss,start=None,end=None,after=None,limit=100,tables=db.metadata.tables,clinic_id=o.clinic_id)
        return {x['event_type'] for x in db.session.execute(q).mappings()}
    assert events()=={'blood_component_issued'}
    t=start(bb,o,u,scan(bb,o,u))
    assert events()=={'blood_component_issued','blood_transfusion_started'}
    barcode.complete_blood_transfusion(t.id,**ctx(bb,'nurse'),post_vitals={'synthetic':True})
    assert events()=={'blood_component_issued','blood_transfusion_started','blood_transfusion_completed'}


def test_r02_offline_operations_have_no_replay_or_disposition_permission(app):
    from app.core.offline_operations.services.disposition_policies import resolve_disposition_policy
    from app.modules.blood_bank.offline_policy import mutation_contracts
    rules=mutation_contracts(app)
    assert len(rules)>=20 and all(x['automatic_replay'] is False and x['server_no_effect_disposition'] is False for x in rules)
    assert all(resolve_disposition_policy(x['operation_type']) is None for x in rules)


def grant_header(c,staff):
    from app.modules.clinic.models.organization_model import TenantMembership,ClinicMembership
    from app.core.auth.user.services.user_service import issue_auth_tokens
    clinic=c['clinic'];app=current_app._get_current_object()
    db.session.add(TenantMembership(user_id=staff.user_id,organization_id=clinic.organization_id,role='member',is_active=True,status='active'));db.session.flush()
    db.session.add(ClinicMembership(user_id=staff.user_id,organization_id=clinic.organization_id,clinic_id=clinic.id,role=staff.user.role.value,is_active=True));db.session.commit()
    with app.test_request_context(): tokens=issue_auth_tokens(staff.user,clinic_context_id=clinic.id)
    return {'Authorization':'Bearer '+tokens['access_token']}


def test_r02_auth_inbox_notification_and_workspace(bb,client,make_staff):
    own=grant_header(bb,bb['nurse']);lab=grant_header(bb,bb['lab_tech']);verifier=grant_header(bb,bb['verifier'])
    s=supplier(bb);u=receive(bb,s)
    root='/api/v1/blood-bank'
    body=dict(reference='HTTP-ARTIFACT',kind='screening',marker='hiv',specimen_identifier='SPEC-HTTP',test_code='SYN-SCREEN',result='non_reactive',source_reference='synthetic',observed_at=datetime.now(timezone.utc).isoformat(),blood_unit_id=u.id)
    assert client.post(root+'/lab-artifacts',headers=own,json=body).status_code==403
    created=client.post(root+'/lab-artifacts',headers=lab,json=body);assert created.status_code==201,created.get_json()
    aid=created.get_json()['id'];assert created.get_json()['verification'] is None
    verified=client.post(root+f'/lab-artifacts/{aid}/verify',headers=verifier,json={'verification_reference':'independent synthetic review'});assert verified.status_code==200,verified.get_json()
    assert verified.get_json()['verification']['verified_by_staff_id']==bb['verifier'].id
    u=release(bb,u);o=order(bb);reserve(bb,o,u);issue(bb,o,u);t=start(bb,o,u,scan(bb,o,u))
    target=root+f'/patients/{o.patient_id}/transfusions'
    assert client.get(target,headers=own).get_json()['items'][0]['id']==t.id
    unassigned=make_staff(clinic=bb['clinic'],role=Role.NURSE);db.session.commit()
    loop.policy_for(bb['clinic'].id)['bedside_staff_ids'].append(unassigned.id)
    other=grant_header(bb,unassigned)
    response=client.get(target,headers=other);assert response.status_code==200,response.get_json();assert response.get_json()['items']==[]
    recall=loop.recall_components(**ctx(bb,'release'),supplier_id=s.id,din=u.din,reason='synthetic recall')[0]
    inbox=client.get(root+'/safety-inbox',headers=own);assert inbox.status_code==200,inbox.get_json()
    row=next(x for x in inbox.get_json()['items'] if x['recall_id']==recall.id)
    stored=db.session.get(BloodSafetyInbox,row['id']);assert stored.notification_id is not None
    denied=client.post(root+f'/safety-inbox/{row["id"]}/acknowledge',headers=other,json={});assert denied.status_code==404,denied.get_json()
    ok=client.post(root+f'/safety-inbox/{row["id"]}/acknowledge',headers=own,json={});assert ok.status_code==200,ok.get_json()
    assert ok.get_json()['acknowledged_at'] is not None


def test_r02_emergency_release_requires_physical_and_verified_evidence(bb):
    from app.tests.modules.clinical_orders.test_emergency_blood_request_security import _requested,_verify
    current_app.config['EMERGENCY_BLOOD_RELEASE_ENABLED']=True
    s=supplier(bb);u=release(bb,receive(bb,s,group=BloodType.O_NEG))
    o=_requested(bb,bb['clinic'],units_requested=1);_verify(bb,bb['clinic'],o)
    reserve(bb,o,u);issue(bb,o,u)
    assert o.units_issued==1 and len(o.issued_units)==1 and o.issued_units[0].blood_unit_id==u.id
    assert len(o.lab_followups)==1 and o.lab_followups[0].status=='pending'
    t=start(bb,o,u,scan(bb,o,u));assert t.status=='in_progress'


def test_r02_postgres_concurrent_barcode_start_has_one_winner(bb):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    assert db.engine.dialect.name=='postgresql'
    _,o,u=issued(bb);event=scan(bb,o,u)
    app=current_app._get_current_object();args=dict(**ctx(bb,'nurse'),blood_product_order_id=o.id,blood_unit_id=u.id,barcode_verification_event_id=event.id,pre_vitals={'synthetic':True});uid=u.id
    db.session.commit();db.session.remove();barrier=Barrier(2)
    def attempt(_):
        with app.app_context():
            barrier.wait(timeout=20)
            try:barcode.start_blood_transfusion(**args);return 'started'
            except ConflictError:return 'blocked'
            finally:db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as ex:result=list(ex.map(attempt,range(2)))
    assert sorted(result)==['blocked','started']
    from app.modules.blood_bank.models.blood_bank_model import BloodTransfusion
    assert len(db.session.execute(select(BloodTransfusion.id).where(BloodTransfusion.blood_unit_id==uid)).scalars().all())==1


def test_r02_canonical_lab_result_changed_blocks_component(bb):
    from app.modules.lab.models.lab_model import LabOrderItem
    s=supplier(bb);u=release(bb,receive(bb,s));o=order(bb);data=verified_reservation(bb,o,u)
    a=db.session.execute(select(BloodLabArtifact).where(BloodLabArtifact.reference==data['evidence_reference'],BloodLabArtifact.clinic_id==o.clinic_id)).scalar_one()
    item=db.session.get(LabOrderItem,a.lab_order_item_id);item.result_value='synthetic changed';db.session.commit()
    with pytest.raises(ConflictError):loop.reserve_order_units(o.id,**ctx(bb),expected_order_version=o.version,units=[data])
    assert u.status=='available'


def test_r02_patient_history_requires_release_and_hides_stale_version(bb,make_user):
    from app.modules.blood_bank.services import read_history
    c=bb['clinic'];user=make_user(clinic=c,role=Role.PATIENT)
    bb['patient'].user_id=user.id;db.session.commit()
    _,o,u=issued(bb);t=start(bb,o,u,scan(bb,o,u))
    assert read_history.own_history(clinic_id=c.id,patient_id=o.patient_id,user_id=user.id)['items']==[]
    with pytest.raises(ConflictError):read_history.release_patient_history(t.id,**ctx(bb,'doctor'),reference='not finished')
    t=barcode.complete_blood_transfusion(t.id,**ctx(bb,'nurse'),post_vitals={'synthetic':True})
    read_history.release_patient_history(t.id,**ctx(bb,'doctor'),reference='synthetic clinician review')
    rows=read_history.own_history(clinic_id=c.id,patient_id=o.patient_id,user_id=user.id)['items']
    assert len(rows)==1 and rows[0]['status']=='completed' and not any('donor' in k for k in rows[0])
    assert read_history.own_history(clinic_id=c.id,patient_id=o.patient_id,user_id=bb['nurse'].user_id)['items']==[]
    t.version+=1;db.session.commit()
    assert read_history.own_history(clinic_id=c.id,patient_id=o.patient_id,user_id=user.id)['items']==[]
