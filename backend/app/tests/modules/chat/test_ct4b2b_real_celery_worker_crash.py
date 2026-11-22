"""CT4B2B: real solo Celery worker process crash at case-chat acknowledgement.

One focused test.  PostgreSQL 18 and Redis 8 are disposable Docker containers;
the worker processes are locally launched Python processes, not shared workers.
The test does not prove automatic Redis-broker redelivery: it explicitly sends
one new retry task after the crash, exercising the existing durable SQL queue.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url


def _guarded_configuration():
    db_url = os.environ.get("DATABASE_URL", "")
    redis_url = os.environ.get("REDIS_URL", "")
    pg = make_url(db_url)
    ru = urlsplit(redis_url)
    required = (
        pg.get_backend_name() == "postgresql",
        pg.drivername == "postgresql+psycopg2",
        pg.username == "ct4b2b",
        pg.host == "127.0.0.1",
        pg.database == "ct4b2b_disposable",
        str(pg.port) == os.environ.get("CT4B2B_PG_PORT"),
        ru.scheme == "redis",
        ru.hostname == "127.0.0.1",
        str(ru.port) == os.environ.get("CT4B2B_REDIS_PORT"),
        ru.path == "/15",
        os.environ.get("CT4B2B_OWNER_TOKEN", "") != "",
    )
    assert all(required), "CT4B2B_ISOLATED_INFRA_GUARD_FAILED"
    token = os.environ["CT4B2B_OWNER_TOKEN"]
    assert len(token) >= 25
    for prefix, portname, publicport in (
        ("PG", "5432/tcp", "CT4B2B_PG_PORT"),
        ("REDIS", "6379/tcp", "CT4B2B_REDIS_PORT"),
    ):
        name = os.environ["CT4B2B_" + prefix + "_CONTAINER"]
        assert name.startswith("ct4b2b-" + prefix.lower() + "-")
        result = subprocess.run(
            ["docker", "inspect", name],
            text=True, capture_output=True, timeout=15, check=True,
        )
        raw = json.loads(result.stdout)
        assert len(raw) == 1
        d = raw[0]
        assert d["Name"] == "/" + name
        assert d["State"]["Running"] is True
        assert d["Config"]["Labels"].get("org.openai.ct4b2b.owner") == token
        bind = d["NetworkSettings"]["Ports"][portname]
        assert len(bind) == 1
        assert bind[0]["HostIp"] == "127.0.0.1"
        assert int(bind[0]["HostPort"]) == int(os.environ[publicport])
    evidence = Path(os.environ["CT4B2B_EVIDENCE_DIR"]).resolve()
    assert evidence.is_dir() and "CT4B2B_Evidence_" in evidence.name
    launcher = Path(os.environ["CT4B2B_WORKER_LAUNCHER"]).resolve()
    assert launcher.is_file() and launcher.name == "CT4B2B_Worker_Launcher.py"
    return evidence, launcher


def _assert_state(*, expected_processed: int, expected_conversations: int):
    from app.extensions import db
    from app.modules.nursing.models.admission_care_assignment_model import AdmissionCareAssignment
    from app.modules.chat.models.admission_case_chat_sync_request_model import AdmissionCaseChatSyncRequest
    from app.modules.chat.models.admission_case_conversation_model import AdmissionCaseConversation
    from app.modules.chat.models.conversation_model import Conversation
    from app.modules.chat.models.conversation_participant_model import ConversationParticipant

    db.session.rollback()  # release reads before crossing process boundaries
    assert db.session.query(AdmissionCareAssignment).count() == 1
    queue = db.session.query(AdmissionCaseChatSyncRequest).one()
    assert queue.request_sequence == 1
    assert queue.processed_sequence == expected_processed
    assert db.session.query(AdmissionCaseConversation).count() == expected_conversations
    assert db.session.query(Conversation).count() == expected_conversations
    assert db.session.query(ConversationParticipant).count() == expected_conversations
    return queue


def _seed():
    from app.extensions import db
    from app.core.enums.clinic_enums import ClinicStatus
    from app.core.enums.role_enums import Role
    from app.core.enums.staff_enums import StaffStatus
    from app.core.enums.ward_enums import AdmissionStatus, BedStatus, WardType
    from app.core.auth.user.models.user_model import User
    from app.modules.clinic.models.clinic_model import Clinic
    from app.modules.staff.models.staff_model import Staff
    from app.modules.patient.models.patient_model import Patient
    from app.modules.ward.models.ward_model import Ward, Bed, Admission
    from app.modules.nursing.services import care_team_assignment_service as assignment
    from app.modules.chat.services import case_chat_assignment_event_service as events

    from app import models_registry  # noqa: F401, imports the same canonical tables
    db.create_all()  # disposable PostgreSQL only; no Alembic and no dev DB
    clinic = Clinic(name="CT4B2B Disposable Clinic", status=ClinicStatus.ACTIVE, ai_credits=5)
    db.session.add(clinic)
    db.session.flush()
    admin = User(clinic_id=clinic.id, role=Role.ADMIN, is_active=True,
                 email="ct4b2b-admin@example.test")
    admin.set_password("synthetic-only-test-password")
    doctor_user = User(clinic_id=clinic.id, role=Role.DOCTOR, is_active=True,
                       email="ct4b2b-doctor@example.test")
    doctor_user.set_password("synthetic-only-test-password")
    db.session.add_all([admin, doctor_user]); db.session.flush()
    doctor = Staff(clinic_id=clinic.id, user_id=doctor_user.id,
                   first_name="CT4B2B", last_name="Doctor", status=StaffStatus.ACTIVE)
    patient = Patient(clinic_id=clinic.id, first_name="Synthetic", last_name="Patient",
                      patient_number="CT4B2B-ONLY", is_active=True)
    db.session.add_all([doctor, patient]); db.session.flush()
    ward = Ward(clinic_id=clinic.id, name="CT4B2B Test Ward",
                ward_type=WardType.GENERAL, capacity=1)
    db.session.add(ward); db.session.flush()
    bed = Bed(ward_id=ward.id, bed_number="CT4B2B-1", status=BedStatus.OCCUPIED)
    db.session.add(bed); db.session.flush()
    admission = Admission(patient_id=patient.id, bed_id=bed.id,
                          admitted_by_id=doctor.id, status=AdmissionStatus.ADMITTED)
    db.session.add(admission); db.session.commit()
    clinic_id, admission_id, actor_id, doctor_id = (
        clinic.id, admission.id, admin.id, doctor.id
    )

    original = events.ensure_admission_case_conversation

    def transport_down(**_):
        raise RuntimeError("CT4B2B intentional initial chat outage")

    events.ensure_admission_case_conversation = transport_down
    try:
        assignment.assign_primary_clinician(
            clinic_id=clinic_id, admission_id=admission_id,
            actor_user_id=actor_id, staff_id=doctor_id,
            assignment_kind="primary_doctor",
            assignment_reason="CT4B2B disposable worker crash boundary",
        )
    finally:
        events.ensure_admission_case_conversation = original
    _assert_state(expected_processed=0, expected_conversations=0)
    print("CT4B2B_POSTGRES_CLINICAL_ASSIGNMENT_DURABLE=PASS", flush=True)
    print("CT4B2B_SQL_CHAT_REQUEST_PENDING=PASS", flush=True)


def _spawn_worker(evidence: Path, launcher: Path, *, blocking: bool, queue: str):
    env = os.environ.copy()
    env["CT4B2B_BLOCK_ACK"] = "1" if blocking else "0"
    env["CT4B2B_QUEUE"] = queue
    logfile = evidence / ("CT4B2B_Worker_Crash.log" if blocking else "CT4B2B_Worker_Recovery.log")
    handle = logfile.open("w", encoding="utf-8")
    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            [sys.executable, "-u", str(launcher)],
            cwd=str(Path(os.environ["CT4B2B_WORKTREE"]) / "backend"),
            env=env, stdout=handle, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, **kwargs,
        )
        (evidence / ("CT4B2B_CrashWorker_PID.txt" if blocking else "CT4B2B_RecoveryWorker_PID.txt")).write_text(
            str(proc.pid), encoding="ascii"
        )
        return proc, handle, logfile
    except Exception:
        handle.close()
        raise


def _wait_for_marker(proc, marker: Path, logfile: Path, seconds: float = 80):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if marker.exists() and marker.read_text(encoding="ascii").strip() == "ACK_BOUNDARY_REACHED":
            return
        if proc.poll() is not None:
            raise AssertionError("CT4B2B_WORKER_EXITED_BEFORE_ACK_MARKER; see " + str(logfile))
        time.sleep(0.35)
    raise AssertionError("CT4B2B_ACK_BOUNDARY_TIMEOUT; see " + str(logfile))


def _stop_owned_worker(proc, handle):
    try:
        if proc.poll() is None:
            proc.kill()  # exact Python subprocess started by this test, not shared workers
        proc.wait(timeout=20)
    finally:
        if handle is not None:
            handle.close()


def test_real_worker_crash_after_chat_commit_before_sql_ack_and_recovery():
    """Kills a *real* Celery solo worker, restarts another, retries SQL queue.

    This is deliberate manual retry after an unacknowledged broker task; not an
    assertion about the Redis visibility-timeout redelivery scheduler.
    """
    evidence, launcher = _guarded_configuration()
    from app import create_app
    from app.extensions import celery, db
    from app.modules.chat.tasks import case_chat_sync_task  # noqa: F401

    app = create_app("development")
    crashed = recovered = None
    crash_handle = recovery_handle = None
    with app.app_context():
        assert db.engine.url.get_backend_name() == "postgresql"
        assert db.engine.url.database == "ct4b2b_disposable"
        queue = "ct4b2b-" + os.environ["CT4B2B_OWNER_TOKEN"][:12]
        marker = evidence / "CT4B2B_Ack_Boundary_Reached.marker"
        try:
            _seed()
            crashed, crash_handle, crash_log = _spawn_worker(
                evidence, launcher, blocking=True, queue=queue
            )
            task = celery.send_task(
                "process_pending_case_chat_sync", kwargs={"limit": 1},
                queue=queue, ignore_result=True,
            )
            assert task.id
            _wait_for_marker(crashed, marker, crash_log)
            print("CT4B2B_LIVE_WORKER_REACHED_ACK_BOUNDARY=PASS", flush=True)
            _assert_state(expected_processed=0, expected_conversations=1)
            print("CT4B2B_CHAT_COMMITTED_BEFORE_ACK=PASS", flush=True)
            _stop_owned_worker(crashed, crash_handle)
            crash_handle = None
            assert crashed.returncode is not None
            print("CT4B2B_REAL_SOLO_WORKER_KILLED=PASS", flush=True)
            _assert_state(expected_processed=0, expected_conversations=1)
            print("CT4B2B_SQL_PENDING_AFTER_CRASH=PASS", flush=True)
            recovered, recovery_handle, recovery_log = _spawn_worker(
                evidence, launcher, blocking=False, queue=queue
            )
            recovery = celery.send_task(
                "process_pending_case_chat_sync", kwargs={"limit": 1},
                queue=queue, expires=150, ignore_result=False,
            )
            # New Celery worker must execute this task and publish its result.
            result = recovery.get(timeout=100, propagate=True)
            assert result == {"claimed": 1, "processed": 1, "failed": 0, "still_pending": 0}, result
            _assert_state(expected_processed=1, expected_conversations=1)
            print("CT4B2B_REAL_WORKER_RETRY_DURABLE_ACK=PASS", flush=True)
            again = celery.send_task(
                "process_pending_case_chat_sync", kwargs={"limit": 1},
                queue=queue, expires=120, ignore_result=False,
            )
            assert again.get(timeout=70, propagate=True) == {
                "claimed": 0, "processed": 0, "failed": 0, "still_pending": 0,
            }
            _assert_state(expected_processed=1, expected_conversations=1)
            print("CT4B2B_SINGLE_CASE_LINK_NO_DUPLICATES=PASS", flush=True)
        finally:
            for proc, handle in ((crashed, crash_handle), (recovered, recovery_handle)):
                if proc is not None:
                    _stop_owned_worker(proc, handle)
            db.session.rollback()
            db.session.remove()
            db.engine.dispose()
