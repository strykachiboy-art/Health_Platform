import 'dart:async';
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
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

// Simulates an OS-backed secure-store surviving app/coordinator recreation.
// No live credentials, PHI, Google browser, or network endpoints are used.
class _DeviceSecrets implements SecureKeyValueStorage {
  final values = <String, String>{};
  bool failDelete = false;

  @override
  Future<String?> read({required String key}) async => values[key];

  @override
  Future<void> write({required String key, required String value}) async {
    values[key] = value;
  }

  @override
  Future<void> delete({required String key}) async {
    if (failDelete) throw StateError('Injected secure-store deletion failure.');
    values.remove(key);
  }
}

const _patientSession = AuthTokenResponse(
  accessToken: 'aaa.bbb.ccc',
  refreshToken: 'ddd.eee.fff',
  userId: 42,
  role: 'PATIENT',
  clinicContextId: null,
);

const _protected = <String>[
  '/patients',
  '/appointments',
  '/consultations',
  '/clinical-orders',
  '/home',
  '/devices',
  '/account',
  '/emergency-access',
  '/clinical-safety',
];

SessionNavigationGuard _guard() =>
    SessionNavigationGuard(protectedRoutePrefixes: _protected);

AuthRemoteDataSource _remote(http.Client client) => AuthRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
);

GoogleLoginScreen _screen({
  required _DeviceSecrets storage,
  required AuthRemoteDataSource remote,
  required SessionNavigationGuard guard,
  FutureOr<void> Function()? onSessionEnding,
}) => GoogleLoginScreen(
  enableNativeGoogleSignIn: false,
  enableDesktopGoogleSignIn: true,
  acquireGoogleIdToken: () async =>
      fail('WD01 lifecycle must never invoke the native Google provider.'),
  acquireDesktopGoogleIdToken: () async =>
      fail('WD01 lifecycle must not initiate a fresh Google sign-in.'),
  authRemoteDataSource: remote,
  sessionCoordinator: AuthSessionCoordinator(
    sessionStore: AuthSessionStore(storage: storage),
    remoteDataSource: remote,
  ),
  sessionNavigationGuard: guard,
  onSessionEnding: onSessionEnding,
);

void _assertProtectedRoutesClosed(SessionNavigationGuard guard) {
  expect(guard.canAccessProtectedRoutes, isFalse);
  for (final route in <String>[
    '/home',
    '/home/child',
    '/patients',
    '/patients/7',
    '/account',
    '/devices',
    '/appointments',
    '/clinical-orders',
    '/clinical-safety',
  ]) {
    expect(guard.redirect(route), '/login', reason: 'Blocked $route');
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets(
    'WD01: saved unscoped patient session survives two cold UI restarts',
    (tester) async {
      final storage = _DeviceSecrets();
      await AuthSessionStore(storage: storage).save(_patientSession);
      storage.values['unrelated.encrypted.queue'] = 'preserved';
      var networkCalls = 0;
      final client = MockClient((_) async {
        networkCalls++;
        throw StateError('Cold restore must not perform network I/O.');
      });
      addTearDown(client.close);
      final remote = _remote(client);

      for (var restart = 0; restart < 2; restart++) {
        final guard = _guard();
        addTearDown(guard.dispose);
        await tester.pumpWidget(
          MaterialApp(
            home: _screen(storage: storage, remote: remote, guard: guard),
          ),
        );
        await tester.pumpAndSettle();
        expect(
          find.byKey(const ValueKey('authenticated-session')),
          findsOneWidget,
        );
        expect(find.byKey(const ValueKey('sign-out-button')), findsOneWidget);
        expect(guard.canAccessProtectedRoutes, isTrue);
        expect((await AuthSessionStore(storage: storage).read())?.userId, 42);
        expect(
          (await AuthSessionStore(storage: storage).read())?.clinicContextId,
          isNull,
        );
        await tester.pumpWidget(const SizedBox.shrink());
      }
      expect(networkCalls, 0);
      expect(storage.values['unrelated.encrypted.queue'], 'preserved');
    },
  );

  testWidgets(
    'WD01: logout locks protected routes before remote ACK; restart stays signed out',
    (tester) async {
      final storage = _DeviceSecrets();
      await AuthSessionStore(storage: storage).save(_patientSession);
      storage.values['unrelated.encrypted.queue'] = 'preserved';
      final remoteStarted = Completer<void>();
      final remoteResponse = Completer<http.Response>();
      var calls = 0;
      final client = MockClient((request) {
        calls++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/auth/logout');
        expect(request.headers['authorization'], 'Bearer aaa.bbb.ccc');
        expect(jsonDecode(request.body), <String, dynamic>{
          'refresh_token': 'ddd.eee.fff',
        });
        remoteStarted.complete();
        return remoteResponse.future;
      });
      addTearDown(client.close);
      final remote = _remote(client);
      final guard = _guard();
      addTearDown(guard.dispose);
      var replayPaused = 0;
      await tester.pumpWidget(
        MaterialApp(
          home: _screen(
            storage: storage,
            remote: remote,
            guard: guard,
            onSessionEnding: () {
              replayPaused++;
            },
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(guard.canAccessProtectedRoutes, isTrue);

      await tester.tap(find.byKey(const ValueKey('sign-out-button')));
      await tester.pump();
      _assertProtectedRoutesClosed(guard);
      await remoteStarted.future;
      expect(await AuthSessionStore(storage: storage).readRecord(), isNull);
      expect(replayPaused, 1);
      expect(calls, 1);
      expect(storage.values['unrelated.encrypted.queue'], 'preserved');

      remoteResponse.complete(http.Response('{"success":true}', 200));
      await tester.pumpAndSettle();
      expect(guard.status, SessionNavigationStatus.signedOut);
      _assertProtectedRoutesClosed(guard);
      expect(
        find.byKey(const ValueKey('google-sign-in-button')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('authenticated-session')), findsNothing);

      await tester.pumpWidget(const SizedBox.shrink());
      final afterRestart = _guard();
      addTearDown(afterRestart.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: _screen(storage: storage, remote: remote, guard: afterRestart),
        ),
      );
      await tester.pumpAndSettle();
      expect(afterRestart.status, SessionNavigationStatus.signedOut);
      _assertProtectedRoutesClosed(afterRestart);
      expect(find.byKey(const ValueKey('authenticated-session')), findsNothing);
      expect(
        find.byKey(const ValueKey('google-sign-in-button')),
        findsOneWidget,
      );
      expect(calls, 1);
    },
  );

  testWidgets(
    'WD01: remote logout failure never restores local session after restart',
    (tester) async {
      final storage = _DeviceSecrets();
      await AuthSessionStore(storage: storage).save(_patientSession);
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        expect(request.url.path, '/api/v1/auth/logout');
        return http.Response('{"success":false}', 503);
      });
      addTearDown(client.close);
      final remote = _remote(client);
      final guard = _guard();
      addTearDown(guard.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: _screen(storage: storage, remote: remote, guard: guard),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('sign-out-button')));
      await tester.pumpAndSettle();
      expect(requests, 1);
      expect(guard.status, SessionNavigationStatus.signedOut);
      _assertProtectedRoutesClosed(guard);
      expect(await AuthSessionStore(storage: storage).read(), isNull);

      await tester.pumpWidget(const SizedBox.shrink());
      final newGuard = _guard();
      addTearDown(newGuard.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: _screen(storage: storage, remote: remote, guard: newGuard),
        ),
      );
      await tester.pumpAndSettle();
      expect(newGuard.status, SessionNavigationStatus.signedOut);
      _assertProtectedRoutesClosed(newGuard);
      expect(
        find.byKey(const ValueKey('google-sign-in-button')),
        findsOneWidget,
      );
      expect(requests, 1);
    },
  );

  testWidgets(
    'WD01: storage deletion failure remains in recovery-required state',
    (tester) async {
      final storage = _DeviceSecrets()..failDelete = true;
      await AuthSessionStore(storage: storage).save(_patientSession);
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        throw StateError('No request until local deletion succeeds.');
      });
      addTearDown(client.close);
      final guard = _guard();
      addTearDown(guard.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: _screen(
            storage: storage,
            remote: _remote(client),
            guard: guard,
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('sign-out-button')));
      await tester.pumpAndSettle();
      expect(guard.status, SessionNavigationStatus.invalidated);
      _assertProtectedRoutesClosed(guard);
      expect(
        find.byKey(const ValueKey('session-recovery-required')),
        findsOneWidget,
      );
      expect(calls, 0);
      expect(storage.values.containsKey(AuthSessionStore.storageKey), isTrue);
    },
  );

  test(
    'WD01: unscoped patient navigation never advertises clinical staff entry',
    () {
      final destinations = homeDestinationsFor(
        role: 'patient',
        clinicContextId: null,
      );
      final ids = destinations.map((destination) => destination.id).toSet();
      expect(ids, contains('device_management'));
      expect(ids, contains('health'));
      expect(ids, isNot(contains('appointments')));
      expect(ids, isNot(contains('consultations')));
      expect(ids, isNot(contains('clinical_orders')));
      expect(ids, isNot(contains('patient_directory')));
      expect(ids, isNot(contains('billing')));
      expect(ids, isNot(contains('clinical_safety')));
    },
  );
}
