import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_store.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/auth/presentation/google_login_screen.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _timestamp = '2026-11-04T12:30:00+00:00';

Map<String, dynamic> _alert({int id = 19}) => <String, dynamic>{
  'id': id,
  'clinic_id': 3,
  'patient_id': 7,
  'rule_id': 12,
  'rule_version': 2,
  'severity': 'critical',
  'action': 'block',
  'status': 'open',
  'title': 'Medication safety warning',
  'message': 'Review medication safety before proceeding.',
  'context': <String, dynamic>{'private_note': 'DO_NOT_RENDER'},
  'source_type': 'prescription',
  'source_id': 9,
  'deduplication_key': 'private-key',
  'generated_at': _timestamp,
  'resolved_at': null,
  'created_at': _timestamp,
  'updated_at': _timestamp,
};

http.Response _ok(Map<String, dynamic> data) =>
    http.Response(jsonEncode(data), 200);

http.Response _forbidden(int code) => http.Response(
  jsonEncode(<String, dynamic>{'message': 'Backend access denied'}),
  code,
);

ApiClient _api(MockClient client) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: client,
);

GoRouter _router({
  required ApiClient api,
  required SessionNavigationGuard guard,
  bool withDataSource = true,
}) {
  return createAppRouter(
    healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
    loginScreen: Builder(
      builder: (context) => Scaffold(
        body: Center(
          child: TextButton(
            key: const ValueKey('route-test-open-inbox'),
            onPressed: () => context.push<void>('/clinical-safety'),
            child: const Text('Open clinical alerts'),
          ),
        ),
      ),
    ),
    clinicalSafetyDataSource: withDataSource
        ? ClinicalSafetyRemoteDataSource(
            apiClient: api,
            loadAccessToken: () async => 'staff-test-token',
          )
        : null,
    sessionNavigationGuard: guard,
  );
}

class _MemorySecureStorage implements SecureKeyValueStorage {
  final Map<String, String> _values = <String, String>{};

  @override
  Future<String?> read({required String key}) async => _values[key];

  @override
  Future<void> write({required String key, required String value}) async {
    _values[key] = value;
  }

  @override
  Future<void> delete({required String key}) async {
    _values.remove(key);
  }
}

void main() {
  testWidgets('inbox row opens its exact server-authoritative detail route', (
    tester,
  ) async {
    final paths = <String>[];
    final client = MockClient((request) async {
      paths.add(request.url.path);
      expect(request.method, 'GET');
      expect(request.headers['authorization'], 'Bearer staff-test-token');
      if (request.url.path == '/api/v1/clinical-safety/alerts') {
        expect(request.url.hasQuery, isFalse);
        return _ok(<String, dynamic>{
          'items': <Map<String, dynamic>>[_alert()],
          'total': 1,
          'page': 1,
          'per_page': 50,
        });
      }
      if (request.url.path == '/api/v1/clinical-safety/alerts/19') {
        return _ok(<String, dynamic>{'alert': _alert()});
      }
      throw StateError('Unexpected endpoint: ${request.url.path}');
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);

    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('route-test-open-inbox')));
    await tester.pumpAndSettle();
    expect(find.text('Clinical safety alerts'), findsOneWidget);
    expect(find.byKey(const ValueKey('clinical-alert-row-19')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('clinical-alert-row-19')));
    await tester.pumpAndSettle();
    expect(find.text('Clinical alert detail'), findsOneWidget);
    expect(find.text('Alert ID: 19'), findsOneWidget);
    expect(find.text('Status: Open'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-block-warning')),
      findsOneWidget,
    );
    expect(find.text('DO_NOT_RENDER'), findsNothing);
    expect(paths, <String>[
      '/api/v1/clinical-safety/alerts',
      '/api/v1/clinical-safety/alerts/19',
    ]);
  });

  testWidgets('signed out cannot deep-link to inbox or detail', (tester) async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return _ok(<String, dynamic>{'items': <dynamic>[]});
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSignedOut();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    await tester.pumpAndSettle();

    router.go('/clinical-safety');
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('route-test-open-inbox')), findsOneWidget);
    expect(find.text('Clinical safety alerts'), findsNothing);

    router.go('/clinical-safety/alerts/19');
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('route-test-open-inbox')), findsOneWidget);
    expect(find.text('Clinical alert detail'), findsNothing);
    expect(requests, 0);
  });

  testWidgets('invalid alert ID fails closed without calling backend', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return _ok(<String, dynamic>{'alert': _alert()});
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    await tester.pumpAndSettle();

    router.go('/clinical-safety/alerts/0');
    await tester.pumpAndSettle();
    expect(find.text('Clinical safety route is unavailable.'), findsOneWidget);
    router.go('/clinical-safety/alerts/not-an-id');
    await tester.pumpAndSettle();
    expect(find.text('Clinical safety route is unavailable.'), findsOneWidget);
    expect(requests, 0);
  });

  testWidgets('missing clinical data source fails closed with no HTTP', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return _ok(<String, dynamic>{'items': <dynamic>[]});
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    final router = _router(
      api: _api(client),
      guard: guard,
      withDataSource: false,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    await tester.pumpAndSettle();

    router.go('/clinical-safety');
    await tester.pumpAndSettle();
    expect(find.text('Clinical safety route is unavailable.'), findsOneWidget);
    router.go('/clinical-safety/alerts/19');
    await tester.pumpAndSettle();
    expect(find.text('Clinical safety route is unavailable.'), findsOneWidget);
    expect(requests, 0);
  });

  testWidgets('backend detail 403 denies record without revealing data', (
    tester,
  ) async {
    final client = MockClient((request) async {
      expect(request.url.path, '/api/v1/clinical-safety/alerts/19');
      return _forbidden(403);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    await tester.pumpAndSettle();

    router.go('/clinical-safety/alerts/19');
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-unconfirmed')),
      findsOneWidget,
    );
    expect(find.text('Medication safety warning'), findsNothing);
    expect(find.text('Backend access denied'), findsNothing);
  });

  testWidgets(
    'backend 401 invalidates clinical navigation and removes detail',
    (tester) async {
      final client = MockClient((request) async {
        expect(request.url.path, '/api/v1/clinical-safety/alerts/19');
        return _forbidden(401);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard(
        protectedRoutePrefixes: const <String>['/clinical-safety'],
      )..markSessionAvailable();
      addTearDown(guard.dispose);
      final api = _api(client)..onAuthenticationRejected = guard.invalidate;
      final router = _router(api: api, guard: guard);
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      await tester.pumpAndSettle();

      router.go('/clinical-safety/alerts/19');
      await tester.pumpAndSettle();
      expect(guard.canAccessProtectedRoutes, isFalse);
      expect(
        find.byKey(const ValueKey('route-test-open-inbox')),
        findsOneWidget,
      );
      expect(find.text('Medication safety warning'), findsNothing);
    },
  );

  testWidgets('sign-out during detail removes protected content', (
    tester,
  ) async {
    final client = MockClient((request) async {
      expect(request.url.path, '/api/v1/clinical-safety/alerts/19');
      return _ok(<String, dynamic>{'alert': _alert()});
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    await tester.pumpAndSettle();

    router.go('/clinical-safety/alerts/19');
    await tester.pumpAndSettle();
    expect(find.text('Medication safety warning'), findsOneWidget);
    guard.markSignedOut();
    await tester.pumpAndSettle();
    expect(find.text('Medication safety warning'), findsNothing);
    expect(find.byKey(const ValueKey('route-test-open-inbox')), findsOneWidget);
  });

  for (final role in <String>[
    'PATIENT',
    'RECEPTIONIST',
    'DOCTOR',
    'NURSE',
    'PHARMACIST',
    'SUPER_ADMIN',
  ]) {
    testWidgets('login clinical link follows staff role contract: $role', (
      tester,
    ) async {
      final storage = _MemorySecureStorage();
      final store = AuthSessionStore(storage: storage);
      await store.save(
        AuthTokenResponse(
          accessToken: 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiJ9.signature',
          refreshToken: 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiIsInR5cGUiOiJyZWZyZXNoIn0.signature',
          userId: 42,
          role: role,
          clinicContextId: 3,
        ),
      );
      final client = MockClient(
        (_) async => _ok(<String, dynamic>{'success': true}),
      );
      addTearDown(client.close);
      final remote = AuthRemoteDataSource(apiClient: _api(client));
      await tester.pumpWidget(
        MaterialApp(
          home: GoogleLoginScreen(
            acquireGoogleIdToken: () async => null,
            authRemoteDataSource: remote,
            sessionCoordinator: AuthSessionCoordinator(
              sessionStore: store,
              remoteDataSource: remote,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('authenticated-session')),
        findsOneWidget,
      );
      final allowed = !const <String>{'PATIENT', 'RECEPTIONIST'}.contains(role);
      expect(
        find.byKey(const ValueKey('open-clinical-alerts-button')),
        allowed ? findsOneWidget : findsNothing,
      );
    });
  }
}
