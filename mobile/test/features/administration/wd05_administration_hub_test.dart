import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/administration/presentation/wd05_administration_hub.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';

AuthSessionState _ready(String role, {int? clinic = 7}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'wd05-only-test-token',
        refreshToken: 'wd05-only-refresh',
        userId: 45,
        role: role,
        clinicContextId: clinic,
      ),
    );

List<String> _ids(String role, {int? clinic = 7}) => wd05AdministrationLinks(
  role: role,
  clinicContextId: clinic,
).map((item) => item.id).toList();

void main() {
  test('WD05 clinic/role matrix links only registered safe Windows routes', () {
    expect(_ids('admin'), <String>[
      'dashboard',
      'account',
      'billing',
      'pharmacy_stock',
      'reports',
      'staff',
      'departments',
      'inventory',
      'assets',
      'settings',
      'audit',
    ]);
    expect(_ids('accountant'), <String>[
      'dashboard',
      'account',
      'reports',
      'assets',
      'settings',
    ]);
    expect(_ids('pharmacist'), <String>[
      'dashboard',
      'account',
      'pharmacy_stock',
      'inventory',
    ]);
    for (final role in <String>['admin', 'accountant', 'pharmacist']) {
      expect(_ids(role, clinic: null), isEmpty);
      expect(_ids(role, clinic: 0), isEmpty);
      expect(_ids(role, clinic: -1), isEmpty);
    }
    for (final role in <String>[
      'patient',
      'doctor',
      'nurse',
      'driver',
      'invalid',
    ]) {
      expect(_ids(role), isEmpty, reason: role);
    }
    expect(_ids('super_admin', clinic: null), <String>[
      'dashboard',
      'account',
      'audit',
      'users',
    ]);
    expect(_ids('super_admin', clinic: 7), <String>[
      'dashboard',
      'account',
      'audit',
      'users',
    ]);
    for (final item in wd05AdministrationLinks(
      role: 'admin',
      clinicContextId: 7,
    )) {
      expect(
        item.path,
        isIn(<String>[
          '/home',
          '/account',
          '/billing',
          '/pharmacy/stock',
          '/reports',
          '/administration/staff',
          '/administration/departments',
          '/administration/inventory',
          '/administration/assets',
          '/administration/settings',
          '/administration/audit-logs',
        ]),
      );
      expect(item.path, isNot(contains(':')));
    }
    expect(wd05PendingAdministrationCapabilities, isNotEmpty);
    expect(
      homeDestinationsFor(
        role: 'super_admin',
        clinicContextId: null,
      ).map((item) => item.id),
      contains('administration'),
    );
  });

  test(
    'WD05 home shortcut is appended after existing patient/WD04 ordering',
    () {
      final ids = homeDestinationsFor(
        role: 'admin',
        clinicContextId: 7,
      ).map((item) => item.id).toList();
      expect(ids, contains('administration'));
      expect(ids, contains('patients'));
      expect(
        ids.indexOf('administration'),
        greaterThan(ids.indexOf('patients')),
      );
      expect(
        ids.indexOf('administration'),
        greaterThan(ids.indexOf('clinical_workflows')),
      );
      expect(
        homeDestinationsFor(
          role: 'patient',
          clinicContextId: 7,
        ).map((item) => item.id),
        isNot(contains('administration')),
      );
      expect(
        homeDestinationsFor(
          role: 'admin',
          clinicContextId: null,
        ).map((item) => item.id),
        isNot(contains('administration')),
      );
    },
  );

  testWidgets('WD05 verifies the live session again before opening billing', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final opened = <String>[];
    var session = _ready('admin');
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Wd05AdministrationHub(
            loadSession: () async => session,
            sessionNavigationGuard: guard,
            onOpen: opened.add,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('wd05-admin-workspace')), findsOneWidget);
    expect(find.byKey(const ValueKey('wd05-admin-billing')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('wd05-admin-billing')));
    await tester.pumpAndSettle();
    expect(opened, <String>['/billing']);
    session = _ready('patient');
    await tester.tap(find.byKey(const ValueKey('wd05-admin-billing')));
    await tester.pumpAndSettle();
    expect(opened, hasLength(1));
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('wd05-admin-unavailable')),
      findsOneWidget,
    );
  });

  testWidgets('WD05 stale restoration cannot mount after revocation', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final pending = Completer<AuthSessionState>();
    final opened = <String>[];
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Wd05AdministrationHub(
            loadSession: () => pending.future,
            sessionNavigationGuard: guard,
            onOpen: opened.add,
          ),
        ),
      ),
    );
    expect(find.byKey(const ValueKey('wd05-admin-loading')), findsOneWidget);
    guard.invalidate();
    pending.complete(_ready('admin'));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('wd05-admin-unavailable')),
      findsOneWidget,
    );
    expect(opened, isEmpty);
  });

  testWidgets(
    'WD05 default router guard denies anonymous admin and report deep links',
    (tester) async {
      final guard = SessionNavigationGuard();
      addTearDown(guard.dispose);
      final client = MockClient(
        (request) async => throw StateError('Unexpected HTTP: ${request.url}'),
      );
      addTearDown(client.close);
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(
          apiClient: ApiClient(
            config: const BackendConfig(
              baseUrl: 'http://127.0.0.1:5000',
              allowInsecureHttp: true,
            ),
            client: client,
          ),
        ),
        loginScreen: const Scaffold(
          body: Center(child: Text('Login sentinel')),
        ),
        sessionNavigationGuard: guard,
        loadHomeSession: () async => _ready('admin'),
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      for (final path in <String>[
        '/administration',
        '/reports',
        '/administration/staff',
        '/administration/departments',
        '/administration/inventory',
        '/administration/assets',
        '/administration/settings',
        '/administration/audit-logs',
        '/administration/users',
      ]) {
        router.go(path);
        await tester.pumpAndSettle();
        expect(find.text('Login sentinel'), findsOneWidget, reason: path);
        expect(
          find.byKey(const ValueKey('wd05-admin-workspace')),
          findsNothing,
        );
        expect(
          find.byKey(const ValueKey('wd05-reports-workspace')),
          findsNothing,
        );
      }
    },
  );
}
