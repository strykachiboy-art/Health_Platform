"""Patient-owned, current approved laboratory report copy using RAD6 renderer."""
import json
from flask import current_app
from app.extensions import db
from app.core.enums.audit_enums import AuditAction
from app.core.audit.services.audit_service import create_audit_log
from app.core.utils.decorators import transactional
from app.modules.lab.services import lab_release_service as release
from app.modules.lab.models.lab_model import LabOrder
from app.modules.printing.services.print_policy import authorize, build_document, PrintDenied
from app.modules.printing.services.print_renderer import render_pdf
from app.modules.printing.services.branding_payload import render_authorized


@transactional
def released_pdf(*, order_id, clinic_id, user_id):
    if not current_app.testing or current_app.config.get('RAD6_PRINTING_ENABLED') is not True:
        raise PrintDenied('Print unavailable')
    patient = release._own_patient(user_id, clinic_id)
    order = db.session.execute(release._visible(patient,user_id).where(LabOrder.id==order_id).with_for_update()).scalar_one_or_none()
    if order is None:
        raise release._unavailable()
    report = release._released(order)
    authorize(kind='released_lab_result',actor_clinic_id=clinic_id,record_clinic_id=order.clinic_id,actor_role='PATIENT')
    # The laboratory-specific renderer validates actual glyph coverage.
    # Never silently replace or truncate a result or unit.
    def field(value):
        return 'Not recorded' if value is None else json.dumps(value, ensure_ascii=False)
    lines = []
    structured = []
    for i, row in enumerate(report['results'], 1):
        structured.append(tuple(field(row[key]) for key in ('test_name','loinc_code','result_value','unit','reference_range','flag','resulted_at')))
        lines.extend([f"{i}. Test: {field(row['test_name'])}", f"LOINC: {field(row['loinc_code'])}",
            f"Result: {field(row['result_value'])}   Unit: {field(row['unit'])}",
            f"Reference range: {field(row['reference_range'])}   Flag: {field(row['flag'])}",
            f"Recorded at: {field(row['resulted_at'])}", ''])
    if report.get('correction_verified_at') is not None:
        lines.insert(0, 'Correction verified at: '+field(report['correction_verified_at']))
    text = '\n'.join(lines)
    from app.modules.printing.services import hospital_grade as grade
    extra = grade.patient_fields(patient.id, clinic_id) if grade.enabled() else {}
    doc = build_document(kind='released_lab_result',clinic_id=clinic_id,record_id=order.id,
        source_version=report.get('record_version',1),source={'patient_id':patient.id,'verified_at':report['verified_at'],
        'completed_at':report['completed_at'],'released_at':report['released_at'],'report_text':text,**extra})
    data, branding_audit = render_authorized(doc, render_pdf, owning_clinic_id=order.clinic_id, lab_rows=tuple(structured), correction=field(report['correction_verified_at']) if report.get('correction_verified_at') is not None else None)
    create_audit_log(action=AuditAction.VIEW,entity_type='LabOrder',entity_id=order.id,clinic_id=clinic_id,patient_id=patient.id,user_id=user_id,
        description='Patient downloaded current approved laboratory report copy',new_value={'record_version':doc.source_version,'source_digest':doc.source_digest,**branding_audit})
    return data,doc
