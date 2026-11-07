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
import 'package:clinic_system_pro/features/patients/data/models/patient_prescription.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';

AuthSessionState _ready({
  String role = 'doctor',
  int clinicId = 3,
  int userId = 42,
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 's04-prescription-token',
    refreshToken: 's04-prescription-refresh',
    userId: userId,
    role: role,
    clinicContextId: clinicId,
  ),
);

Map<String, dynamic> _row({
  int id = 11,
  int patientId = 7,
  int clinicId = 3,
  String status = 'active',
}) => {
  'id': id,
  'clinic_id': clinicId,
  'patient_id': patientId,
  'consultation_id': null,
  'prescribed_by_id': 8,
  'status': status,
  'notes': null,
  'issued_at': '2026-11-06T09:00:00',
  'expires_at': null,
  'items': [
    {
      'id': id + 100,
      'prescription_id': id,
      'drug_id': 15,
      'dosage': '500 mg',
      'frequency': 'twice daily',
      'duration': '5 days',
      'quantity': 10,
      'instructions': 'Use as prescribed',
    },
  ],
};

Map<String, dynamic> _response({
  List<Map<String, dynamic>>? items,
  int? total,
  int page = 1,
  int perPage = 20,
}) {
  final rows = items ?? [_row()];

  return {
    'success': true,
    'data': {
      'items': rows,
      'total': total ?? rows.length,
      'page': page,
      'per_page': perPage,
    },
  };
}

PatientPrescriptionRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) => PatientPrescriptionRemoteDataSource(
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
  test(
    'authenticated GET preserves prescription and medication fields',
    () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/prescriptions/patients/7');
        expect(request.url.queryParameters, {'page': '1', 'per_page': '20'});
        expect(
          request.headers['authorization'],
          'Bearer s04-prescription-token',
        );
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        return http.Response(jsonEncode(_response()), 200);
      });
      addTearDown(client.close);

      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);

      final result = await _source(
        client,
        guard,
      ).fetchPrescriptions(patientId: 7);

      expect(calls, 1);
      expect(result.total, 1);
      expect(result.items.single.status, PatientPrescriptionStatus.active);
      expect(result.items.single.clinicId, 3);
      expect(result.items.single.patientId, 7);
      expect(result.items.single.items.single.drugId, 15);
      expect(result.items.single.items.single.dosage, '500 mg');
      expect(result.items.single.issuedAt, '2026-11-06T09:00:00');
    },
  );

  test('explicitly empty first page is valid', () async {
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode(_response(items: [], total: 0)), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final page = await _source(client, guard).fetchPrescriptions(patientId: 7);

    expect(page.items, isEmpty);
    expect(page.total, 0);
  });

  for (final status in <String>[
    'active',
    'completed',
    'cancelled',
    'expired',
  ]) {
    test('server status $status is accepted without inference', () async {
      final client = MockClient(
        (_) async => http.Response(
          jsonEncode(_response(items: [_row(status: status)])),
          200,
        ),
      );
      addTearDown(client.close);

      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);

      final page = await _source(
        client,
        guard,
      ).fetchPrescriptions(patientId: 7);

      expect(page.items.single.status.name, status);
    });
  }

  test('wrong patient or clinic in prescription fails closed', () async {
    for (final row in <Map<String, dynamic>>[
      _row(patientId: 99),
      _row(clinicId: 99),
    ]) {
      final client = MockClient(
        (_) async => http.Response(jsonEncode(_response(items: [row])), 200),
      );

      final guard = SessionNavigationGuard()..markSessionAvailable();

      await expectLater(
        _source(client, guard).fetchPrescriptions(patientId: 7),
        throwsA(isA<FormatException>()),
      );

      client.close();
      guard.dispose();
    }
  });

  test('patient and admin roles cannot dispatch the GET', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_response()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    for (final role in <String>['patient', 'admin', 'nurse']) {
      await expectLater(
        _source(
          client,
          guard,
          loader: () async => _ready(role: role),
        ).fetchPrescriptions(patientId: 7),
        throwsA(isA<PatientPrescriptionSessionMismatchException>()),
      );
    }

    expect(calls, 0);
  });

  test('pharmacist is a valid normal reader', () async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_response()), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final result = await _source(
      client,
      guard,
      loader: () async => _ready(role: 'pharmacist'),
    ).fetchPrescriptions(patientId: 7);

    expect(result.items.single.id, 11);
  });

  test('logout during HTTP response discards records', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();

    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final future = _source(client, guard).fetchPrescriptions(patientId: 7);

    await started.future;
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_response()), 200));

    await expectLater(
      future,
      throwsA(isA<PatientPrescriptionSessionMismatchException>()),
    );
  });

  test('clinic switch during GET discards old records', () async {
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
      guard,
      loader: () async => session,
    ).fetchPrescriptions(patientId: 7);

    await started.future;
    session = _ready(clinicId: 9);
    pending.complete(http.Response(jsonEncode(_response()), 200));

    await expectLater(
      future,
      throwsA(isA<PatientPrescriptionSessionMismatchException>()),
    );
  });

  test('invalid inputs never dispatch HTTP', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_response()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);

    await expectLater(
      source.fetchPrescriptions(patientId: 0),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchPrescriptions(patientId: 7, page: 0),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchPrescriptions(patientId: 7, perPage: 501),
      throwsArgumentError,
    );
    expect(calls, 0);
  });

  test('wrong pagination metadata and unknown status fail closed', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    for (final response in <Map<String, dynamic>>[
      _response(page: 2),
      _response(items: [_row(status: 'unknown')]),
    ]) {
      final client = MockClient(
        (_) async => http.Response(jsonEncode(response), 200),
      );

      await expectLater(
        _source(client, guard).fetchPrescriptions(patientId: 7),
        throwsA(isA<FormatException>()),
      );

      client.close();
    }
  });

  test('cross-prescription medication item is rejected', () async {
    final row = _row();
    final items = row['items'] as List<dynamic>;
    (items.first as Map<String, dynamic>)['prescription_id'] = 99;

    final client = MockClient(
      (_) async => http.Response(jsonEncode(_response(items: [row])), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard).fetchPrescriptions(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('403 stays a typed backend rejection without retry', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{"error":"Forbidden"}', 403);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard).fetchPrescriptions(patientId: 7),
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
}
