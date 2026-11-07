import 'dart:async';
import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

AuthSessionState _ready({String role = 'doctor', int clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 's02b-token',
        refreshToken: 's02b-refresh',
        userId: 42,
        clinicContextId: clinicId,
        role: role,
      ),
    );

Map<String, dynamic> _row({
  int patientId = 7,
  int staffId = 8,
  int clinicId = 3,
}) => {
  'id': 11,
  'clinic_id': clinicId,
  'patient_id': patientId,
  'staff_id': staffId,
  'scheduled_start': '2026-11-08T09:00:00',
  'scheduled_end': '2026-11-08T09:30:00',
  'status': 'scheduled',
  'appointment_type': 'in_person',
  'reason': 'Follow-up',
  'notes': null,
  'google_calendar_event_id': null,
  'reminder_sent': false,
  'created_at': '2026-11-07T08:00:00+00:00',
  'updated_at': '2026-11-07T08:00:00+00:00',
  'cancelled_at': null,
  'cancellation_reason': null,
};

http.Response _ok({Map<String, dynamic>? record}) => http.Response(
  jsonEncode(<String, dynamic>{
    'success': true,
    'data': <String, dynamic>{
      'items': <Map<String, dynamic>>[record ?? _row()],
      'total': 1,
      'page': 1,
      'per_page': 20,
      'pages': 1,
      'has_next': false,
      'has_prev': false,
    },
  }),
  200,
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
  bool includeSource = true,
  SessionNavigationGuard? sourceGuard,
  String role = 'doctor',
}) => createAppRouter(
  healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
  loginScreen: const Scaffold(body: Center(child: Text('Login sentinel'))),
  appointmentDataSource: includeSource
      ? AppointmentRemoteDataSource(
          apiClient: api,
          loadSession: () async => _ready(role: role),
          sessionNavigationGuard: sourceGuard ?? guard,
        )
      : null,
  sessionNavigationGuard: guard,
);

SessionNavigationGuard _guard({bool signedOut = false}) {
  final guard = SessionNavigationGuard(
    protectedRoutePrefixes: const <String>['/appointments'],
  );
  if (signedOut) {
    guard.markSignedOut();
  } else {
    guard.markSessionAvailable();
  }
  return guard;
}

void main() {
  testWidgets(
    'patient route loads exact authenticated patient page and detail',
    (tester) async {
      final paths = <String>[];
      final client = MockClient((request) async {
        paths.add(request.url.path);
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/appointments/patient/7');
        expect(request.url.queryParameters, {'page': '1', 'per_page': '20'});
        expect(request.headers['authorization'], 'Bearer s02b-token');
        return _ok();
      });
      addTearDown(client.close);
      final guard = _guard();
      addTearDown(guard.dispose);
      final router = _router(api: _api(client), guard: guard);
      addTearDown(router.dispose);

      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/appointments/patient/7');
      await tester.pumpAndSettle();

      expect(find.text('Patient appointments'), findsOneWidget);
      expect(find.byKey(const ValueKey('appointment-row-11')), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('appointment-row-11')));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('appointment-detail-11')),
        findsOneWidget,
      );
      expect(find.text('Patient ID: 7'), findsOneWidget);
      expect(find.text('Reason: Follow-up'), findsOneWidget);
      expect(paths, <String>['/api/v1/appointments/patient/7']);
    },
  );

  testWidgets('staff route uses server staff scope and date filter', (
    tester,
  ) async {
    final dates = <String?>[];
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/appointments/staff/8');
      expect(request.headers['authorization'], 'Bearer s02b-token');
      dates.add(request.url.queryParameters['date']);
      return _ok();
    });
    addTearDown(client.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(
      api: _api(client),
      guard: guard,
      role: 'receptionist',
    );
    addTearDown(router.dispose);

    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/appointments/staff/8');
    await tester.pumpAndSettle();
    expect(find.text('Staff appointment schedule'), findsOneWidget);
    expect(find.byKey(const ValueKey('appointment-row-11')), findsOneWidget);
    await tester.enterText(
      find.byKey(const ValueKey('appointments-staff-date')),
      '2026-11-08',
    );
    await tester.tap(find.byKey(const ValueKey('appointments-apply-date')));
    await tester.pumpAndSettle();
    expect(dates, <String?>[null, '2026-11-08']);
  });

  for (final route in <String>[
    '/appointments/patient/0',
    '/appointments/patient/not-a-number',
    '/appointments/staff/-1',
    '/appointments/staff/0',
  ]) {
    testWidgets('invalid route $route fails without HTTP', (tester) async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return _ok();
      });
      addTearDown(client.close);
      final guard = _guard();
      addTearDown(guard.dispose);
      final router = _router(api: _api(client), guard: guard);
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go(route);
      await tester.pumpAndSettle();
      expect(find.text('Appointment route is unavailable.'), findsOneWidget);
      expect(calls, 0);
    });
  }

  testWidgets(
    'missing appointment data source returns unavailable without HTTP',
    (tester) async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return _ok();
      });
      addTearDown(client.close);
      final guard = _guard();
      addTearDown(guard.dispose);
      final router = _router(
        api: _api(client),
        guard: guard,
        includeSource: false,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/appointments/patient/7');
      await tester.pumpAndSettle();
      expect(find.text('Appointment route is unavailable.'), findsOneWidget);
      router.go('/appointments/staff/8');
      await tester.pumpAndSettle();
      expect(find.text('Appointment route is unavailable.'), findsOneWidget);
      expect(calls, 0);
    },
  );

  testWidgets('mismatched source guard cannot render records or dispatch GET', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return _ok();
    });
    addTearDown(client.close);
    final guard = _guard();
    final differentGuard = _guard();
    addTearDown(guard.dispose);
    addTearDown(differentGuard.dispose);
    final router = _router(
      api: _api(client),
      guard: guard,
      sourceGuard: differentGuard,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/appointments/patient/7');
    await tester.pumpAndSettle();
    expect(find.text('Appointment route is unavailable.'), findsOneWidget);
    expect(calls, 0);
  });

  testWidgets('signed out deep links are redirected without request', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return _ok();
    });
    addTearDown(client.close);
    final guard = _guard(signedOut: true);
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/appointments/patient/7');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    router.go('/appointments/staff/8');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(calls, 0);
  });

  testWidgets('HTTP 403 produces no PHI and no automatic second request', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{"error":"Forbidden"}', 403);
    });
    addTearDown(client.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/appointments/patient/7');
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('appointments-error')), findsOneWidget);
    expect(find.text('Appointment #11'), findsNothing);
    expect(calls, 1);
  });

  testWidgets('wrong-clinic response fails closed in routed patient list', (
    tester,
  ) async {
    final client = MockClient((_) async => _ok(record: _row(clinicId: 9)));
    addTearDown(client.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/appointments/patient/7');
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('appointments-error')), findsOneWidget);
    expect(find.text('Appointment #11'), findsNothing);
  });

  testWidgets(
    'session invalidation removes routed records before late response',
    (tester) async {
      final pending = Completer<http.Response>();
      final started = Completer<void>();
      final client = MockClient((request) {
        expect(request.url.path, '/api/v1/appointments/patient/7');
        if (!started.isCompleted) {
          started.complete();
        }
        return pending.future;
      });
      addTearDown(client.close);
      final guard = _guard();
      addTearDown(guard.dispose);
      final router = _router(api: _api(client), guard: guard);
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/appointments/patient/7');
      for (var i = 0; i < 20 && !started.isCompleted; i++) {
        await tester.pump(const Duration(milliseconds: 10));
      }
      expect(started.isCompleted, isTrue);
      guard.invalidate();
      pending.complete(_ok());
      await tester.pumpAndSettle();
      expect(find.text('Login sentinel'), findsOneWidget);
      expect(find.text('Appointment #11', skipOffstage: false), findsNothing);
    },
  );

  testWidgets(
    '401 invalidation redirects without showing appointment details',
    (tester) async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response('{"error":"Expired"}', 401);
      });
      addTearDown(client.close);
      final guard = _guard();
      addTearDown(guard.dispose);
      final api = _api(client)..onAuthenticationRejected = guard.invalidate;
      final router = _router(api: api, guard: guard);
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/appointments/staff/8');
      await tester.pumpAndSettle();
      expect(guard.canAccessProtectedRoutes, isFalse);
      expect(find.text('Login sentinel'), findsOneWidget);
      expect(find.text('Appointment #11'), findsNothing);
      expect(calls, 1);
    },
  );
}
