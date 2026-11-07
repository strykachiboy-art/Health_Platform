import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_remote_data_source.dart';

AuthSessionState _ready({
  int clinic = 3,
  String role = 'doctor',
  String token = 'token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'refresh',
    userId: 42,
    role: role,
    clinicContextId: clinic,
  ),
);

Map<String, dynamic> _record({
  int patient = 7,
  int clinic = 3,
  int staff = 8,
  int id = 11,
}) => {
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'staff_id': staff,
  'appointment_id': 22,
  'icd10_code': null,
  'consultation_type': 'general',
  'status': 'in_progress',
  'record_status': 'draft',
  'record_version': 1,
  'finalized_by_staff_id': null,
  'finalized_at': null,
  'latest_amendment_id': null,
  'chief_complaint': 'Pain',
  'symptoms': null,
  'diagnosis': null,
  'treatment_plan': null,
  'notes': null,
  'voice_note_url': null,
  'transcribed_text': null,
  'template_id': null,
  'started_at': '2026-11-07T08:00:00',
  'ended_at': null,
  'created_at': '2026-11-07T08:00:00+00:00',
  'updated_at': '2026-11-07T08:00:00+00:00',
};

ConsultationRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) => ConsultationRemoteDataSource(
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

const _cmd = ConsultationStartCommand(
  patientId: 7,
  staffId: 8,
  appointmentId: 22,
  idempotencyKey: 'p23c-stable-intent',
);

void main() {
  test(
    'POST uses exact backend schema, idempotency key and no retry',
    () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/consultations/');
        expect(request.headers['idempotency-key'], 'p23c-stable-intent');
        expect(request.headers['authorization'], 'Bearer token');
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body['patient_id'], 7);
        expect(body['staff_id'], 8);
        expect(body['appointment_id'], 22);
        expect(body.containsKey('clinic_id'), isFalse);
        return http.Response(
          jsonEncode({'success': true, 'data': _record()}),
          201,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final result = await _source(client, guard).start(_cmd);
      expect(result.id, 11);
      expect(calls, 1);
    },
  );

  test('GET validates identity and requires no write header', () async {
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/consultations/11');
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      return http.Response(
        jsonEncode({'success': true, 'data': _record()}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    expect(
      (await _source(
        client,
        guard,
      ).getForPatient(patientId: 7, consultationId: 11)).id,
      11,
    );
  });

  test('unsupported role and missing clinic never dispatch', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final state in <AuthSessionState>[
      _ready(role: 'receptionist'),
      _ready(clinic: 0),
    ]) {
      await expectLater(
        _source(client, guard, loader: () async => state).start(_cmd),
        throwsA(isA<ConsultationSessionMismatchException>()),
      );
    }
    expect(calls, 0);
  });

  test('signed-out guard never sends consultation POST', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).start(_cmd),
      throwsA(isA<ConsultationSessionMismatchException>()),
    );
    expect(calls, 0);
  });

  test('invalid command and IDs do not send HTTP', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await expectLater(
      source.start(
        const ConsultationStartCommand(
          patientId: -1,
          staffId: 8,
          idempotencyKey: 'stable',
        ),
      ),
      throwsArgumentError,
    );
    expect(
      () => source.getForPatient(patientId: 0, consultationId: 1),
      throwsArgumentError,
    );
    await expectLater(
      source.start(
        const ConsultationStartCommand(
          patientId: 7,
          staffId: 8,
          idempotencyKey: '',
        ),
      ),
      throwsArgumentError,
    );
    expect(calls, 0);
  });

  test(
    'wrong clinic, wrong patient and wrong consultation ID fail closed',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      for (final row in [
        _record(clinic: 12),
        _record(patient: 9),
        _record(id: 90),
      ]) {
        final client = MockClient(
          (_) async =>
              http.Response(jsonEncode({'success': true, 'data': row}), 200),
        );
        await expectLater(
          _source(
            client,
            guard,
          ).getForPatient(patientId: 7, consultationId: 11),
          throwsA(isA<FormatException>()),
        );
        client.close();
      }
    },
  );

  test('logout while GET pending drops returned clinical record', () async {
    final pending = Completer<http.Response>();
    final client = MockClient((_) => pending.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final read = _source(
      client,
      guard,
    ).getForPatient(patientId: 7, consultationId: 11);
    await Future<void>.delayed(Duration.zero);
    guard.markSignedOut();
    pending.complete(
      http.Response(jsonEncode({'success': true, 'data': _record()}), 200),
    );
    await expectLater(
      read,
      throwsA(isA<ConsultationSessionMismatchException>()),
    );
  });

  test(
    'clinic switch with same guard revision is detected by revalidation',
    () async {
      var count = 0;
      final client = MockClient(
        (_) async => http.Response(
          jsonEncode({'success': true, 'data': _record()}),
          200,
        ),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(
        client,
        guard,
        loader: () async {
          count++;
          return _ready(clinic: count == 1 ? 3 : 9);
        },
      );
      await expectLater(
        source.getForPatient(patientId: 7, consultationId: 11),
        throwsA(isA<ConsultationSessionMismatchException>()),
      );
    },
  );

  test('backend invalid envelope is never displayed', () async {
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode({'success': false, 'data': _record()}), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).getForPatient(patientId: 7, consultationId: 11),
      throwsA(isA<FormatException>()),
    );
  });
}
