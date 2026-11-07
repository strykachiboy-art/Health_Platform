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
import 'package:clinic_system_pro/features/patients/data/models/patient_consultation.dart';
import 'package:clinic_system_pro/features/patients/data/patient_consultation_remote_data_source.dart';

AuthSessionState _ready({
  int userId = 42,
  int? clinicId = 3,
  String role = 'doctor',
  String token = 'consultation-test-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'consultation-refresh-token',
    userId: userId,
    role: role,
    clinicContextId: clinicId,
  ),
);

Map<String, dynamic> _row({
  int patientId = 7,
  int clinicId = 3,
  String type = 'general',
  String status = 'in_progress',
}) => {
  'id': 11,
  'clinic_id': clinicId,
  'patient_id': patientId,
  'staff_id': 8,
  'appointment_id': null,
  'icd10_code': null,
  'consultation_type': type,
  'status': status,
  'record_status': 'draft',
  'record_version': 1,
  'finalized_by_staff_id': null,
  'finalized_at': null,
  'latest_amendment_id': null,
  'chief_complaint': 'Fever',
  'symptoms': 'Fatigue',
  'diagnosis': null,
  'treatment_plan': null,
  'notes': null,
  'voice_note_url': null,
  'transcribed_text': null,
  'template_id': null,
  'started_at': '2026-11-06T09:00:00',
  'ended_at': null,
  'created_at': '2026-11-06T09:00:00+00:00',
  'updated_at': '2026-11-06T09:00:00+00:00',
};

Map<String, dynamic> _response({
  List<Map<String, dynamic>>? rows,
  int total = 1,
  int page = 1,
  int perPage = 50,
  int? pages,
}) {
  final numberOfPages = pages ?? (total + perPage - 1) ~/ perPage;
  final hasNext = page < numberOfPages;
  final hasPrev = page > 1;

  return {
    'success': true,
    'data': rows ?? [_row()],
    'pagination': {
      'total': total,
      'page': page,
      'per_page': perPage,
      'pages': numberOfPages,
      'has_next': hasNext,
      'has_prev': hasPrev,
      'next_page': hasNext ? page + 1 : null,
      'prev_page': hasPrev ? page - 1 : null,
    },
  };
}

PatientConsultationRemoteDataSource _source(
  MockClient client, {
  required SessionNavigationGuard guard,
  Future<AuthSessionState> Function()? loader,
}) => PatientConsultationRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: loader ?? () async => _ready(),
  sessionNavigationGuard: guard,
);

void main() {
  test('GET uses typed consultation and separate pagination', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/consultations/patient/7');
      expect(request.url.queryParameters, {'page': '1', 'per_page': '50'});
      expect(
        request.headers['authorization'],
        'Bearer consultation-test-token',
      );
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      return http.Response(jsonEncode(_response()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final result = await _source(
      client,
      guard: guard,
    ).fetchConsultationHistory(patientId: 7);

    expect(calls, 1);
    expect(result.items.single.patientId, 7);
    expect(result.items.single.clinicId, 3);
    expect(
      result.items.single.consultationType,
      PatientConsultationType.general,
    );
    expect(result.items.single.recordStatus, PatientClinicalRecordStatus.draft);
    expect(result.items.single.startedAt, '2026-11-06T09:00:00');
    expect(result.total, 1);
    expect(result.pages, 1);
    expect(result.hasNext, isFalse);
  });

  test('consultation type filter is passed to backend', () async {
    final client = MockClient((request) async {
      expect(request.url.queryParameters, {
        'page': '1',
        'per_page': '50',
        'consultation_type': 'follow_up',
      });
      return http.Response(
        jsonEncode(_response(rows: [_row(type: 'follow_up')])),
        200,
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final result = await _source(
      client,
      guard: guard,
    ).fetchConsultationHistory(patientId: 7, consultationType: 'follow_up');

    expect(
      result.items.single.consultationType,
      PatientConsultationType.followUp,
    );
  });

  test('empty consultation history remains empty', () async {
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode(_response(rows: [], total: 0)), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final result = await _source(
      client,
      guard: guard,
    ).fetchConsultationHistory(patientId: 7);

    expect(result.items, isEmpty);
    expect(result.total, 0);
    expect(result.pages, 0);
  });

  test('finalized record preserves version and diagnosis', () async {
    final record = _row()
      ..['record_status'] = 'final'
      ..['finalized_by_staff_id'] = 8
      ..['finalized_at'] = '2026-11-06T10:00:00'
      ..['diagnosis'] = 'Confirmed clinical finding';

    final client = MockClient(
      (_) async => http.Response(jsonEncode(_response(rows: [record])), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final result = await _source(
      client,
      guard: guard,
    ).fetchConsultationHistory(patientId: 7);

    expect(
      result.items.single.recordStatus,
      PatientClinicalRecordStatus.finalized,
    );
    expect(result.items.single.recordVersion, 1);
    expect(result.items.single.diagnosis, 'Confirmed clinical finding');
  });

  test('amended record requires a valid amendment reference', () async {
    final record = _row()
      ..['record_status'] = 'amended'
      ..['record_version'] = 2
      ..['finalized_by_staff_id'] = 8
      ..['finalized_at'] = '2026-11-06T10:00:00'
      ..['latest_amendment_id'] = 25;

    final client = MockClient(
      (_) async => http.Response(jsonEncode(_response(rows: [record])), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final result = await _source(
      client,
      guard: guard,
    ).fetchConsultationHistory(patientId: 7);

    expect(
      result.items.single.recordStatus,
      PatientClinicalRecordStatus.amended,
    );
    expect(result.items.single.recordVersion, 2);
    expect(result.items.single.latestAmendmentId, 25);
  });

  test('invalid input is rejected before HTTP', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_response()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final source = _source(client, guard: guard);

    await expectLater(
      source.fetchConsultationHistory(patientId: 0),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchConsultationHistory(patientId: 7, page: 0),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchConsultationHistory(patientId: 7, perPage: 501),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchConsultationHistory(
        patientId: 7,
        consultationType: 'unknown',
      ),
      throwsArgumentError,
    );
    expect(calls, 0);
  });

  test('signed-out and recovery sessions issue no GET', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_response()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    for (final state in <AuthSessionState>[
      const AuthSessionState.signedOut(),
      const AuthSessionState.recoveryRequired(),
    ]) {
      await expectLater(
        _source(
          client,
          guard: guard,
          loader: () async => state,
        ).fetchConsultationHistory(patientId: 7),
        throwsA(isA<PatientConsultationSessionMismatchException>()),
      );
    }
    expect(calls, 0);
  });

  test('unauthorized roles and missing clinic issue no GET', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_response()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    for (final state in [
      _ready(role: 'patient'),
      _ready(role: 'receptionist'),
      _ready(role: 'super_admin'),
      _ready(clinicId: null),
    ]) {
      await expectLater(
        _source(
          client,
          guard: guard,
          loader: () async => state,
        ).fetchConsultationHistory(patientId: 7),
        throwsA(isA<PatientConsultationSessionMismatchException>()),
      );
    }
    expect(calls, 0);
  });

  test('inactive guard prevents a consultation GET', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_response()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchConsultationHistory(patientId: 7),
      throwsA(isA<PatientConsultationSessionMismatchException>()),
    );
    expect(calls, 0);
  });

  test('logout during GET discards consultation history', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final future = _source(
      client,
      guard: guard,
    ).fetchConsultationHistory(patientId: 7);

    await started.future;
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_response()), 200));

    await expectLater(
      future,
      throwsA(isA<PatientConsultationSessionMismatchException>()),
    );
  });

  test('clinic switch during GET discards history', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
    var session = _ready();
    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final future = _source(
      client,
      guard: guard,
      loader: () async => session,
    ).fetchConsultationHistory(patientId: 7);

    await started.future;
    session = _ready(clinicId: 9, token: 'new-context-token');
    pending.complete(http.Response(jsonEncode(_response()), 200));

    await expectLater(
      future,
      throwsA(isA<PatientConsultationSessionMismatchException>()),
    );
  });

  test('account switch during GET discards history', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
    var session = _ready();
    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final future = _source(
      client,
      guard: guard,
      loader: () async => session,
    ).fetchConsultationHistory(patientId: 7);

    await started.future;
    session = _ready(userId: 99);
    pending.complete(http.Response(jsonEncode(_response()), 200));

    await expectLater(
      future,
      throwsA(isA<PatientConsultationSessionMismatchException>()),
    );
  });

  for (final scope in <String>['patient', 'clinic']) {
    test('rejects wrong $scope in backend record', () async {
      final row = scope == 'patient' ? _row(patientId: 99) : _row(clinicId: 99);

      final client = MockClient(
        (_) async => http.Response(jsonEncode(_response(rows: [row])), 200),
      );
      addTearDown(client.close);

      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);

      await expectLater(
        _source(client, guard: guard).fetchConsultationHistory(patientId: 7),
        throwsA(isA<FormatException>()),
      );
    });
  }

  test('unknown workflow status fails closed', () async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode(_response(rows: [_row(status: 'other')])),
        200,
      ),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchConsultationHistory(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('unknown clinical record status fails closed', () async {
    final record = _row()..['record_status'] = 'unsigned';
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_response(rows: [record])), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchConsultationHistory(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('inconsistent finalized record is rejected', () async {
    final record = _row()
      ..['record_status'] = 'final'
      ..['record_version'] = 2;

    final client = MockClient(
      (_) async => http.Response(jsonEncode(_response(rows: [record])), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchConsultationHistory(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('malformed pagination metadata is rejected', () async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_response(pages: 99)), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchConsultationHistory(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('duplicate consultation IDs are rejected', () async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode(_response(rows: [_row(), _row()], total: 2)),
        200,
      ),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchConsultationHistory(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('malformed timestamps fail closed', () async {
    final record = _row()..['started_at'] = 'not-a-timestamp';

    final client = MockClient(
      (_) async => http.Response(jsonEncode(_response(rows: [record])), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchConsultationHistory(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('malformed success envelope is rejected', () async {
    final client = MockClient(
      (_) async => http.Response('{"success":true,"data":[]}', 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchConsultationHistory(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  for (final status in <int>[401, 403, 404, 422, 500]) {
    test('HTTP $status remains typed and is not retried', () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response('{"error":"Rejected"}', status);
      });
      addTearDown(client.close);

      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);

      await expectLater(
        _source(client, guard: guard).fetchConsultationHistory(patientId: 7),
        throwsA(
          isA<ApiException>().having(
            (error) => error.statusCode,
            'statusCode',
            status,
          ),
        ),
      );
      expect(calls, 1);
    });
  }
}
