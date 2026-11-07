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
import 'package:clinic_system_pro/features/appointments/data/appointment_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/data/models/appointment_record.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_mutation_screen.dart';

const _newStart = '2026-12-11T09:00:00Z';
const _newEnd = '2026-12-11T10:00:00Z';

Map<String, dynamic> _row({
  int id = 9,
  int clinic = 3,
  int patient = 7,
  int staff = 11,
  String status = 'scheduled',
  String start = '2026-12-10T09:00:00Z',
  String end = '2026-12-10T10:00:00Z',
  String? cancelledAt,
  String? cancellationReason,
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'staff_id': staff,
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

AppointmentRecord _record({String status = 'scheduled'}) =>
    AppointmentRecord.fromJson(
      _row(status: status),
      expectedClinicId: 3,
      expectedPatientId: 7,
    );

http.Response _ok(Map<String, dynamic> data) => http.Response(
  jsonEncode(<String, dynamic>{'success': true, 'data': data}),
  200,
);

http.Response _page(Map<String, dynamic> record) => _ok(<String, dynamic>{
  'items': <Map<String, dynamic>>[record],
  'total': 1,
  'page': 1,
  'per_page': 100,
  'pages': 1,
  'has_next': false,
  'has_prev': false,
});

AuthSessionState _session({String role = 'DOCTOR', int? clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 's04c-token',
        refreshToken: 's04c-refresh',
        userId: 42,
        clinicContextId: clinic,
        role: role,
      ),
    );

class _Rig {
  _Rig(
    Future<http.Response> Function(http.Request) handler, {
    String role = 'DOCTOR',
    int? clinic = 3,
    bool signedOut = false,
    AppointmentRecord? appointment,
    SessionNavigationGuard? alternateReadGuard,
  }) : guard = SessionNavigationGuard(
         protectedRoutePrefixes: const <String>['/appointments'],
       ),
       record = appointment ?? _record() {
    if (signedOut) {
      guard.markSignedOut();
    } else {
      guard.markSessionAvailable();
    }
    client = MockClient(handler);
    api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
      requestTimeout: const Duration(seconds: 1),
    );
    Future<AuthSessionState> loadSession() async =>
        _session(role: role, clinic: clinic);
    mutation = AppointmentMutationRemoteDataSource(
      apiClient: api,
      loadSession: loadSession,
      sessionNavigationGuard: guard,
    );
    read = AppointmentRemoteDataSource(
      apiClient: api,
      loadSession: loadSession,
      sessionNavigationGuard: alternateReadGuard ?? guard,
    );
  }

  final SessionNavigationGuard guard;
  final AppointmentRecord record;
  late final MockClient client;
  late final ApiClient api;
  late final AppointmentMutationRemoteDataSource mutation;
  late final AppointmentRemoteDataSource read;

  Widget widget() => MaterialApp(
    home: AppointmentMutationScreen(
      appointment: record,
      mutationDataSource: mutation,
      readDataSource: read,
    ),
  );

  void close() {
    client.close();
    guard.dispose();
  }
}

Future<void> _tap(WidgetTester tester, String key) async {
  final finder = find.byKey(ValueKey(key));
  await tester.ensureVisible(finder);
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

Future<void> _consent(WidgetTester tester) => _tap(tester, 's04c-acknowledge');

Future<void> _dates(
  WidgetTester tester, {
  String start = _newStart,
  String end = _newEnd,
}) async {
  await tester.enterText(find.byKey(const ValueKey('s04c-start')), start);
  await tester.enterText(find.byKey(const ValueKey('s04c-end')), end);
  await tester.pump();
}

void main() {
  testWidgets(
    'create-route UI cannot send without explicit user confirmation',
    (tester) async {
      var requests = 0;
      final rig = _Rig((_) async {
        requests++;
        return _ok(_row());
      });
      addTearDown(rig.close);
      await tester.pumpWidget(rig.widget());
      await tester.pumpAndSettle();
      await _dates(tester);
      await _tap(tester, 's04c-reschedule');
      await _tap(tester, 's04c-cancel');
      expect(requests, 0);
      expect(find.textContaining('single-send'), findsOneWidget);
    },
  );

  testWidgets(
    'reschedule sends once, validates server acknowledgement, locks further action',
    (tester) async {
      var posts = 0;
      final rig = _Rig((request) async {
        posts++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/appointments/9/reschedule');
        expect(request.headers['authorization'], 'Bearer s04c-token');
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        expect(jsonDecode(request.body), <String, dynamic>{
          'scheduled_start': DateTime.parse(_newStart).toIso8601String(),
          'scheduled_end': DateTime.parse(_newEnd).toIso8601String(),
        });
        return _ok(_row(start: _newStart, end: _newEnd));
      });
      addTearDown(rig.close);
      await tester.pumpWidget(rig.widget());
      await tester.pumpAndSettle();
      await _dates(tester);
      await _consent(tester);
      await _tap(tester, 's04c-reschedule');
      expect(find.byKey(const ValueKey('s04c-confirmed')), findsOneWidget);
      await _tap(tester, 's04c-cancel');
      expect(posts, 1);
    },
  );

  testWidgets(
    'cancel is online only, reason trimmed, and acknowledgement required',
    (tester) async {
      var posts = 0;
      final rig = _Rig((request) async {
        posts++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/appointments/9/cancel');
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        expect(jsonDecode(request.body), <String, dynamic>{
          'cancellation_reason': 'Patient requested',
        });
        return _ok(
          _row(
            status: 'cancelled',
            cancelledAt: '2026-12-10T12:00:00Z',
            cancellationReason: 'Patient requested',
          ),
        );
      });
      addTearDown(rig.close);
      await tester.pumpWidget(rig.widget());
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const ValueKey('s04c-reason')),
        '  Patient requested  ',
      );
      await _consent(tester);
      await _tap(tester, 's04c-cancel');
      expect(find.byKey(const ValueKey('s04c-confirmed')), findsOneWidget);
      expect(posts, 1);
    },
  );

  testWidgets('no timezone in schedule is rejected without network', (
    tester,
  ) async {
    var posts = 0;
    final rig = _Rig((_) async {
      posts++;
      return _ok(_row());
    });
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await tester.pumpAndSettle();
    await _dates(tester, start: '2026-12-11T09:00:00', end: _newEnd);
    await _consent(tester);
    await _tap(tester, 's04c-reschedule');
    expect(find.textContaining('explicit timezone'), findsOneWidget);
    expect(posts, 0);
  });

  testWidgets('end before start is rejected without HTTP', (tester) async {
    var posts = 0;
    final rig = _Rig((_) async {
      posts++;
      return _ok(_row());
    });
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await tester.pumpAndSettle();
    await _dates(tester, start: _newEnd, end: _newStart);
    await _consent(tester);
    await _tap(tester, 's04c-reschedule');
    expect(posts, 0);
    expect(find.byKey(const ValueKey('s04c-message')), findsOneWidget);
  });

  testWidgets('server 409 rejects, no second mutation from same snapshot', (
    tester,
  ) async {
    var posts = 0;
    final rig = _Rig((_) async {
      posts++;
      return http.Response('{"success":false,"error":"conflict"}', 409);
    });
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await tester.pumpAndSettle();
    await _consent(tester);
    await _tap(tester, 's04c-cancel');
    expect(find.textContaining('Server rejected'), findsOneWidget);
    await _tap(tester, 's04c-cancel');
    expect(posts, 1);
  });

  testWidgets(
    'connection loss makes outcome uncertain and recheck is explicit GET only',
    (tester) async {
      var posts = 0;
      var reads = 0;
      final rig = _Rig((request) async {
        if (request.method == 'POST') {
          posts++;
          throw http.ClientException('disconnected');
        }
        reads++;
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/appointments/patient/7');
        expect(request.url.queryParameters['page'], '1');
        return _page(
          _row(status: 'cancelled', cancelledAt: '2026-12-10T12:00:00Z'),
        );
      });
      addTearDown(rig.close);
      await tester.pumpWidget(rig.widget());
      await tester.pumpAndSettle();
      await _consent(tester);
      await _tap(tester, 's04c-cancel');
      expect(find.byKey(const ValueKey('s04c-uncertain')), findsOneWidget);
      expect(posts, 1);
      expect(reads, 0);
      await _tap(tester, 's04c-recheck');
      expect(reads, 1);
      expect(find.textContaining('Attribution is not proven'), findsOneWidget);
      expect(find.byKey(const ValueKey('s04c-uncertain')), findsOneWidget);
      await _tap(tester, 's04c-cancel');
      expect(posts, 1);
    },
  );

  testWidgets('recheck mismatch cannot claim cancellation and never retries', (
    tester,
  ) async {
    var posts = 0;
    final rig = _Rig((request) async {
      if (request.method == 'POST') {
        posts++;
        return http.Response('server unavailable', 503);
      }
      return _page(_row());
    });
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await tester.pumpAndSettle();
    await _consent(tester);
    await _tap(tester, 's04c-cancel');
    await _tap(tester, 's04c-recheck');
    expect(find.textContaining('Outcome remains uncertain'), findsOneWidget);
    expect(find.byKey(const ValueKey('s04c-confirmed')), findsNothing);
    expect(posts, 1);
  });

  testWidgets(
    'recheck read 403 does not expose backend error or unblock action',
    (tester) async {
      final rig = _Rig((request) async {
        if (request.method == 'POST') {
          throw http.ClientException('offline');
        }
        return http.Response('{"error":"SECRET_BACKEND_ERROR"}', 403);
      });
      addTearDown(rig.close);
      await tester.pumpWidget(rig.widget());
      await tester.pumpAndSettle();
      await _consent(tester);
      await _tap(tester, 's04c-cancel');
      await _tap(tester, 's04c-recheck');
      expect(find.textContaining('Unable to obtain'), findsOneWidget);
      expect(find.textContaining('SECRET_BACKEND_ERROR'), findsNothing);
      expect(find.byKey(const ValueKey('s04c-uncertain')), findsOneWidget);
    },
  );

  testWidgets(
    'malformed HTTP 200 is uncertain; cannot claim booked or cancelled',
    (tester) async {
      var posts = 0;
      final rig = _Rig((_) async {
        posts++;
        return http.Response('{"success":true,"data":{"id":9}}', 200);
      });
      addTearDown(rig.close);
      await tester.pumpWidget(rig.widget());
      await tester.pumpAndSettle();
      await _consent(tester);
      await _tap(tester, 's04c-cancel');
      expect(find.byKey(const ValueKey('s04c-uncertain')), findsOneWidget);
      expect(find.byKey(const ValueKey('s04c-confirmed')), findsNothing);
      expect(posts, 1);
    },
  );

  testWidgets(
    'signed-out deep link hides appointment identity and never sends',
    (tester) async {
      var posts = 0;
      final rig = _Rig((_) async {
        posts++;
        return _ok(_row());
      }, signedOut: true);
      addTearDown(rig.close);
      await tester.pumpWidget(rig.widget());
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('s04c-session-unavailable')),
        findsOneWidget,
      );
      expect(find.text('Appointment #9'), findsNothing);
      expect(find.byKey(const ValueKey('s04c-cancel')), findsNothing);
      expect(posts, 0);
    },
  );

  testWidgets(
    'logout during in-flight POST clears appointment and discards late acknowledgement',
    (tester) async {
      final response = Completer<http.Response>();
      var posts = 0;
      final rig = _Rig((_) {
        posts++;
        return response.future;
      });
      addTearDown(rig.close);
      await tester.pumpWidget(rig.widget());
      await tester.pumpAndSettle();
      await _consent(tester);
      final button = find.byKey(const ValueKey('s04c-cancel'));
      await tester.ensureVisible(button);
      await tester.tap(button);
      await tester.pump();
      rig.guard.markSignedOut();
      response.complete(
        _ok(_row(status: 'cancelled', cancelledAt: '2026-12-10T12:00:00Z')),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('s04c-session-unavailable')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('s04c-confirmed')), findsNothing);
      expect(find.text('Appointment #9'), findsNothing);
      expect(posts, 1);
    },
  );

  for (final role in <String>['PATIENT', 'PHARMACIST', 'SUPER_ADMIN']) {
    testWidgets('unsupported role $role cannot send via mutation adapter', (
      tester,
    ) async {
      var posts = 0;
      final rig = _Rig((_) async {
        posts++;
        return _ok(_row());
      }, role: role);
      addTearDown(rig.close);
      await tester.pumpWidget(rig.widget());
      await tester.pumpAndSettle();
      await _consent(tester);
      await _tap(tester, 's04c-cancel');
      expect(posts, 0);
      expect(find.byKey(const ValueKey('s04c-confirmed')), findsNothing);
    });
  }

  testWidgets('missing clinic context cannot send', (tester) async {
    var posts = 0;
    final rig = _Rig((_) async {
      posts++;
      return _ok(_row());
    }, clinic: null);
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await tester.pumpAndSettle();
    await _consent(tester);
    await _tap(tester, 's04c-cancel');
    expect(posts, 0);
  });

  testWidgets('mismatched read/mutation guards hide all appointment content', (
    tester,
  ) async {
    var posts = 0;
    final other = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/appointments'],
    )..markSessionAvailable();
    addTearDown(other.dispose);
    final rig = _Rig((_) async {
      posts++;
      return _ok(_row());
    }, alternateReadGuard: other);
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('s04c-session-unavailable')),
      findsOneWidget,
    );
    expect(find.text('Appointment #9'), findsNothing);
    expect(posts, 0);
  });

  for (final status in <String>['cancelled', 'completed', 'no_show']) {
    testWidgets('terminal status $status offers no mutation action', (
      tester,
    ) async {
      var posts = 0;
      final rig = _Rig((_) async {
        posts++;
        return _ok(_row());
      }, appointment: _record(status: status));
      addTearDown(rig.close);
      await tester.pumpWidget(rig.widget());
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('s04c-ineligible')), findsOneWidget);
      expect(find.byKey(const ValueKey('s04c-cancel')), findsNothing);
      expect(posts, 0);
    });
  }
}
