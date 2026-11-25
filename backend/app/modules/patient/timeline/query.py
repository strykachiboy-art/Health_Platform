"""Bounded read projection over authoritative existing clinical tables.

No event is derived from updated_at, audit action text, or inferred clinical state.
Only documentary metadata is returned. Existing detail APIs remain authoritative.
"""
from sqlalchemy import DateTime, Integer, String, and_, or_, select, literal, union_all, func, type_coerce, cast


def _ts(column, dialect):
    # All legacy naive clinical timestamps are written in UTC. Normalize native
    # PostgreSQL timestamptz to the same UTC-naive ordering domain. SQLite's
    # DateTime type already stores UTC-naive values in the test profile.
    if dialect == 'postgresql' and getattr(column.type, 'timezone', False):
        return type_coerce(func.timezone('UTC', column), DateTime())
    return type_coerce(column, DateTime())


def event_selects(tables, *, clinic_id, patient_id, organization_id, staff_id, role, assigned_clinic, categories, dialect, problem_enabled, lab_release_enabled, surgery_case_scope=None, blood_enabled=False):
    t = tables
    result = []
    C = t['consultations']
    A = t['admissions']
    B = t['beds']
    W = t['wards']
    P = t['prescriptions']
    PI = t['prescription_items']
    clinic, patient = clinic_id, patient_id

    def scoped(table):
        return and_(table.c.clinic_id == clinic, table.c.patient_id == patient)

    def consultation_lineage(column):
        return or_(column.is_(None), select(C.c.id).where(C.c.id == column, scoped(C)).exists())

    def bed_in_clinic(column):
        return select(B.c.id).join(W, W.c.id == B.c.ward_id).where(B.c.id == column, W.c.clinic_id == clinic).exists()

    def admission_lineage(column):
        return select(A.c.id).join(B, A.c.bed_id == B.c.id).join(W, W.c.id == B.c.ward_id).where(A.c.id == column, A.c.patient_id == patient, W.c.clinic_id == clinic).exists()

    def add(category, event, phase, source_kind, table, time, where, *, source_id=None, actor=None, from_=None, version=None):
        if category not in categories:
            return
        q = select(
            cast(literal(category), String()).label('category'), cast(literal(event), String()).label('event_type'),
            cast(literal(phase), String()).label('phase'), cast(literal(source_kind), String()).label('source_kind'),
            table.c.id.label('event_row_id'), (source_id if source_id is not None else table.c.id).label('source_id'),
            _ts(time, dialect).label('occurred_at'),
            (actor if actor is not None else cast(literal(None), Integer())).label('actor_staff_id'),
            (version if version is not None else cast(literal(None), Integer())).label('version'),
        ).select_from(from_ if from_ is not None else table).where(time.is_not(None), where)
        result.append(q)

    ap = t['appointments']
    add('appointments', 'appointment_booking_recorded', 'recorded', 'appointment', ap, ap.c.created_at, scoped(ap), actor=ap.c.staff_id)
    add('appointments', 'appointment_current_slot', 'scheduled', 'appointment', ap, ap.c.scheduled_start, and_(scoped(ap), ap.c.status.in_(['SCHEDULED','CONFIRMED','COMPLETED','NO_SHOW'])), actor=ap.c.staff_id)
    add('appointments', 'appointment_cancelled', 'cancelled', 'appointment', ap, ap.c.cancelled_at, scoped(ap))

    # Current consultation endpoints use the assigned clinic. Do not broaden
    # that visibility through an alternate clinic-bound session.
    if assigned_clinic == clinic:
        consultation_scope = and_(scoped(C), or_(C.c.appointment_id.is_(None), select(ap.c.id).where(ap.c.id == C.c.appointment_id, scoped(ap)).exists()))
        add('consultations', 'consultation_started', 'recorded', 'consultation', C, C.c.started_at, consultation_scope, actor=C.c.staff_id)
        add('consultations', 'consultation_completed', 'completed', 'consultation', C, C.c.ended_at, and_(consultation_scope, C.c.status == 'COMPLETED'), actor=C.c.staff_id)
        add('consultations', 'consultation_note_finalized', 'recorded', 'consultation', C, C.c.finalized_at, and_(consultation_scope, C.c.record_status.in_(['final','amended'])), actor=C.c.finalized_by_staff_id, version=literal(1))
        # The original clinical document is preserved by the amendment service.
        # This is the date the original diagnosis was finalized, not its onset
        # or the date it was first typed in a draft.
        add('consultations', 'diagnosis_documented_at_finalization', 'recorded', 'consultation', C, C.c.finalized_at, and_(consultation_scope, C.c.record_status.in_(['final','amended']), C.c.diagnosis.is_not(None), func.length(func.trim(C.c.diagnosis)) > 0), actor=C.c.finalized_by_staff_id, version=literal(1))

    v = t['patient_vitals']
    add('vitals', 'vitals_recorded', 'recorded', 'vitals', v, v.c.recorded_at, and_(v.c.patient_id == patient, consultation_lineage(v.c.consultation_id)), actor=v.c.recorded_by_id)
    am = t['clinical_record_amendments']
    pp = t['patient_problems']
    if problem_enabled:
        ps = and_(scoped(pp), pp.c.organization_id == organization_id)
        add('problems', 'problem_added', 'recorded', 'problem', pp, pp.c.recorded_at, ps, actor=pp.c.recorded_by_staff_id, version=literal(1))
        add('problems', 'problem_review_or_change_recorded', 'amended', 'problem', am, am.c.amended_at, and_(scoped(am), ps, am.c.source_record_type == 'patient_problem', am.c.new_version <= pp.c.version), source_id=pp.c.id, actor=am.c.amended_by_staff_id, from_=am.join(pp, am.c.source_record_id == pp.c.id), version=am.c.new_version)

    lo = t['lab_orders']; li = t['lab_order_items']; lt = t['lab_tests']
    ls = and_(scoped(lo), consultation_lineage(lo.c.consultation_id))
    add('laboratory', 'lab_order_created', 'recorded', 'lab_order', lo, lo.c.created_at, ls, actor=lo.c.ordered_by_id)
    add('laboratory', 'lab_sample_collected', 'completed', 'lab_order', lo, lo.c.sample_collected_at, ls, actor=lo.c.collected_by_id)
    add('laboratory', 'lab_sample_processed', 'completed', 'lab_order', lo, lo.c.processed_at, ls, actor=lo.c.processed_by_id)
    add('laboratory', 'lab_result_entry_recorded', 'recorded', 'lab_order', li, li.c.resulted_at, and_(ls, or_(lt.c.clinic_id.is_(None), lt.c.clinic_id == clinic)), source_id=lo.c.id, from_=li.join(lo, li.c.order_id == lo.c.id).join(lt, lt.c.id == li.c.test_id))
    add('laboratory', 'lab_results_verified', 'completed', 'lab_order', lo, lo.c.verified_at, ls, actor=lo.c.verified_by_id)
    add('laboratory', 'lab_order_completed', 'completed', 'lab_order', lo, lo.c.completed_at, ls)
    if lab_release_enabled and role == 'doctor' and assigned_clinic == clinic:
        # Reuse the narrower release-status visibility: ordering or assigned consultation doctor.
        release_scope = and_(ls, or_(lo.c.ordered_by_id == staff_id, select(C.c.id).where(C.c.id == lo.c.consultation_id, scoped(C), C.c.staff_id == staff_id).exists()))
        add('laboratory', 'lab_patient_release_recorded', 'recorded', 'lab_order', lo, lo.c.patient_released_at, release_scope, actor=lo.c.patient_released_by_id, version=literal(1))
        add('laboratory', 'lab_patient_release_withdrawn', 'withdrawn', 'lab_order', lo, lo.c.patient_release_withdrawn_at, release_scope, actor=lo.c.patient_release_withdrawn_by_id, version=literal(1))
        lp = t['lab_report_publications']
        publication_scope = and_(release_scope, select(am.c.id).where(am.c.id == lp.c.amendment_id, scoped(am), am.c.source_record_type == 'lab_report', am.c.source_record_id == lo.c.id, am.c.new_version == lp.c.version, am.c.new_content_sha256 == lp.c.content_sha256).exists())
        add('laboratory', 'lab_corrected_release_recorded', 'recorded', 'lab_order', lp, lp.c.released_at, publication_scope, source_id=lo.c.id, actor=lp.c.released_by_id, from_=lp.join(lo, lp.c.order_id == lo.c.id), version=lp.c.version)
        add('laboratory', 'lab_corrected_release_withdrawn', 'withdrawn', 'lab_order', lp, lp.c.withdrawn_at, publication_scope, source_id=lo.c.id, actor=lp.c.withdrawn_by_id, from_=lp.join(lo, lp.c.order_id == lo.c.id), version=lp.c.version)

    if role == 'doctor':
        add('prescriptions', 'prescription_issued', 'recorded', 'prescription', P, P.c.issued_at, and_(scoped(P), consultation_lineage(P.c.consultation_id)), actor=P.c.prescribed_by_id)

    e = t['medication_administrations']
    es = and_(scoped(e), admission_lineage(e.c.admission_id), select(P.c.id).join(PI, PI.c.prescription_id == P.c.id).where(P.c.id == e.c.prescription_id, PI.c.id == e.c.prescription_item_id, scoped(P)).exists())
    for event, phase, field, actor, status in [
        ('medication_administered','completed','administered_at','administered_by_staff_id','administered'),
        ('medication_held','held','held_at','held_by_staff_id','held'),
        ('medication_refused','refused','refused_at','refused_recorded_by_staff_id','refused'),
        ('medication_missed','missed','missed_at',None,'missed'),
        ('medication_cancelled','cancelled','cancelled_at',None,'cancelled'),
    ]:
        add('medication_administration', event, phase, 'medication_administration', e, e.c[field], and_(es, e.c.status == status), actor=e.c[actor] if actor else None)
    add('medication_administration', 'medication_current_slot', 'scheduled', 'medication_administration', e, e.c.scheduled_at, and_(es, e.c.status.in_(['scheduled','due'])))

    io = t['imaging_orders']; po = t['procedure_orders']; no = t['nursing_orders']
    def order_scope(table):
        return and_(scoped(table), admission_lineage(table.c.admission_id), consultation_lineage(table.c.consultation_id))
    add('imaging', 'imaging_order_created', 'recorded', 'imaging_order', io, io.c.ordered_at, order_scope(io), actor=io.c.ordered_by_staff_id)
    add('imaging', 'imaging_current_slot', 'scheduled', 'imaging_order', io, io.c.scheduled_at, and_(order_scope(io), io.c.status != 'cancelled'), actor=io.c.ordered_by_staff_id)
    add('imaging', 'imaging_order_completed', 'completed', 'imaging_order', io, io.c.completed_at, and_(order_scope(io), io.c.status == 'completed'), actor=io.c.completed_by_staff_id)
    # No clinical diagnostic report event from synthetic RAD-C freeze/release.

    ads = and_(A.c.patient_id == patient, bed_in_clinic(A.c.bed_id))
    add('admissions', 'patient_admitted', 'completed', 'admission', A, A.c.admitted_at, ads, actor=A.c.admitted_by_id)
    add('admissions', 'patient_discharged', 'completed', 'admission', A, A.c.discharged_at, and_(ads, A.c.status == 'DISCHARGED'))
    tr = t['ward_transfers']
    add('admissions', 'bed_transfer_recorded', 'completed', 'admission', tr, tr.c.transferred_at, and_(ads, bed_in_clinic(tr.c.to_bed_id), or_(tr.c.from_bed_id.is_(None), bed_in_clinic(tr.c.from_bed_id))), source_id=A.c.id, from_=tr.join(A, tr.c.admission_id == A.c.id))
    ds = t['discharge_summaries']
    dscope = and_(scoped(ds), admission_lineage(ds.c.admission_id))
    add('admissions', 'discharge_summary_finalized', 'recorded', 'discharge_summary', ds, ds.c.finalized_at, and_(dscope, ds.c.record_status.in_(['final','amended'])), source_id=ds.c.admission_id, actor=ds.c.finalized_by_staff_id, version=literal(1))

    nt = t['nursing_care_tasks']; np = t['nursing_care_plans']; nn = t['nursing_notes']; nx = t['nursing_order_executions']
    ns = and_(scoped(nt), admission_lineage(nt.c.admission_id), select(np.c.id).where(np.c.id == nt.c.care_plan_id, scoped(np), np.c.admission_id == nt.c.admission_id).exists())
    add('nursing', 'nursing_task_completed', 'completed', 'nursing_task', nt, nt.c.completed_at, and_(ns, nt.c.status == 'COMPLETED'), actor=nt.c.completed_by_staff_id)
    add('nursing', 'nursing_order_created', 'recorded', 'nursing_order', no, no.c.ordered_at, order_scope(no), actor=no.c.ordered_by_staff_id)
    add('nursing', 'nursing_order_execution_recorded', 'completed', 'nursing_order', nx, nx.c.performed_at, and_(scoped(nx), order_scope(no), nx.c.admission_id == no.c.admission_id), source_id=no.c.id, actor=nx.c.performed_by_staff_id, from_=nx.join(no, nx.c.nursing_order_id == no.c.id))
    nscope = and_(scoped(nn), admission_lineage(nn.c.admission_id))
    add('nursing', 'nursing_note_signed', 'recorded', 'nursing_note', nn, nn.c.signed_at, and_(nscope, nn.c.status == 'SIGNED'), actor=nn.c.author_staff_id, version=literal(1))
    add('procedures', 'procedure_performance_recorded', 'completed', 'procedure_order', po, po.c.performed_at, and_(order_scope(po), po.c.status == 'completed', po.c.procedure_type != 'surgical'), actor=po.c.completed_by_staff_id)
    # Existing legacy surgical orders may have real recorded completion. Modern
    # Surgery documentation is not operation-performance evidence.
    if surgery_case_scope is not None and assigned_clinic == clinic:
        sc = t['surgical_cases']
        sscope = and_(scoped(sc), sc.c.id.in_(surgery_case_scope), admission_lineage(sc.c.admission_id), select(po.c.id).where(po.c.id == sc.c.procedure_order_id, order_scope(po), po.c.admission_id == sc.c.admission_id).correlate(sc).exists())
        add('surgery', 'surgical_case_plan_recorded', 'recorded', 'surgical_case', sc, sc.c.created_at, sscope, actor=sc.c.planned_by_staff_id)
        # A completed historical surgical order is not exposed unless a case
        # is visible under the existing participant authorization.
        add('surgery', 'surgical_procedure_performance_recorded', 'completed', 'procedure_order', po, po.c.performed_at, and_(order_scope(po), po.c.status == 'completed', po.c.procedure_type == 'surgical', select(sc.c.id).where(sscope, sc.c.procedure_order_id == po.c.id).correlate(po).exists()), actor=po.c.completed_by_staff_id)
        sr = t['surgical_operative_reports']; ss = t['surgical_operative_signatures']
        add('surgery', 'operative_document_signature_recorded', 'recorded', 'surgical_report', ss, ss.c.signed_at, and_(ss.c.clinic_id == clinic, scoped(sr), sscope, sr.c.admission_id == sc.c.admission_id, or_(sr.c.status == 'dual_signed', and_(sr.c.status == 'surgeon_signed', ss.c.signer_kind == 'surgeon')), ss.c.document_sha256 == sr.c.content_sha256), source_id=sr.c.id, actor=ss.c.signed_by_staff_id, from_=ss.join(sr, ss.c.report_id == sr.c.id).join(sc, sr.c.surgical_case_id == sc.c.id), version=sr.c.version)

    # Only documentary issue/administration events, never donor identifiers.
    if blood_enabled and role in ('doctor','nurse') and assigned_clinic == clinic:
        bo=t['blood_product_orders']; bu=t['blood_product_issued_units']; bt=t['blood_transfusions']; br=t['blood_reaction_evidence']
        ass=t['admission_care_assignments']
        own=select(ass.c.id).where(ass.c.clinic_id==clinic,ass.c.patient_id==patient,ass.c.admission_id==bo.c.admission_id,ass.c.staff_id==staff_id,ass.c.ended_at.is_(None)).exists()
        bs=and_(order_scope(bo),own)
        add('blood_transfusion','blood_component_issued','recorded','blood_product_order',bu,bu.c.issued_at,and_(bs,scoped(bu),bu.c.admission_id==bo.c.admission_id,bu.c.blood_unit_id.is_not(None)),source_id=bo.c.id,actor=bu.c.issued_by_staff_id,from_=bu.join(bo,bu.c.order_id==bo.c.id))
        ts=and_(bs,scoped(bt),bt.c.admission_id==bo.c.admission_id)
        add('blood_transfusion','blood_transfusion_started','recorded','blood_transfusion',bt,bt.c.started_at,ts,actor=bt.c.administered_by_staff_id,from_=bt.join(bo,bt.c.blood_product_order_id==bo.c.id))
        add('blood_transfusion','blood_transfusion_completed','completed','blood_transfusion',bt,bt.c.ended_at,and_(ts,bt.c.status=='completed'),actor=bt.c.administered_by_staff_id,from_=bt.join(bo,bt.c.blood_product_order_id==bo.c.id))
        add('blood_transfusion','blood_transfusion_stopped','recorded','blood_transfusion',bt,bt.c.ended_at,and_(ts,bt.c.status.in_(['stopped_reaction','stopped_other'])),from_=bt.join(bo,bt.c.blood_product_order_id==bo.c.id))
        add('blood_transfusion','blood_reaction_documented','recorded','blood_transfusion',br,br.c.created_at,and_(ts,br.c.clinic_id==clinic),source_id=bt.c.id,actor=br.c.actor_staff_id,from_=br.join(bt,br.c.transfusion_id==bt.c.id).join(bo,bt.c.blood_product_order_id==bo.c.id))

    # Historical amendment metadata: no snapshots, amendment reasons, notes,
    # or current values are copied into the timeline. Source ownership is joined.
    def amendment(category, record_type, kind, parent, parent_scope, source_id=None):
        add(category, record_type + '_amendment_recorded', 'amended', kind, am, am.c.amended_at,
            and_(scoped(am), parent_scope, am.c.source_record_type == record_type, am.c.new_version <= parent.c.record_version),
            source_id=source_id if source_id is not None else parent.c.id, actor=am.c.amended_by_staff_id, from_=am.join(parent, am.c.source_record_id == parent.c.id), version=am.c.new_version)
    if assigned_clinic == clinic:
        amendment('consultations','consultation_note','consultation',C,consultation_scope)
    amendment('nursing','nursing_note','nursing_note',nn,nscope)
    amendment('medication_administration','medication_administration','medication_administration',e,es)
    amendment('admissions','discharge_summary','discharge_summary',ds,dscope,ds.c.admission_id)
    return result


def page_statement(selects, *, start, end, after, limit, tables, clinic_id):
    if not selects:
        return None
    events = union_all(*selects).subquery('clinical_events') if len(selects) > 1 else selects[0].subquery('clinical_events')
    c = events.c
    q = select(events)
    if start is not None:
        q = q.where(c.occurred_at >= start.replace(tzinfo=None))
    if end is not None:
        q = q.where(c.occurred_at < end.replace(tzinfo=None))
    if after is not None:
        timestamp, kind, row_id = after
        timestamp = timestamp.replace(tzinfo=None)
        q = q.where(or_(c.occurred_at < timestamp, and_(c.occurred_at == timestamp, c.event_type > kind), and_(c.occurred_at == timestamp, c.event_type == kind, c.event_row_id < row_id)))
    page = q.order_by(c.occurred_at.desc(), c.event_type.asc(), c.event_row_id.desc()).limit(limit + 1).subquery('event_page')
    staff = tables['staff']; dept = tables['departments']
    # Staff and department display are current registry metadata, not inferred
    # historical department attribution; foreign actor metadata is never exposed.
    return select(page, staff.c.id.label('visible_staff_id'), staff.c.first_name, staff.c.last_name, dept.c.name.label('current_department_name')).select_from(page.outerjoin(staff, and_(page.c.actor_staff_id == staff.c.id, staff.c.clinic_id == clinic_id)).outerjoin(dept, and_(staff.c.department_id == dept.c.id, dept.c.clinic_id == clinic_id))).order_by(page.c.occurred_at.desc(), page.c.event_type.asc(), page.c.event_row_id.desc())
