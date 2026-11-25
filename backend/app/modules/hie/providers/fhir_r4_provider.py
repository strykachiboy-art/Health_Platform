"""Staged concrete read-only provider ported from 4eca347.

Uses the existing pinned HTTPS transport. No implicit credential provisioning,
no patient matching by first row, and no silent partial-page results.
"""
import json
import re
from urllib.parse import urlsplit

from .endpoint_policy import validate_hie_endpoint_url
from .exceptions import HIEFailClosedError, HIEProviderHTTPError, HIERetryableError
from .fhir_contract import (FHIR_READ_ONLY_RESOURCE_TYPES, FHIRContractError,
                           validate_capability_statement, advertised_read_only_interactions,
                           validate_search_bundle)
from .registry import register_provider
from .transport import HIEHTTPTransport, HIERedirectRejectedError, HIETransportError

PROVIDER_NAME = 'fhir-r4'
_ID = re.compile(r'^[A-Za-z0-9\-.]{1,64}$')


def _fail(message):
    raise HIEFailClosedError(message)


def _identifier(value):
    if not isinstance(value, str) or len(value) > 300 or value.count('|') != 1 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        _fail('Issuer-qualified patient identifier required')
    system, identifier = value.split('|')
    try:
        parts = urlsplit(system)
    except ValueError:
        _fail('Invalid patient identifier issuer')
    if (parts.scheme not in ('http', 'https', 'urn')
            or (parts.scheme in ('http', 'https') and not parts.hostname)
            or (parts.scheme == 'urn' and not parts.path)
            or not identifier or any(c in system + identifier for c in ',\\$')
            or identifier != identifier.strip() or any(c.isspace() for c in system)):
        _fail('Issuer-qualified patient identifier required')
    return system, identifier


class FHIRR4Provider:
    def __init__(self, endpoint_url, *, allowed_hosts, transport=None, bearer_token=None):
        if isinstance(allowed_hosts, str):
            allowed_hosts = tuple(x.strip() for x in allowed_hosts.split(',') if x.strip())
        try:
            hosts = tuple(allowed_hosts)
            self._endpoint_url = validate_hie_endpoint_url(endpoint_url, allowed_hosts=hosts).rstrip('/')
        except (ValueError, TypeError, AttributeError) as exc:
            raise HIEFailClosedError('Endpoint outside trusted HIE policy') from exc
        self._transport = transport if transport is not None else HIEHTTPTransport(allowed_hosts=hosts)
        self._bearer_token = bearer_token

    def _request(self, path, params=None):
        if not isinstance(self._bearer_token, str) or not self._bearer_token.strip() or any(c.isspace() for c in self._bearer_token):
            _fail('Approved partner credential required')
        try:
            response = self._transport.request(endpoint_url=self._endpoint_url, path=path,
                params=params, headers={'Authorization': 'Bearer ' + self._bearer_token})
        except HIERedirectRejectedError as exc:
            raise HIEFailClosedError('FHIR redirect rejected') from exc
        except HIETransportError as exc:
            raise HIERetryableError('FHIR transport failure') from exc
        except (ValueError, TypeError) as exc:
            raise HIEFailClosedError('FHIR transport policy rejected request') from exc
        status = response.status_code
        if status in (408, 425, 429) or status >= 500:
            raise HIERetryableError('FHIR server returned retryable response')
        if not 200 <= status < 300:
            raise HIEProviderHTTPError(status)
        try:
            return json.loads(response.body.decode('utf-8'))
        except (UnicodeDecodeError, ValueError) as exc:
            raise HIEFailClosedError('Invalid FHIR JSON') from exc

    def _discover(self, kinds):
        try:
            capability = self._request('metadata')
            validate_capability_statement(capability)
            for kind in kinds:
                if 'search-type' not in advertised_read_only_interactions(capability, kind):
                    _fail('FHIR server does not advertise required search')
        except FHIRContractError as exc:
            raise HIEFailClosedError('FHIR capability contract rejected') from exc

    def _search(self, kind, params):
        try:
            bundle = validate_search_bundle(self._request(kind, params), kind)
        except FHIRContractError as exc:
            raise HIEFailClosedError('FHIR search contract rejected') from exc
        links = bundle.get('link', [])
        if not isinstance(links, list) or any(not isinstance(x, dict) for x in links):
            _fail('Malformed FHIR pagination metadata')
        # Do not follow unapproved opaque next URLs or return a partial chart as complete.
        if any(x.get('relation') == 'next' for x in links):
            _fail('FHIR search continuation requires approved pagination contract')
        entries = bundle.get('entry', [])
        if len(entries) > 100 or ('total' in bundle and bundle['total'] != len(entries)):
            _fail('Incomplete or oversized FHIR search result')
        rows = [x['resource'] for x in entries]
        ids = [x.get('id') for x in rows]
        if any(not isinstance(i, str) or not _ID.fullmatch(i) for i in ids) or len(set(ids)) != len(ids):
            _fail('Invalid or duplicate FHIR resource identity')
        return rows

    def _match(self, patient_identifier):
        system, identifier = _identifier(patient_identifier)
        rows = self._search('Patient', {'identifier': system + '|' + identifier, '_count': '2'})
        if len(rows) > 1:
            _fail('Ambiguous external patient match')
        if rows:
            identifiers = rows[0].get('identifier', [])
            if not isinstance(identifiers, list) or not any(isinstance(x, dict) and x.get('system') == system and x.get('value') == identifier for x in identifiers):
                _fail('External patient identity mismatch')
        return rows

    def query_patient(self, patient_identifier):
        _identifier(patient_identifier)
        self._discover(('Patient',))
        rows = self._match(patient_identifier)
        return {'status_code': 200, 'external_reference': rows[0]['id'] if rows else None, 'records': rows}

    def query_clinical_data(self, patient_identifier, filters=None):
        _identifier(patient_identifier)
        filters = {} if filters is None else filters
        if not isinstance(filters, dict) or set(filters) - {'resource_type', 'params'}:
            _fail('Unsupported FHIR search filter')
        kind = filters.get('resource_type', 'Patient')
        if not isinstance(kind, str) or kind not in FHIR_READ_ONLY_RESOURCE_TYPES:
            _fail('Unsupported FHIR resource')
        params = filters.get('params', {})
        if not isinstance(params, dict) or set(params) - {'_count'}:
            _fail('Unsupported FHIR search parameters')
        count = params.get('_count', '100')
        if isinstance(count, bool) or not str(count).isdigit() or not 1 <= int(count) <= 100:
            _fail('Invalid FHIR page size')
        if kind == 'Patient':
            return self.query_patient(patient_identifier)
        self._discover(('Patient', kind))
        patients = self._match(patient_identifier)
        if not patients:
            return {'status_code': 200, 'external_reference': None, 'records': []}
        pid = patients[0]['id']
        reference = 'Patient/' + pid
        rows = self._search(kind, {'patient': reference, '_count': str(count)})
        field = 'patient' if kind == 'AllergyIntolerance' else 'subject'
        for row in rows:
            ref = row.get(field)
            if not isinstance(ref, dict) or ref.get('reference') not in (reference, self._endpoint_url + '/' + reference):
                _fail('FHIR clinical resource patient mismatch')
        return {'status_code': 200, 'external_reference': pid, 'records': rows}

    def submit_patient(self, payload):
        _fail('FHIR provider is read-only')

    def submit_clinical_data(self, payload):
        _fail('FHIR provider is read-only')

    def submit_clinical_document(self, payload):
        _fail('FHIR provider is read-only')


def _fhir_r4_factory(endpoint):
    from flask import current_app, has_app_context
    from app.config import Config
    hosts = current_app.config.get('HIE_ALLOWED_ENDPOINT_HOSTS', ()) if has_app_context() else Config.HIE_ALLOWED_ENDPOINT_HOSTS
    # The existing service has no approved credential resolver. Never source a
    # token from user filters or integration metadata; the factory stays held.
    return FHIRR4Provider(endpoint, allowed_hosts=hosts)


def register_fhir_r4_provider():
    register_provider(PROVIDER_NAME, _fhir_r4_factory)
