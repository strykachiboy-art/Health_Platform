"""CT4B3: real disposable PostgreSQL outage / unknown ACK and closed-case policy.

Do not run against developer, staging, or production databases. The companion
PowerShell runner binds only an owned Docker PostgreSQL 18 and Redis 8 pair on
127.0.0.1, validates all endpoint and container identity invariants, and runs
only this test module. Real PostgreSQL stop/start happens in two tests; a
synthetic lost reply AFTER a durable SQL ACK models an unknown outcome.

No source modules, migrations or existing test modules are patched on disk.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import subprocess
import time
from urllib.parse import urlsplit

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy import create_engine, text

from app import create_app
from app.core.enums.chat_enums import ConversationStatus, ParticipantStatus
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus, BedStatus, WardType
from app.core.exceptions import ConflictError, NotFoundError
from app.core.auth.user.models.user_model import User
from app.extensions import db
from app.modules.chat.models.admission_case_chat_sync_request_model import (
    AdmissionCaseChatSyncRequest,
)
from app.modules.chat.models.admission_case_conversation_model import (
    AdmissionCaseConversation,
)
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.conversation_participant_model import ConversationParticipant
from app.modules.chat.services import case_chat_assignment_event_service as events
from app.modules.chat.services.chat_security_service import ChatSecurityService
from app.modules.chat.tasks import case_chat_sync_task as worker
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.nursing.models.admission_care_assignment_model import AdmissionCareAssignment
from app.modules.nursing.services import care_team_assignment_service as assignment
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission, Bed, Ward


def _config_and_owned_pg():
    uri = make_url(os.environ.get("DATABASE_URL", ""))
    redis = urlsplit(os.environ.get("REDIS_URL", ""))
    pg_port = os.environ.get("CT4B3_PG_PORT", "")
    redis_port = os.environ.get("CT4B3_REDIS_PORT", "")
    owner = os.environ.get("CT4B3_OWNER_TOKEN", "")
    name = os.environ.get("CT4B3_PG_CONTAINER", "")
    rn = os.environ.get("CT4B3_REDIS_CONTAINER", "")
    assert (
        uri.drivername == "postgresql+psycopg2"
        and uri.username == "ct4b3"
        and uri.host == "127.0.0.1"
        and uri.database == "ct4b3_disposable"
        and str(uri.port) == pg_port
        and redis.scheme == "redis"
        and redis.hostname == "127.0.0.1"
        and str(redis.port) == redis_port
        and redis.path == "/15"
        and len(owner) >= 25
        and name.startswith("ct4b3-pg-")
        and rn.startswith("ct4b3-redis-")
    ), "CT4B3_DISPOSABLE_URL_OR_CONTAINER_GUARD_FAILED"
    for container, tcp, expected_port in (
        (name, "5432/tcp", pg_port),
        (rn, "6379/tcp", redis_port),
    ):
        r = subprocess.run(
            ["docker", "inspect", container],
            capture_output=True, text=True, timeout=20, check=True,
        )
        contents = json.loads(r.stdout)
        assert len(contents) == 1
        info = contents[0]
        assert info["Name"] == "/" + container
        assert info["State"]["Running"] is True
        assert info["Config"]["Labels"].get("org.openai.ct4b3.owner") == owner
        binding = info["NetworkSettings"]["Ports"][tcp]
        assert len(binding) == 1
        assert binding[0]["HostIp"] == "127.0.0.1"
        assert str(binding[0]["HostPort"]) == expected_port
    return name


def _docker(*args, timeout=35):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True,
        timeout=timeout, check=True,
    )


def _stop_pg():
    container = _config_and_owned_pg()
    _docker("stop", "--time", "1", container, timeout=45)
    status = _docker("inspect", "--format", "{{.State.Running}}", container)
    assert status.stdout.strip() == "false", "OWNED_POSTGRES_DID_NOT_STOP"
    print("CT4B3_REAL_POSTGRES_STOP=PASS", flush=True)


def _restart_pg():
    container = os.environ["CT4B3_PG_CONTAINER"]
    # The launcher knows the unique, label-verified container name. The stop
    # phase has already validated ownership; this function only starts it.
    r = _docker("start", container, timeout=45)
    assert r.stdout.strip() == container
    deadline = time.monotonic() + 100
    ready = False
    while time.monotonic() < deadline:
        try:
            probe = _docker(
                "exec", container, "pg_isready", "-h", "127.0.0.1",
                "-U", "ct4b3", "-d", "ct4b3_disposable", timeout=8,
            )
            if "accepting connections" in probe.stdout:
                engine = create_engine(
                    os.environ["DATABASE_URL"],
                    connect_args={"connect_timeout": 3},
                    pool_pre_ping=True,
                )
                try:
                    with engine.connect() as conn:
                        got = conn.execute(text("select current_database(), current_user")).one()
                        assert tuple(got) == ("ct4b3_disposable", "ct4b3")
                    ready = True
                    break
                finally:
                    engine.dispose()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                DBAPIError, OSError):
            pass
        time.sleep(0.6)
    assert ready, "CT4B3_POSTGRES_DID_NOT_RECOVER_ON_SAME_PORT"
    _config_and_owned_pg()
    # All ORM connections acquired before the outage are invalid. Reconnect.
    db.session.remove()
    db.engine.dispose()
    print("CT4B3_REAL_POSTGRES_RESTART_HOST_SQL=PASS", flush=True)


def _scoped_request(ids):
    return db.session.execute(
        db.select(AdmissionCaseChatSyncRequest).where(
            AdmissionCaseChatSyncRequest.clinic_id == ids["clinic"],
            AdmissionCaseChatSyncRequest.admission_id == ids["admission"],
        )
    ).scalar_one()


def _scoped_assignments(ids):
    return db.session.execute(
        db.select(AdmissionCareAssignment).where(
            AdmissionCareAssignment.clinic_id == ids["clinic"],
            AdmissionCareAssignment.admission_id == ids["admission"],
        ).order_by(AdmissionCareAssignment.id)
    ).scalars().all()


def _case_counts(ids):
    links = db.session.execute(
        db.select(AdmissionCaseConversation).where(
            AdmissionCaseConversation.clinic_id == ids["clinic"],
            AdmissionCaseConversation.admission_id == ids["admission"],
        )
    ).scalars().all()
    return links


def _seed(*, suffix: str, initial_chat_deferred: bool):
    """Synthetic clinical assignment created through the committed CT1 API."""
    db.session.remove()
    clinic = Clinic(name=f"CT4B3 {suffix}", status=ClinicStatus.ACTIVE, ai_credits=5)
    db.session.add(clinic)
    db.session.flush()
    admin = User(
        clinic_id=clinic.id, role=Role.ADMIN, is_active=True,
        email=f"ct4b3-admin-{suffix}@example.test",
    )
    doctor_user = User(
        clinic_id=clinic.id, role=Role.DOCTOR, is_active=True,
        email=f"ct4b3-doc1-{suffix}@example.test",
    )
    next_user = User(
        clinic_id=clinic.id, role=Role.DOCTOR, is_active=True,
        email=f"ct4b3-doc2-{suffix}@example.test",
    )
    for u in (admin, doctor_user, next_user):
        u.set_password("ct4b3-test-only-password")
    db.session.add_all([admin, doctor_user, next_user])
    db.session.flush()
    doc = Staff(
        clinic_id=clinic.id, user_id=doctor_user.id,
        first_name="First", last_name="Doctor", status=StaffStatus.ACTIVE,
    )
    next_doc = Staff(
        clinic_id=clinic.id, user_id=next_user.id,
        first_name="Replacement", last_name="Doctor", status=StaffStatus.ACTIVE,
    )
    patient = Patient(
        clinic_id=clinic.id, first_name="Test", last_name="Patient",
        patient_number=f"CT4B3-{suffix}", is_active=True,
    )
    db.session.add_all([doc, next_doc, patient])
    db.session.flush()
    ward = Ward(
        clinic_id=clinic.id, name=f"CT4B3 Ward {suffix}",
        ward_type=WardType.GENERAL, capacity=1,
    )
    db.session.add(ward)
    db.session.flush()
    bed = Bed(ward_id=ward.id, bed_number="CT4B3-1", status=BedStatus.OCCUPIED)
    db.session.add(bed)
    db.session.flush()
    admission = Admission(
        patient_id=patient.id, bed_id=bed.id, admitted_by_id=doc.id,
        status=AdmissionStatus.ADMITTED,
    )
    db.session.add(admission)
    db.session.flush()
    ids = dict(
        clinic=clinic.id, admission=admission.id, admin=admin.id,
        doctor=doc.id, next_doctor=next_doc.id,
        doctor_user=doctor_user.id, next_user=next_user.id,
    )
    db.session.commit()
    if initial_chat_deferred:
        original = events.ensure_admission_case_conversation
        def chat_unavailable(**_kwargs):
            raise RuntimeError("CT4B3 planned initial chat unavailability")
        events.ensure_admission_case_conversation = chat_unavailable
        try:
            first = assignment.assign_primary_clinician(
                clinic_id=ids["clinic"], admission_id=ids["admission"],
                actor_user_id=ids["admin"], staff_id=ids["doctor"],
                assignment_kind="primary_doctor",
                assignment_reason="CT4B3 synthetic primary assignment",
            )
        finally:
            events.ensure_admission_case_conversation = original
    else:
        first = assignment.assign_primary_clinician(
            clinic_id=ids["clinic"], admission_id=ids["admission"],
            actor_user_id=ids["admin"], staff_id=ids["doctor"],
            assignment_kind="primary_doctor",
            assignment_reason="CT4B3 synthetic primary assignment",
        )
    ids["initial_assignment"] = first.id
    db.session.expire_all()
    row = _scoped_request(ids)
    assert row.request_sequence == 1
    assert row.processed_sequence == (0 if initial_chat_deferred else 1)
    assert len(_scoped_assignments(ids)) == 1
    assert len(_case_counts(ids)) == (0 if initial_chat_deferred else 1)
    db.session.rollback()
    return ids


@pytest.fixture(scope="module")
def disposable_pg_app():
    """One dedicated PostgreSQL 18 database; no SQLite/dev DB fixture."""
    _config_and_owned_pg()
    app = create_app("development")
    with app.app_context():
        from app import models_registry  # noqa: F401
        assert db.engine.dialect.name == "postgresql"
        assert db.engine.url.database == "ct4b3_disposable"
        db.create_all()  # confined to the runner-owned disposable database
        try:
            yield app
        finally:
            db.session.rollback()
            db.session.remove()
            db.engine.dispose()


def test_real_postgres_stop_start_pending_clinical_queue_recovers(disposable_pg_app):
    ids = _seed(suffix="outage", initial_chat_deferred=True)
    row = _scoped_request(ids)
    assert (row.request_sequence, row.processed_sequence) == (1, 0)
    assert len(_scoped_assignments(ids)) == 1
    db.session.rollback()
    db.session.remove()
    db.engine.dispose()
    _stop_pg()
    try:
        with pytest.raises(SQLAlchemyError):
            events.deliver_pending_case_chat_sync(
                clinic_id=ids["clinic"], admission_id=ids["admission"],
                actor_user_id=ids["admin"],
            )
        print("CT4B3_REAL_PG_OUTAGE_DELIVERY_REJECTED=PASS", flush=True)
    finally:
        _restart_pg()
    row = _scoped_request(ids)
    assert (row.request_sequence, row.processed_sequence) == (1, 0)
    assert len(_scoped_assignments(ids)) == 1
    assert len(_case_counts(ids)) == 0
    db.session.rollback()
    recovered = events.deliver_pending_case_chat_sync(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
        actor_user_id=ids["admin"],
    )
    assert recovered["pending"] is False and recovered["created"] is True
    assert _scoped_request(ids).processed_sequence == 1
    assert len(_scoped_assignments(ids)) == 1
    assert len(_case_counts(ids)) == 1
    db.session.rollback()
    print("CT4B3_PG_OUTAGE_PENDING_AND_RECOVERY_NO_DUPLICATES=PASS", flush=True)


def test_unknown_outcome_after_durable_ack_then_real_pg_stop(disposable_pg_app, monkeypatch):
    ids = _seed(suffix="unknown", initial_chat_deferred=True)
    canonical = events._acknowledge_request_if_current
    hit = {"count": 0}
    def acknowledged_but_unreported(**kwargs):
        completed = canonical(**kwargs)  # This call commits processed_sequence first.
        if completed:
            hit["count"] += 1
            db.session.remove()
            db.engine.dispose()
            _stop_pg()  # An actual PostgreSQL 18 interruption at response boundary.
            raise RuntimeError("CT4B3 synthetic lost caller reply AFTER durable SQL ACK")
        return completed
    monkeypatch.setattr(events, "_acknowledge_request_if_current", acknowledged_but_unreported)
    try:
        with pytest.raises(RuntimeError, match="lost caller reply AFTER durable SQL ACK"):
            events.deliver_pending_case_chat_sync(
                clinic_id=ids["clinic"], admission_id=ids["admission"],
                actor_user_id=ids["admin"],
            )
        assert hit["count"] == 1
        print("CT4B3_ACK_COMMITTED_BUT_REPLY_UNKNOWN=PASS", flush=True)
    finally:
        _restart_pg()
        monkeypatch.setattr(events, "_acknowledge_request_if_current", canonical)
    row = _scoped_request(ids)
    assert row.request_sequence == row.processed_sequence == 1
    assert len(_case_counts(ids)) == 1
    conv_id = _case_counts(ids)[0].conversation_id
    assert db.session.execute(db.select(Conversation).where(
        Conversation.id == conv_id,
    )).scalar_one() is not None
    assert len(db.session.execute(db.select(ConversationParticipant).where(
        ConversationParticipant.conversation_id == conv_id,
    )).scalars().all()) == 1
    db.session.rollback()
    replay = events.deliver_pending_case_chat_sync(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
        actor_user_id=ids["admin"],
    )
    assert replay == {"pending": False, "changed": False}
    assert len(_case_counts(ids)) == 1
    assert _scoped_request(ids).processed_sequence == 1
    db.session.rollback()
    print("CT4B3_UNKNOWN_ACK_REPLAY_NO_DUPLICATE=PASS", flush=True)


@pytest.mark.parametrize("state", [ConversationStatus.ARCHIVED, ConversationStatus.CLOSED])
def test_closed_or_archived_link_never_reactivated_or_granted(
    disposable_pg_app, state,
):
    suffix = "closed" if state == ConversationStatus.CLOSED else "archived"
    ids = _seed(suffix=suffix, initial_chat_deferred=False)
    link = _case_counts(ids)[0]
    conv = db.session.get(Conversation, link.conversation_id)
    assert conv.status == ConversationStatus.ACTIVE
    original_conversation_id = conv.id
    conv.status = state
    now = datetime.now(timezone.utc)
    if state == ConversationStatus.CLOSED:
        conv.closed_at = now
    else:
        conv.archived_at = now
    db.session.commit()
    new_assignment = assignment.assign_primary_clinician(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
        actor_user_id=ids["admin"], staff_id=ids["next_doctor"],
        assignment_kind="primary_doctor", expected_current_assignment_id=ids["initial_assignment"],
        assignment_reason="CT4B3 policy boundary replacement",
    )
    assert new_assignment.id != ids["initial_assignment"]
    db.session.expire_all()
    row = _scoped_request(ids)
    assert (row.request_sequence, row.processed_sequence) == (2, 1)
    assert len(_scoped_assignments(ids)) == 2
    assert _scoped_assignments(ids)[0].ended_at is not None
    assert _scoped_assignments(ids)[1].ended_at is None
    assert len(_case_counts(ids)) == 1
    with pytest.raises(ConflictError):
        events.deliver_pending_case_chat_sync(
            clinic_id=ids["clinic"], admission_id=ids["admission"],
            actor_user_id=ids["admin"],
        )
    db.session.rollback()
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(
            ids["doctor_user"], original_conversation_id,
        )
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(
            ids["next_user"], original_conversation_id,
        )
    db.session.rollback()
    row = _scoped_request(ids)
    assert (row.request_sequence, row.processed_sequence) == (2, 1)
    assert len(_case_counts(ids)) == 1
    conv = db.session.get(Conversation, original_conversation_id)
    assert conv.status == state
    parts = db.session.execute(
        db.select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == original_conversation_id
        )
    ).scalars().all()
    assert len(parts) == 1
    assert parts[0].user_id == ids["doctor_user"]
    assert parts[0].status == ParticipantStatus.ACCEPTED
    # Historical membership remains stored but cannot confer live access.
    db.session.rollback()
    print(f"CT4B3_{state.value.upper()}_DENIAL_PENDING_NO_REACTIVATION=PASS", flush=True)
