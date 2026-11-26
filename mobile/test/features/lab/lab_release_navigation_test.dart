import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:go_router/go_router.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';
import 'package:clinic_system_pro/features/lab/presentation/lab_release_workspace_screen.dart';
import 'package:clinic_system_pro/features/notifications/data/notification_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';

import 'lab_release_staged_contract_test.dart' as f;

const roles = [
  'admin',
  'super_admin',
  'doctor',
  'nurse',
  'pharmacist',
  'lab_technician',
  'patient',
  'receptionist',
  'accountant',
  'paramedic',
  'emt',
  'ambulance_dispatcher',
  'ambulance_coordinator',
  'driver',
  'other',
  'unknown',
];
ApiClient apiFor(MockClient client) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  addTearDown(api.close);
  addTearDown(client.close);
  return api;
}

GoRouter routerFor(
  SessionNavigationGuard guard,
  ApiClient api,
  Future<AuthSessionState> Function() load,
) {
  final router = createAppRouter(
    healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
    loginScreen: const Scaffold(body: Text('Login sentinel')),
    dashboardDataSource: DashboardRemoteDataSource(
      apiClient: api,
      loadSession: load,
      sessionNavigationGuard: guard,
    ),
    patientAdmissionDataSource: PatientAdmissionRemoteDataSource(
      apiClient: api,
      loadSession: load,
      sessionNavigationGuard: guard,
    ),
    patientPrescriptionDataSource: PatientPrescriptionRemoteDataSource(
      apiClient: api,
      loadSession: load,
      sessionNavigationGuard: guard,
    ),
    sessionNavigationGuard: guard,
  );
  addTearDown(router.dispose);
  return router;
}

Map<String, dynamic> notification({
  String type = 'lab',
  String reference = 'released_lab_result',
  String channel = 'in_app',
  int user = 8,
  int clinic = 4,
  int? target = 9,
}) => {
  'id': 21,
  'user_id': user,
  'clinic_id': clinic,
  'title': 'A new record is available',
  'message': 'Sign in to view your new clinical record.',
  'notification_type': type,
  'priority': 'normal',
  'channel': channel,
  'status': 'pending',
  'is_read': false,
  'created_at': f.stamp,
  'read_at': null,
  'reference_type': reference,
  'reference_id': target,
};
Map<String, dynamic> order() => {
  'id': 9,
  'clinic_id': 4,
  'patient_id': 2,
  'consultation_id': null,
  'ordered_by_id': 18,
  'status': 'completed',
  'created_at': f.stamp,
  'items': [
    {
      'id': 17,
      'order_id': 9,
      'test_id': 3,
      'test': {
        'id': 3,
        'clinic_id': 4,
        'name': 'Synthetic test',
        'sample_type': 'blood',
        'is_active': true,
      },
      'result_value': '5',
      'flag': 'normal',
    },
  ],
};
void viewport(WidgetTester tester) {
  tester.view.physicalSize = const Size(1200, 1100);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
}

void main() {
  test('exact role and clinic navigation; lab precedes Blood Bank', () {
    for (final role in roles) {
      for (final clinic in <int?>[null, 0, 4]) {
        final all = homeDestinationsFor(role: role, clinicContextId: clinic);
        final links = all.where((d) => d.id == 'lab_release').toList();
        final allowed =
            clinic == 4 &&
            const {'doctor', 'lab_technician', 'patient'}.contains(role);
        expect(links.length, allowed ? 1 : 0, reason: '$role $clinic');
        if (allowed) {
          // BB integration subsequently appended one authorized destination.
          // Preserve the old lab position relative to all earlier entries,
          // and verify the exact new tail rather than relaxing the ordering.
          expect(all.map((d) => d.id).toList().sublist(all.length - 2), [
            'lab_release',
            role == 'patient' ? 'my_transfusions' : 'blood_bank',
          ]);
          expect(
            links.single.path,
            role == 'patient' ? '/patient-lab-results' : '/lab-release',
          );
        }
      }
    }
  });
  testWidgets('signed-out direct routes redirect before any HTTP', (
    tester,
  ) async {
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
    var calls = 0;
    final api = apiFor(
      MockClient((r) async {
        calls++;
        return f.receipt({});
      }),
    );
    final router = routerFor(guard, api, () async => f.session('patient'));
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    for (final path in [
      '/lab-release',
      '/lab-release/9',
      '/patient-lab-results',
      '/patient-lab-results/9',
    ]) {
      router.go(path);
      await tester.pumpAndSettle();
      expect(find.text('Login sentinel'), findsOneWidget);
    }
    expect(calls, 0);
  });
  for (final role in ['doctor', 'lab_technician', 'admin', 'patient']) {
    testWidgets('staff route role $role is separated from patient route', (
      tester,
    ) async {
      final guard = f.ready();
      var calls = 0;
      final api = apiFor(
        MockClient((r) async {
          calls++;
          return f.receipt({});
        }),
      );
      final router = routerFor(guard, api, () async => f.session(role));
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/lab-release');
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('lab-release-order')),
        const {'doctor', 'lab_technician'}.contains(role)
            ? findsOneWidget
            : findsNothing,
      );
      expect(calls, 0);
      if (role != 'patient') {
        router.go('/patient-lab-results/9');
        await tester.pumpAndSettle();
        expect(
          find.text('Laboratory release session unavailable.'),
          findsWidgets,
        );
        expect(calls, 0);
      }
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.text('Login sentinel'), findsOneWidget);
    });
  }
  testWidgets('patient list and direct detail only use patient-owned GETs', (
    tester,
  ) async {
    viewport(tester);
    final guard = f.ready();
    final paths = <String>[];
    final api = apiFor(
      MockClient((r) async {
        paths.add(r.url.path);
        expect(r.method, 'GET');
        return f.receipt(
          r.url.path.endsWith('/9') ? f.report(published: true) : f.page(),
        );
      }),
    );
    final router = routerFor(guard, api, () async => f.session('patient'));
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/patient-lab-results');
    await tester.pumpAndSettle();
    expect(find.text('My Lab Results'), findsOneWidget);
    expect(find.text('Laboratory report 9 · Version 1'), findsOneWidget);
    router.go('/patient-lab-results/9');
    await tester.pumpAndSettle();
    expect(find.text('Synthetic test'), findsOneWidget);
    expect(paths, [
      '/api/v1/lab/me/released-results',
      '/api/v1/lab/me/released-results/9',
    ]);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Synthetic test'), findsNothing);
  });
  testWidgets('invalid or query-bearing routes make no clinical calls', (
    tester,
  ) async {
    final guard = f.ready();
    var calls = 0;
    final api = apiFor(
      MockClient((r) async {
        calls++;
        return f.receipt({});
      }),
    );
    final router = routerFor(guard, api, () async => f.session('patient'));
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    for (final path in [
      '/patient-lab-results/0',
      '/patient-lab-results/09',
      '/patient-lab-results/2147483648',
      '/patient-lab-results/9?clinic_id=4',
    ]) {
      router.go(path);
      await tester.pumpAndSettle();
      expect(
        find.text('Laboratory release navigation unavailable.'),
        findsOneWidget,
      );
    }
    expect(calls, 0);
  });
  test('fresh notification reference allowlist and patient role', () async {
    final guard = f.ready();
    var role = 'patient', calls = 0;
    var row = notification();
    final api = apiFor(
      MockClient((r) async {
        calls++;
        expect(r.url.path, '/api/v1/notifications/21');
        return f.receipt(row);
      }),
    );
    final ds = NotificationRemoteDataSource(
      apiClient: api,
      loadSession: () async => f.session(role),
      sessionNavigationGuard: guard,
    );
    expect(await ds.releasedLabTarget(21), 9);
    for (final invalid in [
      notification(type: 'general'),
      notification(reference: 'lab_order'),
      notification(channel: 'push'),
      notification(target: null),
      notification(target: 2147483648),
    ]) {
      row = invalid;
      expect(await ds.releasedLabTarget(21), isNull);
    }
    role = 'doctor';
    expect(await ds.releasedLabTarget(21), isNull);
    expect(calls, 6);
  });
  test(
    'foreign notification and session change during read fail closed',
    () async {
      final guard = f.ready();
      var row = notification(user: 99);
      var user = 8;
      var change = false;
      final api = apiFor(
        MockClient((r) async {
          if (change) {
            user = 10;
          }
          return f.receipt(row);
        }),
      );
      final ds = NotificationRemoteDataSource(
        apiClient: api,
        loadSession: () async => f.session('patient', user: user),
        sessionNavigationGuard: guard,
      );
      await expectLater(ds.releasedLabTarget(21), throwsFormatException);
      row = notification();
      change = true;
      await expectLater(
        ds.releasedLabTarget(21),
        throwsA(isA<NotificationSessionUnavailable>()),
      );
    },
  );
  for (final withdrawn in [false, true]) {
    testWidgets(
      'notification rechecks owner then ${withdrawn ? "denies withdrawn" : "opens approved"} report',
      (tester) async {
        viewport(tester);
        final guard = f.ready();
        final paths = <String>[];
        final api = apiFor(
          MockClient((r) async {
            paths.add(r.url.path);
            expect(r.method, 'GET');
            if (r.url.path == '/api/v1/notifications/21') {
              return f.receipt(notification());
            }
            expect(r.url.path, '/api/v1/lab/me/released-results/9');
            return withdrawn
                ? http.Response('{"error":"unavailable"}', 404)
                : f.receipt(f.report(published: true));
          }),
        );
        final router = routerFor(guard, api, () async => f.session('patient'));
        await tester.pumpWidget(MaterialApp.router(routerConfig: router));
        router.go('/notifications/21');
        await tester.pumpAndSettle();
        final button = find.byKey(
          const ValueKey('notification-open-released-lab'),
        );
        expect(button, findsOneWidget);
        await tester.ensureVisible(button);
        await tester.tap(button);
        await tester.pumpAndSettle();
        expect(find.byType(LabReleaseWorkspaceScreen), findsOneWidget);
        expect(
          find.text('Synthetic test'),
          withdrawn ? findsNothing : findsOneWidget,
        );
        expect(paths, [
          '/api/v1/notifications/21',
          '/api/v1/notifications/21',
          '/api/v1/lab/me/released-results/9',
        ]);
        guard.invalidate();
        await tester.pumpAndSettle();
        expect(find.text('Synthetic test'), findsNothing);
      },
    );
  }
  testWidgets(
    'patient portal opens results without changing existing portal APIs',
    (tester) async {
      viewport(tester);
      final guard = f.ready();
      final paths = <String>[];
      final api = apiFor(
        MockClient((r) async {
          paths.add(r.url.path);
          expect(r.method, 'GET');
          if (r.url.path.endsWith('/me')) {
            return f.receipt({
              'first_name': 'Synthetic',
              'last_name': 'Patient',
            });
          }
          if (r.url.path == '/api/v1/lab/me/released-results') {
            return f.receipt(f.page());
          }
          expect(r.url.path.startsWith('/api/v1/patient-portal/'), isTrue);
          return f.receipt({'items': []});
        }),
      );
      final router = routerFor(guard, api, () async => f.session('patient'));
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/patient-portal');
      await tester.pumpAndSettle();
      final link = find.byKey(const ValueKey('patient-portal-lab-results'));
      expect(link, findsOneWidget);
      await tester.ensureVisible(link);
      await tester.tap(link);
      await tester.pumpAndSettle();
      expect(find.text('My Lab Results'), findsOneWidget);
      expect(paths.last, '/api/v1/lab/me/released-results');
      expect(paths.take(5), [
        '/api/v1/patient-portal/me',
        '/api/v1/patient-portal/clinicians',
        '/api/v1/patient-portal/appointments',
        '/api/v1/patient-portal/prescriptions',
        '/api/v1/patient-portal/invoices',
      ]);
    },
  );
  testWidgets(
    'staff existing order detail opens release status for same order',
    (tester) async {
      viewport(tester);
      final guard = f.ready();
      final paths = <String>[];
      final api = apiFor(
        MockClient((r) async {
          paths.add(r.url.path);
          expect(r.method, 'GET');
          if (r.url.path.endsWith('/patient-release')) {
            return f.receipt(f.status());
          }
          if (r.url.path.endsWith('/9')) return f.receipt(order());
          expect(r.url.path, '/api/v1/lab/orders');
          return f.receipt({
            'items': [order()],
            'total': 1,
            'page': 1,
            'per_page': 20,
          });
        }),
      );
      final router = routerFor(guard, api, () async => f.session('doctor'));
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/patients/2/lab-orders');
      await tester.pumpAndSettle();
      final detail = find.byKey(const ValueKey('lab-view-detail-9'));
      await tester.ensureVisible(detail);
      await tester.tap(detail);
      await tester.pumpAndSettle();
      final link = find.byKey(const ValueKey('lab-open-patient-release'));
      expect(link, findsOneWidget);
      await tester.ensureVisible(link);
      await tester.tap(link);
      await tester.pumpAndSettle();
      expect(find.text('Order 9 · unreleased · Version 0'), findsOneWidget);
      expect(paths.last, '/api/v1/lab/orders/9/patient-release');
    },
  );
}
