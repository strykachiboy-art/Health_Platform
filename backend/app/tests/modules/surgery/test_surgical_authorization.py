"""CE1-S03 focused invariants: consent evidence and procedure-scoped privileges.

All tests use the existing test database and never reach an application database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.surgery.models.surgical_authorization_model import (
    SurgicalConsentEvidence, SurgicalProfessionalPrivilege,
)
from app.modules.surgery.services import surgical_authorization_service as svc
from app.tests.modules.surgery.test_surgical_case_foundation import (
    _context, _order, _plan,
)

DOC = b"FAKE test-only signed consent bytes; no real patient information"
CRED = b"FAKE test-only professional credential bytes"
CODE = "SURG-LAP"


def _utc():
    return datetime.now(timezone.utc)


@pytest.fixture
def scenario(monkeypatch, clinic, make_patient, make_staff):
    ctx = _context(monkeypatch, clinic, make_patient, make_staff)
    monkeypatch.setattr(svc, "create_audit_log", lambda **kw: ctx["events"].append(kw))
    ctx["clinic"] = clinic
    ctx["make_staff"] = make_staff
    ctx["admin"] = make_staff(clinic, role=Role.ADMIN)
    ctx["reviewer"] = make_staff(clinic, role=Role.DOCTOR)
    ctx["anesthetist"] = make_staff(clinic, role=Role.DOCTOR)
    ctx["order"] = _order(
        clinic, ctx, procedure_code=CODE, body_site="abdomen", laterality=None
    )
    ctx["case"] = _plan(clinic, ctx, ctx["order"])
    return ctx


def _grant(ctx, *, kind="surgeon", staff=None, code=CODE, **extra):
    value = dict(
        clinic_id=ctx["clinic"].id,
        admin_staff_id=ctx["admin"].id,
        staff_id=(staff or ctx["doctor"]).id,
        privilege_kind=kind,
        procedure_code=code,
        license_reference="TEST-LICENSE-EXAMPLE",
        credential_document_ref="testing:credential-evidence-001",
        credential_document_bytes=CRED,
        verification_source="Test admin attestation; external registry not queried",
        expires_at=_utc() + timedelta(days=60),
    )
    value.update(extra)
    return svc.grant_surgical_privilege(**value)


def _record(ctx, **extra):
    value = dict(
        clinic_id=ctx["clinic"].id,
        surgical_case_id=ctx["case"].id,
        doctor_staff_id=ctx["doctor"].id,
        signer_kind="patient", signer_name="Example Test Patient",
        signer_authority=None,
        signed_document_ref="testing:signed-consent-001",
        signed_document_bytes=DOC,
        signed_at=_utc() - timedelta(hours=1),
        expires_at=_utc() + timedelta(days=30),
    )
    value.update(extra)
    return svc.record_surgical_consent(**value)


def _verify(ctx, consent, **extra):
    value = dict(
        consent_id=consent.id, clinic_id=ctx["clinic"].id,
        doctor_staff_id=ctx["reviewer"].id,
        expected_version=consent.version, reviewed_document_bytes=DOC,
    )
    value.update(extra)
    return svc.verify_surgical_consent(**value)


def _authorized(ctx):
    return svc.assert_surgical_consent_and_privileges(
        surgical_case_id=ctx["case"].id,
        clinic_id=ctx["clinic"].id,
        surgeon_staff_id=ctx["doctor"].id,
        anesthetist_staff_id=ctx["anesthetist"].id,
    )


def _ready(ctx):
    _grant(ctx)
    _grant(ctx, kind="anesthetist", staff=ctx["anesthetist"])
    consent = _record(ctx)
    _verify(ctx, consent)
    return consent


def test_privilege_is_scoped_and_audited(scenario):
    p = _grant(scenario)
    assert p.id is not None
    assert p.clinic_id == scenario["clinic"].id
    assert p.privilege_kind == "surgeon"
    assert p.procedure_code == CODE
    assert p.credential_sha256 == hashlib.sha256(CRED).hexdigest()
    assert p.granted_by_staff_id == scenario["admin"].id
    assert any(e["entity_type"] == "SurgicalProfessionalPrivilege" for e in scenario["events"])


def test_privilege_duplicate_scope_rejected(scenario):
    _grant(scenario)
    with pytest.raises(ConflictError, match="already exists"):
        _grant(scenario)
    assert db.session.query(SurgicalProfessionalPrivilege).count() == 1


def test_only_clinic_admin_can_attest_privileges(scenario):
    with pytest.raises(ValidationError, match="administrator"):
        _grant(scenario, admin_staff_id=scenario["doctor"].id)


def test_non_doctor_cannot_receive_operating_privilege(scenario):
    with pytest.raises(ValidationError, match="doctor"):
        _grant(scenario, staff=scenario["nurse"])


def test_credential_grant_denies_cross_clinic_and_invalid_input(scenario, make_clinic, make_staff):
    other = make_clinic(name="Unrelated credential authority")
    external_admin = make_staff(other, role=Role.ADMIN)
    with pytest.raises(NotFoundError):
        _grant(scenario, admin_staff_id=external_admin.id)
    with pytest.raises(ValidationError, match="privilege kind"):
        _grant(scenario, kind="general_doctor")
    with pytest.raises(ValidationError, match="procedure_code"):
        _grant(scenario, code="sur g")


def test_expired_credential_and_empty_document_rejected(scenario):
    with pytest.raises(ValidationError, match="future"):
        _grant(scenario, expires_at=_utc() - timedelta(days=1))
    with pytest.raises(ValidationError, match="nonempty bytes"):
        _grant(scenario, credential_document_bytes=b"")


def test_privilege_revocation_is_versioned_and_audited(scenario):
    p = _grant(scenario)
    with pytest.raises(ConflictError, match="version changed"):
        svc.revoke_surgical_privilege(
            privilege_id=p.id, clinic_id=scenario["clinic"].id,
            admin_staff_id=scenario["admin"].id, expected_version=7,
            reason="License suspended",
        )
    p = svc.revoke_surgical_privilege(
        privilege_id=p.id, clinic_id=scenario["clinic"].id,
        admin_staff_id=scenario["admin"].id, expected_version=1,
        reason="License suspended",
    )
    assert p.revoked_at is not None and p.version == 2
    with pytest.raises(ConflictError, match="already revoked"):
        svc.revoke_surgical_privilege(
            privilege_id=p.id, clinic_id=scenario["clinic"].id,
            admin_staff_id=scenario["admin"].id, expected_version=2,
            reason="Duplicate",
        )


def test_revoked_privilege_can_be_reissued_with_history_preserved(scenario):
    prior = _grant(scenario)
    svc.revoke_surgical_privilege(
        privilege_id=prior.id, clinic_id=scenario["clinic"].id,
        admin_staff_id=scenario["admin"].id, expected_version=1,
        reason="Credential renewed",
    )
    newer = _grant(scenario)
    assert newer.id != prior.id
    assert db.session.query(SurgicalProfessionalPrivilege).count() == 2


def test_consent_record_fingerprint_and_patient_lineage(scenario):
    c = _record(scenario)
    assert c.status == "recorded" and c.verified_at is None
    assert c.signed_document_sha256 == hashlib.sha256(DOC).hexdigest()
    assert c.patient_id == scenario["patient"].id
    assert c.procedure_order_id == scenario["order"].id
    assert c.procedure_order_version == scenario["order"].version
    assert c.body_site == "abdomen"
    assert c.version == 1
    assert any(e["entity_type"] == "SurgicalConsentEvidence" for e in scenario["events"])


def test_duplicate_open_consent_fails_closed(scenario):
    _record(scenario)
    with pytest.raises(ConflictError, match="already exists"):
        _record(scenario)
    assert db.session.query(SurgicalConsentEvidence).count() == 1


def test_guardian_requires_authority(scenario):
    with pytest.raises(ValidationError, match="signer_authority"):
        _record(scenario, signer_kind="guardian")
    c = _record(scenario, signer_kind="guardian", signer_authority="Court authorization reviewed")
    assert c.signer_kind == "guardian" and c.signer_authority


def test_consent_dates_and_document_required(scenario):
    with pytest.raises(ValidationError, match="not expired"):
        _record(scenario, expires_at=_utc() - timedelta(days=2))
    with pytest.raises(ValidationError, match="not expired"):
        _record(scenario, signed_at=_utc() + timedelta(days=1))
    with pytest.raises(ValidationError, match="nonempty bytes"):
        _record(scenario, signed_document_bytes=b"")


def test_consent_independent_verifier_required(scenario):
    c = _record(scenario)
    with pytest.raises(ConflictError, match="Independent doctor"):
        _verify(scenario, c, doctor_staff_id=scenario["doctor"].id)
    assert c.status == "recorded"


def test_consent_document_fingerprint_must_match(scenario):
    c = _record(scenario)
    with pytest.raises(ConflictError, match="fingerprint mismatch"):
        _verify(scenario, c, reviewed_document_bytes=b"tampered bytes")
    assert c.status == "recorded"


def test_consent_verify_only_from_active_doctor(scenario):
    c = _record(scenario)
    with pytest.raises(ValidationError, match="doctor"):
        _verify(scenario, c, doctor_staff_id=scenario["nurse"].id)
    c = _verify(scenario, c)
    assert c.status == "verified" and c.verified_at is not None
    assert c.verified_by_staff_id == scenario["reviewer"].id and c.version == 2


def test_consent_wrong_clinic_and_stale_version_fail(scenario, make_clinic, make_staff):
    c = _record(scenario)
    with pytest.raises(ConflictError, match="version changed"):
        _verify(scenario, c, expected_version=4)
    other = make_clinic(name="Another consent verification clinic")
    reviewer = make_staff(other, role=Role.DOCTOR)
    with pytest.raises(NotFoundError):
        _verify(scenario, c, clinic_id=other.id, doctor_staff_id=reviewer.id)


def test_withdrawn_consent_releases_case_for_new_record(scenario):
    c = _record(scenario)
    _verify(scenario, c)
    withdrawn = svc.withdraw_surgical_consent(
        consent_id=c.id, clinic_id=scenario["clinic"].id,
        doctor_staff_id=scenario["doctor"].id, expected_version=2,
        reason="Patient withdrew consent",
    )
    assert withdrawn.status == "withdrawn" and withdrawn.version == 3
    with pytest.raises(ConflictError, match="already withdrawn"):
        svc.withdraw_surgical_consent(
            consent_id=c.id, clinic_id=scenario["clinic"].id,
            doctor_staff_id=scenario["doctor"].id, expected_version=3,
            reason="Again",
        )
    newer = _record(scenario)
    assert newer.id != c.id


def test_unverified_consent_never_satisfies_authorization(scenario):
    _grant(scenario)
    _grant(scenario, kind="anesthetist", staff=scenario["anesthetist"])
    _record(scenario)
    with pytest.raises(ConflictError, match="verified surgical consent"):
        _authorized(scenario)


def test_valid_consent_and_both_privileges_pass_internal_evidence_check(scenario):
    consent = _ready(scenario)
    assert _authorized(scenario).id == consent.id
    assert scenario["case"].status == "planned"  # Never begins surgery.


def test_unprivileged_or_same_person_cannot_pass(scenario):
    c = _record(scenario)
    _verify(scenario, c)
    _grant(scenario)
    with pytest.raises(ConflictError, match="anesthetist privilege"):
        _authorized(scenario)
    with pytest.raises(ConflictError, match="distinct professionals"):
        svc.assert_surgical_consent_and_privileges(
            surgical_case_id=scenario["case"].id, clinic_id=scenario["clinic"].id,
            surgeon_staff_id=scenario["doctor"].id,
            anesthetist_staff_id=scenario["doctor"].id,
        )


def test_credential_scope_and_revocation_fail_closed(scenario):
    surgeon = _grant(scenario, code="SURG-OTHER")
    anesthetist = _grant(scenario, kind="anesthetist", staff=scenario["anesthetist"])
    c = _record(scenario)
    _verify(scenario, c)
    with pytest.raises(ConflictError, match="surgeon privilege"):
        _authorized(scenario)
    # A privilege for this order does not survive revocation.
    correct_surgeon = _grant(scenario)
    assert _authorized(scenario).id == c.id
    svc.revoke_surgical_privilege(
        privilege_id=correct_surgeon.id, clinic_id=scenario["clinic"].id,
        admin_staff_id=scenario["admin"].id, expected_version=1,
        reason="Scope suspended",
    )
    with pytest.raises(ConflictError, match="surgeon privilege"):
        _authorized(scenario)
    assert surgeon.id != correct_surgeon.id and anesthetist.id is not None


def test_expired_privilege_is_not_usable(scenario):
    p = _grant(scenario)
    _grant(scenario, kind="anesthetist", staff=scenario["anesthetist"])
    c = _record(scenario)
    _verify(scenario, c)
    p.granted_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=60)
    p.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    db.session.commit()
    with pytest.raises(ConflictError, match="surgeon privilege"):
        _authorized(scenario)


def test_changed_order_invalidates_verified_consent(scenario):
    _ready(scenario)
    scenario["order"].version += 1
    db.session.commit()
    with pytest.raises(ConflictError, match="verified surgical consent"):
        _authorized(scenario)



def test_expired_consent_cannot_pass_internal_check(scenario):
    c = _ready(scenario)
    c.signed_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
    c.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    db.session.commit()
    with pytest.raises(ConflictError, match="verified surgical consent"):
        _authorized(scenario)


def test_unknown_signer_kind_and_stale_withdrawal_rejected(scenario):
    with pytest.raises(ValidationError, match="signer kind"):
        _record(scenario, signer_kind="clinician")
    c = _record(scenario)
    with pytest.raises(ConflictError, match="version changed"):
        svc.withdraw_surgical_consent(
            consent_id=c.id, clinic_id=scenario["clinic"].id,
            doctor_staff_id=scenario["doctor"].id, expected_version=9,
            reason="Stale request",
        )


def test_withdrawal_invalidates_evidence_check(scenario):
    c = _ready(scenario)
    svc.withdraw_surgical_consent(
        consent_id=c.id, clinic_id=scenario["clinic"].id,
        doctor_staff_id=scenario["doctor"].id, expected_version=2,
        reason="Patient refused",
    )
    with pytest.raises(ConflictError, match="verified surgical consent"):
        _authorized(scenario)


def test_model_db_constraints_block_forged_verified_state(scenario):
    c = _record(scenario)
    c.status = "verified"
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_no_public_surgery_routes_and_no_execution_transition(app, scenario):
    _ready(scenario)
    assert not any("/surgery" in rule.rule for rule in app.url_map.iter_rules())
    assert scenario["case"].status == "planned"
    assert scenario["order"].status == "active"
