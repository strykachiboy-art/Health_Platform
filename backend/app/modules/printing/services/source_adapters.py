"""Canonical read-only adapters: billing, appointments, lab orders, RIS draft.

Each adapter fetches from the existing clinic-scoped source and never trusts an
HTTP-provided patient ID, invoice amount, report body, or signature status.
No generic arbitrary-model or arbitrary-column print endpoint is exposed.
"""
from __future__ import annotations
from hashlib import sha256
from sqlalchemy import select

from app.extensions import db
from app.modules.printing.services.print_policy import authorize, build_document, PrintDenied


def _row(model, *, record_id: int, clinic_id: int):
    if type(record_id) is not int or record_id <= 0 or type(clinic_id) is not int or clinic_id <= 0:
        raise PrintDenied('Print unavailable')
    row=db.session.execute(select(model).where(model.id==record_id,
                                       model.clinic_id==clinic_id)).scalar_one_or_none()
    if row is None:
        raise PrintDenied('Print unavailable')
    return row


def _text(value):
    return str(getattr(value,'value',value))


def snapshot(*, kind: str, record_id: int, clinic_id: int,
             actor_role: str, actor_staff_id: int):
    if kind=='invoice':
        from app.modules.billing.models.billing_model import Invoice
        row=_row(Invoice, record_id=record_id, clinic_id=clinic_id)
        authorize(kind=kind,actor_clinic_id=clinic_id,record_clinic_id=row.clinic_id,
                  actor_role=actor_role)
        fields={'invoice_number':str(row.invoice_number),'patient_id':row.patient_id,
                'status':_text(row.status),'currency':row.currency or 'NOT RECORDED',
                'total_amount':str(row.total_amount),'amount_paid':str(row.amount_paid)}
        return build_document(kind=kind,clinic_id=clinic_id,record_id=row.id,
                              source_version=0,source=fields)
    if kind=='appointment':
        from app.modules.appointment.models.appointment_model import Appointment
        row=_row(Appointment, record_id=record_id, clinic_id=clinic_id)
        authorize(kind=kind,actor_clinic_id=clinic_id,record_clinic_id=row.clinic_id,
                  actor_role=actor_role,actor_staff_id=actor_staff_id,
                  owner_staff_id=row.staff_id)
        fields={'patient_id':row.patient_id,'assigned_staff_id':row.staff_id,
                'status':_text(row.status),'scheduled_start':str(row.scheduled_start),
                'scheduled_end':str(row.scheduled_end)}
        return build_document(kind=kind,clinic_id=clinic_id,record_id=row.id,
                              source_version=0,source=fields)
    if kind=='lab_order':
        from app.modules.lab.models.lab_model import LabOrder
        row=_row(LabOrder,record_id=record_id,clinic_id=clinic_id)
        authorize(kind=kind,actor_clinic_id=clinic_id,record_clinic_id=row.clinic_id,
                  actor_role=actor_role)
        fields={'patient_id':row.patient_id,'status':_text(row.status),
                'created_at':str(row.created_at),
                'sample_collected_at':str(row.sample_collected_at) if row.sample_collected_at else 'NOT COLLECTED'}
        return build_document(kind=kind,clinic_id=clinic_id,record_id=row.id,
                              source_version=0,source=fields)
    if kind=='radiology_draft':
        from app.modules.radiology.models.ris_model import (RadiologyExam,RadiologyReport,RadiologyReportVersion)
        from app.modules.clinical_orders.models.imaging_order_model import ImagingOrder
        exam=db.session.execute(select(RadiologyExam).join(ImagingOrder,
            RadiologyExam.imaging_order_id==ImagingOrder.id).where(
            RadiologyExam.id==record_id,ImagingOrder.clinic_id==clinic_id)).scalar_one_or_none()
        if exam is None:raise PrintDenied('Print unavailable')
        order=_row(ImagingOrder,record_id=exam.imaging_order_id,clinic_id=clinic_id)
        authorize(kind=kind,actor_clinic_id=clinic_id,record_clinic_id=order.clinic_id,
                  actor_role=actor_role,actor_staff_id=actor_staff_id,
                  owner_staff_id=order.ordered_by_staff_id)
        report=db.session.execute(select(RadiologyReport).where(
            RadiologyReport.exam_id==exam.id)).scalar_one_or_none()
        if report is None or report.status!='private_draft' or report.version<1:
            raise PrintDenied('No authorized private draft')
        version=db.session.execute(select(RadiologyReportVersion).where(
            RadiologyReportVersion.report_id==report.id,
            RadiologyReportVersion.revision==report.version)).scalar_one_or_none()
        if version is None or sha256(version.content.encode('utf8')).hexdigest()!=version.content_sha256:
            raise PrintDenied('Draft version integrity unavailable')
        fields={'imaging_order_id':order.id,'accession_number':exam.accession_number,
                'report_revision':version.revision,'report_text':version.content}
        return build_document(kind=kind,clinic_id=clinic_id,record_id=exam.id,
                              source_version=version.revision,source=fields)
    raise PrintDenied('Unknown print source')
