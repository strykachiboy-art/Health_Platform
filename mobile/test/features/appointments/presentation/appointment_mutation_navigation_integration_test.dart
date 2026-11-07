import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/data/models/appointment_record.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_mutation_screen.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

AuthSessionState _ready({String role = 'DOCTOR', int clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 's04cb-token',
        refreshToken: 's04cb-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinicId,
      ),
    );

Map<String, dynamic> _record({
  int id = 9,
  String status = 'scheduled',
  String start = '2026-12-10T09:00:00Z',
  String end = '2026-12-10T10:00:00Z',
  String? cancelledAt,
  String? cancellationReason,
}) => <String, dynamic>{
  'id': id,
  'clinic_id': 3,
  'patient_id': 7,
  'staff_id': 11,
  'scheduled_start': start,
  'scheduled_end': end,
  'status': status,
  'appointment_type': 'in_person',
  'reason': null,
  'notes': null,
  'google_calendar_event_id': null,
  'reminder_sent': false,
  'created_at': '2026-12-01T10:00:00Z',
  'updated_at': '2026-12-01T10:00:00Z',
  'cancelled_at': cancelledAt,
  'cancellation_reason': cancellationReason,
};

http.Response _ok(Map<String, dynamic> data) => http.Response(
  jsonEncode(<String, dynamic>{'success': true, 'data': data}),
  200,
);

http.Response _page({String status = 'scheduled'}) => _ok(<String, dynamic>{
  'items': <Map<String, dynamic>>[_record(status: status)],
  'total': 1,
  'page': 1,
  'per_page': 20,
  'pages': 1,
  'has_next': false,
  'has_prev': false,
});

class _Rig {
  _Rig(
    Future<http.Response> Function(http.Request) handler, {
    this.includeSource = true,
    this.includeLoader = true,
    this.mismatchGuard = false,
    this.signedOut = false,
  }) : guard = SessionNavigationGuard(
         protectedRoutePrefixes: const <String>['/appointments'],
       ),
       otherGuard = SessionNavigationGuard(
         protectedRoutePrefixes: const <String>['/appointments'],
       ),
       client = MockClient(handler) {
    if (signedOut) {
      guard.markSignedOut();
    } else {
      guard.markSessionAvailable();
    }
    otherGuard.markSessionAvailable();
    api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    Future<AuthSessionState> load() async =>
        _ready(role: 'DOCTOR', clinicId: 3);
    router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Center(child: Text('Login sentinel'))),
      loadHomeSession: includeLoader ? load : null,
      appointmentDataSource: includeSource
          ? AppointmentRemoteDataSource(
              apiClient: api,
              loadSession: load,
              sessionNavigationGuard: mismatchGuard ? otherGuard : guard,
            )
          : null,
      sessionNavigationGuard: guard,
    );
  }

  final bool includeSource;
  final bool includeLoader;
  final bool mismatchGuard;
  final bool signedOut;
  final SessionNavigationGuard guard;
  final SessionNavigationGuard otherGuard;
  final MockClient client;
  late final ApiClient api;
  late final GoRouter router;

  Widget widget() => MaterialApp.router(routerConfig: router);

  void close() {
    router.dispose();
    guard.dispose();
    otherGuard.dispose();
    client.close();
  }
}

Future<void> _openChange(
  WidgetTester tester,
  _Rig rig, {
  bool staff = false,
}) async {
  await tester.pumpWidget(rig.widget());
  rig.router.go(staff ? '/appointments/staff/11' : '/appointments/patient/7');
  await tester.pumpAndSettle();
  final change = find.byKey(const ValueKey('appointment-change-9'));
  expect(change, findsOneWidget);
  await tester.ensureVisible(change);
  await tester.tap(change);
  await tester.pumpAndSettle();
  expect(find.byType(AppointmentMutationScreen), findsOneWidget);
  expect(find.byKey(const ValueKey('s04c-appointment-id')), findsOneWidget);
}

Future<void> _tap(WidgetTester tester, String key) async {
  final finder = find.byKey(ValueKey(key));
  await tester.ensureVisible(finder);
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
    'patient list opens change screen from verified server row without POST',
    (tester) async {
      var gets = 0;
      var posts = 0;
      final rig = _Rig((request) async {
        if (request.method == 'GET') {
          gets++;
          expect(request.url.path, '/api/v1/appointments/patient/7');
          return _page();
        }
        posts++;
        throw StateError('Unexpected appointment mutation.');
      });
      addTearDown(rig.close);
      await _openChange(tester, rig);
      expect(gets, 1);
      expect(posts, 0);
    },
  );

  testWidgets('staff schedule opens the same controlled mutation screen', (
    tester,
  ) async {
    final rig = _Rig((request) async {
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/appointments/staff/11');
      return _page();
    });
    addTearDown(rig.close);
    await _openChange(tester, rig, staff: true);
  });

  testWidgets(
    'confirmed cancellation posts once without idempotency header or queue',
    (tester) async {
      var posts = 0;
      final rig = _Rig((request) async {
        if (request.method == 'GET') return _page();
        posts++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/appointments/9/cancel');
        expect(request.headers['authorization'], 'Bearer s04cb-token');
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        expect(jsonDecode(request.body), <String, dynamic>{});
        return _ok(
          _record(status: 'cancelled', cancelledAt: '2026-12-11T11:00:00Z'),
        );
      });
      addTearDown(rig.close);
      await _openChange(tester, rig);
      await _tap(tester, 's04c-acknowledge');
      await _tap(tester, 's04c-cancel');
      expect(find.byKey(const ValueKey('s04c-confirmed')), findsOneWidget);
      expect(posts, 1);
    },
  );

  testWidgets('confirmed reschedule uses one POST and validated server times', (
    tester,
  ) async {
    var posts = 0;
    final rig = _Rig((request) async {
      if (request.method == 'GET') return _page();
      posts++;
      expect(request.method, 'POST');
      expect(request.url.path, '/api/v1/appointments/9/reschedule');
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      return _ok(
        _record(start: '2026-12-11T09:00:00Z', end: '2026-12-11T10:00:00Z'),
      );
    });
    addTearDown(rig.close);
    await _openChange(tester, rig);
    await tester.enterText(
      find.byKey(const ValueKey('s04c-start')),
      '2026-12-11T09:00:00Z',
    );
    await tester.enterText(
      find.byKey(const ValueKey('s04c-end')),
      '2026-12-11T10:00:00Z',
    );
    await _tap(tester, 's04c-acknowledge');
    await _tap(tester, 's04c-reschedule');
    expect(find.byKey(const ValueKey('s04c-confirmed')), findsOneWidget);
    expect(posts, 1);
  });

  for (final status in <String>['cancelled', 'completed', 'no_show']) {
    testWidgets('non-editable $status row has no mutation action', (
      tester,
    ) async {
      var posts = 0;
      final rig = _Rig((request) async {
        if (request.method == 'GET') return _page(status: status);
        posts++;
        throw StateError('Mutation should be unavailable.');
      });
      addTearDown(rig.close);
      await tester.pumpWidget(rig.widget());
      rig.router.go('/appointments/patient/7');
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('appointment-change-9')), findsNothing);
      expect(posts, 0);
    });
  }

  for (final extra in <Object?>[
    null,
    'not-an-appointment',
    9,
    AppointmentRecord.fromJson(
      _record(),
      expectedClinicId: 3,
      expectedPatientId: 7,
    ),
  ]) {
    testWidgets(
      'direct change deep link without verified record fails closed: $extra',
      (tester) async {
        var requests = 0;
        final rig = _Rig((_) async {
          requests++;
          throw StateError('Unexpected request.');
        });
        addTearDown(rig.close);
        await tester.pumpWidget(rig.widget());
        rig.router.go('/appointments/change', extra: extra);
        await tester.pumpAndSettle();
        expect(find.text('Appointment route is unavailable.'), findsOneWidget);
        expect(find.byType(AppointmentMutationScreen), findsNothing);
        expect(requests, 0);
      },
    );
  }

  testWidgets('missing appointment source makes patient route unavailable', (
    tester,
  ) async {
    var calls = 0;
    final rig = _Rig((_) async {
      calls++;
      throw StateError('No HTTP expected');
    }, includeSource: false);
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    rig.router.go('/appointments/patient/7');
    await tester.pumpAndSettle();
    expect(find.text('Appointment route is unavailable.'), findsOneWidget);
    expect(calls, 0);
  });

  testWidgets('missing session loader does not grant change action', (
    tester,
  ) async {
    final rig = _Rig((request) async {
      expect(request.method, 'GET');
      return _page();
    }, includeLoader: false);
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    rig.router.go('/appointments/patient/7');
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('appointment-row-9')), findsOneWidget);
    expect(find.byKey(const ValueKey('appointment-change-9')), findsNothing);
  });

  testWidgets('guard mismatch removes mutation action from patient list', (
    tester,
  ) async {
    var requests = 0;
    final rig = _Rig((_) async {
      requests++;
      throw StateError('Unexpected GET or POST');
    }, mismatchGuard: true);
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    rig.router.go('/appointments/patient/7');
    await tester.pumpAndSettle();
    expect(find.text('Appointment route is unavailable.'), findsOneWidget);
    expect(requests, 0);
  });

  testWidgets('signed-out deep link redirects without network', (tester) async {
    var requests = 0;
    final rig = _Rig((_) async {
      requests++;
      throw StateError('Unexpected HTTP');
    }, signedOut: true);
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    rig.router.go('/appointments/change');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(requests, 0);
  });

  testWidgets('logout after opening mutation clears the protected screen', (
    tester,
  ) async {
    final rig = _Rig((request) async {
      expect(request.method, 'GET');
      return _page();
    });
    addTearDown(rig.close);
    await _openChange(tester, rig);
    rig.guard.markSignedOut();
    await tester.pumpAndSettle();
    expect(find.byType(AppointmentMutationScreen), findsNothing);
    expect(find.text('Login sentinel'), findsOneWidget);
  });

  testWidgets(
    'uncertain outcome cannot be resent after leaving and reopening route',
    (tester) async {
      var gets = 0;
      var posts = 0;
      final rig = _Rig((request) async {
        if (request.method == 'GET') {
          gets++;
          return _page();
        }
        posts++;
        return http.Response('{"success":false}', 503);
      });
      addTearDown(rig.close);
      await _openChange(tester, rig);
      await _tap(tester, 's04c-acknowledge');
      await _tap(tester, 's04c-cancel');
      expect(find.byKey(const ValueKey('s04c-uncertain')), findsOneWidget);
      expect(posts, 1);

      rig.router.go('/appointments/patient/7');
      await tester.pumpAndSettle();
      await _tap(tester, 'appointment-change-9');
      await _tap(tester, 's04c-acknowledge');
      await _tap(tester, 's04c-cancel');
      expect(posts, 1);
      expect(find.byKey(const ValueKey('s04c-uncertain')), findsOneWidget);
      expect(gets, 1);
    },
  );
}
