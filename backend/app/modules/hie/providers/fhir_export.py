"""Staged local FHIR R4 export projections, ported from 4eca347.

No database access, network, implicit diagnoses or fabricated measurements.
The caller must establish scope, clinical authority and export approval.
"""
from datetime import date, datetime, timezone
import math
import re
from urllib.parse import urlsplit

FHIR_ID = re.compile(r'^[A-Za-z0-9\-.]{1,64}$')
LOINC = 'http://loinc.org'
UCUM = 'http://unitsofmeasure.org'


def _value(value):
    return getattr(value, 'value', value)


def _required_text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Required source text missing')
    return value.strip()


def _id(value):
    value = str(value) if type(value) is int else value
    if not isinstance(value, str) or not FHIR_ID.fullmatch(value):
        raise ValueError('Invalid source identifier')
    return value


def _isoformat(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        # Existing source model timestamps are written in UTC; SQLite and
        # legacy ward DateTime fields can omit the timezone on read.
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
                else value.astimezone(timezone.utc)).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise ValueError('Invalid source timestamp')


def _patient_reference(patient_id):
    return {'reference': 'Patient/' + _id(patient_id)}


def _coding(system, code, display=None):
    result = {'system': system, 'code': code}
    if display:
        result['display'] = display
    return {'coding': [result]}


def _resource(kind, record_id, patient_id=None, subject='subject'):
    result = {'resourceType': kind, 'id': _id(record_id)}
    if patient_id is not None:
        result[subject] = _patient_reference(patient_id)
    return result


def patient_to_fhir(patient, *, issuer):
    resource = _resource('Patient', patient.id)
    # Issuer is supplied by the trusted deployment, already tenant/clinic scoped.
    resource.update(identifier=[{'system': issuer + '/identifier/patient', 'value': _id(patient.id)}],
                    active=bool(patient.is_active), name=[{'family': _required_text(patient.last_name),
                    'given': [_required_text(patient.first_name)]}])
    gender = _value(patient.gender)
    resource['gender'] = gender if gender in ('male', 'female', 'other', 'unknown') else 'unknown'
    if patient.date_of_birth is not None:
        resource['birthDate'] = patient.date_of_birth.isoformat()
    if patient.patient_number:
        resource['identifier'].append({'system': issuer + '/identifier/patient-number',
                                       'value': _required_text(patient.patient_number)})
    # Minimum-necessary export: no phone, email, address, national IDs, or legacy notes.
    return resource


def admission_to_fhir_encounter(admission):
    resource = _resource('Encounter', admission.id, admission.patient_id)
    status = _value(admission.status)
    if status in ('discharged', 'transferred') and admission.discharged_at is not None:
        mapped = 'finished'
    elif status == 'admitted' and admission.discharged_at is None:
        mapped = 'in-progress'
    else:
        # Transferred without an end timestamp is ambiguous; do not infer discharge.
        mapped = 'unknown'
    resource.update(status=mapped, **{'class': {'system': 'http://terminology.hl7.org/CodeSystem/v3-ActCode',
                                                'code': 'IMP'}})
    start, end = _isoformat(admission.admitted_at), _isoformat(admission.discharged_at)
    if start is None:
        raise ValueError('Admission start missing')
    if end and datetime.fromisoformat(end) < datetime.fromisoformat(start):
        raise ValueError('Admission timestamps inconsistent')
    resource['period'] = {'start': start}
    if end:
        resource['period']['end'] = end
    return resource


def prescription_to_fhir_medication_request(item):
    parent = item.prescription
    if item.drug is None:
        raise ValueError('Medication identity missing')
    resource = _resource('MedicationRequest', item.id, parent.patient_id)
    # Expiry is not evidence of completion or cancellation.
    status = {'active': 'active', 'completed': 'completed', 'cancelled': 'cancelled',
              'expired': 'unknown'}.get(_value(parent.status), 'unknown')
    resource.update(status=status, intent='order',
                    medicationCodeableConcept={'text': _required_text(item.drug.name)})
    if parent.issued_at is not None:
        resource['authoredOn'] = _isoformat(parent.issued_at)
    # Preserve original authored dosing text; do not derive a new schedule.
    fields = [getattr(item, field, None) for field in ('dosage', 'frequency', 'duration', 'instructions')]
    text = '; '.join(_required_text(x) for x in fields if x is not None and str(x).strip())
    dosage = {'text': text} if text else {}
    if getattr(item, 'route_display', None):
        dosage['route'] = {'text': _required_text(item.route_display)}
    if getattr(item, 'dose_value', None) is not None and getattr(item, 'dose_unit', None):
        dosage['doseAndRate'] = [{'doseQuantity': {'value': _number(item.dose_value),
                                                 'unit': _required_text(item.dose_unit)}}]
    if dosage:
        resource['dosageInstruction'] = [dosage]
    return resource


def allergy_to_fhir(allergy):
    resource = _resource('AllergyIntolerance', allergy.id, allergy.patient_id, 'patient')
    clinical = _value(allergy.clinical_status)
    verification = _value(allergy.verification_status)
    if clinical not in ('active', 'inactive', 'resolved') or verification not in ('unconfirmed', 'confirmed', 'refuted'):
        raise ValueError('Unknown allergy status')
    resource.update(clinicalStatus=_coding('http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical', clinical),
                    verificationStatus=_coding('http://terminology.hl7.org/CodeSystem/allergyintolerance-verification', verification),
                    code={'text': _required_text(allergy.allergen_name)})
    # Source code has no coding-system field; never guess SNOMED or RxNorm.
    if allergy.allergen_code:
        resource['code']['coding'] = [{'code': _required_text(allergy.allergen_code)}]
    category = {'drug': 'medication', 'food': 'food', 'environmental': 'environment',
                'biologic': 'biologic'}.get(_value(allergy.allergen_type))
    if category:
        resource['category'] = [category]
    if allergy.reaction and allergy.reaction.strip():
        reaction = {'manifestation': [{'text': allergy.reaction.strip()}]}
        severity = _value(allergy.severity)
        if severity in ('mild', 'moderate', 'severe', 'life_threatening'):
            reaction['severity'] = 'severe' if severity == 'life_threatening' else severity
        resource['reaction'] = [reaction]
    if allergy.recorded_at is not None:
        resource['recordedDate'] = _isoformat(allergy.recorded_at)
    if getattr(allergy, 'onset_at', None) is not None:
        resource['onsetDateTime'] = _isoformat(allergy.onset_at)
    return resource


def _number(value):
    if isinstance(value, bool):
        raise ValueError('Invalid measurement')
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Invalid measurement')
    return number


def _quantity(value, unit, code):
    return {'value': _number(value), 'unit': unit, 'system': UCUM, 'code': code}


def _observation(vitals, code, display):
    resource = _resource('Observation', f'{vitals.id}-{code}', vitals.patient_id)
    resource.update(status='final', category=[_coding('http://terminology.hl7.org/CodeSystem/observation-category', 'vital-signs')],
                    code=_coding(LOINC, code, display), effectiveDateTime=_isoformat(vitals.recorded_at))
    if resource['effectiveDateTime'] is None:
        raise ValueError('Observation timestamp missing')
    return resource


def vitals_to_fhir_observation(vitals):
    resources = []
    for field, code, display, unit, ucum in (
        ('temperature_c', '8310-5', 'Body temperature', '°C', 'Cel'),
        ('heart_rate_bpm', '8867-4', 'Heart rate', 'beats/min', '/min'),
        ('respiratory_rate', '9279-1', 'Respiratory rate', 'breaths/min', '/min'),
        ('oxygen_saturation', '2708-6', 'Oxygen saturation', '%', '%'),
        ('weight_kg', '29463-7', 'Body weight', 'kg', 'kg'),
        ('height_cm', '8302-2', 'Body height', 'cm', 'cm'),
    ):
        value = getattr(vitals, field)
        if value is not None:
            obs = _observation(vitals, code, display)
            obs['valueQuantity'] = _quantity(value, unit, ucum)
            resources.append(obs)
    bp = (vitals.blood_pressure_systolic, vitals.blood_pressure_diastolic)
    if any(value is not None for value in bp):
        obs = _observation(vitals, '85354-9', 'Blood pressure panel')
        obs['component'] = []
        for value, code, display in zip(bp, ('8480-6', '8462-4'), ('Systolic blood pressure', 'Diastolic blood pressure')):
            component = {'code': _coding(LOINC, code, display)}
            if value is None:
                component['dataAbsentReason'] = _coding('http://terminology.hl7.org/CodeSystem/data-absent-reason', 'unknown')
            else:
                component['valueQuantity'] = _quantity(value, 'mmHg', 'mm[Hg]')
            obs['component'].append(component)
        resources.append(obs)
    return resources


def released_lab_to_fhir(report):
    """Only a validated current release snapshot may reach this mapper."""
    if report['release_status'] != 'released' or not report['results']:
        raise ValueError('Current released laboratory report required')
    order_id, patient_id = report['order_id'], report['patient_id']
    version = report.get('record_version', 1)
    diagnostic = _resource('DiagnosticReport', f'lab-{order_id}', patient_id)
    diagnostic.update(status='corrected' if version > 1 else 'final',
                      meta={'versionId': str(version)}, code={'text': 'Laboratory report'},
                      category=[_coding('http://terminology.hl7.org/CodeSystem/v2-0074', 'LAB')],
                      issued=report['released_at'], result=[])
    resources = [diagnostic]
    for i, row in enumerate(report['results'], 1):
        obs = _resource('Observation', f'lab-{order_id}-{i}', patient_id)
        obs.update(status='corrected' if version > 1 else 'final', meta={'versionId': str(version)},
                   category=[_coding('http://terminology.hl7.org/CodeSystem/observation-category', 'laboratory')],
                   code={'text': _required_text(row['test_name'])}, issued=report['released_at'],
                   valueString=_required_text(row['result_value']))
        if row['loinc_code']:
            obs['code']['coding'] = [{'system': LOINC, 'code': row['loinc_code']}]
        if row['unit']:
            # Preserve the source unit without inventing a UCUM conversion.
            obs['valueString'] += ' ' + row['unit']
        if row['reference_range']:
            obs['referenceRange'] = [{'text': row['reference_range']}]
        obs['interpretation'] = [_coding('http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation',
                                       {'normal': 'N', 'abnormal': 'A', 'critical': 'AA'}[row['flag']])]
        diagnostic['result'].append({'reference': 'Observation/' + obs['id']})
        resources.append(obs)
    return resources


def build_fhir_collection_bundle(resources, *, issuer, bundle_id=None, timestamp=None):
    parts = urlsplit(issuer)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('Invalid trusted issuer')
    if len(resources) > 1000:
        raise ValueError('Export exceeds staged resource limit')
    entries, seen = [], set()
    for resource in resources:
        path = resource['resourceType'] + '/' + _id(resource['id'])
        if path in seen:
            raise ValueError('Duplicate resource identity')
        seen.add(path)
        entries.append({'fullUrl': issuer.rstrip('/') + '/' + path, 'resource': resource})
    result = {'resourceType': 'Bundle', 'type': 'collection', 'entry': entries}
    if bundle_id:
        result['id'] = _id(bundle_id)
    if timestamp:
        result['timestamp'] = timestamp
    # R4 bdl-1: total is forbidden on collection bundles.
    return result
