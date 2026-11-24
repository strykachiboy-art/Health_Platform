"""CE1-S03: internal-only surgical consent/privilege evidence and fail-closed checks.

The records are *attestations*, not integrations with a licensing registry or
an encrypted document store. They cannot trigger or authorize surgery itself.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.clinical_orders.models.procedure_order_model import ProcedureOrder
from app.modules.staff.models.staff_model import Staff
from app.modules.surgery.models.surgical_case_model import SurgicalCase
from app.modules.surgery.models.surgical_authorization_model import (
    SurgicalConsentEvidence,
    SurgicalProfessionalPrivilege,
)
from app.modules.surgery.services.surgical_case_service import (
    _active_admission, _doctor, _positive,
)
from app.modules.surgery.services.theatre_booking_service import _admin, _required_text

_CODE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{1,63}$")
_KINDS = frozenset({"surgeon", "anesthetist"})
_SIGNERS = frozenset({"patient", "guardian", "authorized_representative"})


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _time(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{name} must include a timezone offset")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _document_digest(content: bytes, name: str) -> str:
    if not isinstance(content, bytes) or not content:
        raise ValidationError(f"{name} must be nonempty bytes")
    if len(content) > 20 * 1024 * 1024:
        raise ValidationError(f"{name} exceeds 20 MiB")
    return hashlib.sha256(content).hexdigest()


def _code(value: str) -> str:
    code = _required_text(value, "procedure_code", 64).upper()
    if not _CODE.fullmatch(code):
        raise ValidationError("procedure_code must be a stable uppercase code")
    return code


def _kind(value: str) -> str:
    if value not in _KINDS:
        raise ValidationError("Unknown surgical privilege kind")
    return value


def _doctor_in_clinic(staff_id: int, clinic_id: int) -> Staff:
    return _doctor(staff_id, clinic_id)


def _case_and_order(case_id: int, clinic_id: int, *, lock: bool = False):
    statement = select(SurgicalCase).where(
        SurgicalCase.id == _positive(case_id, "surgical_case_id"),
        SurgicalCase.clinic_id == clinic_id,
    )
    if lock:
        statement = statement.with_for_update()
    case = db.session.execute(statement).scalar_one_or_none()
    if case is None:
        raise NotFoundError("Surgical case not found")
    if case.status != "planned":
        raise ConflictError("Only planned surgical cases are eligible")
    order = db.session.execute(select(ProcedureOrder).where(
        ProcedureOrder.id == case.procedure_order_id,
        ProcedureOrder.clinic_id == clinic_id,
    )).scalar_one_or_none()
    admission = _active_admission(case.admission_id, clinic_id)
    if (
        order is None or order.procedure_type != "surgical"
        or order.status != "active" or order.consent_required is not True
        or order.patient_id != case.patient_id
        or order.admission_id != case.admission_id
        or case.patient_id != admission.patient_id
    ):
        raise ConflictError("Surgical order, admission or consent requirement changed")
    if not order.procedure_code:
        raise ConflictError("Surgical procedure code is required for privilege scope")
    return case, order


def _same_procedure(evidence: SurgicalConsentEvidence, order: ProcedureOrder) -> bool:
    return (
        evidence.procedure_order_id == order.id
        and evidence.procedure_order_version == order.version
        and evidence.procedure_code == order.procedure_code
        and evidence.procedure_name == order.procedure_name
        and evidence.body_site == order.body_site
        and evidence.laterality == order.laterality
    )


def _consent(consent_id: int, clinic_id: int, *, lock: bool = False) -> SurgicalConsentEvidence:
    statement = select(SurgicalConsentEvidence).where(
        SurgicalConsentEvidence.id == _positive(consent_id, "consent_id"),
        SurgicalConsentEvidence.clinic_id == clinic_id,
    )
    if lock:
        statement = statement.with_for_update()
    result = db.session.execute(statement).scalar_one_or_none()
    if result is None:
        raise NotFoundError("Surgical consent evidence not found")
    return result


def _privilege(privilege_id: int, clinic_id: int, *, lock: bool = False) -> SurgicalProfessionalPrivilege:
    statement = select(SurgicalProfessionalPrivilege).where(
        SurgicalProfessionalPrivilege.id == _positive(privilege_id, "privilege_id"),
        SurgicalProfessionalPrivilege.clinic_id == clinic_id,
    )
    if lock:
        statement = statement.with_for_update()
    result = db.session.execute(statement).scalar_one_or_none()
    if result is None:
        raise NotFoundError("Surgical privilege not found")
    return result


@transactional
def grant_surgical_privilege(
    *, clinic_id: int, admin_staff_id: int, staff_id: int,
    privilege_kind: str, procedure_code: str,
    license_reference: str, credential_document_ref: str,
    credential_document_bytes: bytes, verification_source: str,
    expires_at: datetime,
) -> SurgicalProfessionalPrivilege:
    clinic_id = _positive(clinic_id, "clinic_id")
    admin = _admin(admin_staff_id, clinic_id)
    professional = _doctor_in_clinic(staff_id, clinic_id)
    kind, scope = _kind(privilege_kind), _code(procedure_code)
    now, expiry = _now(), _time(expires_at, "expires_at")
    if expiry <= now:
        raise ValidationError("Surgical privilege expiry must be in the future")
    license_reference = _required_text(license_reference, "license_reference", 128)
    credential_document_ref = _required_text(
        credential_document_ref, "credential_document_ref", 255
    )
    verification_source = _required_text(verification_source, "verification_source", 255)
    digest = _document_digest(credential_document_bytes, "credential_document_bytes")
    db.session.execute(select(Staff).where(Staff.id == professional.id).with_for_update()).scalar_one()
    existing = db.session.execute(select(SurgicalProfessionalPrivilege.id).where(
        SurgicalProfessionalPrivilege.clinic_id == clinic_id,
        SurgicalProfessionalPrivilege.staff_id == professional.id,
        SurgicalProfessionalPrivilege.privilege_kind == kind,
        SurgicalProfessionalPrivilege.procedure_code == scope,
        SurgicalProfessionalPrivilege.revoked_at.is_(None),
    )).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("Active surgical privilege already exists for scope")
    privilege = SurgicalProfessionalPrivilege(
        clinic_id=clinic_id, staff_id=professional.id, privilege_kind=kind,
        procedure_code=scope, license_reference=license_reference,
        credential_document_ref=credential_document_ref, credential_sha256=digest,
        verification_source=verification_source, granted_by_staff_id=admin.id,
        granted_at=now, expires_at=expiry, version=1,
    )
    db.session.add(privilege)
    db.session.flush()
    create_audit_log(
        action=AuditAction.CREATE, entity_type="SurgicalProfessionalPrivilege",
        entity_id=privilege.id,
        description="Recorded verified-source privilege attestation; no execution authority",
        new_value={"staff_id": professional.id, "kind": kind, "procedure_code": scope,
                   "expires_at": expiry.isoformat(), "version": 1},
        clinic_id=clinic_id, user_id=admin.user_id,
    )
    return privilege


@transactional
def revoke_surgical_privilege(
    *, privilege_id: int, clinic_id: int, admin_staff_id: int,
    expected_version: int, reason: str,
) -> SurgicalProfessionalPrivilege:
    clinic_id = _positive(clinic_id, "clinic_id")
    admin = _admin(admin_staff_id, clinic_id)
    reason = _required_text(reason, "reason", 500)
    privilege = _privilege(privilege_id, clinic_id, lock=True)
    if privilege.revoked_at is not None:
        raise ConflictError("Surgical privilege already revoked")
    if privilege.version != _positive(expected_version, "expected_version"):
        raise ConflictError("Surgical privilege version changed")
    privilege.revoked_at, privilege.revoked_by_staff_id = _now(), admin.id
    privilege.revocation_reason = reason
    privilege.version += 1
    db.session.flush()
    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="SurgicalProfessionalPrivilege", entity_id=privilege.id,
        description="Revoked procedure-scoped surgical privilege",
        old_value={"version": expected_version, "revoked": False},
        new_value={"version": privilege.version, "revoked": True, "reason": reason},
        clinic_id=clinic_id, user_id=admin.user_id,
    )
    return privilege


@transactional
def record_surgical_consent(
    *, clinic_id: int, surgical_case_id: int, doctor_staff_id: int,
    signer_kind: str, signer_name: str, signer_authority: str | None,
    signed_document_ref: str, signed_document_bytes: bytes,
    signed_at: datetime, expires_at: datetime,
) -> SurgicalConsentEvidence:
    clinic_id = _positive(clinic_id, "clinic_id")
    doctor = _doctor_in_clinic(doctor_staff_id, clinic_id)
    case, order = _case_and_order(surgical_case_id, clinic_id, lock=True)
    if signer_kind not in _SIGNERS:
        raise ValidationError("Unsupported surgical consent signer kind")
    signer_name = _required_text(signer_name, "signer_name", 255)
    if signer_kind != "patient":
        signer_authority = _required_text(signer_authority, "signer_authority", 255)
    else:
        signer_authority = None
    ref = _required_text(signed_document_ref, "signed_document_ref", 255)
    signed, expiry, now = _time(signed_at, "signed_at"), _time(expires_at, "expires_at"), _now()
    if signed > now or expiry <= now or expiry <= signed:
        raise ValidationError("Consent must be signed and not expired")
    digest = _document_digest(signed_document_bytes, "signed_document_bytes")
    existing = db.session.execute(select(SurgicalConsentEvidence.id).where(
        SurgicalConsentEvidence.surgical_case_id == case.id,
        SurgicalConsentEvidence.status.in_(("recorded", "verified")),
    )).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("An open surgical consent record already exists")
    consent = SurgicalConsentEvidence(
        clinic_id=clinic_id, patient_id=case.patient_id, surgical_case_id=case.id,
        procedure_order_id=order.id, procedure_order_version=order.version,
        procedure_code=order.procedure_code, procedure_name=order.procedure_name,
        body_site=order.body_site, laterality=order.laterality,
        signer_kind=signer_kind, signer_name=signer_name,
        signer_authority=signer_authority, signed_document_ref=ref,
        signed_document_sha256=digest, signed_at=signed, expires_at=expiry,
        status="recorded", recorded_by_staff_id=doctor.id, recorded_at=now,
        version=1,
    )
    db.session.add(consent)
    db.session.flush()
    create_audit_log(
        action=AuditAction.CREATE, entity_type="SurgicalConsentEvidence",
        entity_id=consent.id,
        description="Recorded document fingerprint; independent review pending",
        new_value={"surgical_case_id": case.id, "order_version": order.version,
                   "status": consent.status, "version": 1},
        clinic_id=clinic_id, patient_id=case.patient_id, user_id=doctor.user_id,
    )
    return consent


@transactional
def verify_surgical_consent(
    *, consent_id: int, clinic_id: int, doctor_staff_id: int,
    expected_version: int, reviewed_document_bytes: bytes,
) -> SurgicalConsentEvidence:
    clinic_id = _positive(clinic_id, "clinic_id")
    doctor = _doctor_in_clinic(doctor_staff_id, clinic_id)
    consent = _consent(consent_id, clinic_id, lock=True)
    if consent.status != "recorded":
        raise ConflictError("Only unverified consent evidence may be reviewed")
    if consent.version != _positive(expected_version, "expected_version"):
        raise ConflictError("Surgical consent version changed")
    if doctor.id == consent.recorded_by_staff_id:
        raise ConflictError("Independent doctor is required to verify consent")
    case, order = _case_and_order(consent.surgical_case_id, clinic_id)
    if consent.patient_id != case.patient_id or not _same_procedure(consent, order):
        raise ConflictError("Surgical consent no longer matches current procedure")
    if consent.expires_at <= _now():
        raise ConflictError("Surgical consent has expired")
    digest = _document_digest(reviewed_document_bytes, "reviewed_document_bytes")
    if digest != consent.signed_document_sha256:
        raise ConflictError("Surgical signed document fingerprint mismatch")
    consent.status = "verified"
    consent.verified_by_staff_id, consent.verified_at = doctor.id, _now()
    consent.version += 1
    db.session.flush()
    create_audit_log(
        action=AuditAction.STATUS_CHANGE, entity_type="SurgicalConsentEvidence",
        entity_id=consent.id, description="Independent doctor reviewed exact signed-document fingerprint",
        old_value={"status": "recorded", "version": expected_version},
        new_value={"status": "verified", "version": consent.version},
        clinic_id=clinic_id, patient_id=case.patient_id, user_id=doctor.user_id,
    )
    return consent


@transactional
def withdraw_surgical_consent(
    *, consent_id: int, clinic_id: int, doctor_staff_id: int,
    expected_version: int, reason: str,
) -> SurgicalConsentEvidence:
    clinic_id = _positive(clinic_id, "clinic_id")
    doctor = _doctor_in_clinic(doctor_staff_id, clinic_id)
    reason = _required_text(reason, "reason", 500)
    consent = _consent(consent_id, clinic_id, lock=True)
    if consent.status == "withdrawn":
        raise ConflictError("Surgical consent already withdrawn")
    if consent.version != _positive(expected_version, "expected_version"):
        raise ConflictError("Surgical consent version changed")
    previous_status = consent.status
    consent.status, consent.withdrawn_at = "withdrawn", _now()
    consent.withdrawn_by_staff_id = doctor.id
    consent.withdrawal_reason = reason
    consent.version += 1
    db.session.flush()
    create_audit_log(
        action=AuditAction.STATUS_CHANGE, entity_type="SurgicalConsentEvidence",
        entity_id=consent.id, description="Recorded withdrawal of surgical consent",
        old_value={"status": previous_status, "version": expected_version},
        new_value={"status": "withdrawn", "version": consent.version, "reason": reason},
        clinic_id=clinic_id, patient_id=consent.patient_id, user_id=doctor.user_id,
    )
    return consent


def assert_surgical_consent_and_privileges(
    *, surgical_case_id: int, clinic_id: int,
    surgeon_staff_id: int, anesthetist_staff_id: int,
) -> SurgicalConsentEvidence:
    """Fail-closed internal evidence check; NOT an operative-release decision."""
    clinic_id = _positive(clinic_id, "clinic_id")
    case, order = _case_and_order(surgical_case_id, clinic_id)
    if surgeon_staff_id == anesthetist_staff_id:
        raise ConflictError("Surgeon and anesthetist must be distinct professionals")
    now = _now()
    consent = db.session.execute(select(SurgicalConsentEvidence).where(
        SurgicalConsentEvidence.surgical_case_id == case.id,
        SurgicalConsentEvidence.clinic_id == clinic_id,
        SurgicalConsentEvidence.status == "verified",
    )).scalar_one_or_none()
    if (consent is None or consent.expires_at <= now
            or consent.patient_id != case.patient_id or not _same_procedure(consent, order)):
        raise ConflictError("Current independently verified surgical consent required")
    for staff_id, kind in ((surgeon_staff_id, "surgeon"),
                           (anesthetist_staff_id, "anesthetist")):
        professional = _doctor_in_clinic(staff_id, clinic_id)
        privilege = db.session.execute(select(SurgicalProfessionalPrivilege).where(
            SurgicalProfessionalPrivilege.clinic_id == clinic_id,
            SurgicalProfessionalPrivilege.staff_id == professional.id,
            SurgicalProfessionalPrivilege.privilege_kind == kind,
            SurgicalProfessionalPrivilege.procedure_code == order.procedure_code,
            SurgicalProfessionalPrivilege.revoked_at.is_(None),
            SurgicalProfessionalPrivilege.expires_at > now,
        )).scalar_one_or_none()
        if privilege is None:
            raise ConflictError(f"Active procedure-scoped {kind} privilege required")
    return consent
