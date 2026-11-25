"""Local, bounded FHIR R4 Condition projection; no import or outbound transport."""
from app.modules.hie.providers.fhir_contract import validate_fhir_resource
from .contracts import ProblemContent

CLINICAL_SYSTEM = 'http://terminology.hl7.org/CodeSystem/condition-clinical'
VERIFICATION_SYSTEM = 'http://terminology.hl7.org/CodeSystem/condition-ver-status'
CATEGORY_SYSTEM = 'http://terminology.hl7.org/CodeSystem/condition-category'


def local_condition(snapshot: dict) -> dict:
    item = ProblemContent.model_validate(snapshot['content'])
    result = {
        'resourceType': 'Condition',
        'id': str(snapshot['id']),
        'meta': {'versionId': str(snapshot['version'])},
        'verificationStatus': {'coding': [{'system': VERIFICATION_SYSTEM, 'code': item.verification_status.replace('_', '-')}]},
        'code': {'text': item.name},
        'subject': {'reference': f"Patient/{snapshot['patient_id']}"},
        'recordedDate': snapshot['recorded_at'],
    }
    if item.verification_status != 'entered_in_error':
        result['clinicalStatus'] = {'coding': [{'system': CLINICAL_SYSTEM, 'code': item.clinical_status}]}
        result['category'] = [{'coding': [{'system': CATEGORY_SYSTEM, 'code': 'problem-list-item'}]}]
    if item.code:
        result['code']['coding'] = [{'system': item.coding_system, 'code': item.code}]
    if item.onset_date:
        result['onsetDateTime'] = item.onset_date.isoformat()
    elif item.onset_description:
        result['onsetString'] = item.onset_description
    if item.resolution_date and item.verification_status != 'entered_in_error':
        result['abatementDateTime'] = item.resolution_date.isoformat()
    # No external patient/encounter/Practitioner mapping is inferred.
    # Private clinical notes are deliberately omitted from this projection.
    return validate_fhir_resource(result, 'Condition')
