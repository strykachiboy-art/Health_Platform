import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/hie/data/hie_remote_data_source.dart';
import 'package:clinic_system_pro/features/hie/presentation/hie_fhir_workspace_screen.dart';

AuthSessionState _ready({String role = 'admin', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'hie-ui-token',
        refreshToken: 'hie-ui-refresh',
        userId: 7,
        role: role,
        clinicContextId: clinic,
      ),
    );

HieRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'admin',
}) => HieRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => _ready(role: role),
  sessionNavigationGuard: guard,
);

void main() {
  testWidgets('doctor sees query & integrations tabs but not admin export tab', (
    tester,
  ) async {
    final client = MockClient((req) async => http.Response('{}', 200));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(
      MaterialApp(
        home: HieFhirWorkspaceScreen(
          dataSource: _source(client, guard, role: 'doctor'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('hie-workspace')), findsOneWidget);
    expect(find.text('FHIR Query'), findsOneWidget);
    expect(find.text('Integrations'), findsOneWidget);
    expect(find.text('FHIR Export'), findsNothing);
  });

  testWidgets('admin can query external FHIR and export FHIR R4 bundle', (
    tester,
  ) async {
    final client = MockClient((req) async {
      if (req.url.path == '/api/v1/hie/queries/patient') {
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              'status_code': 200,
              'external_reference': 'EXT-PAT-99',
              'records': [
                {'resourceType': 'Patient', 'id': 'EXT-PAT-99'},
              ],
            },
          }),
          200,
        );
      }
      if (req.url.path == '/api/v1/hie/fhir/export/patient/12') {
        return http.Response(
          jsonEncode({
            'resourceType': 'Bundle',
            'type': 'collection',
            'total': 1,
            'entry': [
              {
                'resource': {'resourceType': 'Patient', 'id': '12'},
              },
            ],
          }),
          200,
        );
      }
      return http.Response('{}', 404);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(
      MaterialApp(
        home: HieFhirWorkspaceScreen(
          dataSource: _source(client, guard, role: 'admin'),
          initialPatientId: 12,
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('hie-run-query-button')));
    await tester.pumpAndSettle();

    expect(find.text('Patient / EXT-PAT-99'), findsOneWidget);

    await tester.tap(find.text('FHIR Export'));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('hie-run-export-button')));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('hie-export-summary-card')), findsOneWidget);
    expect(find.text('Bundle (collection) — 1 resource(s)'), findsOneWidget);
    expect(find.text('• Patient (12)'), findsOneWidget);
  });

  testWidgets('admin can create and update fhir-r4 integration', (tester) async {
    final client = MockClient((req) async {
      if (req.method == 'POST' && req.url.path == '/api/v1/hie/integrations') {
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              'id': 5,
              'clinic_id': 3,
              'provider': 'fhir-r4',
              'status': 'pending',
              'endpoint_url': 'https://fhir.partnerhealth.org/fhir/r4',
              'organization_id': null,
              'facility_id': null,
              'last_sync_at': null,
              'created_at': '2026-11-25T10:00:00+00:00',
              'updated_at': '2026-11-25T10:00:00+00:00',
            },
          }),
          201,
        );
      }
      if (req.method == 'PATCH' && req.url.path == '/api/v1/hie/integrations/5') {
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              'id': 5,
              'clinic_id': 3,
              'provider': 'fhir-r4',
              'status': 'active',
              'endpoint_url': 'https://fhir.partnerhealth.org/fhir/r4',
              'organization_id': null,
              'facility_id': null,
              'last_sync_at': null,
              'created_at': '2026-11-25T10:00:00+00:00',
              'updated_at': '2026-11-25T10:01:00+00:00',
            },
          }),
          200,
        );
      }
      return http.Response('{}', 404);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(
      MaterialApp(
        home: HieFhirWorkspaceScreen(
          dataSource: _source(client, guard, role: 'admin'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('Integrations'));
    await tester.pumpAndSettle();

    await tester.enterText(
      find.byKey(const ValueKey('hie-endpoint-url-field')),
      'https://fhir.partnerhealth.org/fhir/r4',
    );
    await tester.tap(find.byKey(const ValueKey('hie-create-integration-button')));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('hie-integration-detail-card')),
      findsOneWidget,
    );
    expect(find.text('Integration #5 — fhir-r4'), findsOneWidget);
  });

  testWidgets('session guard invalidation clears loaded FHIR records', (
    tester,
  ) async {
    final client = MockClient((req) async {
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {
            'status_code': 200,
            'external_reference': 'SECRET-PHI-1',
            'records': [
              {'resourceType': 'Patient', 'id': 'SECRET-PHI-1'},
            ],
          },
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(
      MaterialApp(
        home: HieFhirWorkspaceScreen(
          dataSource: _source(client, guard, role: 'doctor'),
          initialPatientId: 9,
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('hie-run-query-button')));
    await tester.pumpAndSettle();
    expect(find.text('Patient / SECRET-PHI-1'), findsOneWidget);

    guard.invalidate();
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('hie-unauthorized')), findsOneWidget);
    expect(find.text('Patient / SECRET-PHI-1'), findsNothing);
  });
}
