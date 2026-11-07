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
import 'package:clinic_system_pro/features/patients/data/models/patient_allergy.dart';
import 'package:clinic_system_pro/features/patients/data/patient_allergy_remote_data_source.dart';

AuthSessionState _ready({
  int userId = 42,
  int? clinicId = 3,
  String role = 'doctor',
  String token = 'patient-allergy-test-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'test-refresh-token',
    userId: userId,
    role: role,
    clinicContextId: clinicId,
  ),
);

Map<String, dynamic> _allergy({
  int patientId = 7,
  int clinicId = 3,
  String severity = 'life_threatening',
  String clinicalStatus = 'active',
}) => {
  'id': 11,
  'clinic_id': clinicId,
  'patient_id': patientId,
  'allergen_name': 'Penicillin',
  'allergen_code': 'RXNORM:7980',
  'allergen_type': 'drug',
  'reaction': 'Anaphylaxis',
  'severity': severity,
  'clinical_status': clinicalStatus,
  'verification_status': 'confirmed',
  'onset_at': null,
  'recorded_at': '2026-11-06T09:00:00+00:00',
  'recorded_by_id': 12,
  'notes': null,
  'created_at': '2026-11-06T09:00:00+00:00',
  'updated_at': '2026-11-06T09:00:00+00:00',
};

Map<String, dynamic> _response({
  List<Map<String, dynamic>>? items,
  int total = 1,
  int page = 1,
  int perPage = 50,
}) => {
  'success': true,
  'data': {
    'items': items ?? [_allergy()],
    'total': total,
    'page': page,
    'per_page': perPage,
  },
};

PatientAllergyRemoteDataSource _source(
  MockClient client, {
  required SessionNavigationGuard guard,
  Future<AuthSessionState> Function()? loader,
}) => PatientAllergyRemoteDataSource(
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
  test('GET preserves query, token, enum and timestamp contracts', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/patients/7/allergies');
      expect(request.url.queryParameters, {
        'page': '1',
        'per_page': '50',
        'active_only': 'true',
        'include_refuted': 'false',
      });
      expect(
        request.headers['authorization'],
        'Bearer patient-allergy-test-token',
      );
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      return http.Response(jsonEncode(_response()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final page = await _source(
      client,
      guard: guard,
    ).fetchAllergies(patientId: 7, activeOnly: true, includeRefuted: false);

    expect(calls, 1);
    expect(page.total, 1);
    expect(page.items.single.allergenName, 'Penicillin');
    expect(page.items.single.severity, PatientAllergySeverity.lifeThreatening);
    expect(
      page.items.single.verificationStatus,
      PatientAllergyVerificationStatus.confirmed,
    );
    expect(page.items.single.recordedAt.isUtc, isTrue);
  });

  test('empty structured allergy page stays empty', () async {
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode(_response(items: [], total: 0)), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final page = await _source(
      client,
      guard: guard,
    ).fetchAllergies(patientId: 7);

    expect(page.items, isEmpty);
    expect(page.total, 0);
  });

  test('invalid patient and pagination fail before HTTP', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_response()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard: guard);

    await expectLater(source.fetchAllergies(patientId: 0), throwsArgumentError);
    await expectLater(
      source.fetchAllergies(patientId: 7, page: 0),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchAllergies(patientId: 7, perPage: 501),
      throwsArgumentError,
    );
    expect(calls, 0);
  });

  test('signed-out and recovery sessions fail without GET', () async {
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
        ).fetchAllergies(patientId: 7),
        throwsA(isA<PatientAllergySessionMismatchException>()),
      );
    }
    expect(calls, 0);
  });

  test('patient, super-admin and missing clinic fail before GET', () async {
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
      _ready(role: 'super_admin'),
      _ready(clinicId: null),
    ]) {
      await expectLater(
        _source(
          client,
          guard: guard,
          loader: () async => state,
        ).fetchAllergies(patientId: 7),
        throwsA(isA<PatientAllergySessionMismatchException>()),
      );
    }
    expect(calls, 0);
  });

  test('unavailable navigation guard rejects before GET', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_response()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchAllergies(patientId: 7),
      throwsA(isA<PatientAllergySessionMismatchException>()),
    );
    expect(calls, 0);
  });

  test('logout during GET discards the response', () async {
    final pending = Completer<http.Response>();
    final started = Completer<void>();
    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final future = _source(client, guard: guard).fetchAllergies(patientId: 7);
    await started.future;
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_response()), 200));

    await expectLater(
      future,
      throwsA(isA<PatientAllergySessionMismatchException>()),
    );
  });

  test('clinic switch during GET discards the response', () async {
    final pending = Completer<http.Response>();
    final started = Completer<void>();
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
    ).fetchAllergies(patientId: 7);

    await started.future;
    session = _ready(clinicId: 9, token: 'rotated-token');
    pending.complete(http.Response(jsonEncode(_response()), 200));

    await expectLater(
      future,
      throwsA(isA<PatientAllergySessionMismatchException>()),
    );
  });

  for (final mismatch in <String>['patient', 'clinic']) {
    test('rejects backend $mismatch scope mismatch', () async {
      final row = mismatch == 'patient'
          ? _allergy(patientId: 99)
          : _allergy(clinicId: 99);
      final client = MockClient(
        (_) async => http.Response(jsonEncode(_response(items: [row])), 200),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);

      await expectLater(
        _source(client, guard: guard).fetchAllergies(patientId: 7),
        throwsA(isA<FormatException>()),
      );
    });
  }

  test('unknown severity cannot be silently downgraded', () async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode(_response(items: [_allergy(severity: 'unsupported')])),
        200,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchAllergies(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('timestamp without timezone is rejected', () async {
    final row = _allergy()..['recorded_at'] = '2026-11-06T09:00:00';
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_response(items: [row])), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchAllergies(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('malformed envelope fails closed', () async {
    final client = MockClient(
      (_) async => http.Response('{"success":true,"data":[]}', 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchAllergies(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  for (final status in <int>[401, 403, 422, 500]) {
    test('HTTP $status stays typed and is not retried', () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response('{"error":"Rejected"}', status);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);

      await expectLater(
        _source(client, guard: guard).fetchAllergies(patientId: 7),
        throwsA(
          isA<ApiException>().having((e) => e.statusCode, 'statusCode', status),
        ),
      );
      expect(calls, 1);
    });
  }
}
