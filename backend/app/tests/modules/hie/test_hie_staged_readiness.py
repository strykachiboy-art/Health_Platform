"""Synthetic readiness tests: no external network or partner credentials."""
from unittest.mock import Mock

import pytest
from flask import Flask

from app.config import Config, ProductionConfig
from app.core.enums.hie_enums import (
    HIEIntegrationStatus, HIEOperation, HIESubmissionStatus,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.modules.hie.models.hie_model import HIEIntegration, HIESubmission
from app.modules.hie.providers.registry import register_provider, unregister_provider
from app.modules.hie.services import hie_service as service


@pytest.fixture
def held(app):
    app.config['HIE_PARTNER_RELEASE_HOLD'] = True
    return app


@pytest.fixture
def registered():
    factory = Mock(side_effect=AssertionError('Provider must not be invoked'))
    register_provider('hie01b-synthetic', factory)
    try:
        yield factory
    finally:
        unregister_provider('hie01b-synthetic')


def integration(db, clinic, status=HIEIntegrationStatus.ACTIVE):
    item = HIEIntegration(clinic_id=clinic.id, provider='hie01b-synthetic',
                          status=status, endpoint_url='https://fhir.partnerhealth.org/fhir/r4',
                          organization_id='synthetic-org', facility_id='synthetic-facility')
    db.session.add(item)
    db.session.flush()
    return item


def test_production_hold_cannot_be_lifted_by_config():
    assert Config.HIE_PARTNER_RELEASE_HOLD is True
    assert ProductionConfig.HIE_PARTNER_RELEASE_HOLD is True
    flask_app = Flask(__name__)
    flask_app.config.update(TESTING=False, HIE_PARTNER_RELEASE_HOLD=False)
    with flask_app.app_context():
        assert service.hie_partner_release_held() is True


def test_pending_create_and_registered_activation_denied(held, db, clinic, user, auth_headers_for, registered):
    db.session.commit()
    client = held.test_client()
    headers = auth_headers_for(user)
    response = client.post('/api/v1/hie/integrations', headers=headers, json={
        'provider': 'hie01b-synthetic', 'endpoint_url': 'https://fhir.partnerhealth.org/fhir/r4',
        'organization_id': 'synthetic-org', 'facility_id': 'synthetic-facility',
    })
    assert response.status_code == 201
    record = response.get_json()['data']
    assert record['status'] == 'pending'
    assert record['readiness'] == {'provider_registered': True, 'external_access_allowed': False,
                                    'reason_code': 'HIE_PARTNER_APPROVAL_REQUIRED'}
    path = f"/api/v1/hie/integrations/{record['id']}"
    response = client.patch(path, headers=headers, json={'status': 'active'})
    assert response.status_code == 422
    assert response.get_json()['code'] == 'HIE_PARTNER_APPROVAL_REQUIRED'
    assert client.get(path, headers=headers).get_json()['data']['status'] == 'pending'
    registered.assert_not_called()


@pytest.mark.parametrize('method', ['query_patient', 'query_clinical_data', 'submit_patient',
                                    'submit_clinical_data', 'submit_clinical_document'])
def test_stale_active_record_blocks_all_provider_work_before_persistence(
        held, db, clinic, make_staff, patient, registered, method):
    actor = make_staff(clinic, role=Role.DOCTOR)
    item = integration(db, clinic)
    db.session.commit()
    before = db.session.query(HIESubmission).count()
    kwargs = {'clinic_id': clinic.id, 'integration_id': item.id}
    if method.startswith('query_'):
        kwargs.update(requesting_user_id=actor.user_id, purpose_of_use='treatment',
                      patient_identifier='synthetic-identifier')
    else:
        kwargs.update(patient_id=patient.id, payload={'synthetic': True})
    with pytest.raises(ValidationError) as exc:
        getattr(service, method)(**kwargs)
    assert exc.value.code == 'HIE_PARTNER_APPROVAL_REQUIRED'
    assert db.session.query(HIESubmission).count() == before
    registered.assert_not_called()


def test_active_record_can_be_disabled_but_not_reconfigured_active(held, db, clinic, user, auth_headers_for, registered):
    item = integration(db, clinic)
    db.session.commit()
    client = held.test_client()
    headers = auth_headers_for(user)
    path = f'/api/v1/hie/integrations/{item.id}'
    response = client.patch(path, headers=headers, json={'facility_id': 'different'})
    assert response.status_code == 422
    assert response.get_json()['code'] == 'HIE_PARTNER_APPROVAL_REQUIRED'
    response = client.patch(path, headers=headers, json={'status': 'disabled'})
    assert response.status_code == 200
    assert response.get_json()['data']['status'] == 'disabled'
    assert response.get_json()['data']['readiness']['external_access_allowed'] is False
    registered.assert_not_called()


def test_submission_history_withholds_payloads_and_preserves_clinic_scope(
        held, db, clinic, make_clinic, user, auth_headers_for):
    other = make_clinic()
    a, b = integration(db, clinic), integration(db, other)
    for item in (a, b):
        db.session.add(HIESubmission(integration_id=item.id, clinic_id=item.clinic_id,
            operation=HIEOperation.PATIENT_QUERY, status=HIESubmissionStatus.SUCCESS,
            request_data={'patient_identifier': 'synthetic-private-id'},
            response_data={'payload': 'synthetic-private-chart'},
            external_reference='synthetic-private-reference', error_message='synthetic-private-error',
            status_code=200, retry_count=0))
    db.session.commit()
    response = held.test_client().get('/api/v1/hie/submissions', headers=auth_headers_for(user))
    assert response.status_code == 200
    data = response.get_json()['data']
    assert data['total'] == 1
    record = data['items'][0]
    assert record['clinic_id'] == clinic.id and record['integration_id'] == a.id
    assert record['status_code'] == 200 and record['status'] == 'success'
    for key in ('request_data', 'response_data', 'external_reference', 'error_message'):
        assert record[key] is None
    assert 'synthetic-private-' not in response.get_data(as_text=True)
    # Read-only withholding must not destroy historical records.
    original = db.session.query(HIESubmission).filter_by(integration_id=a.id).one()
    assert original.response_data == {'payload': 'synthetic-private-chart'}


@pytest.mark.parametrize('role', [Role.ADMIN, Role.DOCTOR, Role.NURSE, Role.LAB_TECHNICIAN, Role.PHARMACIST])
def test_existing_view_roles_keep_known_integration_read_access(held, db, clinic, make_user, auth_headers_for, role):
    item = integration(db, clinic, HIEIntegrationStatus.PENDING)
    actor = make_user(clinic, role=role)
    db.session.commit()
    response = held.test_client().get(f'/api/v1/hie/integrations/{item.id}', headers=auth_headers_for(actor))
    assert response.status_code == 200
    assert response.get_json()['data']['readiness']['external_access_allowed'] is False


def test_wrong_clinic_and_stale_role_denied(held, db, clinic, make_clinic, make_user, auth_headers_for):
    other = make_clinic()
    item = integration(db, other)
    actor = make_user(clinic, role=Role.ADMIN)
    patient_actor = make_user(clinic, role=Role.PATIENT)
    doctor = make_user(clinic, role=Role.DOCTOR)
    db.session.commit()
    client = held.test_client()
    response = client.get(f'/api/v1/hie/integrations/{item.id}', headers=auth_headers_for(actor))
    assert response.status_code in (403, 404, 422)
    for headers in (auth_headers_for(patient_actor, role=Role.ADMIN), auth_headers_for(doctor)):
        response = client.post('/api/v1/hie/integrations', headers=headers, json={'provider': 'hie01b-synthetic'})
        assert response.status_code in (401, 403)
    assert db.session.query(HIEIntegration).count() == 1
