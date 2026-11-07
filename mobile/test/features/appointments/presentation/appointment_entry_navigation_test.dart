import 'dart:async';
import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_entry_screen.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

AuthSessionState _ready({String role = 'doctor', int? clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 's02c-token',
        refreshToken: 's02c-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinic,
      ),
    );

SessionNavigationGuard _guard({bool ready = true}) {
  final guard = SessionNavigationGuard(
    protectedRoutePrefixes: const <String>['/appointments', '/home'],
  );
  if (ready) {
    guard.markSessionAvailable();
  } else {
    guard.markSignedOut();
  }
  return guard;
}

ApiClient _api(MockClient client) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: client,
);

http.Response _emptyPage() => http.Response(
  jsonEncode({
    'success': true,
    'data': {
      'items': <dynamic>[],
      'total': 0,
      'page': 1,
      'per_page': 20,
      'pages': 0,
      'has_next': false,
      'has_prev': false,
    },
  }),
  200,
);

GoRouter _router({
  required ApiClient api,
  required SessionNavigationGuard guard,
  Future<AuthSessionState> Function()? load,
  bool includeSource = true,
  SessionNavigationGuard? sourceGuard,
}) {
  final loader = load ?? () async => _ready();
  return createAppRouter(
    healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
    loginScreen: const Scaffold(body: Center(child: Text('Login sentinel'))),
    loadHomeSession: loader,
    appointmentDataSource: includeSource
        ? AppointmentRemoteDataSource(
            apiClient: api,
            loadSession: loader,
            sessionNavigationGuard: sourceGuard ?? guard,
          )
        : null,
    sessionNavigationGuard: guard,
  );
}

void main() {
  test('home destination requires an allowed role and active clinic', () {
    List<String> ids(String role, {int? clinic}) => homeDestinationsFor(
      role: role,
      clinicContextId: clinic,
    ).map((entry) => entry.id).toList();

    for (final role in ['admin', 'DOCTOR', 'nurse', 'receptionist']) {
      expect(ids(role, clinic: 3), contains('appointments'));
      expect(ids(role), isNot(contains('appointments')));
    }
    for (final role in ['patient', 'pharmacist', 'super_admin', 'accountant']) {
      expect(ids(role, clinic: 3), isNot(contains('appointments')));
    }
  });

  testWidgets('ready hub routes patient ID to verified patient list only', (
    tester,
  ) async {
    final paths = <String>[];
    final client = MockClient((request) async {
      paths.add(request.url.path);
      expect(request.method, 'GET');
      expect(request.headers['authorization'], 'Bearer s02c-token');
      return _emptyPage();
    });
    addTearDown(client.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);

    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/appointments');
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointments-entry-ready')),
      findsOneWidget,
    );
    expect(paths, isEmpty);
    await tester.enterText(
      find.byKey(const ValueKey('appointments-patient-id')),
      '7',
    );
    await tester.tap(find.byKey(const ValueKey('appointments-open-patient')));
    await tester.pumpAndSettle();
    expect(find.text('Patient appointments'), findsOneWidget);
    expect(paths, ['/api/v1/appointments/patient/7']);
  });

  testWidgets('ready hub routes staff ID to staff schedule only', (
    tester,
  ) async {
    final paths = <String>[];
    final client = MockClient((request) async {
      paths.add(request.url.path);
      return _emptyPage();
    });
    addTearDown(client.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/appointments');
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('appointments-staff-id')),
      '8',
    );
    await tester.tap(find.byKey(const ValueKey('appointments-open-staff')));
    await tester.pumpAndSettle();
    expect(find.text('Staff appointment schedule'), findsOneWidget);
    expect(paths, ['/api/v1/appointments/staff/8']);
  });

  testWidgets('invalid patient and staff IDs make no HTTP request', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return _emptyPage();
    });
    addTearDown(client.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/appointments');
    await tester.pumpAndSettle();

    for (final bad in ['0', '01', 'not-an-id', '2147483648', ' 7']) {
      await tester.enterText(
        find.byKey(const ValueKey('appointments-patient-id')),
        bad,
      );
      await tester.tap(find.byKey(const ValueKey('appointments-open-patient')));
      await tester.pumpAndSettle();
      expect(find.text('Enter a valid patient ID.'), findsOneWidget);
    }
    await tester.enterText(
      find.byKey(const ValueKey('appointments-staff-id')),
      '-1',
    );
    await tester.tap(find.byKey(const ValueKey('appointments-open-staff')));
    await tester.pumpAndSettle();
    expect(find.text('Enter a valid staff ID.'), findsOneWidget);
    expect(calls, 0);
  });

  for (final role in ['patient', 'pharmacist', 'super_admin']) {
    testWidgets('role $role cannot open appointment chooser', (tester) async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return _emptyPage();
      });
      addTearDown(client.close);
      final guard = _guard();
      addTearDown(guard.dispose);
      final router = _router(
        api: _api(client),
        guard: guard,
        load: () async => _ready(role: role),
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/appointments');
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('appointments-entry-denied')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('appointments-patient-id')),
        findsNothing,
      );
      expect(calls, 0);
    });
  }

  testWidgets('missing clinic context hides appointment chooser', (
    tester,
  ) async {
    final client = MockClient((_) async => _emptyPage());
    addTearDown(client.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(
      api: _api(client),
      guard: guard,
      load: () async => _ready(clinic: null),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/appointments');
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointments-entry-denied')),
      findsOneWidget,
    );
  });

  testWidgets('missing or mismatched appointment source fails closed', (
    tester,
  ) async {
    for (final mismatch in [false, true]) {
      final client = MockClient((_) async => _emptyPage());
      final guard = _guard();
      final sourceGuard = _guard();
      final router = _router(
        api: _api(client),
        guard: guard,
        includeSource: mismatch,
        sourceGuard: sourceGuard,
      );
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/appointments');
      await tester.pumpAndSettle();
      expect(find.text('Appointment route is unavailable.'), findsOneWidget);
      await tester.pumpWidget(const SizedBox.shrink());
      router.dispose();
      guard.dispose();
      sourceGuard.dispose();
      client.close();
    }
  });

  testWidgets('signed-out deep link redirects and performs no request', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return _emptyPage();
    });
    addTearDown(client.close);
    final guard = _guard(ready: false);
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/appointments');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('appointments-entry-ready')),
      findsNothing,
    );
    expect(calls, 0);
  });

  testWidgets('logout clears chooser and redirects before any request', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return _emptyPage();
    });
    addTearDown(client.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/appointments');
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointments-entry-ready')),
      findsOneWidget,
    );
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(find.byKey(const ValueKey('appointments-patient-id')), findsNothing);
    expect(calls, 0);
  });

  testWidgets(
    'late session resolution after invalidation cannot restore chooser',
    (tester) async {
      final pending = Completer<AuthSessionState>();
      final guard = _guard();
      addTearDown(guard.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: AppointmentEntryScreen(
            loadSession: () => pending.future,
            sessionNavigationGuard: guard,
            onPatientSelected: (_) =>
                fail('Cannot navigate after invalidation'),
            onStaffSelected: (_) => fail('Cannot navigate after invalidation'),
          ),
        ),
      );
      await tester.pump();
      expect(
        find.byKey(const ValueKey('appointments-entry-loading')),
        findsOneWidget,
      );
      guard.invalidate();
      pending.complete(_ready());
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('appointments-entry-denied')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('appointments-entry-ready')),
        findsNothing,
      );
    },
  );
}
