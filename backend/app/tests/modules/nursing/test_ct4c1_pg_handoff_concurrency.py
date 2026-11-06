"""CT4C1: two PostgreSQL workers compete for acknowledged nurse ownership.

Test-only: disposable PostgreSQL and Redis; no main/development DB, migration,
production source change, or alteration to prior CT4 tests.
Lock order is verified through real concurrent calls to the unchanged service,
not barriers injected after its admission row lock.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import create_app
from app.config import config_by_name
from app.core.audit.models.audit_model import AuditLog
from app.core.auth.user.models.user_model import User
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.nursing_enums import NursingHandoffStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus, BedStatus, WardType
from app.core.exceptions import ConflictError
from app.core.idempotency.models.idempotency_model import IdempotencyRecord
from app.extensions import db
from app.modules.chat.models.admission_case_chat_sync_request_model import (
    AdmissionCaseChatSyncRequest,
)
from app.modules.chat.models.admission_case_conversation_model import (
    AdmissionCaseConversation,
)
from app.modules.chat.services import case_chat_assignment_event_service as events
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.nursing.services import nursing_service
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission, Bed, Ward
from app.tests.core.concurrency.concurrency_harness import PostgresConcurrencyHarness


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def ct4c_postgres_app(monkeypatch):
    url = os.getenv('CONCURRENCY_TEST_DATABASE_URL', '')
    assert url.startswith('postgresql+'), 'CT4C1 requires disposable PostgreSQL URL'
    assert 'ct4c1_disposable' in url, 'CT4C1 refuses a non-disposable database name'
    original = config_by_name['testing'].SQLALCHEMY_DATABASE_URI
    config_by_name['testing'].SQLALCHEMY_DATABASE_URI = url
    app = None
    try:
        app = create_app('testing')
        with app.app_context():
            from app import models_registry  # noqa: F401
            assert db.engine.dialect.name == 'postgresql'
            db.create_all()
        # Deliberately defer chat transport AFTER clinical commit: the queue,
        # ownership and authentic audit rows must commit together.
        def offline(**_kw):
            raise RuntimeError('CT4C1 deferred chat delivery')
        monkeypatch.setattr(events, 'ensure_admission_case_conversation', offline)
        yield app
    finally:
        if app is not None:
            with app.app_context():
                db.session.rollback()
                db.session.remove()
                db.drop_all()
                db.engine.dispose()
        config_by_name['testing'].SQLALCHEMY_DATABASE_URI = original


def _seed_case(*, second_handoff: bool = False):
    now = _now()
    clinic = Clinic(name='CT4C1 Concurrent Clinic', status=ClinicStatus.ACTIVE, ai_credits=5)
    db.session.add(clinic)
    db.session.flush()
    people = {}
    for label, role in (
        ('admin', Role.ADMIN), ('doctor', Role.DOCTOR),
        ('outgoing', Role.NURSE), ('incoming1', Role.NURSE),
        ('incoming2', Role.NURSE),
    ):
        u = User(clinic_id=clinic.id, role=role, is_active=True,
                 email=f'ct4c1-{label}@test.invalid')
        db.session.add(u)
        db.session.flush()
        st = Staff(clinic_id=clinic.id, user_id=u.id,
                   first_name='CT4C1', last_name=label, status=StaffStatus.ACTIVE)
        db.session.add(st)
        db.session.flush()
        people[label] = (u.id, st.id)
    p = Patient(clinic_id=clinic.id, first_name='Race', last_name='Case',
                patient_number='CT4C1-PATIENT', is_active=True)
    db.session.add(p)
    db.session.flush()
    ward = Ward(clinic_id=clinic.id, name='CT4C1 Ward',
                ward_type=WardType.GENERAL, capacity=3)
    db.session.add(ward)
    db.session.flush()
    bed = Bed(ward_id=ward.id, bed_number='CT4C1-A', status=BedStatus.OCCUPIED)
    db.session.add(bed)
    db.session.flush()
    admission = Admission(patient_id=p.id, bed_id=bed.id,
                          admitted_by_id=people['doctor'][1],
                          status=AdmissionStatus.ADMITTED, admitted_at=now)
    db.session.add(admission)
    db.session.flush()
    initial = AdmissionCareAssignment(
        clinic_id=clinic.id, patient_id=p.id, admission_id=admission.id,
        staff_id=people['outgoing'][1], assignment_kind='primary_nurse',
        assigned_by_user_id=people['admin'][0],
        assignment_reason='CT4C1 initial assignment', assigned_at=now,
    )
    db.session.add(initial)
    db.session.flush()
    handoffs = {}
    for target in ('incoming1', 'incoming2') if second_handoff else ('incoming1',):
        handoff = NursingHandoff(
            clinic_id=clinic.id, patient_id=p.id, admission_id=admission.id,
            outgoing_staff_id=people['outgoing'][1],
            incoming_staff_id=people[target][1],
            status=NursingHandoffStatus.READY, ready_at=now,
            shift_start_at=now, shift_end_at=now + timedelta(hours=8),
            version=2, situation='Stable', assessment='Observed',
            recommendation='Continue monitoring',
        )
        db.session.add(handoff)
        db.session.flush()
        handoffs[target] = handoff.id
    ids = {
        'clinic_id': clinic.id, 'patient_id': p.id, 'admission_id': admission.id,
        'initial_assignment_id': initial.id,
        'incoming1_id': people['incoming1'][1],
        'incoming1_user_id': people['incoming1'][0],
        'incoming2_id': people['incoming2'][1],
        'incoming2_user_id': people['incoming2'][0],
        'outgoing_id': people['outgoing'][1],
        'handoffs': handoffs,
    }
    db.session.commit()
    return ids


def _call(ids, target, idem_key=None):
    args = dict(
        clinic_id=ids['clinic_id'],
        actor_staff_id=ids[f'{target}_id'],
    )
    if idem_key is not None:
        args.update(
            idempotency_key=idem_key,
            idempotency_user_id=ids[f'{target}_user_id'],
        )
    return nursing_service.acknowledge_handoff(ids['handoffs'][target], **args)


def _race(app, ids, chooser):
    harness = PostgresConcurrencyHarness(
        os.environ['CONCURRENCY_TEST_DATABASE_URL'],
        workers=2, barrier_timeout_seconds=20,
    )
    try:
        def operation(worker_index):
            target, key = chooser(worker_index)
            try:
                result = _call(ids, target, key)
                return {
                    'outcome': 'accepted',
                    'target': target, 'handoff_id': result.id,
                    'status': result.status.value,
                }
            except ConflictError as exc:
                return {
                    'outcome': 'conflict',
                    'target': target, 'error_type': type(exc).__name__,
                }
        results = harness.run_flask(app, operation)
    finally:
        harness.close()
    assert len(results) == 2
    assert all(r.error is None for r in results), [repr(r.error) for r in results]
    assert len({r.backend_pid for r in results}) == 2
    return [r.value for r in results]


def _assert_one_transfer(ids, expected_target):
    db.session.expire_all()
    assignments = db.session.execute(
        select(AdmissionCareAssignment)
        .where(AdmissionCareAssignment.clinic_id == ids['clinic_id'],
               AdmissionCareAssignment.admission_id == ids['admission_id'],
               AdmissionCareAssignment.assignment_kind == 'primary_nurse')
        .order_by(AdmissionCareAssignment.id)
    ).scalars().all()
    assert len(assignments) == 2
    assert assignments[0].id == ids['initial_assignment_id']
    assert assignments[0].ended_at is not None
    assert assignments[1].ended_at is None
    assert assignments[1].staff_id == ids[f'{expected_target}_id']
    assert assignments[1].assigned_at == assignments[0].ended_at

    queue = db.session.execute(
        select(AdmissionCaseChatSyncRequest).where(
            AdmissionCaseChatSyncRequest.clinic_id == ids['clinic_id'],
            AdmissionCaseChatSyncRequest.admission_id == ids['admission_id'],
        )
    ).scalar_one()
    assert (queue.request_sequence, queue.processed_sequence) == (1, 0)
    assert queue.authorization_handoff_id == ids['handoffs'][expected_target]
    assert queue.requested_by_user_id == ids[f'{expected_target}_user_id']
    assert db.session.scalar(select(func.count()).select_from(AdmissionCaseConversation)) == 0

    # Real audit writes are deliberately NOT mocked; each accepted handoff
    # writes two ownership audit rows plus its nursing acknowledgement audit.
    audits = db.session.execute(select(AuditLog).where(
        AuditLog.clinic_id == ids['clinic_id']
    )).scalars().all()
    assert len(audits) == 3, [(a.entity_type, a.description) for a in audits]
    assert all(len(a.evidence_sha256) == 64 for a in audits)
    print('CT4C1_PG_SINGLE_PRIMARY_OWNER=PASS')
    print('CT4C1_ATOMIC_AUDIT_AND_DURABLE_QUEUE=PASS')


def test_two_workers_same_handoff_distinct_keys_exact_one_wins(ct4c_postgres_app):
    with ct4c_postgres_app.app_context():
        ids = _seed_case()
        outcomes = _race(ct4c_postgres_app, ids,
                         lambda n: ('incoming1', f'ct4c1-distinct-{n}'))
        assert sorted(x['outcome'] for x in outcomes) == ['accepted', 'conflict']
        _assert_one_transfer(ids, 'incoming1')
        assert db.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
        print('CT4C1_DISTINCT_KEY_ONE_WINNER_ONE_CONFLICT=PASS')


def test_two_workers_same_handoff_same_key_both_replay_once(ct4c_postgres_app):
    with ct4c_postgres_app.app_context():
        ids = _seed_case()
        outcomes = _race(ct4c_postgres_app, ids,
                         lambda n: ('incoming1', 'ct4c1-identical-key'))
        assert [x['outcome'] for x in outcomes].count('accepted') == 2
        assert {x['handoff_id'] for x in outcomes} == {ids['handoffs']['incoming1']}
        _assert_one_transfer(ids, 'incoming1')
        assert db.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
        print('CT4C1_SAME_KEY_TWO_WORKER_REPLAY_ONCE=PASS')


def test_two_workers_same_handoff_without_key_one_conflict(ct4c_postgres_app):
    with ct4c_postgres_app.app_context():
        ids = _seed_case()
        outcomes = _race(ct4c_postgres_app, ids,
                         lambda n: ('incoming1', None))
        assert sorted(x['outcome'] for x in outcomes) == ['accepted', 'conflict']
        _assert_one_transfer(ids, 'incoming1')
        assert db.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0
        print('CT4C1_NO_KEY_DUPLICATE_ACK_BLOCKED=PASS')


def test_competing_ready_handoffs_exact_one_primary_owner(ct4c_postgres_app):
    with ct4c_postgres_app.app_context():
        ids = _seed_case(second_handoff=True)
        outcomes = _race(ct4c_postgres_app, ids,
                         lambda n: ('incoming1' if n == 0 else 'incoming2', None))
        assert sorted(x['outcome'] for x in outcomes) == ['accepted', 'conflict']
        winner = next(x['target'] for x in outcomes if x['outcome'] == 'accepted')
        loser = 'incoming2' if winner == 'incoming1' else 'incoming1'
        _assert_one_transfer(ids, winner)
        assert db.session.get(NursingHandoff, ids['handoffs'][winner]).status == NursingHandoffStatus.ACKNOWLEDGED
        assert db.session.get(NursingHandoff, ids['handoffs'][loser]).status == NursingHandoffStatus.READY
        print('CT4C1_COMPETING_HANDOFFS_SINGLE_WINNER=PASS')
