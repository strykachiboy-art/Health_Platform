"""Staged external worklist contract; one actual isolated Orthanc round trip."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
from threading import Barrier
import uuid

import pytest
from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError
from app.extensions import db
from app.modules.radiology.models.ris_model import RadiologyWorklistDelivery
from app.modules.radiology.services import radc_worklist_service as local, ris_service as ris
from app.modules.radiology.services import radc_delivery_service as delivery
from app.modules.radiology.services.radc_orthanc_transport import ArchiveUnconfirmed, StagedOrthanc
from app.tests.modules.radiology.test_rad1_ris_foundation import setup_ris
from app.tests.modules.radiology.test_radc_b01_worklist import scheduled


class FakeArchive:
    archive_key = 'CSP-RADC-B02A-123456abcdef'
    def __init__(self):
        self.rows = {}
        self.creates = self.deletes = 0
        self.lose_create = self.lose_delete = self.bad_read = False
    def list(self):
        return copy.deepcopy(list(self.rows.values()))
    def create(self, tags):
        self.creates += 1
        key = str(uuid.uuid4())
        self.rows[key] = {'ID': key, 'Tags': copy.deepcopy(tags)}
        if self.lose_create:
            raise ArchiveUnconfirmed('synthetic lost response')
        return key
    def get(self, key):
        row = copy.deepcopy(self.rows.get(key))
        if row and self.bad_read:
            row['Tags']['IssuerOfPatientID'] = 'wrong'
        return row
    def delete(self, key):
        self.deletes += 1
        del self.rows[key]
        if self.lose_delete:
            raise ArchiveUnconfirmed('synthetic lost deletion response')


@pytest.fixture
def published(scheduled):
    s = scheduled
    p = local.publish(**s['args'], expected_version=2)
    s['delivery_args'] = dict(s['args'], publication_id=p['publication_id'])
    return s


@pytest.fixture
def fake(published, monkeypatch):
    archive = FakeArchive()
    monkeypatch.setattr(delivery, '_archive', lambda: archive)
    return published, archive


def test_staged_gate_default_closed(published, app):
    with pytest.raises(NotFoundError):
        delivery.dispatch(**published['delivery_args'])
    app.config['RAD_C_EXTERNAL_MWL_STAGED_ENABLED'] = True
    with pytest.raises(NotFoundError):
        delivery.dispatch(**published['delivery_args'])
    assert db.session.query(RadiologyWorklistDelivery).count() == 0


def test_ack_requires_exact_readback(fake):
    s,a = fake; args = s['delivery_args']
    result = delivery.dispatch(**args)
    assert result['state'] == 'acknowledged' and result['current_intent']
    assert result['archive_acknowledged'] and not result['modality_receipt_confirmed']
    assert not result['archive_verified'] and not result['diagnostic_report_signed']
    assert a.creates == 1
    assert delivery.status(**args) == result
    with pytest.raises(ConflictError):
        delivery.dispatch(**args)
    assert a.creates == 1


def test_lost_create_response_reconciles_without_replay(fake):
    s,a = fake; args = s['delivery_args']; a.lose_create = True
    with pytest.raises(ArchiveUnconfirmed):
        delivery.dispatch(**args)
    assert delivery.status(**args)['state'] == 'dispatch_pending'
    with pytest.raises(ConflictError):
        delivery.dispatch(**args)
    assert delivery.reconcile(**args)['state'] == 'acknowledged'
    assert a.creates == 1


def test_missing_readback_never_authorizes_resend(fake):
    s,a = fake; args = s['delivery_args']; a.lose_create = True
    with pytest.raises(ArchiveUnconfirmed):
        delivery.dispatch(**args)
    a.rows.clear()
    with pytest.raises(ArchiveUnconfirmed):
        delivery.reconcile(**args)
    with pytest.raises(ConflictError):
        delivery.dispatch(**args)
    assert a.creates == 1 and delivery.status(**args)['state'] == 'dispatch_pending'


def test_wrong_issuer_readback_held(fake):
    s,a = fake; args = s['delivery_args']; a.bad_read = True
    with pytest.raises(ArchiveUnconfirmed):
        delivery.dispatch(**args)
    assert delivery.status(**args)['state'] == 'dispatch_pending'
    with pytest.raises(ArchiveUnconfirmed):
        delivery.reconcile(**args)
    assert a.creates == 1


def test_duplicate_matching_rows_held(fake):
    s,a = fake; args = s['delivery_args']; a.lose_create = True
    with pytest.raises(ArchiveUnconfirmed):
        delivery.dispatch(**args)
    row = copy.deepcopy(next(iter(a.rows.values())))
    row['ID'] = str(uuid.uuid4()); a.rows[row['ID']] = row
    with pytest.raises(ArchiveUnconfirmed):
        delivery.reconcile(**args)
    assert delivery.status(**args)['state'] == 'dispatch_pending'


def test_reschedule_requires_prior_external_withdrawal(fake):
    s,a = fake; args = s['delivery_args']; delivery.dispatch(**args)
    ris.schedule_intent(**s['args'], expected_version=2, scheduled_at=s['when']+timedelta(days=1), station_code='XR02')
    assert not delivery.status(**args)['current_intent']
    new = local.publish(**s['args'], expected_version=3)
    second = dict(s['args'], publication_id=new['publication_id'])
    with pytest.raises(ConflictError):
        delivery.dispatch(**second)
    assert delivery.withdraw(**args)['state'] == 'withdrawn'
    assert delivery.dispatch(**second)['state'] == 'acknowledged'
    assert a.creates == 2 and a.deletes == 1
    assert db.session.query(RadiologyWorklistDelivery).count() == 2


def test_lost_withdrawal_response_reconciles_without_replay(fake):
    s,a = fake; args = s['delivery_args']; delivery.dispatch(**args)
    a.lose_delete = True
    with pytest.raises(ArchiveUnconfirmed):
        delivery.withdraw(**args)
    assert delivery.status(**args)['state'] == 'withdraw_pending'
    with pytest.raises(ConflictError):
        delivery.withdraw(**args)
    assert delivery.reconcile(**args)['state'] == 'withdrawn'
    assert a.deletes == 1


def test_wrong_doctor_and_wrong_clinic_denied(fake, make_staff, make_clinic):
    s,a = fake; args = s['delivery_args']
    other = make_staff(s['clinic'], role=Role.DOCTOR)
    foreign_clinic = make_clinic(); foreign = make_staff(foreign_clinic, role=Role.DOCTOR)
    other_id, foreign_id, foreign_clinic_id = other.id, foreign.id, foreign_clinic.id
    db.session.commit()
    with pytest.raises(NotFoundError):
        delivery.dispatch(**dict(args, actor_staff_id=other_id))
    with pytest.raises(NotFoundError):
        delivery.dispatch(**dict(args, clinic_id=foreign_clinic_id, actor_staff_id=foreign_id))
    assert a.creates == 0


def test_failed_pending_audit_prevents_send(fake, monkeypatch):
    s,a = fake; args = s['delivery_args']
    def fail(*_):
        raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(delivery, '_audit', fail)
    with pytest.raises(RuntimeError):
        delivery.dispatch(**args)
    assert a.creates == 0 and db.session.query(RadiologyWorklistDelivery).count() == 0


def test_archive_transport_rejects_unapproved_key():
    with pytest.raises(ArchiveUnconfirmed):
        StagedOrthanc(port=8042, username='staged_user', password='a'*32, archive_key='production')


def test_real_disposable_orthanc_readback_and_withdraw(published, app):
    # Mandatory gate, not skipped when the isolated archive is absent.
    app.config['RAD_C_EXTERNAL_MWL_STAGED_ENABLED'] = True
    app.config['RAD_C_EXTERNAL_MWL_ARCHIVE'] = json.loads(os.environ['RADC_DISPOSABLE_ORTHANC_CONFIG'])
    args = published['delivery_args']
    ack = delivery.dispatch(**args)
    assert ack['archive_acknowledged'] and ack['current_intent']
    assert delivery.reconcile(**args)['state'] == 'acknowledged'
    assert delivery.withdraw(**args)['state'] == 'withdrawn'
    assert not delivery.status(**args)['archive_acknowledged']


def test_concurrent_external_dispatch_on_postgres(fake, app):
    if db.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL row locking required')
    s,a = fake; args = s['delivery_args']; db.session.remove()
    barrier = Barrier(2, timeout=20)
    def attempt(_):
        with app.app_context():
            barrier.wait()
            try:
                return delivery.dispatch(**args)['state']
            except ConflictError:
                return 'conflict'
            finally:
                db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ['acknowledged','conflict']
    assert a.creates == 1 and db.session.query(RadiologyWorklistDelivery).count() == 1
