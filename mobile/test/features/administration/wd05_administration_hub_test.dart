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
  test('Doctor-only shared workspace navigation requires a clinic', () {
    const roles = <String>['super_admin', 'admin', 'doctor', 'nurse',
      'pharmacist', 'lab_technician', 'receptionist', 'accountant', 'patient',
      'paramedic', 'emt', 'ambulance_dispatcher', 'ambulance_coordinator',
      'driver', 'other'];
    for (final role in roles) {
      final links = wd05AdministrationLinks(role: role, clinicContextId: 7);
      expect(links.where((link) => link.path == '/administration/doctor-workspace'),
          hasLength(role == 'doctor' ? 1 : 0), reason: role);
    }
    expect(_ids('doctor', clinic: null), isNot(contains('doctor_workspace')));
    expect(_ids('doctor'), isNot(contains('clinic_workspace')));
  });
  test('WD09C-F role matrix preserves clinic authority and 15 roles', () {
    const staffRoles = <String>[
      'admin',
      'doctor',
      'nurse',
      'pharmacist',
      'lab_technician',
      'receptionist',
      'accountant',
      'paramedic',
      'emt',
      'driver',
      'ambulance_dispatcher',
      'ambulance_coordinator',
      'other',
    ];
    for (final role in staffRoles) {
      expect(
        _ids(role),
        containsAll(<String>['staff', 'departments', 'operations']),
      );
      expect(_ids(role, clinic: null), isEmpty, reason: role);
      expect(_ids(role, clinic: 0), isEmpty, reason: role);
      expect(_ids(role, clinic: -1), isEmpty, reason: role);
      expect(_ids(role), isNot(contains('users')), reason: role);
      if (role != 'admin') {
        expect(_ids(role), isNot(contains('audit')), reason: role);
      }
    }
    expect(
      _ids('admin'),
      containsAll(<String>[
        'dashboard',
        'account',
        'billing',
        'pharmacy_stock',
        'reports',
        'inventory',
        'staff',
        'departments',
        'assets',
        'settings',
        'operations',
        'audit',
      ]),
    );
    expect(
      _ids('accountant'),
      containsAll(<String>[
        'reports',
        'staff',
        'departments',
        'assets',
        'settings',
        'operations',
      ]),
    );
    expect(
      _ids('pharmacist'),
      containsAll(<String>[
        'pharmacy_stock',
        'inventory',
        'staff',
        'departments',
        'reports',
        'settings',
        'operations',
      ]),
    );
    expect(_ids('patient'), isEmpty);
    expect(_ids('invalid'), isEmpty);
    expect(_ids('super_admin', clinic: null), <String>[
      'dashboard',
      'account',
      'audit',
      'users',
      'clinics',
      'sa_workspace',
    ]);
    expect(_ids('super_admin', clinic: 7), <String>[
      'dashboard',
      'account',
      'audit',
      'users',
      'clinics',
      'sa_workspace',
      'staff',
      'departments',
      'operations',
    ]);
    expect(_ids('admin'), isNot(contains('sa_workspace')));
    expect(_ids('doctor'), isNot(contains('sa_workspace')));
    expect(_ids('doctor'), isNot(contains('assets')));
    expect(_ids('doctor'), isNot(contains('inventory')));
    for (final link in wd05AdministrationLinks(
      role: 'admin',
      clinicContextId: 7,
    )) {
      expect(link.path, isNot(contains(':')));
      expect(link.path, startsWith('/'));
    }
    expect(wd05PendingAdministrationCapabilities, isNotEmpty);
    expect(
      homeDestinationsFor(
        role: 'super_admin',
        clinicContextId: null,
      ).map((item) => item.id),
      contains('administration'),
    );
    expect(
      homeDestinationsFor(
        role: 'doctor',
        clinicContextId: 7,
      ).map((item) => item.id),
      contains('administration'),
    );
    expect(
      homeDestinationsFor(
        role: 'patient',
        clinicContextId: 7,
      ).map((item) => item.id),
      isNot(contains('administration')),
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
        '/administration/clinics',
        '/administration/sa-workspace',
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
