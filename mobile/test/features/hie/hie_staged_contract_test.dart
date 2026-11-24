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
import 'package:clinic_system_pro/features/hie/presentation/hie_workspace_screen.dart';

AuthSessionState session(String role, {int clinic = 4}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'test-access',
        refreshToken: 'test-refresh',
        userId: 8,
        role: role,
        clinicContextId: clinic,
      ),
    );
Map<String, dynamic> integration({
  int clinic = 4,
  String status = 'pending',
  bool readiness = true,
}) => {
  'id': 9,
  'clinic_id': clinic,
  'provider': 'synthetic-partner',
  'status': status,
  'endpoint_url': null,
  'organization_id': null,
  'facility_id': null,
  if (readiness)
    'readiness': {
      'provider_registered': false,
      'external_access_allowed': false,
      'reason_code': hieHoldCode,
    },
};
http.Response receipt(Object data, {int status = 200}) => http.Response(
  jsonEncode({'success': true, 'data': data}),
  status,
  headers: {'content-type': 'application/json'},
);
HieRemoteDataSource makeSource(
  SessionNavigationGuard guard,
  MockClient client,
  Future<AuthSessionState> Function() load,
) {
  addTearDown(client.close);
  return HieRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: load,
    guard: guard,
  );
}

SessionNavigationGuard readyGuard() {
  final guard = SessionNavigationGuard()..markSessionAvailable();
  addTearDown(guard.dispose);
  return guard;
}

void main() {
  test('readiness absent, held, active or permissive never enables external access', () {
    for (final row in [
      integration(),
      integration(status: 'active'),
      integration(readiness: false),
      {
        ...integration(),
        'readiness': {
          'provider_registered': true,
          'external_access_allowed': true,
        },
      },
    ]) {
      expect(HieIntegration.parse(row, 4).externalAccessAllowed, isFalse);
    }
    expect(HieIntegration.parse(integration(), 4).holdConfirmed, isTrue);
    expect(
      HieIntegration.parse(integration(readiness: false), 4).holdConfirmed,
      isFalse,
    );
    expect(
      () => HieIntegration.parse(integration(clinic: 5), 4),
      throwsFormatException,
    );
    expect(
      () => HieIntegration.parse(integration(), 4, expectedId: 10),
      throwsFormatException,
    );
  });
  test(
    'all five view roles read known ID, no unsupported list endpoint',
    () async {
      final guard = readyGuard();
      var role = 'admin';
      var calls = 0;
      final source = makeSource(
        guard,
        MockClient((r) async {
          calls++;
          expect(r.url.path, '/api/v1/hie/integrations/9');
          expect(r.method, 'GET');
          expect(r.headers['authorization'], 'Bearer test-access');
          return receipt(integration());
        }),
        () async => session(role),
      );
      for (final value in hieViewRoles) {
        role = value;
        expect((await source.read(9)).id, 9);
      }
      expect(calls, 5);
      for (final value in [
        'super_admin',
        'patient',
        'receptionist',
        'accountant',
        'paramedic',
        'emt',
        'ambulance_dispatcher',
        'ambulance_coordinator',
        'driver',
        'other',
      ]) {
        role = value;
        await expectLater(source.read(9), throwsA(isA<HieUnavailable>()));
      }
      expect(calls, 5);
    },
  );
  test(
    'clinical roles cannot configure; active transitions never send',
    () async {
      final guard = readyGuard();
      var role = 'doctor';
      var calls = 0;
      final source = makeSource(
        guard,
        MockClient((r) async {
          calls++;
          return receipt(integration());
        }),
        () async => session(role),
      );
      await expectLater(
        source.create(const HieDraft(provider: 'synthetic-partner')),
        throwsA(isA<HieUnavailable>()),
      );
      await expectLater(
        source.update(9, status: 'disabled'),
        throwsA(isA<HieUnavailable>()),
      );
      role = 'admin';
      await expectLater(
        source.update(9, status: 'active'),
        throwsA(isA<HieUnavailable>()),
      );
      expect(calls, 0);
    },
  );
  test(
    'create pending once, exact nonsecret payload and no idempotency header',
    () async {
      final guard = readyGuard();
      var calls = 0;
      final source = makeSource(
        guard,
        MockClient((r) async {
          calls++;
          expect(r.method, 'POST');
          expect(r.url.path, '/api/v1/hie/integrations');
          expect(jsonDecode(r.body), {'provider': 'synthetic-partner'});
          expect(r.headers.containsKey('idempotency-key'), isFalse);
          return receipt(integration(), status: 201);
        }),
        () async => session('admin'),
      );
      const draft = HieDraft(provider: ' Synthetic-Partner ');
      expect((await source.create(draft)).status, 'pending');
      await expectLater(source.create(draft), throwsA(isA<HieUnavailable>()));
      expect(calls, 1);
    },
  );
  test(
    'held stale active record can be disabled via existing PATCH only',
    () async {
      final guard = readyGuard();
      final methods = <String>[];
      final source = makeSource(
        guard,
        MockClient((r) async {
          methods.add(r.method);
          expect(r.url.path, '/api/v1/hie/integrations/9');
          if (r.method == 'GET') return receipt(integration(status: 'active'));
          expect(jsonDecode(r.body), {'status': 'disabled'});
          return receipt(integration(status: 'disabled'));
        }),
        () async => session('admin'),
      );
      expect((await source.update(9, status: 'disabled')).status, 'disabled');
      expect(methods, ['GET', 'PATCH']);
    },
  );
  test('unknown readiness blocks update before sending mutation', () async {
    final guard = readyGuard();
    var calls = 0;
    final source = makeSource(
      guard,
      MockClient((r) async {
        calls++;
        expect(r.method, 'GET');
        return receipt(integration(readiness: false));
      }),
      () async => session('admin'),
    );
    await expectLater(
      source.update(9, status: 'disabled'),
      throwsA(isA<HieUnavailable>()),
    );
    expect(calls, 1);
  });
  test('unconfirmed write locks subsequent writes without replay', () async {
    final guard = readyGuard();
    var calls = 0;
    final source = makeSource(
      guard,
      MockClient((r) async {
        calls++;
        return receipt(integration(clinic: 99), status: 201);
      }),
      () async => session('admin'),
    );
    const draft = HieDraft(provider: 'synthetic-partner');
    await expectLater(
      source.create(draft),
      throwsA(isA<HieMutationUncertain>()),
    );
    await expectLater(
      source.create(draft),
      throwsA(isA<HieMutationUncertain>()),
    );
    expect(calls, 1);
  });
  test('stale session response rejected', () async {
    final guard = readyGuard();
    var clinic = 4;
    final source = makeSource(
      guard,
      MockClient((r) async {
        clinic = 5;
        return receipt(integration());
      }),
      () async => session('doctor', clinic: clinic),
    );
    await expectLater(source.read(9), throwsA(isA<HieUnavailable>()));
  });
  test('history uses metadata only and verifies clinic/filter/page', () async {
    final guard = readyGuard();
    final source = makeSource(
      guard,
      MockClient((r) async {
        expect(r.url.path, '/api/v1/hie/submissions');
        expect(r.url.queryParameters, {
          'integration_id': '9',
          'page': '1',
          'per_page': '20',
        });
        return receipt({
          'items': [
            {
              'id': 1,
              'clinic_id': 4,
              'integration_id': 9,
              'operation': 'patient_query',
              'status': 'failed',
              'failure_class': 'fail_closed',
              'status_code': 422,
              'retry_count': 1,
              'created_at': '2026-11-24T10:00:00Z',
              'request_data': {'secret': 'not retained'},
              'response_data': {'secret': 'not retained'},
            },
          ],
          'total': 1,
          'page': 1,
          'per_page': 20,
        });
      }),
      () async => session('pharmacist'),
    );
    final result = await source.submissions(integrationId: 9);
    expect(result.items.single.failureClass, 'fail_closed');
    expect(result.hasNext, isFalse);
    expect(
      () => HieSubmission.parse({
        'id': 1,
        'clinic_id': 5,
        'integration_id': 9,
        'operation': 'patient_query',
        'status': 'failed',
        'retry_count': 0,
        'created_at': '2026-11-24T10:00:00Z',
      }, 4),
      throwsFormatException,
    );
  });
  test('unsafe URL forms rejected without request', () {
    for (final url in [
      'http://partner.org',
      'https://u:p@partner.org',
      'https://partner.org?a=b',
      'https://partner.org#x',
      'https://partner.org:80',
    ]) {
      expect(
        () => HieDraft(provider: 'synthetic-partner', endpoint: url).toJson(),
        throwsFormatException,
      );
    }
  });
  testWidgets('doctor sees held query controls but no admin mutation', (
    tester,
  ) async {
    final guard = readyGuard();
    final source = makeSource(
      guard,
      MockClient((r) async => throw StateError('Unexpected HTTP')),
      () async => session('doctor'),
    );
    await tester.pumpWidget(
      MaterialApp(home: HieWorkspaceScreen(source: source)),
    );
    await tester.pumpAndSettle();
    expect(find.text('Create pending'), findsNothing);
    final button = tester.widget<OutlinedButton>(
      find.widgetWithText(OutlinedButton, 'Query external patient (held)'),
    );
    expect(button.onPressed, isNull);
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
