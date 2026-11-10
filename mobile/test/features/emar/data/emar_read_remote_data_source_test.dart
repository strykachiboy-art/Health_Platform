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
import 'package:clinic_system_pro/features/emar/data/emar_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';

AuthSessionState ready({
  String role = 'nurse',
  int clinicId = 3,
  String token = 'emar-test-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'emar-refresh',
    userId: 43,
    role: role,
    clinicContextId: clinicId,
  ),
);

EmarReadRemoteDataSource source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) => EmarReadRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: loader ?? () async => ready(),
  sessionNavigationGuard: guard,
);

Map<String, dynamic> record({
  int clinicId = 3,
  int admissionId = 7,
  int patientId = 5,
}) => {
  'id': 11,
  'clinic_id': clinicId,
  'patient_id': patientId,
  'admission_id': admissionId,
  'prescription_id': 17,
  'prescription_item_id': 19,
  'status': 'due',
  'record_status': 'draft',
  'record_version': 1,
  'dose_value': '1.50',
};

Map<String, dynamic> verificationEvent({int clinicId = 3}) => {
  'id': 13,
  'clinic_id': clinicId,
  'medication_administration_id': 11,
  'actor_staff_id': 19,
  'verification_result': 'not_barcode_verified',
  'failure_code': 'wrong_medication',
  'failure_reason': 'Drug mismatch',
  'patient_match': true,
  'medication_match': false,
  'dose_match': true,
  'route_match': true,
  'time_window_match': true,
  'scan_timestamp': '2026-11-09T12:00:00Z',
  'verification_policy_version': 1,
};

void main() {
  test(
    'GET admission list uses raw envelope, exact page, token and status',
    () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/emar/admissions/7');
        expect(request.url.queryParameters, {
          'page': '2',
          'per_page': '20',
          'status': 'due',
        });
        expect(request.headers['authorization'], 'Bearer emar-test-token');
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        return http.Response(
          jsonEncode({
            'items': [record()],
            'total': 25,
            'page': 2,
            'per_page': 20,
          }),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final page = await source(client, guard).listForAdmission(
        admissionId: 7,
        page: 2,
        perPage: 20,
        expectedPatientId: 5,
        status: EmarAdministrationStatus.due,
      );
      expect(page.items.single.id, 11);
      expect(page.hasNext, isFalse);
      expect(calls, 1);
    },
  );

  test('doctor can read; patient and admin are denied before HTTP', () async {
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      return http.Response(jsonEncode({'administration': record()}), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final doctor = await source(
      client,
      guard,
      loader: () async => ready(role: 'doctor'),
    ).getAdministration(administrationId: 11);
    expect(doctor.id, 11);
    expect(requests, 1);
    for (final role in ['patient', 'admin', 'super_admin', 'pharmacist']) {
      await expectLater(
        source(
          client,
          guard,
          loader: () async => ready(role: role),
        ).getAdministration(administrationId: 11),
        throwsA(isA<EmarReadSessionException>()),
      );
    }
    expect(requests, 1);
  });

  test(
    'detail and history enforce exact clinic and administration scope',
    () async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        if (request.url.path.endsWith('/barcode-events')) {
          return http.Response(
            jsonEncode({
              'history': {
                'items': [verificationEvent()],
                'total': 1,
              },
            }),
            200,
          );
        }
        expect(request.url.path, '/api/v1/emar/11');
        return http.Response(jsonEncode({'administration': record()}), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final remote = source(client, guard);
      final row = await remote.getAdministration(
        administrationId: 11,
        expectedPatientId: 5,
        expectedAdmissionId: 7,
      );
      expect(row.doseValue, '1.50');
      final history = await remote.getBarcodeHistory(administrationId: 11);
      expect(history.items.single.isVerified, isFalse);
      expect(requests, 2);
    },
  );

  test(
    'wrong clinic, patient, admission or detail ID always fail closed',
    () async {
      final rows = [
        record(clinicId: 70),
        record(patientId: 99),
        record(admissionId: 20),
        {...record(), 'id': 12},
      ];
      var next = 0;
      final client = MockClient(
        (_) async =>
            http.Response(jsonEncode({'administration': rows[next++]}), 200),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final remote = source(client, guard);
      for (var n = 0; n < rows.length; n++) {
        await expectLater(
          remote.getAdministration(
            administrationId: 11,
            expectedPatientId: 5,
            expectedAdmissionId: 7,
          ),
          throwsFormatException,
        );
      }
    },
  );

  test(
    'invalidated navigation while request is pending rejects stale result',
    () async {
      final pending = Completer<http.Response>();
      var requests = 0;
      final client = MockClient((_) {
        requests++;
        return pending.future;
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final result = source(client, guard).listForAdmission(admissionId: 7);
      await Future<void>.delayed(const Duration(milliseconds: 10));
      expect(requests, 1);
      guard.invalidate();
      pending.complete(
        http.Response(
          jsonEncode({
            'items': [record()],
            'total': 1,
            'page': 1,
            'per_page': 50,
          }),
          200,
        ),
      );
      await expectLater(result, throwsA(isA<EmarReadSessionException>()));
    },
  );

  test('session token change during GET discards result', () async {
    var loadCount = 0;
    final client = MockClient(
      (_) async => http.Response(jsonEncode({'administration': record()}), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      source(
        client,
        guard,
        loader: () async {
          loadCount++;
          return ready(token: loadCount == 1 ? 'token-before' : 'token-after');
        },
      ).getAdministration(administrationId: 11),
      throwsA(isA<EmarReadSessionException>()),
    );
  });

  test(
    'backend 403 remains failure; response is never treated as empty',
    () async {
      final client = MockClient(
        (_) async => http.Response(jsonEncode({'error': 'Forbidden'}), 403),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        source(client, guard).listForAdmission(admissionId: 7),
        throwsA(isA<ApiException>()),
      );
    },
  );

  test('invalid arguments do not reach the network', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final remote = source(client, guard);
    expect(() => remote.listForAdmission(admissionId: 0), throwsArgumentError);
    expect(
      () => remote.listForAdmission(admissionId: 7, perPage: 501),
      throwsArgumentError,
    );
    expect(
      () => remote.getBarcodeHistory(administrationId: 0),
      throwsArgumentError,
    );
    expect(calls, 0);
  });
}
