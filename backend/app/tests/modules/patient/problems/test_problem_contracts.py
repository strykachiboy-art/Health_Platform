"""Pure contract tests; run independently of Flask/PostgreSQL."""
from datetime import date
from pathlib import Path
import importlib.util
import sys
import pytest
from pydantic import ValidationError

# Load the pure domain module without bootstrapping the Flask application.
path = Path(__file__).resolve().parents[4] / 'modules/patient/problems/contracts.py'
spec = importlib.util.spec_from_file_location('problem_contracts_pure', path)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
TODAY = date(2026, 11, 24)


def content(**kw):
    return m.ProblemContent.model_validate(dict(name='Hypertension', category='chronic', clinical_status='active', verification_status='confirmed') | kw)


@pytest.mark.parametrize('extra', [{'clinic_id': 2}, {'patient_id': 8}, {'organization_id': 'forged'}, {'recorded_by_staff_id': 5}, {'version': 2}])
def test_client_cannot_assign_authority(extra):
    with pytest.raises(ValidationError):
        content(**extra)


@pytest.mark.parametrize('status', ['active','inactive','resolved','remission','recurrence','relapse'])
@pytest.mark.parametrize('verification', ['unconfirmed','provisional','differential','confirmed','refuted','entered_in_error'])
def test_context_eligibility(status, verification):
    item = content(clinical_status=status, verification_status=verification, resolution_date=TODAY if status=='resolved' else None)
    assert m.eligible_for_clinical_context(item) == (status in {'active','recurrence','relapse'} and verification=='confirmed')


@pytest.mark.parametrize('kw', [{'name':'  '}, {'code':'I10'}, {'coding_system':'http://hl7.org/fhir/sid/icd-10'}, {'code':'abc', 'coding_system':'http://snomed.info/sct'}, {'clinical_status':'resolved'}, {'resolution_date':TODAY}, {'clinical_status':'resolved','resolution_date':date(2025,1,1),'onset_date':TODAY}])
def test_invalid_content(kw):
    with pytest.raises(ValidationError):
        content(**kw)


@pytest.mark.parametrize('verification', ['refuted','entered_in_error'])
def test_terminal_creation_denied(verification):
    with pytest.raises(ValueError):
        m.validate_create(content(verification_status=verification),TODAY)


def test_no_future_dates():
    with pytest.raises(ValueError):
        m.validate_create(content(onset_date=date(2027,1,1)),TODAY)


def test_explicit_resolution_recurrence():
    a=content(); b=content(clinical_status='resolved',resolution_date=TODAY); c=content(clinical_status='recurrence')
    m.validate_change(a,b,'transition',TODAY)
    m.validate_change(b,c,'transition',TODAY)
    with pytest.raises(ValueError):
        m.validate_change(b,a,'transition',TODAY)


def test_update_cannot_change_status_or_identity():
    for item in (content(verification_status='provisional'), content(name='Diabetes')):
        with pytest.raises(ValueError):
            m.validate_change(content(),item,'update',TODAY)


def test_correction_preserves_error_distinction():
    with pytest.raises(ValueError):
        m.validate_change(content(),content(verification_status='entered_in_error'),'transition',TODAY)
    m.validate_change(content(),content(verification_status='entered_in_error'),'correct',TODAY)
    with pytest.raises(ValueError):
        m.validate_change(content(verification_status='entered_in_error'),content(),'correct',TODAY)


@pytest.mark.parametrize('version', [True,0,-1,'1',1.5])
def test_strict_version(version):
    with pytest.raises(ValidationError):
        m.ProblemChange(action='review',expected_version=version,reason='Review')


def test_action_payload_is_explicit():
    m.ProblemChange(action='associate',expected_version=1,reason='Association',consultation_id=3)
    for kw in ({'action':'associate'}, {'action':'review','consultation_id':3}, {'action':'update'}, {'action':'associate','consultation_id':True}):
        with pytest.raises(ValidationError):
            m.ProblemChange(expected_version=1,reason='Review',**kw)
