import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';
import 'package:clinic_system_pro/features/home/presentation/wd04_clinical_workflow_hub.dart';

AuthSessionState _ready(String role, {int? clinic = 7}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'unit-test-access',
        refreshToken: 'unit-test-refresh',
        userId: 43,
        role: role,
        clinicContextId: clinic,
      ),
    );

List<String> _ids(String role, {int? clinic = 7}) => wd04ClinicalWorkflowLinks(
  role: role,
  clinicContextId: clinic,
).map((link) => link.id).toList();

void main() {
  test(
    'WD04 role/clinic suggestion matrix fails closed without clinic context',
    () {
      for (final role in <String>[
        'admin',
        'doctor',
        'nurse',
        'receptionist',
        'pharmacist',
        'lab_technician',
        'driver',
        'ambulance_dispatcher',
        'paramedic',
        'emt',
        'patient',
        'super_admin',
        'unknown',
      ]) {
        expect(_ids(role, clinic: null), isEmpty, reason: role);
        expect(_ids(role, clinic: 0), isEmpty, reason: role);
        expect(_ids(role, clinic: -2), isEmpty, reason: role);
      }
      expect(_ids('patient'), isEmpty);
      expect(_ids('accountant'), isEmpty);
      expect(_ids('super_admin'), isEmpty);
      expect(_ids('not-a-role'), isEmpty);
    },
  );

  test('WD04 advanced modules map only to real safe route entry points', () {
    final doctor = wd04ClinicalWorkflowLinks(
      role: 'doctor',
      clinicContextId: 7,
    );
    final ids = doctor.map((entry) => entry.id).toSet();
    expect(
      ids,
      containsAll(<String>{
        'wards',
        'clinical_orders',
        'emar',
        'lab',
        'radiology',
        'surgery',
        'emergency',
      }),
    );
    expect(ids, isNot(contains('ambulance')));
    expect(ids, isNot(contains('pharmacy')));
    for (final link in doctor) {
      expect(link.path, startsWith('/'));
      expect(link.path, isNot(contains(':patientId')));
      expect(link.path, isNot(contains('/patient/7')));
      expect(link.path, isNot(contains('/cases/')));
    }
    expect(doctor.singleWhere((e) => e.id == 'emar').path, '/patients');
    expect(doctor.singleWhere((e) => e.id == 'lab').path, '/patients');
    expect(doctor.singleWhere((e) => e.id == 'radiology').path, '/radiology');
  });

  test(
    'WD04 laboratory, pharmacy and dispatch links never borrow doctor grants',
    () {
      expect(_ids('lab_technician'), contains('lab'));
      expect(_ids('lab_technician'), isNot(contains('clinical_orders')));
      expect(
        wd04ClinicalWorkflowLinks(
          role: 'lab_technician',
          clinicContextId: 7,
        ).singleWhere((entry) => entry.id == 'lab').path,
        '/clinical-orders/blood-products',
      );
      expect(
        _ids('pharmacist'),
        containsAll(<String>['pharmacy', 'dispensing']),
      );
      expect(_ids('pharmacist'), isNot(contains('emar')));
      expect(_ids('pharmacist'), isNot(contains('surgery')));
      expect(_ids('ambulance_dispatcher'), contains('ambulance'));
      expect(_ids('ambulance_dispatcher'), isNot(contains('wards')));
      expect(_ids('driver'), isNot(contains('emergency')));
      expect(_ids('receptionist'), contains('wards'));
      expect(_ids('receptionist'), isNot(contains('emar')));
      expect(_ids('nurse'), contains('emar'));
      expect(_ids('nurse'), isNot(contains('radiology')));
    },
  );

  test('WD04 home advertises entry only to staff with a clinic', () {
    List<String> homeIds(String role, {int? clinic = 7}) => homeDestinationsFor(
      role: role,
      clinicContextId: clinic,
    ).map((destination) => destination.id).toList();
    expect(homeIds('doctor'), contains('clinical_workflows'));
    expect(homeIds('pharmacist'), contains('clinical_workflows'));
    expect(homeIds('ambulance_dispatcher'), contains('clinical_workflows'));
    expect(
      homeIds('doctor', clinic: null),
      isNot(contains('clinical_workflows')),
    );
    expect(homeIds('patient'), isNot(contains('clinical_workflows')));
    expect(homeIds('super_admin'), isNot(contains('clinical_workflows')));
  });

  test('WD04 home preserves earlier patient-directory navigation order', () {
    final ids = homeDestinationsFor(
      role: 'doctor',
      clinicContextId: 7,
    ).map((destination) => destination.id).toList();
    expect(ids, contains('patients'));
    expect(ids, contains('clinical_workflows'));
    expect(
      ids.indexOf('clinical_workflows'),
      greaterThan(ids.indexOf('patients')),
    );
  });

  testWidgets(
    'WD04 hub dispatches a known URL only from verified live context',
    (tester) async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final opened = <String>[];
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Wd04ClinicalWorkflowHub(
              loadSession: () async => _ready('doctor'),
              sessionNavigationGuard: guard,
              onOpen: opened.add,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('wd04-clinical-workflows-hub')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('wd04-module-emar')), findsOneWidget);
      expect(find.byKey(const ValueKey('wd04-module-ambulance')), findsNothing);
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('wd04-module-radiology')),
        180,
        scrollable: find.byType(Scrollable).last,
      );
      await tester.ensureVisible(
        find.byKey(const ValueKey('wd04-module-radiology')),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('wd04-module-radiology')));
      await tester.pumpAndSettle();
      expect(opened, <String>['/radiology']);
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('wd04-hub-session-unavailable')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('wd04-module-radiology')), findsNothing);
      expect(opened, hasLength(1));
    },
  );

  testWidgets(
    'WD04 delayed session restore cannot remount after invalidation',
    (tester) async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final pending = Completer<AuthSessionState>();
      final opened = <String>[];
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Wd04ClinicalWorkflowHub(
              loadSession: () => pending.future,
              sessionNavigationGuard: guard,
              onOpen: opened.add,
            ),
          ),
        ),
      );
      expect(find.byKey(const ValueKey('wd04-hub-loading')), findsOneWidget);
      guard.invalidate();
      pending.complete(_ready('doctor'));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('wd04-hub-session-unavailable')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('wd04-module-surgery')), findsNothing);
      expect(opened, isEmpty);
    },
  );

  testWidgets('WD04 direct-link navigation denies anonymous sessions', (
    tester,
  ) async {
    final client = MockClient((request) async {
      throw StateError('Unexpected API call: ${request.url}');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
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
      loginScreen: const Scaffold(body: Center(child: Text('Login sentinel'))),
      sessionNavigationGuard: guard,
      loadHomeSession: () async => _ready('doctor'),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/clinical-workflows');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('wd04-clinical-workflows-hub')),
      findsNothing,
    );
  });
}
