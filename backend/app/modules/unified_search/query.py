"""Bounded projections of source rows, never an independent patient index."""
from sqlalchemy import Integer, String, and_, case, cast, func, literal, or_, select, union_all

from .contracts import RANKS, RESOURCES


TABLES = {
    'patient': 'patients', 'appointment': 'appointments', 'lab_order': 'lab_orders',
    'prescription': 'prescriptions', 'imaging_order': 'imaging_orders',
    'procedure_order': 'procedure_orders', 'blood_product_order': 'blood_product_orders',
    'diet_order': 'diet_orders', 'nursing_order': 'nursing_orders',
    'nursing_task': 'nursing_care_tasks', 'medication_administration': 'medication_administrations',
}
ADMISSION_ORDERS = frozenset({'imaging_order', 'procedure_order', 'blood_product_order', 'diet_order', 'nursing_order'})


def page_statement(tables, *, clinic_id, filters, kinds, after):
    p = tables['patients']
    a, b, w = (tables[k] for k in ('admissions', 'beds', 'wards'))
    c, staff = tables['consultations'], tables['staff']
    plan = tables['nursing_care_plans']
    rx, item = tables['prescriptions'], tables['prescription_items']
    # Match supported patient identity fields literally. SQL wildcard input is
    # escaped, and bound parameters are used for all user text.
    pattern = '%' + filters.query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    identity_match = or_(*(col.ilike(pattern, escape='\\') for col in (
        p.c.first_name, p.c.last_name, p.c.first_name + literal(' ') + p.c.last_name,
        p.c.patient_number, p.c.phone, p.c.email)))

    def admission_lineage(row):
        return select(a.c.id).select_from(a.join(b, a.c.bed_id == b.c.id).join(w, b.c.ward_id == w.c.id)).where(
            a.c.id == row.c.admission_id, a.c.patient_id == p.c.id, w.c.clinic_id == clinic_id).correlate(row, p).exists()

    def consultation_lineage(row):
        return or_(row.c.consultation_id.is_(None), select(c.c.id).where(
            c.c.id == row.c.consultation_id, c.c.patient_id == p.c.id,
            c.c.clinic_id == clinic_id).correlate(row, p).exists())

    def staff_lineage(column, row):
        return select(staff.c.id).where(staff.c.id == column, staff.c.clinic_id == clinic_id).correlate(row).exists()

    branches = []
    for kind in kinds:
        rank = RANKS[kind]
        if after is not None and rank < after[0]:
            continue
        if filters.status is not None and filters.status not in RESOURCES[kind][1]:
            continue
        row = tables[TABLES[kind]]
        is_patient = kind == 'patient'
        source = p if is_patient else row.join(p, row.c.patient_id == p.c.id)
        where = [p.c.clinic_id == clinic_id]
        if not is_patient:
            where.append(row.c.clinic_id == clinic_id)
        if kind == 'appointment':
            where.append(staff_lineage(row.c.staff_id, row))
        if kind in {'lab_order', 'prescription'} | ADMISSION_ORDERS:
            where.append(consultation_lineage(row))
        if kind == 'lab_order':
            where.append(staff_lineage(row.c.ordered_by_id, row))
        if kind == 'prescription':
            where.append(staff_lineage(row.c.prescribed_by_id, row))
        if kind in ADMISSION_ORDERS | {'nursing_task', 'medication_administration'}:
            where.append(admission_lineage(row))
        if kind == 'nursing_task':
            where.append(select(plan.c.id).where(plan.c.id == row.c.care_plan_id,
                plan.c.clinic_id == clinic_id, plan.c.patient_id == p.c.id,
                plan.c.admission_id == row.c.admission_id).correlate(row, p).exists())
        if kind == 'medication_administration':
            where.append(select(rx.c.id).select_from(rx.join(item, item.c.prescription_id == rx.c.id)).where(
                rx.c.id == row.c.prescription_id, item.c.id == row.c.prescription_item_id,
                rx.c.patient_id == p.c.id, rx.c.clinic_id == clinic_id).correlate(row, p).exists())
        status = (case((p.c.is_active.is_(True), 'active'), else_='inactive') if is_patient
                  else func.lower(cast(row.c.status, String())))
        where.append(status.in_(sorted(RESOURCES[kind][1])))
        if filters.status is not None:
            where.append(status == filters.status)
        where.append(row.c.id == filters.exact_id if filters.exact_id is not None else identity_match)
        if after is not None and rank == after[0]:
            where.append(row.c.id < after[1])
        admission = row.c.admission_id if 'admission_id' in row.c else cast(literal(None), Integer())
        # Limit each branch BEFORE UNION, bounding materialized result rows.
        # No claim that all substring searches avoid a clinic-local scan.
        branch = select(cast(literal(rank), Integer()).label('rank'),
            cast(literal(kind), String()).label('resource_type'), row.c.id.label('record_id'),
            p.c.id.label('patient_id'), p.c.first_name, p.c.last_name, p.c.patient_number,
            status.label('status'), admission.label('admission_id')).select_from(source).where(and_(*where))
        bounded = branch.order_by(row.c.id.desc()).limit(filters.limit + 1).subquery()
        branches.append(select(bounded))
    if not branches:
        return None
    combined = (union_all(*branches) if len(branches) > 1 else branches[0]).subquery()
    return select(combined).order_by(combined.c.rank.asc(), combined.c.record_id.desc()).limit(filters.limit + 1)
