"""Surgery B: authenticated documentation-only HTTP boundary over CE1 services.

No operative execution, anesthesia induction, eMAR, transfer, PACU arrival,
Sign-Out, procedure completion, discharge, or billing clearance route exists.
Document bytes are transient digest input; references are attestations, NOT a
verified durable document store or a professional licensing registry.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime

from flask import Blueprint, g, jsonify, request
from flask_jwt_extended import get_jwt
from sqlalchemy import select

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import NotFoundError, ValidationError
from app.core.utils.decorators import (
    active_staff_required, get_current_clinic_id, role_required,
)
from app.extensions import db
from app.modules.staff.models.staff_model import Staff
from app.modules.surgery.models.anesthesia_model import AnesthesiaPreoperativeAssessment
from app.modules.surgery.models.operative_report_model import SurgicalOperativeReport
from app.modules.surgery.models.surgical_authorization_model import SurgicalConsentEvidence
from app.modules.surgery.models.theatre_model import TheatreBooking
from app.modules.surgery.schemas.surgery_http_schema import (
    BaselineBody, ChecklistBody, ConsentRecordBody, ConsentVerifyBody,
    CreateReportBody, CreateTheatreBody, PlanCaseBody, PreanesthesiaBody,
    PrivilegeBody, RescheduleBody, ReviewBody, ReviseReportBody, ScheduleBody,
    SignReportBody, VersionReasonBody,
)
from app.modules.surgery.services import (
    anesthesia_service as anesthesia,
    operative_report_service as reports,
    surgical_authorization_service as authority,
    surgical_case_service as cases,
    surgical_checklist_service as checklist,
    surgical_dashboard_read_service as read,
    theatre_booking_service as booking,
)

surgery_http_bp = Blueprint("surgery_http", __name__, url_prefix="/surgery")

# This API accepts at most 256 KiB of source-document evidence per request.
# CE1 stores a fingerprint only, not the document itself. Larger documents
# remain outside this HTTP contract until a protected document store exists.
_MAX_DOCUMENT_BYTES = 256 * 1024
_MAX_JSON_BYTES = 1024 * 1024
_READERS = (Role.ADMIN, Role.SUPER_ADMIN, Role.DOCTOR, Role.NURSE)


@surgery_http_bp.after_request
def _do_not_cache(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _clinic() -> int:
    return get_current_clinic_id(required=True)


def _current_user():
    # role_required() resolves the verified JWT and clinic selection before
    # the endpoint; this second DB lookup also rejects stale JWT role claims.
    user_id = getattr(g, "current_user_id", None)
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise ValidationError("Authenticated user unavailable")
    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        raise ValidationError("Active authenticated user required")
    return user


def _actor(roles: tuple[Role, ...]):
    clinic_id = _clinic()
    user = _current_user()
    # A forged or stale selected-clinic claim must never silently
    # authorize a Surgery mutation against a different clinic.
    # Normal non-super-admin tokens omit this optional claim.
    requested_context = get_jwt().get("clinic_context_id")
    if requested_context is not None and (
        type(requested_context) is not int or requested_context != clinic_id
    ):
        raise NotFoundError("Surgery action unavailable in selected clinic")
    if user.role not in roles or user.role == Role.SUPER_ADMIN:
        raise NotFoundError("Surgery action unavailable")
    if user.clinic_id != clinic_id:
        raise NotFoundError("Surgery action unavailable in selected clinic")
    staff = db.session.execute(select(Staff).where(
        Staff.user_id == user.id, Staff.clinic_id == clinic_id,
    )).scalar_one_or_none()
    if staff is None or staff.status != StaffStatus.ACTIVE:
        raise NotFoundError("Active clinic staff required for Surgery action")
    return clinic_id, staff


def _reader():
    clinic_id = _clinic()
    user = _current_user()
    if user.role not in _READERS:
        raise NotFoundError("Surgery read unavailable")
    if user.role != Role.SUPER_ADMIN:
        if user.clinic_id != clinic_id:
            raise NotFoundError("Surgery read unavailable in selected clinic")
        if user.role in (Role.DOCTOR, Role.NURSE):
            staff = db.session.execute(select(Staff).where(
                Staff.user_id == user.id, Staff.clinic_id == clinic_id,
            )).scalar_one_or_none()
            if staff is None or staff.status != StaffStatus.ACTIVE:
                raise NotFoundError("Active clinical staff required")
    return clinic_id, user


def _body(schema):
    # No implicit idempotency guarantee. Surgery C must use one-shot writes
    # followed by authoritative GET reconciliation after uncertain outcomes.
    if "Idempotency-Key" in request.headers:
        raise ValidationError("Surgery writes do not support idempotency keys")
    if not request.is_json or request.content_length is None or (
        request.content_length < 2 or request.content_length > _MAX_JSON_BYTES
    ):
        raise ValidationError("A bounded JSON object body is required")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError("A JSON object body is required")
    return schema.model_validate(payload)


def _evidence(text: str) -> bytes:
    # Strict canonical base64 protects against malformed and overlarge uploads.
    if not isinstance(text, str) or len(text) > 349528:
        raise ValidationError("Document evidence is too large")
    try:
        data = base64.b64decode(text.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ValidationError("Invalid document evidence encoding") from exc
    if not data or len(data) > _MAX_DOCUMENT_BYTES or (
        base64.b64encode(data).decode("ascii") != text
    ):
        raise ValidationError("Document evidence must be nonempty canonical base64")
    return data


def _positive_id(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(f"Invalid {name}")
    return value


def _serialize(row, *fields: str):
    # No implicit ORM serialization: document contents, signer names,
    # license references, narrative PHI, and credentials never leak here.
    data = {}
    for name in fields:
        value = getattr(row, name)
        if isinstance(value, datetime):
            # CE1 persists UTC naive datetimes. Return an explicit UTC marker.
            value = value.isoformat() + ("Z" if value.tzinfo is None else "")
        data[name] = value
    return data


def _reply(data, status: int = 200):
    return jsonify({
        "success": True,
        "data": data,
        "documentation_only": True,
        "operative_completion_authorized": False,
        "anesthesia_induction_authorized": False,
        "medication_administration_authorized": False,
        "nursing_transfer_authorized": False,
        "billing_clearance_authorized": False,
    }), status


@surgery_http_bp.post("/cases")
@role_required(Role.DOCTOR)
@active_staff_required
def plan_case_http():
    clinic, staff = _actor((Role.DOCTOR,))
    body = _body(PlanCaseBody)
    row = cases.plan_surgical_case(
        procedure_order_id=body.procedure_order_id,
        clinic_id=clinic, actor_staff_id=staff.id,
    )
    return _reply(_serialize(row, "id", "clinic_id", "procedure_order_id", "status", "version"), 201)


@surgery_http_bp.post("/theatres")
@role_required(Role.ADMIN)
@active_staff_required
def create_theatre_http():
    clinic, staff = _actor((Role.ADMIN,))
    body = _body(CreateTheatreBody)
    row = booking.create_operating_theatre(
        clinic_id=clinic, actor_staff_id=staff.id, **body.model_dump(),
    )
    return _reply(_serialize(row, "id", "clinic_id", "code", "is_active"), 201)


@surgery_http_bp.post("/bookings")
@role_required(Role.DOCTOR)
@active_staff_required
def book_theatre_http():
    clinic, staff = _actor((Role.DOCTOR,))
    body = _body(ScheduleBody)
    row = booking.schedule_theatre(
        clinic_id=clinic, actor_staff_id=staff.id, **body.model_dump(),
    )
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id", "theatre_id", "status", "version"), 201)


@surgery_http_bp.patch("/bookings/<int:booking_id>")
@role_required(Role.DOCTOR)
@active_staff_required
def reschedule_http(booking_id: int):
    clinic, staff = _actor((Role.DOCTOR,))
    body = _body(RescheduleBody)
    row = booking.reschedule_theatre(
        booking_id=_positive_id(booking_id, "booking_id"), clinic_id=clinic,
        actor_staff_id=staff.id, **body.model_dump(),
    )
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id", "status", "version"))


@surgery_http_bp.post("/bookings/<int:booking_id>/cancel")
@role_required(Role.DOCTOR)
@active_staff_required
def cancel_booking_http(booking_id: int):
    clinic, staff = _actor((Role.DOCTOR,))
    body = _body(VersionReasonBody)
    row = booking.cancel_theatre_booking(
        booking_id=_positive_id(booking_id, "booking_id"), clinic_id=clinic,
        actor_staff_id=staff.id, **body.model_dump(),
    )
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id", "status", "version"))


@surgery_http_bp.get("/bookings/<int:booking_id>")
@role_required(*_READERS)
@active_staff_required
def get_booking_http(booking_id: int):
    clinic, user = _reader()
    row = booking.get_theatre_booking(booking_id=_positive_id(booking_id, "booking_id"), clinic_id=clinic)
    # The CE1 case read model enforces doctor/nurse case-assignment, clinic,
    # admission and patient lineage before the linked booking is disclosed.
    read.get_surgery_case_read_model(actor_user_id=user.id, clinic_id=clinic, case_id=row.surgical_case_id)
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id", "theatre_id", "status", "version", "starts_at", "ends_at"))


@surgery_http_bp.post("/privileges")
@role_required(Role.ADMIN)
@active_staff_required
def attest_privilege_http():
    clinic, staff = _actor((Role.ADMIN,))
    body = _body(PrivilegeBody)
    fields = body.model_dump(exclude={"credential_document_b64"})
    row = authority.grant_surgical_privilege(
        clinic_id=clinic, admin_staff_id=staff.id,
        credential_document_bytes=_evidence(body.credential_document_b64),
        **fields,
    )
    return _reply(_serialize(row, "id", "clinic_id", "staff_id", "privilege_kind", "procedure_code", "expires_at", "version"), 201)


@surgery_http_bp.post("/privileges/<int:privilege_id>/revoke")
@role_required(Role.ADMIN)
@active_staff_required
def revoke_privilege_http(privilege_id: int):
    clinic, staff = _actor((Role.ADMIN,))
    body = _body(VersionReasonBody)
    row = authority.revoke_surgical_privilege(
        privilege_id=_positive_id(privilege_id, "privilege_id"),
        clinic_id=clinic, admin_staff_id=staff.id, **body.model_dump(),
    )
    return _reply(_serialize(row, "id", "clinic_id", "staff_id", "revoked_at", "version"))


@surgery_http_bp.post("/cases/<int:case_id>/consents")
@role_required(Role.DOCTOR)
@active_staff_required
def record_consent_http(case_id: int):
    clinic, staff = _actor((Role.DOCTOR,))
    body = _body(ConsentRecordBody)
    fields = body.model_dump(exclude={"signed_document_b64"})
    row = authority.record_surgical_consent(
        clinic_id=clinic, surgical_case_id=_positive_id(case_id, "case_id"),
        doctor_staff_id=staff.id,
        signed_document_bytes=_evidence(body.signed_document_b64), **fields,
    )
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id", "status", "version", "signed_document_sha256"), 201)


@surgery_http_bp.post("/consents/<int:consent_id>/verify")
@role_required(Role.DOCTOR)
@active_staff_required
def verify_consent_http(consent_id: int):
    clinic, staff = _actor((Role.DOCTOR,))
    body = _body(ConsentVerifyBody)
    row = authority.verify_surgical_consent(
        consent_id=_positive_id(consent_id, "consent_id"), clinic_id=clinic,
        doctor_staff_id=staff.id,
        expected_version=body.expected_version,
        reviewed_document_bytes=_evidence(body.reviewed_document_b64),
    )
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id", "status", "version"))


@surgery_http_bp.post("/consents/<int:consent_id>/withdraw")
@role_required(Role.DOCTOR)
@active_staff_required
def withdraw_consent_http(consent_id: int):
    clinic, staff = _actor((Role.DOCTOR,))
    body = _body(VersionReasonBody)
    row = authority.withdraw_surgical_consent(
        consent_id=_positive_id(consent_id, "consent_id"),
        clinic_id=clinic, doctor_staff_id=staff.id, **body.model_dump(),
    )
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id", "status", "version"))


@surgery_http_bp.post("/cases/<int:case_id>/checklists/<phase>")
@role_required(Role.DOCTOR, Role.NURSE)
@active_staff_required
def checklist_http(case_id: int, phase: str):
    # The route MUST NEVER expose CE1's reserved Sign-Out phase.
    if phase not in ("sign_in", "time_out"):
        raise ValidationError("Only Sign-In and Time-Out documentation is available")
    role = Role.NURSE if phase == "sign_in" else Role.DOCTOR
    clinic, staff = _actor((role,))
    body = _body(ChecklistBody)
    row = checklist.attest_checklist_phase(
        clinic_id=clinic, surgical_case_id=_positive_id(case_id, "case_id"),
        actor_staff_id=staff.id, phase=phase, **body.model_dump(),
    )
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id", "phase", "attested_at"), 201)


@surgery_http_bp.post("/cases/<int:case_id>/preanesthesia")
@role_required(Role.DOCTOR)
@active_staff_required
def preanesthesia_http(case_id: int):
    clinic, staff = _actor((Role.DOCTOR,))
    body = _body(PreanesthesiaBody)
    row = anesthesia.record_preanesthesia_assessment(
        clinic_id=clinic, surgical_case_id=_positive_id(case_id, "case_id"),
        actor_staff_id=staff.id, anesthetist_staff_id=staff.id,
        **body.model_dump(),
    )
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id", "status", "version"), 201)


@surgery_http_bp.post("/anesthesia/<int:assessment_id>/baselines")
@role_required(Role.DOCTOR)
@active_staff_required
def anesthesia_baseline_http(assessment_id: int):
    clinic, staff = _actor((Role.DOCTOR,))
    body = _body(BaselineBody)
    row = anesthesia.add_anesthesia_baseline(
        assessment_id=_positive_id(assessment_id, "assessment_id"),
        clinic_id=clinic, actor_staff_id=staff.id, **body.model_dump(),
    )
    return _reply(_serialize(row, "id", "clinic_id", "assessment_id", "sequence", "observed_at"), 201)


@surgery_http_bp.post("/anesthesia/<int:assessment_id>/attest")
@role_required(Role.DOCTOR)
@active_staff_required
def anesthesia_attest_http(assessment_id: int):
    clinic, staff = _actor((Role.DOCTOR,))
    body = _body(ReviewBody)
    row = anesthesia.attest_preanesthesia_review(
        assessment_id=_positive_id(assessment_id, "assessment_id"),
        clinic_id=clinic, actor_staff_id=staff.id, **body.model_dump(),
    )
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id", "status", "version"))


@surgery_http_bp.post("/reports")
@role_required(Role.DOCTOR)
@active_staff_required
def create_report_http():
    clinic, staff = _actor((Role.DOCTOR,))
    body = _body(CreateReportBody)
    row = reports.create_operative_report(
        clinic_id=clinic, actor_staff_id=staff.id, **body.model_dump(),
    )
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id", "status", "version", "content_sha256"), 201)


@surgery_http_bp.patch("/reports/<int:report_id>")
@role_required(Role.DOCTOR)
@active_staff_required
def revise_report_http(report_id: int):
    clinic, staff = _actor((Role.DOCTOR,))
    body = _body(ReviseReportBody)
    row = reports.revise_operative_report(
        report_id=_positive_id(report_id, "report_id"),
        clinic_id=clinic, actor_staff_id=staff.id, **body.model_dump(),
    )
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id", "status", "version", "content_sha256"))


@surgery_http_bp.post("/reports/<int:report_id>/sign")
@role_required(Role.DOCTOR)
@active_staff_required
def sign_report_http(report_id: int):
    clinic, staff = _actor((Role.DOCTOR,))
    body = _body(SignReportBody)
    row = reports.sign_operative_report(
        report_id=_positive_id(report_id, "report_id"),
        clinic_id=clinic, actor_staff_id=staff.id, **body.model_dump(),
    )
    return _reply(_serialize(row, "id", "clinic_id", "report_id", "signer_kind", "document_sha256"), 201)


@surgery_http_bp.get("/reports/<int:report_id>")
@role_required(*_READERS)
@active_staff_required
def get_report_http(report_id: int):
    clinic, user = _reader()
    row = reports.get_operative_report(
        report_id=_positive_id(report_id, "report_id"), clinic_id=clinic,
    )
    read.get_surgery_case_read_model(
        actor_user_id=user.id, clinic_id=clinic, case_id=row.surgical_case_id,
    )
    # No clinical narratives, PHI or signed-document bytes in a status GET.
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id", "status", "version", "content_sha256"))


# Scoped, authoritative status reads for uncertain mutation outcomes.
# A GET is never authority to retry a previous clinical mutation.


@surgery_http_bp.get("/cases/<int:case_id>/bookings")
@role_required(*_READERS)
@active_staff_required
def case_bookings_status_http(case_id: int):
    clinic, user = _reader()
    case_id = _positive_id(case_id, "case_id")
    read.get_surgery_case_read_model(actor_user_id=user.id, clinic_id=clinic, case_id=case_id)
    rows = db.session.execute(select(TheatreBooking).where(
        TheatreBooking.clinic_id == clinic, TheatreBooking.surgical_case_id == case_id,
    ).order_by(TheatreBooking.id.desc()).limit(51)).scalars().all()
    fields = ("id", "clinic_id", "surgical_case_id", "theatre_id", "status",
              "version", "starts_at", "ends_at")
    return _reply({"case_id": case_id, "items": [_serialize(x, *fields) for x in rows[:50]],
                   "has_more": len(rows) > 50})


@surgery_http_bp.get("/cases/<int:case_id>/consents")
@role_required(*_READERS)
@active_staff_required
def case_consents_status_http(case_id: int):
    clinic, user = _reader()
    case_id = _positive_id(case_id, "case_id")
    read.get_surgery_case_read_model(actor_user_id=user.id, clinic_id=clinic, case_id=case_id)
    rows = db.session.execute(select(SurgicalConsentEvidence).where(
        SurgicalConsentEvidence.clinic_id == clinic,
        SurgicalConsentEvidence.surgical_case_id == case_id,
    ).order_by(SurgicalConsentEvidence.id.desc()).limit(51)).scalars().all()
    fields = ("id", "clinic_id", "surgical_case_id", "status", "version",
              "signed_document_sha256", "expires_at")
    return _reply({"case_id": case_id, "items": [_serialize(x, *fields) for x in rows[:50]],
                   "has_more": len(rows) > 50})


@surgery_http_bp.get("/cases/<int:case_id>/checklists/<phase>")
@role_required(*_READERS)
@active_staff_required
def checklist_status_http(case_id: int, phase: str):
    if phase not in ("sign_in", "time_out"):
        raise ValidationError("Only Sign-In and Time-Out documentation is available")
    clinic, user = _reader()
    case_id = _positive_id(case_id, "case_id")
    read.get_surgery_case_read_model(actor_user_id=user.id, clinic_id=clinic, case_id=case_id)
    row = checklist.get_checklist_phase(surgical_case_id=case_id, clinic_id=clinic, phase=phase)
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id", "phase",
                             "attested_at", "evidence_sha256"))


@surgery_http_bp.get("/cases/<int:case_id>/preanesthesia")
@role_required(*_READERS)
@active_staff_required
def preanesthesia_status_http(case_id: int):
    clinic, user = _reader()
    case_id = _positive_id(case_id, "case_id")
    read.get_surgery_case_read_model(actor_user_id=user.id, clinic_id=clinic, case_id=case_id)
    row_id = db.session.execute(select(AnesthesiaPreoperativeAssessment.id).where(
        AnesthesiaPreoperativeAssessment.clinic_id == clinic,
        AnesthesiaPreoperativeAssessment.surgical_case_id == case_id,
    )).scalar_one_or_none()
    if row_id is None:
        raise NotFoundError("Pre-anesthesia documentation unavailable")
    row = anesthesia.get_preanesthesia_assessment(assessment_id=row_id, clinic_id=clinic)
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id",
                             "status", "version", "evidence_sha256"))


@surgery_http_bp.get("/cases/<int:case_id>/reports")
@role_required(*_READERS)
@active_staff_required
def case_report_status_http(case_id: int):
    clinic, user = _reader()
    case_id = _positive_id(case_id, "case_id")
    read.get_surgery_case_read_model(actor_user_id=user.id, clinic_id=clinic, case_id=case_id)
    row_id = db.session.execute(select(SurgicalOperativeReport.id).where(
        SurgicalOperativeReport.clinic_id == clinic,
        SurgicalOperativeReport.surgical_case_id == case_id,
    )).scalar_one_or_none()
    if row_id is None:
        raise NotFoundError("Operative documentation unavailable")
    row = reports.get_operative_report(report_id=row_id, clinic_id=clinic)
    return _reply(_serialize(row, "id", "clinic_id", "surgical_case_id",
                             "status", "version", "content_sha256"))


@surgery_http_bp.get("/reports/<int:report_id>/signatures")
@role_required(*_READERS)
@active_staff_required
def report_signatures_status_http(report_id: int):
    clinic, user = _reader()
    report_id = _positive_id(report_id, "report_id")
    row = reports.get_operative_report(report_id=report_id, clinic_id=clinic)
    read.get_surgery_case_read_model(actor_user_id=user.id, clinic_id=clinic,
                                     case_id=row.surgical_case_id)
    signatures = reports.list_operative_signatures(report_id=report_id, clinic_id=clinic)
    fields = ("id", "clinic_id", "report_id", "signer_kind", "signed_at", "document_sha256")
    return _reply({"report_id": report_id, "items": [_serialize(x, *fields) for x in signatures]})
