import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/hie/data/hie_remote_data_source.dart';

AuthSessionState _ready({
  String role = 'admin',
  int clinic = 3,
  String token = 'hie-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'hie-refresh',
    userId: 42,
    role: role,
    clinicContextId: clinic,
  ),
);

Map<String, dynamic> _integrationJson({
  int id = 11,
  int clinicId = 3,
  String provider = 'fhir-r4',
  String status = 'active',
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinicId,
  'provider': provider,
  'status': status,
  'endpoint_url': 'https://fhir.partnerhealth.org/fhir/r4',
  'organization_id': 'ORG-1',
  'facility_id': 'FAC-1',
  'last_sync_at': null,
  'created_at': '2026-11-25T10:00:00+00:00',
  'updated_at': '2026-11-25T10:00:00+00:00',
};

Map<String, dynamic> _submissionJson({
  int id = 101,
  int integrationId = 11,
  int clinicId = 3,
}) => <String, dynamic>{
  'id': id,
  'integration_id': integrationId,
  'clinic_id': clinicId,
  'patient_id': null,
  'operation': 'patient_query',
  'status': 'success',
  'failure_class': null,
  'external_reference': 'P-42',
  'request_data': <String, dynamic>{'patient_identifier': 'P-42'},
  'response_data': <String, dynamic>{'status_code': 200},
  'status_code': 200,
  'error_message': null,
  'retry_count': 0,
  'submitted_at': '2026-11-25T10:05:00+00:00',
  'created_at': '2026-11-25T10:05:00+00:00',
  'updated_at': '2026-11-25T10:05:00+00:00',
};

HieRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? session,
}) => HieRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: session ?? () async => _ready(),
  sessionNavigationGuard: guard,
);

void main() {
  test('createIntegration sends exact payload and parses envelope', () async {
    final client = MockClient((req) async {
      expect(req.method, 'POST');
      expect(req.url.path, '/api/v1/hie/integrations');
      expect(req.headers['Authorization'], 'Bearer hie-token');
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      expect(body, <String, dynamic>{
        'provider': 'fhir-r4',
        'endpoint_url': 'https://fhir.partnerhealth.org/fhir/r4',
        'organization_id': 'ORG-1',
        'facility_id': 'FAC-1',
      });
      return http.Response(
        jsonEncode({'success': true, 'data': _integrationJson(status: 'pending')}),
        201,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final result = await _source(client, guard).createIntegration(
      provider: '  FHIR-R4 ',
      endpointUrl: 'https://fhir.partnerhealth.org/fhir/r4',
      organizationId: 'ORG-1',
      facilityId: 'FAC-1',
    );
    expect(result.id, 11);
    expect(result.provider, 'fhir-r4');
    expect(result.status, 'pending');
  });

  test('non-admin role cannot create, update, or export FHIR bundles', () async {
    final client = MockClient((req) async {
      throw StateError('Unexpected HTTP call: ${req.url}');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(
      client,
      guard,
      session: () async => _ready(role: 'doctor'),
    );

    await expectLater(
      source.createIntegration(provider: 'fhir-r4'),
      throwsA(isA<HieSessionException>()),
    );
    await expectLater(
      source.updateIntegration(integrationId: 11, status: 'active'),
      throwsA(isA<HieSessionException>()),
    );
    await expectLater(
      source.exportPatientFhirBundle(patientId: 7),
      throwsA(isA<HieSessionException>()),
    );
  });

  test('queryPatient sends exact endpoint, purpose, and bearer', () async {
    final client = MockClient((req) async {
      expect(req.method, 'POST');
      expect(req.url.path, '/api/v1/hie/queries/patient');
      expect(req.headers['Authorization'], 'Bearer hie-token');
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      expect(body, <String, dynamic>{
        'patient_identifier': 'MRN-100',
        'purpose_of_use': 'treatment',
        'integration_id': 11,
      });
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {
            'status_code': 200,
            'external_reference': 'FHIR-PAT-1',
            'records': [
              {'resourceType': 'Patient', 'id': 'FHIR-PAT-1'},
            ],
          },
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final result = await _source(
      client,
      guard,
      session: () async => _ready(role: 'doctor'),
    ).queryPatient(patientIdentifier: 'MRN-100', integrationId: 11);

    expect(result.statusCode, 200);
    expect(result.externalReference, 'FHIR-PAT-1');
    expect(result.records.single['resourceType'], 'Patient');
  });

  test('queryClinicalData sends resource_type filter and parses records', () async {
    final client = MockClient((req) async {
      expect(req.method, 'POST');
      expect(req.url.path, '/api/v1/hie/queries/clinical-data');
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      expect(body['filters'], {
        'resource_type': 'Observation',
        'params': {'_count': '5'},
      });
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {
            'status_code': 200,
            'external_reference': 'OBS-1',
            'records': [
              {'resourceType': 'Observation', 'id': 'OBS-1'},
            ],
          },
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final result = await _source(
      client,
      guard,
      session: () async => _ready(role: 'nurse'),
    ).queryClinicalData(
      patientIdentifier: 'MRN-100',
      resourceType: 'Observation',
      searchParams: const {'_count': '5'},
    );

    expect(result.records.single['resourceType'], 'Observation');
  });

  test('fetchSubmissions passes query params and enforces clinic match', () async {
    final client = MockClient((req) async {
      expect(req.method, 'GET');
      expect(req.url.path, '/api/v1/hie/submissions');
      expect(req.url.queryParameters, {
        'integration_id': '11',
        'page': '1',
        'per_page': '20',
      });
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {
            'items': [_submissionJson()],
            'total': 1,
            'page': 1,
            'per_page': 20,
          },
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final page = await _source(
      client,
      guard,
      session: () async => _ready(role: 'lab_technician'),
    ).fetchSubmissions(integrationId: 11);

    expect(page.total, 1);
    expect(page.items.single.operation, 'patient_query');
  });

  test('exportPatientFhirBundle parses full and scoped collection bundles', () async {
    final paths = <String>[];
    final client = MockClient((req) async {
      paths.add(req.url.path);
      return http.Response(
        jsonEncode({
          'resourceType': 'Bundle',
          'type': 'collection',
          'id': 'patient-7-bundle',
          'total': 2,
          'timestamp': '2026-11-25T10:15:00+00:00',
          'entry': [
            {
              'resource': {'resourceType': 'Patient', 'id': '7'},
            },
            {
              'resource': {'resourceType': 'Observation', 'id': '40-8310-5'},
            },
          ],
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);

    final full = await source.exportPatientFhirBundle(patientId: 7);
    final obs = await source.exportPatientFhirBundle(
      patientId: 7,
      section: 'observations',
    );

    expect(paths, [
      '/api/v1/hie/fhir/export/patient/7',
      '/api/v1/hie/fhir/export/patient/7/observations',
    ]);
    expect(full.type, 'collection');
    expect(full.total, 2);
    expect(full.resources.first['resourceType'], 'Patient');
    expect(obs.resources.last['resourceType'], 'Observation');
  });

  test('rejects mismatched clinic response and non-collection bundle', () async {
    final client = MockClient((req) async {
      if (req.url.path.contains('/integrations/')) {
        return http.Response(
          jsonEncode({
            'success': true,
            'data': _integrationJson(clinicId: 999),
          }),
          200,
        );
      }
      return http.Response(
        jsonEncode({
          'resourceType': 'Bundle',
          'type': 'searchset',
          'entry': [],
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);

    await expectLater(
      source.fetchIntegration(integrationId: 11),
      throwsA(isA<FormatException>()),
    );
    await expectLater(
      source.exportPatientFhirBundle(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('session change mid-flight throws HieSessionException', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final client = MockClient((req) async {
      guard.invalidate();
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {'status_code': 200, 'records': []},
        }),
        200,
      );
    });
    addTearDown(client.close);

    await expectLater(
      _source(client, guard).queryPatient(patientIdentifier: 'MRN-1'),
      throwsA(isA<HieSessionException>()),
    );
  });
}
