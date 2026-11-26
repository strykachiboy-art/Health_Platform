"""Disposable PostgreSQL only; real independent transactions, real services."""
import os
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select, text
from app.config import config_by_name
from app import create_app
from app.extensions import db as database
from app.core.exceptions import ConflictError
from app.modules.patient.models.patient_model import PatientAllergy
from app.core.audit.models.audit_model import AuditLog
from app.modules.patient.services import patient_service as service
from app.tests.core.concurrency.concurrency_harness import PostgresConcurrencyHarness


@pytest.fixture
def app():
    url = os.environ.get('CONCURRENCY_TEST_DATABASE_URL', '')
    name = os.environ.get('CSP_ALLERGY_DISPOSABLE_DB', '')
    parsed = urlsplit(url)
    assert parsed.scheme == 'postgresql+psycopg' and parsed.hostname == '127.0.0.1'
    assert name.startswith('csp_allergy_') and parsed.path == '/' + name
    config = config_by_name['testing']
    original = config.SQLALCHEMY_DATABASE_URI
    config.SQLALCHEMY_DATABASE_URI = url
    flask_app = create_app('testing')
    verified = False
    try:
        with flask_app.app_context():
            assert database.session.execute(text('select current_database()')).scalar_one() == name
            assert database.session.connection().get_isolation_level() == 'READ COMMITTED'
            verified = True
            database.session.rollback()
            from app import models_registry  # noqa: F401
            database.create_all()
            yield flask_app
    finally:
        # Runner owns the entire disposable container. No production DDL.
        with flask_app.app_context():
            database.session.rollback()
            database.session.remove()
            if verified:
                database.drop_all()
            database.engine.dispose()
        config.SQLALCHEMY_DATABASE_URI = original


@pytest.fixture
def registry(app, db, clinic, patient):
    ids = (clinic.id, patient.id)
    # db fixture ordinarily relies on the app fixture's active context.
    db.session.commit()
    return ids


def run_race(app, operation):
    h = PostgresConcurrencyHarness(os.environ['CONCURRENCY_TEST_DATABASE_URL'], workers=2, barrier_timeout_seconds=20)
    try:
        results = h.run_flask(app, operation)
    finally:
        h.close()
    assert len({r.backend_pid for r in results}) == 2
    assert all(r.backend_pid is not None for r in results)
    assert sum(r.error is None for r in results) == 1, repr(results)
    assert sum(isinstance(r.error, ConflictError) for r in results) == 1, repr(results)
    return next(r.value for r in results if r.error is None)


def create(ids, name, code=None):
    return service.create_patient_allergy(clinic_id=ids[0], patient_id=ids[1], data={'allergen_name': name, 'allergen_type': 'drug', 'allergen_code': code})


def audit_count(ids):
    return database.session.query(AuditLog).filter_by(clinic_id=ids[0], entity_type="patient_allergy").count()


def current(ids):
    database.session.expire_all()
    return database.session.execute(select(PatientAllergy).where(PatientAllergy.patient_id == ids[1], PatientAllergy.clinic_id == ids[0])).scalars().all()


@pytest.mark.parametrize('code', [None, 'TEST:001'])
def test_parallel_duplicate_create(app, registry, code):
    winner = run_race(app, lambda i: create(registry, ['Synthetic', 'synthetic'][i], [code, code.lower() if code else None][i]).id)
    assert [r.id for r in current(registry)] == [winner]
    assert audit_count(registry) == 1


@pytest.mark.parametrize('kind', ['edit_edit', 'status_status', 'edit_status'])
def test_parallel_reviewed_mutations(app, registry, kind):
    row = create(registry, 'Synthetic')
    identity, token = row.id, row.updated_at
    database.session.commit()
    def operation(i):
        args = dict(clinic_id=registry[0], patient_id=registry[1], allergy_id=identity, expected_updated_at=token)
        if kind == 'edit_edit' or (kind == 'edit_status' and i == 0):
            result = service.update_patient_allergy(**args, data={'reaction': f'synthetic-{i}'})
        else:
            result = service.transition_patient_allergy_status(**args, clinical_status=['resolved','inactive'][i], reason='synthetic review')
        return (result.reaction, result.clinical_status, result.updated_at)
    winner = run_race(app, operation)
    saved = current(registry)
    assert len(saved) == 1
    assert (saved[0].reaction, saved[0].clinical_status, saved[0].updated_at) == winner
    assert saved[0].updated_at > token
    assert audit_count(registry) == 2


def test_parallel_reactivation_vs_duplicate_create(app, registry):
    row = create(registry, 'Synthetic')
    row = service.transition_patient_allergy_status(clinic_id=registry[0], patient_id=registry[1], allergy_id=row.id, clinical_status='resolved', reason='synthetic', expected_updated_at=row.updated_at)
    identity, token = row.id, row.updated_at
    database.session.commit()
    def operation(i):
        if i == 0:
            return create(registry, 'synthetic').id
        return service.transition_patient_allergy_status(clinic_id=registry[0], patient_id=registry[1], allergy_id=identity, clinical_status='active', reason='synthetic', expected_updated_at=token).id
    winner = run_race(app, operation)
    assert [r.id for r in current(registry) if r.clinical_status == 'active' and r.verification_status != 'refuted'] == [winner]
    assert audit_count(registry) == 3


def test_parallel_identity_edit_vs_create(app, registry):
    row = create(registry, 'Original')
    identity, token = row.id, row.updated_at
    database.session.commit()
    def operation(i):
        if i == 0:
            return create(registry, 'Collision').id
        return service.update_patient_allergy(clinic_id=registry[0], patient_id=registry[1], allergy_id=identity, data={'allergen_name': 'collision'}, expected_updated_at=token).id
    winner = run_race(app, operation)
    assert [r.id for r in current(registry) if r.allergen_name.lower() == 'collision'] == [winner]
    assert audit_count(registry) == 2


def test_parallel_two_identity_edits(app, registry):
    rows = [create(registry, name) for name in ('Original A', 'Original B')]
    reviews = [(r.id, r.updated_at) for r in rows]
    database.session.commit()
    def operation(i):
        identity, token = reviews[i]
        return service.update_patient_allergy(clinic_id=registry[0], patient_id=registry[1], allergy_id=identity, data={'allergen_name': 'Collision'}, expected_updated_at=token).id
    winner = run_race(app, operation)
    assert [r.id for r in current(registry) if r.allergen_name == 'Collision'] == [winner]
    assert audit_count(registry) == 3
