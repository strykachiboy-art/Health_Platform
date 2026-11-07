import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/data/models/appointment_record.dart';

AuthSessionState _session({
  String role = 'doctor',
  int clinicId = 3,
  int userId = 42,
  String token = 'p23b-read-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'p23b-refresh',
    userId: userId,
    clinicContextId: clinicId,
    role: role,
  ),
);

Map<String, dynamic> _row({
  int id = 11,
  int clinicId = 3,
  int patientId = 7,
  int staffId = 8,
  String status = 'scheduled',
  String type = 'in_person',
  String start = '2026-11-08T09:00:00',
  String end = '2026-11-08T09:30:00',
}) => {
  'id': id,
  'clinic_id': clinicId,
  'patient_id': patientId,
  'staff_id': staffId,
  'scheduled_start': start,
  'scheduled_end': end,
  'status': status,
  'appointment_type': type,
  'reason': 'Follow-up',
  'notes': null,
  'google_calendar_event_id': null,
  'reminder_sent': false,
  'created_at': '2026-11-07T08:00:00+00:00',
  'updated_at': '2026-11-07T08:00:00+00:00',
  'cancelled_at': null,
  'cancellation_reason': null,
};

Map<String, dynamic> _envelope({
  List<Map<String, dynamic>>? rows,
  int? total,
  int page = 1,
  int perPage = 20,
  int? pages,
  bool? hasNext,
  bool? hasPrev,
}) {
  final items = rows ?? [_row()];
  final count = total ?? items.length;
  final countPages = pages ?? ((count + perPage - 1) ~/ perPage);
  return {
    'success': true,
    'data': {
      'items': items,
      'total': count,
      'page': page,
      'per_page': perPage,
      'pages': countPages,
      'has_next': hasNext ?? (page < countPages),
      'has_prev': hasPrev ?? (page > 1),
    },
  };
}

AppointmentRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) => AppointmentRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: loader ?? () async => _session(),
  sessionNavigationGuard: guard,
);

void main() {
  test('patient GET validates backend envelope, scope and raw time', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/appointments/patient/7');
      expect(request.url.queryParameters, {'page': '1', 'per_page': '20'});
      expect(request.headers['authorization'], 'Bearer p23b-read-token');
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      return http.Response(jsonEncode(_envelope()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final page = await _source(client, guard).fetchForPatient(patientId: 7);
    expect(calls, 1);
    expect(page.total, 1);
    expect(page.pages, 1);
    expect(page.hasNext, isFalse);
    expect(page.items.single.status, AppointmentRecordStatus.scheduled);
    expect(page.items.single.appointmentType, AppointmentRecordType.inPerson);
    expect(page.items.single.clinicId, 3);
    expect(page.items.single.staffId, 8);
    expect(page.items.single.scheduledStart, '2026-11-08T09:00:00');
    expect(page.items.single.reason, 'Follow-up');
    expect(page.items.single.reminderSent, isFalse);
  });

  test('staff schedule GET uses date and allows receptionist', () async {
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/appointments/staff/8');
      expect(request.url.queryParameters, {
        'page': '2',
        'per_page': '1',
        'date': '2026-11-08',
      });
      return http.Response(
        jsonEncode(
          _envelope(
            rows: [_row()],
            total: 2,
            page: 2,
            perPage: 1,
            pages: 2,
            hasNext: false,
            hasPrev: true,
          ),
        ),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final page = await _source(
      client,
      guard,
      loader: () async => _session(role: 'receptionist'),
    ).fetchForStaff(staffId: 8, date: '2026-11-08', page: 2, perPage: 1);
    expect(page.page, 2);
    expect(page.hasPrev, isTrue);
    expect(page.items.single.staffId, 8);
  });

  test('empty page is not fabricated and preserves page metadata', () async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode(_envelope(rows: [], total: 0, pages: 0)),
        200,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final page = await _source(client, guard).fetchForPatient(patientId: 7);
    expect(page.items, isEmpty);
    expect(page.total, 0);
    expect(page.pages, 0);
  });

  test('all backend statuses and types are mapped without inference', () {
    const statuses = <String, AppointmentRecordStatus>{
      'scheduled': AppointmentRecordStatus.scheduled,
      'confirmed': AppointmentRecordStatus.confirmed,
      'cancelled': AppointmentRecordStatus.cancelled,
      'completed': AppointmentRecordStatus.completed,
      'no_show': AppointmentRecordStatus.noShow,
    };
    const types = <String, AppointmentRecordType>{
      'in_person': AppointmentRecordType.inPerson,
      'telemedicine': AppointmentRecordType.telemedicine,
      'follow_up': AppointmentRecordType.followUp,
      'emergency': AppointmentRecordType.emergency,
    };
    for (final entry in statuses.entries) {
      final record = AppointmentRecord.fromJson(
        _row(status: entry.key),
        expectedClinicId: 3,
        expectedPatientId: 7,
      );
      expect(record.status, entry.value);
    }
    for (final entry in types.entries) {
      final record = AppointmentRecord.fromJson(
        _row(type: entry.key),
        expectedClinicId: 3,
        expectedPatientId: 7,
      );
      expect(record.appointmentType, entry.value);
    }
  });

  test('nurse and admin are eligible readers', () async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_envelope()), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final role in <String>['nurse', 'admin']) {
      final page = await _source(
        client,
        guard,
        loader: () async => _session(role: role),
      ).fetchForPatient(patientId: 7);
      expect(page.items.single.id, 11);
    }
  });

  test('patient, pharmacist, and unsupported roles issue no GET', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_envelope()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final role in <String>['patient', 'pharmacist', 'lab_technician']) {
      await expectLater(
        _source(
          client,
          guard,
          loader: () async => _session(role: role),
        ).fetchForPatient(patientId: 7),
        throwsA(isA<AppointmentReadSessionMismatchException>()),
      );
    }
    expect(calls, 0);
  });

  test('signed-out guard never dispatches HTTP', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_envelope()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).fetchForPatient(patientId: 7),
      throwsA(isA<AppointmentReadSessionMismatchException>()),
    );
    expect(calls, 0);
  });

  test('invalid IDs, page size or staff date never dispatch', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_envelope()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await expectLater(
      source.fetchForPatient(patientId: 0),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchForPatient(patientId: 7, page: 0),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchForPatient(patientId: 7, perPage: 501),
      throwsArgumentError,
    );
    await expectLater(source.fetchForStaff(staffId: -1), throwsArgumentError);
    await expectLater(
      source.fetchForStaff(staffId: 8, date: '2026-02-30'),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchForStaff(staffId: 8, date: '2026-11-08T10:00:00'),
      throwsArgumentError,
    );
    expect(calls, 0);
  });

  test('wrong clinic and wrong patient list rows fail closed', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final row in <Map<String, dynamic>>[
      _row(clinicId: 9),
      _row(patientId: 8),
    ]) {
      final client = MockClient(
        (_) async => http.Response(jsonEncode(_envelope(rows: [row])), 200),
      );
      await expectLater(
        _source(client, guard).fetchForPatient(patientId: 7),
        throwsA(isA<FormatException>()),
      );
      client.close();
    }
  });

  test('wrong staff schedule row fails closed', () async {
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode(_envelope(rows: [_row(staffId: 9)])), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).fetchForStaff(staffId: 8),
      throwsA(isA<FormatException>()),
    );
  });

  test('duplicate appointment IDs and malformed row fail closed', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final duplicate = _envelope(rows: [_row(), _row()]);
    final malformed = _envelope();
    (malformed['data'] as Map<String, dynamic>)['items'] = <Object?>['wrong'];
    for (final body in <Map<String, dynamic>>[duplicate, malformed]) {
      final client = MockClient(
        (_) async => http.Response(jsonEncode(body), 200),
      );
      await expectLater(
        _source(client, guard).fetchForPatient(patientId: 7),
        throwsA(isA<FormatException>()),
      );
      client.close();
    }
  });

  test('invalid pagination flags, counts or envelope fail closed', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final wrongEnvelope = _envelope()..['success'] = false;
    for (final body in <Map<String, dynamic>>[
      wrongEnvelope,
      _envelope(page: 2),
      _envelope(pages: 9),
      _envelope(hasNext: true),
      _envelope(total: 0),
    ]) {
      final client = MockClient(
        (_) async => http.Response(jsonEncode(body), 200),
      );
      await expectLater(
        _source(client, guard).fetchForPatient(patientId: 7),
        throwsA(isA<FormatException>()),
      );
      client.close();
    }
  });

  test(
    'unknown appointment status/type and invalid timestamp fail closed',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      for (final row in <Map<String, dynamic>>[
        _row(status: 'pending'),
        _row(type: 'other'),
        _row(start: '2026-11-08'),
        _row(end: 'not-a-date'),
      ]) {
        final client = MockClient(
          (_) async => http.Response(jsonEncode(_envelope(rows: [row])), 200),
        );
        await expectLater(
          _source(client, guard).fetchForPatient(patientId: 7),
          throwsA(isA<FormatException>()),
        );
        client.close();
      }
    },
  );

  test('HTTP 403 propagates as typed rejection without retry', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{"error":"Forbidden"}', 403);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).fetchForPatient(patientId: 7),
      throwsA(
        isA<ApiException>().having(
          (error) => error.statusCode,
          'statusCode',
          403,
        ),
      ),
    );
    expect(calls, 1);
  });

  test(
    'logout while response pending rejects stale appointment list',
    () async {
      final started = Completer<void>();
      final pending = Completer<http.Response>();
      final client = MockClient((_) {
        started.complete();
        return pending.future;
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final future = _source(client, guard).fetchForPatient(patientId: 7);
      await started.future;
      guard.invalidate();
      pending.complete(http.Response(jsonEncode(_envelope()), 200));
      await expectLater(
        future,
        throwsA(isA<AppointmentReadSessionMismatchException>()),
      );
    },
  );

  test(
    'clinic switch while HTTP pending rejects stale appointment list',
    () async {
      final started = Completer<void>();
      final pending = Completer<http.Response>();
      var session = _session();
      final client = MockClient((_) {
        started.complete();
        return pending.future;
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final future = _source(
        client,
        guard,
        loader: () async => session,
      ).fetchForPatient(patientId: 7);
      await started.future;
      session = _session(clinicId: 9);
      pending.complete(http.Response(jsonEncode(_envelope()), 200));
      await expectLater(
        future,
        throwsA(isA<AppointmentReadSessionMismatchException>()),
      );
    },
  );
}
