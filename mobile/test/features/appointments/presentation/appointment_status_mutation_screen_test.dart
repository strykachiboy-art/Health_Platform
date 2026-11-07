import 'dart:async';
import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/data/models/appointment_record.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_mutation_screen.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

Map<String, dynamic> _row({
  String status = 'scheduled',
  String? notes,
  int clinic = 3,
  int id = 9,
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'patient_id': 7,
  'staff_id': 11,
  'scheduled_start': '2026-12-10T09:00:00Z',
  'scheduled_end': '2026-12-10T10:00:00Z',
  'status': status,
  'appointment_type': 'in_person',
  'reason': null,
  'notes': notes,
  'google_calendar_event_id': null,
  'reminder_sent': false,
  'created_at': '2026-12-01T10:00:00Z',
  'updated_at': '2026-12-01T10:00:00Z',
  'cancelled_at': null,
  'cancellation_reason': null,
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

http.Response _page(Map<String, dynamic> data) => _ok(<String, dynamic>{
  'items': <Map<String, dynamic>>[data],
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
        accessToken: 's04dc-token',
        refreshToken: 's04dc-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinic,
      ),
    );

class _Rig {
  _Rig(
    Future<http.Response> Function(http.Request) handler, {
    String role = 'DOCTOR',
    int? clinic = 3,
    bool signedOut = false,
    AppointmentRecord? appointment,
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
      requestTimeout: const Duration(seconds: 2),
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
      sessionNavigationGuard: guard,
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

Future<void> _enterNotes(WidgetTester tester, String notes) async {
  final field = find.byKey(const ValueKey('s04d-notes'));
  await tester.ensureVisible(field);
  await tester.enterText(field, notes);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('scheduled row shows confirm, not completion/no-show', (
    tester,
  ) async {
    final rig = _Rig((_) async => _ok(_row(status: 'confirmed')));
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    expect(find.byKey(const ValueKey('s04d-confirm')), findsOneWidget);
    expect(find.byKey(const ValueKey('s04d-complete')), findsNothing);
    expect(find.byKey(const ValueKey('s04d-no-show')), findsNothing);
    expect(find.byKey(const ValueKey('s04d-notes')), findsNothing);
  });

  testWidgets('confirmed row shows complete/no-show, not confirm', (
    tester,
  ) async {
    final rig = _Rig(
      (_) async => _ok(_row(status: 'completed')),
      appointment: _record(status: 'confirmed'),
    );
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    expect(find.byKey(const ValueKey('s04d-confirm')), findsNothing);
    expect(find.byKey(const ValueKey('s04d-complete')), findsOneWidget);
    expect(find.byKey(const ValueKey('s04d-no-show')), findsOneWidget);
    expect(find.byKey(const ValueKey('s04d-notes')), findsOneWidget);
  });

  testWidgets('confirm requires explicit acknowledgement', (tester) async {
    var posts = 0;
    final rig = _Rig((_) async {
      posts++;
      return _ok(_row(status: 'confirmed'));
    });
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await _tap(tester, 's04d-confirm');
    expect(posts, 0);
    await _consent(tester);
    await _tap(tester, 's04d-confirm');
    expect(find.byKey(const ValueKey('s04c-confirmed')), findsOneWidget);
    expect(posts, 1);
    await _tap(tester, 's04d-confirm');
    expect(posts, 1);
  });

  testWidgets('confirm sends online POST without idempotency/replay', (
    tester,
  ) async {
    var posts = 0;
    final rig = _Rig((request) async {
      posts++;
      expect(request.method, 'POST');
      expect(request.url.path, '/api/v1/appointments/9/confirm');
      expect(request.headers['authorization'], 'Bearer s04dc-token');
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      expect(jsonDecode(request.body), isEmpty);
      return _ok(_row(status: 'confirmed'));
    });
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await _consent(tester);
    await _tap(tester, 's04d-confirm');
    expect(posts, 1);
    expect(find.byKey(const ValueKey('s04c-confirmed')), findsOneWidget);
  });

  testWidgets('complete with notes trims and requires server acknowledgement', (
    tester,
  ) async {
    var posts = 0;
    final rig = _Rig((request) async {
      posts++;
      expect(request.url.path, '/api/v1/appointments/9/complete');
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      expect(jsonDecode(request.body), <String, dynamic>{'notes': 'Attended'});
      return _ok(_row(status: 'completed', notes: 'Attended'));
    }, appointment: _record(status: 'confirmed'));
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await _enterNotes(tester, '  Attended  ');
    await _consent(tester);
    await _tap(tester, 's04d-complete');
    expect(find.byKey(const ValueKey('s04c-confirmed')), findsOneWidget);
    expect(posts, 1);
  });

  testWidgets('complete without notes sends an empty JSON object', (
    tester,
  ) async {
    final rig = _Rig((request) async {
      expect(request.url.path, '/api/v1/appointments/9/complete');
      expect(jsonDecode(request.body), isEmpty);
      return _ok(_row(status: 'completed'));
    }, appointment: _record(status: 'confirmed'));
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await _consent(tester);
    await _tap(tester, 's04d-complete');
    expect(find.byKey(const ValueKey('s04c-confirmed')), findsOneWidget);
  });

  testWidgets('whitespace-only completion notes fail before HTTP', (
    tester,
  ) async {
    var posts = 0;
    final rig = _Rig((_) async {
      posts++;
      return _ok(_row(status: 'completed'));
    }, appointment: _record(status: 'confirmed'));
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await _enterNotes(tester, '   ');
    await _consent(tester);
    await _tap(tester, 's04d-complete');
    expect(posts, 0);
    expect(find.textContaining('must contain non-whitespace'), findsOneWidget);
  });

  testWidgets('no-show is one-shot with validated terminal status', (
    tester,
  ) async {
    var posts = 0;
    final rig = _Rig((request) async {
      posts++;
      expect(request.url.path, '/api/v1/appointments/9/no-show');
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      expect(jsonDecode(request.body), isEmpty);
      return _ok(_row(status: 'no_show'));
    }, appointment: _record(status: 'confirmed'));
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await _consent(tester);
    await _tap(tester, 's04d-no-show');
    expect(find.byKey(const ValueKey('s04c-confirmed')), findsOneWidget);
    await _tap(tester, 's04d-complete');
    expect(posts, 1);
  });

  testWidgets('server 409 rejects status action and blocks second send', (
    tester,
  ) async {
    var posts = 0;
    final rig = _Rig((_) async {
      posts++;
      return http.Response('{"success":false,"error":"conflict"}', 409);
    });
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await _consent(tester);
    await _tap(tester, 's04d-confirm');
    expect(find.textContaining('Server rejected'), findsOneWidget);
    await _tap(tester, 's04d-confirm');
    expect(posts, 1);
  });

  testWidgets(
    'HTTP 503 confirm is uncertain, read-only recheck never resends',
    (tester) async {
      var posts = 0;
      var reads = 0;
      final rig = _Rig((request) async {
        if (request.method == 'POST') {
          posts++;
          return http.Response('unavailable', 503);
        }
        reads++;
        expect(request.url.path, '/api/v1/appointments/patient/7');
        return _page(_row(status: 'confirmed'));
      });
      addTearDown(rig.close);
      await tester.pumpWidget(rig.widget());
      await _consent(tester);
      await _tap(tester, 's04d-confirm');
      expect(find.byKey(const ValueKey('s04c-uncertain')), findsOneWidget);
      expect(posts, 1);
      expect(reads, 0);
      await _tap(tester, 's04c-recheck');
      expect(reads, 1);
      expect(find.textContaining('Attribution is not proven'), findsOneWidget);
      await _tap(tester, 's04d-confirm');
      expect(posts, 1);
    },
  );

  testWidgets('HTTP 503 complete notes mismatch remains uncertain', (
    tester,
  ) async {
    var posts = 0;
    final rig = _Rig((request) async {
      if (request.method == 'POST') {
        posts++;
        return http.Response('error', 503);
      }
      return _page(_row(status: 'completed', notes: 'Not requested'));
    }, appointment: _record(status: 'confirmed'));
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await _enterNotes(tester, 'Expected');
    await _consent(tester);
    await _tap(tester, 's04d-complete');
    await _tap(tester, 's04c-recheck');
    expect(find.textContaining('Outcome remains uncertain'), findsOneWidget);
    expect(find.byKey(const ValueKey('s04c-confirmed')), findsNothing);
    expect(posts, 1);
  });

  testWidgets(
    'HTTP 503 complete matching notes observes state, not attribution',
    (tester) async {
      var posts = 0;
      final rig = _Rig((request) async {
        if (request.method == 'POST') {
          posts++;
          return http.Response('error', 503);
        }
        return _page(_row(status: 'completed', notes: 'Expected'));
      }, appointment: _record(status: 'confirmed'));
      addTearDown(rig.close);
      await tester.pumpWidget(rig.widget());
      await _enterNotes(tester, 'Expected');
      await _consent(tester);
      await _tap(tester, 's04d-complete');
      await _tap(tester, 's04c-recheck');
      expect(find.textContaining('Attribution is not proven'), findsOneWidget);
      expect(find.byKey(const ValueKey('s04c-confirmed')), findsNothing);
      expect(posts, 1);
    },
  );

  testWidgets('HTTP 503 no-show read can observe status without retry', (
    tester,
  ) async {
    var posts = 0;
    final rig = _Rig((request) async {
      if (request.method == 'POST') {
        posts++;
        return http.Response('unavailable', 503);
      }
      return _page(_row(status: 'no_show'));
    }, appointment: _record(status: 'confirmed'));
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await _consent(tester);
    await _tap(tester, 's04d-no-show');
    await _tap(tester, 's04c-recheck');
    expect(find.textContaining('Attribution is not proven'), findsOneWidget);
    expect(posts, 1);
  });

  testWidgets('invalid 200 status remains uncertain; never confirms', (
    tester,
  ) async {
    var posts = 0;
    final rig = _Rig((_) async {
      posts++;
      return _ok(_row(status: 'scheduled'));
    });
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await _consent(tester);
    await _tap(tester, 's04d-confirm');
    expect(find.byKey(const ValueKey('s04c-confirmed')), findsNothing);
    expect(find.byKey(const ValueKey('s04c-uncertain')), findsOneWidget);
    expect(posts, 1);
  });

  testWidgets('wrong role or clinic sends no status mutation', (tester) async {
    var posts = 0;
    final rig = _Rig(
      (_) async {
        posts++;
        return _ok(_row(status: 'confirmed'));
      },
      role: 'PATIENT',
      clinic: 5,
    );
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await _consent(tester);
    await _tap(tester, 's04d-confirm');
    expect(posts, 0);
    expect(find.byKey(const ValueKey('s04c-confirmed')), findsNothing);
  });

  testWidgets('logout during confirm discards late backend success and PHI', (
    tester,
  ) async {
    var posts = 0;
    final response = Completer<http.Response>();
    final started = Completer<void>();
    final rig = _Rig((_) {
      posts++;
      started.complete();
      return response.future;
    });
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await _consent(tester);
    final button = find.byKey(const ValueKey('s04d-confirm'));
    await tester.ensureVisible(button);
    await tester.tap(button);
    await tester.pump();
    await started.future;
    rig.guard.markSignedOut();
    response.complete(_ok(_row(status: 'confirmed')));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('s04c-session-unavailable')),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('s04c-confirmed')), findsNothing);
    expect(find.text('Appointment #9'), findsNothing);
    expect(posts, 1);
  });

  testWidgets('duplicate taps while confirm pending send one POST', (
    tester,
  ) async {
    var posts = 0;
    final response = Completer<http.Response>();
    final started = Completer<void>();
    final rig = _Rig((_) {
      posts++;
      started.complete();
      return response.future;
    });
    addTearDown(rig.close);
    await tester.pumpWidget(rig.widget());
    await _consent(tester);
    final button = find.byKey(const ValueKey('s04d-confirm'));
    await tester.ensureVisible(button);
    await tester.tap(button);
    await tester.pump();
    await started.future;
    expect(tester.widget<ElevatedButton>(button).onPressed, isNull);
    response.complete(_ok(_row(status: 'confirmed')));
    await tester.pumpAndSettle();
    expect(posts, 1);
  });

  for (final status in <String>['cancelled', 'completed', 'no_show']) {
    testWidgets('terminal $status has no status-action controls', (
      tester,
    ) async {
      var posts = 0;
      final rig = _Rig((_) async {
        posts++;
        return _ok(_row());
      }, appointment: _record(status: status));
      addTearDown(rig.close);
      await tester.pumpWidget(rig.widget());
      expect(find.byKey(const ValueKey('s04c-ineligible')), findsOneWidget);
      expect(find.byKey(const ValueKey('s04d-confirm')), findsNothing);
      expect(find.byKey(const ValueKey('s04d-complete')), findsNothing);
      expect(find.byKey(const ValueKey('s04d-no-show')), findsNothing);
      expect(posts, 0);
    });
  }
}
