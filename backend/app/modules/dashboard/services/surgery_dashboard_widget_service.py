"""S11V2 existing-dashboard Surgery projector: seven least-privilege views.

Pure read queries; no additional queues, notifications, domain mutations,
role escalation, operative completion or clinical record release.
"""
from __future__ import annotations

from sqlalchemy import and_, func, select

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.extensions import db
from app.modules.billing.models.billing_model import Invoice
from app.modules.dashboard.schemas.dashboard_schema import DashboardAlertSchema, DashboardMetricSchema
from app.modules.dashboard.schemas.surgery_dashboard_schema import (
    SurgeryDashboardCasePreviewSchema, SurgeryDashboardWidgetSchema,
)
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission, Bed, Ward
from app.modules.surgery.models.surgical_case_model import SurgicalCase
from app.modules.surgery.models.theatre_model import TheatreBooking
from app.modules.surgery.models.intraoperative_evidence_model import SurgicalEvidenceJournal
from app.modules.surgery.models.pacu_preparation_model import SurgicalPacuPreparation
from app.modules.surgery.models.operative_report_model import SurgicalOperativeReport
from app.modules.surgery.models.surgical_clinical_reference_model import SurgicalClinicalReference
from app.modules.surgery.services.surgical_dashboard_read_service import _case_scope


def _valid_cases(clinic_id=None, patient_id=None):
    # Match all custody references, never assume that a case.clinic_id by
    # itself proves patient/bed/ward ownership.
    q = (select(SurgicalCase.id)
         .join(Patient, Patient.id == SurgicalCase.patient_id)
         .join(Admission, Admission.id == SurgicalCase.admission_id)
         .join(Bed, Bed.id == Admission.bed_id)
         .join(Ward, Ward.id == Bed.ward_id)
         .where(
             Patient.clinic_id == SurgicalCase.clinic_id,
             Ward.clinic_id == SurgicalCase.clinic_id,
             Admission.patient_id == SurgicalCase.patient_id,
         ))
    if clinic_id is not None:
        q = q.where(SurgicalCase.clinic_id == clinic_id)
    if patient_id is not None:
        q = q.where(SurgicalCase.patient_id == patient_id)
    return q


def _count(entity, cases, *filters):
    q = select(func.count(entity.id)).where(entity.surgical_case_id.in_(cases), *filters)
    return int(db.session.execute(q).scalar_one() or 0)


def _empty(view, scope='restricted', visibility='restricted'):
    return SurgeryDashboardWidgetSchema(view=view, scope=scope, visibility=visibility)


def _case_counts(cases, *, view, scope, visibility, clinic_id=None, with_previews=False):
    result = dict(view=view, scope=scope, visibility=visibility)
    q = select(func.count(SurgicalCase.id)).where(SurgicalCase.id.in_(cases))
    result['total_cases'] = int(db.session.execute(q).scalar_one() or 0)
    for status, key in (('planned', 'planned'), ('on_hold', 'on_hold'), ('cancelled', 'cancelled')):
        result[key] = int(db.session.execute(select(func.count(SurgicalCase.id)).where(
            SurgicalCase.id.in_(cases), SurgicalCase.status == status,
        )).scalar_one() or 0)
    def scoped(entity):
        return (() if clinic_id is None else (entity.clinic_id == clinic_id,))
    result['sealed_documentation'] = _count(
        SurgicalEvidenceJournal, cases, *scoped(SurgicalEvidenceJournal),
        SurgicalEvidenceJournal.status == 'sealed')
    result['reviewed_pacu_preparations'] = _count(
        SurgicalPacuPreparation, cases, *scoped(SurgicalPacuPreparation),
        SurgicalPacuPreparation.status == 'reviewed')
    result['dual_signed_reports'] = _count(
        SurgicalOperativeReport, cases, *scoped(SurgicalOperativeReport),
        SurgicalOperativeReport.status == 'dual_signed')
    result['pending_signatures'] = _count(
        SurgicalOperativeReport, cases, *scoped(SurgicalOperativeReport),
        SurgicalOperativeReport.status.in_(('draft','surgeon_signed')))
    result['scheduled_bookings'] = _count(
        TheatreBooking, cases, *scoped(TheatreBooking),
        TheatreBooking.status == 'scheduled')
    if with_previews:
        case_rows = db.session.execute(select(SurgicalCase).where(
            SurgicalCase.id.in_(cases),
        ).order_by(SurgicalCase.id.desc()).limit(8)).scalars().all()
        previews = []
        for case in case_rows:
            report = db.session.execute(select(SurgicalOperativeReport.status).where(
                SurgicalOperativeReport.surgical_case_id == case.id,
                *scoped(SurgicalOperativeReport),
            )).scalar_one_or_none()
            pacu = db.session.execute(select(SurgicalPacuPreparation.status).where(
                SurgicalPacuPreparation.surgical_case_id == case.id,
                *scoped(SurgicalPacuPreparation),
            )).scalar_one_or_none()
            previews.append(SurgeryDashboardCasePreviewSchema(
                case_id=case.id, case_status=case.status,
                documentation_status=report or 'not_started',
                pacu_preparation_status=pacu or 'not_started',
            ))
        result['cases'] = previews
    return SurgeryDashboardWidgetSchema(**result)


def build_role_surgery_widget(actor: User) -> SurgeryDashboardWidgetSchema:
    role = actor.role
    if role is Role.SUPER_ADMIN:
        # Cross-clinic AGGREGATES ONLY. Never expose patient/case IDs here.
        return _case_counts(_valid_cases(), view='super_admin',
            scope='system_aggregate', visibility='aggregate_only')

    if role is Role.PATIENT:
        patient = actor.patient
        if patient is None or patient.clinic_id is None:
            return _empty('patient', 'own_patient', 'release_required')
        # No operative report, consent, signature or other clinical detail
        # until a separately authorized patient-release workflow exists.
        cases = _valid_cases(clinic_id=patient.clinic_id, patient_id=patient.id)
        total = int(db.session.execute(select(func.count(SurgicalCase.id)).where(
            SurgicalCase.id.in_(cases))).scalar_one() or 0)
        return SurgeryDashboardWidgetSchema(
            view='patient', scope='own_patient', visibility='release_required',
            total_cases=total, release_required=True,
        )

    clinic_id = actor.clinic_id
    if not isinstance(clinic_id, int) or clinic_id <= 0:
        return _empty('restricted')

    if role is Role.ADMIN:
        return _case_counts(_valid_cases(clinic_id=clinic_id),
            view='management', scope='clinic', visibility='clinic_team',
            clinic_id=clinic_id, with_previews=True)

    if role in (Role.DOCTOR, Role.NURSE):
        staff = db.session.execute(select(Staff).where(
            Staff.user_id == actor.id,
            Staff.clinic_id == clinic_id,
            Staff.status == StaffStatus.ACTIVE,
        )).scalar_one_or_none()
        if staff is None:
            return _empty('doctor' if role is Role.DOCTOR else 'nurse')
        cases = _case_scope(clinic_id=clinic_id, role=role, staff_id=staff.id)
        return _case_counts(cases,
            view='doctor' if role is Role.DOCTOR else 'nurse',
            scope='assigned_cases', visibility='assigned_only',
            clinic_id=clinic_id, with_previews=True)

    if role in (Role.RECEPTIONIST, Role.DRIVER, Role.AMBULANCE_DISPATCHER,
                Role.AMBULANCE_COORDINATOR, Role.OTHER):
        if role is not Role.RECEPTIONIST:
            return _empty('operations')
        # Reception alone may view booking capacity. No PHI/case list.
        cases = _valid_cases(clinic_id=clinic_id)
        scheduled = _count(TheatreBooking, cases,
            TheatreBooking.clinic_id == clinic_id,
            TheatreBooking.status == 'scheduled')
        return SurgeryDashboardWidgetSchema(view='operations',
            scope='operational_aggregate', visibility='aggregate_only',
            scheduled_bookings=scheduled)

    if role is Role.ACCOUNTANT:
        # Invoice links are documentation references, not billable charges.
        # Deduplicate invoices and verify tenant + patient custody.
        link = (select(Invoice.id)
            .join(SurgicalClinicalReference,
                and_(SurgicalClinicalReference.source_id == Invoice.id,
                     SurgicalClinicalReference.source_kind == 'billing',
                     SurgicalClinicalReference.clinic_id == Invoice.clinic_id,
                     SurgicalClinicalReference.patient_id == Invoice.patient_id))
            .join(SurgicalOperativeReport,
                and_(SurgicalOperativeReport.id == SurgicalClinicalReference.report_id,
                     SurgicalOperativeReport.clinic_id == Invoice.clinic_id,
                     SurgicalOperativeReport.patient_id == Invoice.patient_id))
            .where(Invoice.clinic_id == clinic_id).distinct())
        linked = int(db.session.execute(select(func.count()).select_from(link.subquery())).scalar_one() or 0)
        return SurgeryDashboardWidgetSchema(view='finance',
            scope='billing_aggregate', visibility='aggregate_only',
            linked_billing_invoices=linked)

    # Clinical roles outside theatre team are not implicitly granted access.
    return _empty('restricted')


def add_surgery_to_existing_dashboard(result, actor):
    """Augment real dashboards; preserve mock/sentinel dispatch contracts."""
    from app.modules.dashboard.schemas.super_admin_dashboard_schema import SuperAdminDashboardSchema
    from app.modules.dashboard.schemas.management_dashboard_schema import ManagementDashboardSchema
    from app.modules.dashboard.schemas.clinical_dashboard_schema import ClinicalDashboardSchema
    from app.modules.dashboard.schemas.operations_dashboard_schema import OperationsDashboardSchema
    from app.modules.dashboard.schemas.finance_dashboard_schema import FinanceDashboardSchema
    from app.modules.dashboard.schemas.patient_dashboard_schema import PatientDashboardSchema
    supported = (SuperAdminDashboardSchema, ManagementDashboardSchema,
                 ClinicalDashboardSchema, OperationsDashboardSchema,
                 FinanceDashboardSchema, PatientDashboardSchema)
    if not isinstance(result, supported):
        return result
    widget = build_role_surgery_widget(actor)
    updates = {'surgery': widget}
    metrics = list(result.metrics)
    if widget.visibility not in ('restricted', 'release_required'):
        if widget.view in ('super_admin', 'management', 'doctor', 'nurse'):
            metrics.append(DashboardMetricSchema(
                key='surgery_documentation_cases', label='Surgical documentation cases',
                value=widget.total_cases, unit='cases'))
            metrics.append(DashboardMetricSchema(
                key='surgery_pending_document_signatures', label='Surgical reports awaiting signatures',
                value=widget.pending_signatures, unit='reports'))
        elif widget.view == 'operations':
            metrics.append(DashboardMetricSchema(
                key='surgery_scheduled_theatre_bookings', label='Scheduled theatre bookings',
                value=widget.scheduled_bookings, unit='bookings'))
        elif widget.view == 'finance':
            metrics.append(DashboardMetricSchema(
                key='surgery_linked_invoice_records', label='Surgery-linked invoices',
                value=widget.linked_billing_invoices, unit='invoices'))
    # Patient shows an opaque count of own cases, but never unreleased notes.
    if widget.view == 'patient':
        metrics.append(DashboardMetricSchema(
            key='surgery_my_records_pending_release', label='My surgical records awaiting release',
            value=widget.total_cases, unit='records'))
    updates['metrics'] = metrics
    if hasattr(result, 'alerts') and widget.view in ('management','doctor','nurse') and widget.pending_signatures:
        updates['alerts'] = [*result.alerts, DashboardAlertSchema(
            key='surgery_pending_document_signatures',
            severity='warning', title='Operative documentation signatures pending',
            count=widget.pending_signatures,
            description='Documentation review only; not evidence of surgery completion.',
        )]
    return result.model_copy(update=updates)
