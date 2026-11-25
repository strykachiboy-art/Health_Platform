"""FHIR-R02 synthetic contract tests. No external network or real patient data."""
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock
import json
import pytest

from app.modules.hie.providers.fhir_export import (
    patient_to_fhir, admission_to_fhir_encounter, prescription_to_fhir_medication_request,
    allergy_to_fhir, vitals_to_fhir_observation, released_lab_to_fhir,
    build_fhir_collection_bundle,
)
from app.modules.hie.providers.fhir_r4_provider import FHIRR4Provider
from app.modules.hie.providers.exceptions import HIEFailClosedError, HIERetryableError, HIEProviderHTTPError
from app.modules.hie.providers.transport import HIEHTTPResponse, HIERedirectRejectedError, HIETransportError

NOW = datetime(2026, 1, 2, 10, 30, tzinfo=timezone.utc)
ISSUER = 'https://synthetic.example.invalid/fhir/tenant/clinic'
ENDPOINT = 'https://fhir.partnerhealth.org/fhir/r4'
HOSTS = ('fhir.partnerhealth.org',)
IDENTIFIER = 'https://issuer.example.org/mrn|MRN-1'


def patient(**kw):
    return NS(**(dict(id=1, first_name='Synthetic', last_name='Patient', patient_number='MRN-1',
        is_active=True, gender='female', date_of_birth=date(1980, 1, 1),
        phone='SECRET', email='SECRET', emirates_id='SECRET', chronic_conditions='SECRET') | kw))


def vitals(**kw):
    return NS(**(dict(id=5, patient_id=1, recorded_at=NOW, temperature_c=37.2,
        heart_rate_bpm=80, respiratory_rate=18, oxygen_saturation=98,
        weight_kg=72, height_cm=175, blood_pressure_systolic=120, blood_pressure_diastolic=80) | kw))


def allergy(**kw):
    return NS(**(dict(id=2, patient_id=1, allergen_name='Synthetic allergen', allergen_code='LOCAL-1',
        allergen_type='drug', clinical_status='active', verification_status='unconfirmed',
        reaction='Recorded rash', severity='severe', recorded_at=NOW, onset_at=None) | kw))


def test_patient_minimum_necessary_and_issuer():
    p = patient_to_fhir(patient(), issuer=ISSUER)
    assert p['resourceType'] == 'Patient' and p['id'] == '1'
    assert p['identifier'][0]['system'] == ISSUER + '/identifier/patient'
    assert p['birthDate'] == '1980-01-01' and p['gender'] == 'female'
    assert 'SECRET' not in json.dumps(p)
    assert 'telecom' not in p and 'address' not in p
    assert patient_to_fhir(patient(gender=None), issuer=ISSUER)['gender'] == 'unknown'


@pytest.mark.parametrize('status,end,expected', [('admitted', None, 'in-progress'), ('discharged', NOW, 'finished'), ('transferred', None, 'unknown')])
def test_encounter_status_source(status, end, expected):
    row = NS(id=2, patient_id=1, admitted_at=NOW, discharged_at=end, status=status)
    out = admission_to_fhir_encounter(row)
    assert out['status'] == expected and out['subject']['reference'] == 'Patient/1'
    assert out['class']['code'] == 'IMP'


@pytest.mark.parametrize('state,expected', [('active','active'),('completed','completed'),('cancelled','cancelled'),('expired','unknown')])
def test_medication_status_and_identity(state, expected):
    row = NS(id=3, prescription=NS(patient_id=1, status=state, issued_at=NOW), drug=NS(name='Synthetic drug'), dosage='5 mg', frequency='Daily')
    r = prescription_to_fhir_medication_request(row)
    assert r['status'] == expected and r['intent'] == 'order'
    assert r['subject']['reference'] == 'Patient/1' and r['medicationCodeableConcept']['text'] == 'Synthetic drug'
    assert r['dosageInstruction'][0]['text'] == '5 mg; Daily'
    row.drug = None
    with pytest.raises(ValueError):
        prescription_to_fhir_medication_request(row)


def test_allergy_actual_manifestation_and_no_fabricated_code_system():
    r = allergy_to_fhir(allergy())
    assert r['reaction'] == [{'manifestation': [{'text':'Recorded rash'}], 'severity':'severe'}]
    assert r['category'] == ['medication'] and 'type' not in r
    assert r['verificationStatus']['coding'][0]['code'] == 'unconfirmed'
    assert r['code']['coding'] == [{'code':'LOCAL-1'}]
    assert 'reaction' not in allergy_to_fhir(allergy(reaction=None))


def test_vitals_bp_components_and_real_time():
    rows = vitals_to_fhir_observation(vitals(blood_pressure_diastolic=None))
    assert len(rows) == 7
    bp = next(r for r in rows if r['code']['coding'][0]['code'] == '85354-9')
    assert bp['component'][0]['valueQuantity']['code'] == 'mm[Hg]'
    assert bp['component'][0]['valueQuantity']['value'] == 120
    assert bp['component'][1]['dataAbsentReason']['coding'][0]['code'] == 'unknown'
    assert 'valueQuantity' not in bp['component'][1]
    assert all(r['effectiveDateTime'] == NOW.isoformat() for r in rows)
    assert all(r['subject']['reference'] == 'Patient/1' for r in rows)
    assert {r['code']['coding'][0]['code'] for r in rows} >= {'29463-7','8302-2'}


def test_vitals_empty_and_nonfinite_denied():
    fields = dict(temperature_c=None, heart_rate_bpm=None, respiratory_rate=None,
        oxygen_saturation=None, weight_kg=None, height_cm=None, blood_pressure_systolic=None,
        blood_pressure_diastolic=None)
    assert vitals_to_fhir_observation(vitals(**fields)) == []
    with pytest.raises(ValueError):
        vitals_to_fhir_observation(vitals(temperature_c=float('nan')))


def report(**kw):
    return dict(order_id=4, patient_id=1, release_status='released', released_at=NOW.isoformat(),
        results=[dict(test_name='Synthetic lab', loinc_code='2345-7', unit='mg/dL', reference_range='1-10',
                      result_value='5', flag='normal', resulted_at=NOW.isoformat())]) | kw


def test_corrected_release_version_and_reference():
    diagnostic, obs = released_lab_to_fhir(report(record_version=2))
    assert diagnostic['status'] == obs['status'] == 'corrected'
    assert diagnostic['meta']['versionId'] == '2'
    assert diagnostic['result'] == [{'reference': 'Observation/' + obs['id']}]
    assert obs['valueString'] == '5 mg/dL'
    assert obs['interpretation'][0]['coding'][0]['code'] == 'N'
    with pytest.raises(ValueError):
        released_lab_to_fhir(report(release_status='withdrawn'))


def test_collection_bdl1_and_fullurl_unique():
    p = patient_to_fhir(patient(), issuer=ISSUER)
    b = build_fhir_collection_bundle([p, *vitals_to_fhir_observation(vitals())], issuer=ISSUER)
    assert b['type'] == 'collection' and 'total' not in b
    assert b['entry'][0]['fullUrl'] == ISSUER + '/Patient/1'
    assert len({x['fullUrl'] for x in b['entry']}) == len(b['entry'])
    with pytest.raises(ValueError):
        build_fhir_collection_bundle([p,p], issuer=ISSUER)
    with pytest.raises(ValueError):
        build_fhir_collection_bundle([p], issuer='http://unsafe.example.org')


def capability(*kinds, version='4.0.1'):
    return dict(resourceType='CapabilityStatement', fhirVersion=version, rest=[dict(mode='server', resource=[
        dict(type=k, interaction=[{'code':'search-type'}]) for k in kinds])])


def external_patient(pid='p1', value='MRN-1'):
    return dict(resourceType='Patient', id=pid, identifier=[dict(system='https://issuer.example.org/mrn', value=value)])


def bundle(*rows, **extra):
    return dict(resourceType='Bundle', type='searchset', total=len(rows), entry=[dict(resource=x) for x in rows]) | extra


def response(body, status=200):
    return HIEHTTPResponse(status_code=status, headers=(), body=json.dumps(body).encode(), connected_ip='203.0.113.1')


def provider(*bodies, token='synthetic-token'):
    transport = Mock()
    transport.request.side_effect = [x if isinstance(x, (Exception,HIEHTTPResponse)) else response(x) for x in bodies]
    return FHIRR4Provider(ENDPOINT, allowed_hosts=HOSTS, transport=transport, bearer_token=token), transport


def test_patient_discovery_and_exact_issuer():
    p,t = provider(capability('Patient'), bundle(external_patient()))
    result = p.query_patient(IDENTIFIER)
    assert result['external_reference'] == 'p1'
    assert t.request.call_args_list[0].kwargs['path'] == 'metadata'
    assert t.request.call_args_list[1].kwargs['params']['identifier'] == IDENTIFIER
    assert t.request.call_args_list[1].kwargs['headers']['Authorization'] == 'Bearer synthetic-token'


@pytest.mark.parametrize('rows', [(external_patient(), external_patient('p2')), (external_patient(value='WRONG'),), (dict(resourceType='Patient',id='p1'),)])
def test_patient_mismatch_ambiguity_denied(rows):
    p,_ = provider(capability('Patient'), bundle(*rows))
    with pytest.raises(HIEFailClosedError):
        p.query_patient(IDENTIFIER)


def test_empty_patient_search_is_not_a_match():
    p,_ = provider(capability('Patient'), bundle())
    assert p.query_patient(IDENTIFIER)['external_reference'] is None


@pytest.mark.parametrize('value', ['unqualified', 'https://issuer.example.org/mrn|', 'https://issuer.example.org/mrn|a,b', 'https://issuer.example.org/mrn|a|b'])
def test_unqualified_or_ambiguous_identifier_no_network(value):
    p,t = provider()
    with pytest.raises(HIEFailClosedError):
        p.query_patient(value)
    t.request.assert_not_called()


def test_scope_filters_and_resource_type_fail_closed():
    p,t = provider()
    for filters in ({'resource_type':'Invoice'},{'resource_type':'Observation','params':{'subject':'Patient/wrong'}},
                    {'resource_type':'Observation','params':{'_include':'*'}}, {'params':{'_count':101}}):
        with pytest.raises(HIEFailClosedError):
            p.query_clinical_data(IDENTIFIER, filters)
    t.request.assert_not_called()


@pytest.mark.parametrize('kind,field', [('Encounter','subject'),('Condition','subject'),('MedicationRequest','subject'),('Observation','subject'),('DiagnosticReport','subject'),('AllergyIntolerance','patient')])
def test_clinical_query_matches_patient_reference(kind,field):
    row = dict(resourceType=kind,id='r1', **{field:{'reference':'Patient/p1'}})
    p,t = provider(capability('Patient',kind), bundle(external_patient()), bundle(row))
    result=p.query_clinical_data(IDENTIFIER, {'resource_type':kind})
    assert result['records']==[row] and result['external_reference']=='p1'
    assert t.request.call_args.kwargs['params']['patient']=='Patient/p1'


@pytest.mark.parametrize('ref', ['Patient/p2','https://other.example.org/fhir/Patient/p1',None])
def test_clinical_foreign_patient_response_denied(ref):
    row = dict(resourceType='Observation',id='r1',subject={'reference':ref})
    p,_=provider(capability('Patient','Observation'),bundle(external_patient()),bundle(row))
    with pytest.raises(HIEFailClosedError):
        p.query_clinical_data(IDENTIFIER, {'resource_type':'Observation'})


@pytest.mark.parametrize('extra', [{'link':[{'relation':'next','url':'https://other.example.org'}]}, {'total':2}])
def test_incomplete_page_never_returned_as_complete(extra):
    p,t=provider(capability('Patient'), bundle(external_patient(), **extra))
    with pytest.raises(HIEFailClosedError):
        p.query_patient(IDENTIFIER)
    assert t.request.call_count==2


def test_unapproved_transport_and_no_credential():
    with pytest.raises(HIEFailClosedError):
        FHIRR4Provider(ENDPOINT, allowed_hosts=('different.example.org',))
    p,t=provider(token=None)
    with pytest.raises(HIEFailClosedError):
        p.query_patient(IDENTIFIER)
    t.request.assert_not_called()


@pytest.mark.parametrize('error,cls', [(HIERedirectRejectedError('redirect'),HIEFailClosedError),
    (HIETransportError('timeout'),HIERetryableError),(response({},503),HIERetryableError),
    (response({},401),HIEProviderHTTPError),(capability('Patient',version='5.0.0'),HIEFailClosedError)])
def test_provider_error_paths(error,cls):
    p,_=provider(error)
    with pytest.raises(cls):
        p.query_patient(IDENTIFIER)


def test_all_external_writes_rejected():
    p,t=provider()
    for method in (p.submit_patient,p.submit_clinical_data,p.submit_clinical_document):
        with pytest.raises(HIEFailClosedError):
            method({})
    t.request.assert_not_called()
