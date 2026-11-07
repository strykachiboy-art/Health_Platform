import 'dart:async';
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
import 'package:clinic_system_pro/features/appointments/data/appointment_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_list_screen.dart';

AuthSessionState _ready({String role = 'doctor', int clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 's02-token',
        refreshToken: 's02-refresh',
        userId: 42,
        clinicContextId: clinicId,
        role: role,
      ),
    );

Map<String, dynamic> _row({
  int id = 11,
  int patientId = 7,
  int staffId = 8,
  int clinicId = 3,
  String status = 'scheduled',
}) => {
  'id': id,
  'clinic_id': clinicId,
  'patient_id': patientId,
  'staff_id': staffId,
  'scheduled_start': '2026-11-08T09:00:00',
  'scheduled_end': '2026-11-08T09:30:00',
  'status': status,
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

Map<String, dynamic> _page({
  List<Map<String, dynamic>>? rows,
  int? total,
  int page = 1,
  int perPage = 20,
}) {
  final items = rows ?? [_row()];
  final count = total ?? items.length;
  final pages = (count + perPage - 1) ~/ perPage;
  return {
    'success': true,
    'data': {
      'items': items,
      'total': count,
      'page': page,
      'per_page': perPage,
      'pages': pages,
      'has_next': page < pages,
      'has_prev': page > 1,
    },
  };
}

AppointmentRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
}) => AppointmentRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => _ready(role: role),
  sessionNavigationGuard: guard,
);

Widget _host(
  AppointmentRemoteDataSource source, {
  int? patientId,
  int? staffId,
  int pageSize = 20,
}) => MaterialApp(
  home: AppointmentListScreen(
    dataSource: source,
    patientId: patientId,
    staffId: staffId,
    pageSize: pageSize,
  ),
);

Future<void> _show(WidgetTester tester, String key) async {
  await tester.scrollUntilVisible(
    find.byKey(ValueKey(key)),
    180,
    maxScrolls: 35,
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('patient list opens read-only appointment details', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/appointments/patient/7');
      expect(request.url.queryParameters, {'page': '1', 'per_page': '20'});
      expect(request.headers['authorization'], 'Bearer s02-token');
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard), patientId: 7));
    await tester.pumpAndSettle();

    expect(find.text('Appointment #11'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('appointments-filter-scope-warning')),
      findsOneWidget,
    );
    await tester.tap(find.byKey(const ValueKey('appointment-row-11')));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('appointment-detail-11')), findsOneWidget);
    expect(find.text('Patient ID: 7'), findsOneWidget);
    expect(find.text('Staff ID: 8'), findsOneWidget);
    expect(find.text('Reason: Follow-up'), findsOneWidget);
    expect(
      find.text('Scheduled start (backend): 2026-11-08T09:00:00'),
      findsOneWidget,
    );
    expect(calls, 1); // Detail uses verified list record, no invented GET.
  });

  testWidgets('staff date filter sends an explicit server date query', (
    tester,
  ) async {
    final dates = <String?>[];
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/appointments/staff/8');
      dates.add(request.url.queryParameters['date']);
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(
      _host(_source(client, guard, role: 'receptionist'), staffId: 8),
    );
    await tester.pumpAndSettle();

    await tester.enterText(
      find.byKey(const ValueKey('appointments-staff-date')),
      '2026-11-08',
    );
    await tester.tap(find.byKey(const ValueKey('appointments-apply-date')));
    await tester.pumpAndSettle();

    expect(dates, <String?>[null, '2026-11-08']);
    expect(find.text('Appointment #11'), findsOneWidget);
  });

  testWidgets('staff invalid date does not send a GET', (tester) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard), staffId: 8));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('appointments-staff-date')),
      '2026-02-30',
    );
    await tester.tap(find.byKey(const ValueKey('appointments-apply-date')));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointments-date-error')),
      findsOneWidget,
    );
    expect(calls, 1);
  });

  testWidgets('load-more combines distinct paginated records', (tester) async {
    final requested = <String>[];
    final client = MockClient((request) async {
      requested.add(request.url.queryParameters['page']!);
      final page = int.parse(request.url.queryParameters['page']!);
      return http.Response(
        jsonEncode(
          _page(
            rows: [_row(id: page == 1 ? 11 : 22)],
            total: 2,
            page: page,
            perPage: 1,
          ),
        ),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(
      _host(_source(client, guard), patientId: 7, pageSize: 1),
    );
    await tester.pumpAndSettle();
    expect(find.text('Appointment #11'), findsOneWidget);

    await _show(tester, 'appointments-load-more');
    await tester.tap(find.byKey(const ValueKey('appointments-load-more')));
    await tester.pumpAndSettle();
    expect(requested, <String>['1', '2']);
    expect(find.text('Loaded 2 of 2 backend records'), findsOneWidget);
    expect(find.text('Appointment #22'), findsOneWidget);
  });

  testWidgets('status filter is local-only and labels partial records', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(
        jsonEncode(
          _page(
            rows: [
              _row(id: 11),
              _row(id: 22, status: 'confirmed'),
            ],
          ),
        ),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard), patientId: 7));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('appointments-status-filter')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Confirmed').last);
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('appointment-row-11')), findsNothing);
    expect(find.byKey(const ValueKey('appointment-row-22')), findsOneWidget);
    expect(
      find.byKey(const ValueKey('appointments-filter-scope-warning')),
      findsOneWidget,
    );
    expect(calls, 1);
  });

  testWidgets('empty backend page is not interpreted as no bookings', (
    tester,
  ) async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page(rows: [], total: 0)), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard), patientId: 7));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('appointments-empty')), findsOneWidget);
    expect(find.text('Loaded 0 of 0 backend records'), findsOneWidget);
  });

  testWidgets('HTTP 403 shows denial without exposing appointment PHI', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{"error":"Forbidden"}', 403);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard), patientId: 7));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('appointments-error')), findsOneWidget);
    expect(find.byKey(const ValueKey('appointment-row-11')), findsNothing);
    expect(calls, 1);
  });

  testWidgets('wrong-clinic backend records fail closed', (tester) async {
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode(_page(rows: [_row(clinicId: 9)])), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard), patientId: 7));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('appointments-error')), findsOneWidget);
    expect(find.byKey(const ValueKey('appointment-row-11')), findsNothing);
  });

  testWidgets('session invalidation clears detail and underlying list', (
    tester,
  ) async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page()), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard), patientId: 7));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('appointment-row-11')));
    await tester.pumpAndSettle();
    expect(find.text('Patient ID: 7'), findsOneWidget);

    guard.invalidate();
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointment-detail-session-error')),
      findsOneWidget,
    );
    expect(find.text('Patient ID: 7', skipOffstage: false), findsNothing);

    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointments-session-error')),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('appointment-row-11')), findsNothing);
  });

  testWidgets('session invalidation rejects a late in-flight GET', (
    tester,
  ) async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard), patientId: 7));
    await tester.pump();
    await started.future;
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_page()), 200));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('appointments-session-error')),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('appointment-row-11')), findsNothing);
  });

  testWidgets('patient switch discards old pending appointment response', (
    tester,
  ) async {
    final oldStarted = Completer<void>();
    final pending = Completer<http.Response>();
    final client = MockClient((request) {
      if (request.url.path == '/api/v1/appointments/patient/7') {
        oldStarted.complete();
        return pending.future;
      }
      expect(request.url.path, '/api/v1/appointments/patient/8');
      return Future.value(
        http.Response(
          jsonEncode(_page(rows: [_row(id: 22, patientId: 8)])),
          200,
        ),
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);

    await tester.pumpWidget(_host(source, patientId: 7));
    await tester.pump();
    await oldStarted.future;
    await tester.pumpWidget(_host(source, patientId: 8));
    await tester.pumpAndSettle();
    expect(find.text('Appointment #22'), findsOneWidget);
    expect(find.text('Appointment #11'), findsNothing);

    pending.complete(http.Response(jsonEncode(_page()), 200));
    await tester.pumpAndSettle();
    expect(find.text('Appointment #22'), findsOneWidget);
    expect(find.text('Appointment #11'), findsNothing);
  });

  testWidgets('duplicate second page clears stale first-page records', (
    tester,
  ) async {
    final client = MockClient((request) async {
      final page = int.parse(request.url.queryParameters['page']!);
      return http.Response(
        jsonEncode(
          _page(rows: [_row(id: 11)], total: 2, page: page, perPage: 1),
        ),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(
      _host(_source(client, guard), patientId: 7, pageSize: 1),
    );
    await tester.pumpAndSettle();
    expect(find.text('Appointment #11'), findsOneWidget);
    await _show(tester, 'appointments-load-more');
    await tester.tap(find.byKey(const ValueKey('appointments-load-more')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('appointments-error')), findsOneWidget);
    expect(find.text('Appointment #11'), findsNothing);
  });
}
