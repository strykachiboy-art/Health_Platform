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
import 'package:clinic_system_pro/features/emar/data/emar_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/emar_barcode_verification_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';

AuthSessionState ready({String role = 'nurse', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'emar-test-token',
        refreshToken: 'emar-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> row({
  String status = 'due',
  int patientId = 5,
  int? verificationEventId,
  String? verificationResult,
  int sourceOrderVersion = 2,
}) => {
  'id': 11,
  'clinic_id': 3,
  'patient_id': patientId,
  'admission_id': 7,
  'prescription_id': 17,
  'prescription_item_id': 19,
  'status': status,
  'record_status': 'draft',
  'record_version': 1,
  'source_order_version': sourceOrderVersion,
  'dose_value': '0.125',
  'dose_unit': 'mg',
  'route_code': 'oral',
  'scheduled_at': '2026-11-09T11:00:00Z',
  'barcode_verification_event_id': verificationEventId,
  'verification_result': verificationResult,
};

Map<String, dynamic> event({bool success = true}) => {
  'id': 41,
  'clinic_id': 3,
  'patient_id': success ? 5 : null,
  'admission_id': success ? 7 : null,
  'medication_administration_id': success ? 11 : null,
  'actor_staff_id': 17,
  'patient_barcode': 'PAT-5',
  'medication_barcode': 'MED-3',
  'scan_timestamp': '2026-11-09T11:00:00+00:00',
  'scan_device': 'test-scanner-1',
  'operation_identity': 'scan-one',
  'verification_result': success ? 'barcode_verified' : 'not_barcode_verified',
  'failure_code': success ? null : 'unknown_patient_barcode',
  'failure_reason': success ? null : 'Patient barcode not found',
  'patient_match': success,
  'medication_match': success,
  'dose_match': success,
  'route_match': success,
  'time_window_match': success,
  'source_order_version': success ? 2 : null,
  'verification_policy_version': 1,
};

EmarAdministration selected() => EmarAdministration.fromJson(
  row(),
  expectedClinicId: 3,
  expectedPatientId: 5,
  expectedAdmissionId: 7,
);

EmarBarcodeVerificationRemoteDataSource source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  final load = loader ?? () async => ready();
  final read = EmarReadRemoteDataSource(
    apiClient: api,
    loadSession: load,
    sessionNavigationGuard: guard,
  );
  return EmarBarcodeVerificationRemoteDataSource(
    apiClient: api,
    readSource: read,
    loadSession: load,
    sessionNavigationGuard: guard,
  );
}

Future<void> invoke(EmarBarcodeVerificationRemoteDataSource adapter) async {
  await adapter.verifyOnce(
    reviewed: selected(),
    patientBarcode: 'PAT-5',
    medicationBarcode: 'MED-3',
    scanDevice: 'test-scanner-1',
    scanTimestamp: DateTime.utc(2026, 11, 9, 11),
    operationKey: 'scan-one',
  );
}

void main() {
  test(
    'successful scan: one POST and authoritative second GET readback',
    () async {
      var gets = 0;
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          gets++;
          expect(request.url.path, '/api/v1/emar/11');
          return http.Response(
            jsonEncode({
              'administration': row(
                verificationEventId: gets == 2 ? 41 : null,
                verificationResult: gets == 2 ? 'barcode_verified' : null,
              ),
            }),
            200,
          );
        }
        posts++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/emar/barcode/verify');
        expect(request.headers['authorization'], 'Bearer emar-test-token');
        expect(request.headers['idempotency-key'], 'scan-one');
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body.keys.toSet(), {
          'patient_barcode',
          'medication_barcode',
          'scan_timestamp',
          'scan_device',
        });
        expect(body['scan_timestamp'], '2026-11-09T11:00:00.000Z');
        expect(body.containsKey('patient_id'), isFalse);
        expect(body.containsKey('administration_id'), isFalse);
        return http.Response(jsonEncode({'verification': event()}), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final adapter = source(client, guard);
      final result = await adapter.verifyOnce(
        reviewed: selected(),
        patientBarcode: 'PAT-5',
        medicationBarcode: 'MED-3',
        scanDevice: 'test-scanner-1',
        scanTimestamp: DateTime.utc(2026, 11, 9, 11),
        operationKey: 'scan-one',
      );
      expect(result.verified, isTrue);
      expect(gets, 2);
      expect(posts, 1);
      expect(adapter.attemptStarted, isTrue);
      await expectLater(
        invoke(adapter),
        throwsA(isA<EmarVerificationOutcomeUnconfirmed>()),
      );
      expect(posts, 1);
    },
  );

  test('HTTP 200 failed barcode: acknowledged as blocked; no readback or administer', () async {
    var gets = 0;
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        gets++;
        return http.Response(jsonEncode({'administration': row()}), 200);
      }
      posts++;
      expect(request.url.path, '/api/v1/emar/barcode/verify');
      return http.Response(
        jsonEncode({'verification': event(success: false)}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final adapter = source(client, guard);
    final result = await adapter.verifyOnce(
      reviewed: selected(),
      patientBarcode: 'PAT-5',
      medicationBarcode: 'MED-3',
      scanDevice: 'test-scanner-1',
      scanTimestamp: DateTime.utc(2026, 11, 9, 11),
      operationKey: 'scan-one',
    );
    expect(result.verified, isFalse);
    expect(result.administrationId, isNull);
    expect(gets, 1);
    expect(posts, 1);
  });

  test(
    'doctor and patient roles do not attempt network verification',
    () async {
      for (final role in ['doctor', 'patient']) {
        var calls = 0;
        final client = MockClient((_) async {
          calls++;
          return http.Response('{}', 200);
        });
        addTearDown(client.close);
        final guard = SessionNavigationGuard()..markSessionAvailable();
        addTearDown(guard.dispose);
        await expectLater(
          invoke(source(client, guard, loader: () async => ready(role: role))),
          throwsA(isA<EmarVerificationSessionUnavailable>()),
        );
        expect(calls, 0);
      }
    },
  );

  test('cross-patient or changed order preflight never sends POST', () async {
    for (final record in [row(patientId: 99), row(sourceOrderVersion: 3)]) {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'POST') posts++;
        return http.Response(jsonEncode({'administration': record}), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        invoke(source(client, guard)),
        throwsA(
          anyOf(
            isA<FormatException>(),
            isA<EmarVerificationPreflightChanged>(),
          ),
        ),
      );
      expect(posts, 0);
    }
  });

  test('timeout is terminal and does not auto retry POST', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return http.Response(jsonEncode({'administration': row()}), 200);
      }
      posts++;
      throw http.ClientException('simulated lost response');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final adapter = source(client, guard);
    await expectLater(
      invoke(adapter),
      throwsA(isA<EmarVerificationOutcomeUnconfirmed>()),
    );
    await expectLater(
      invoke(adapter),
      throwsA(isA<EmarVerificationOutcomeUnconfirmed>()),
    );
    expect(posts, 1);
  });

  test(
    'successful response without matching server readback is uncertain',
    () async {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return http.Response(jsonEncode({'administration': row()}), 200);
        }
        posts++;
        return http.Response(jsonEncode({'verification': event()}), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        invoke(source(client, guard)),
        throwsA(isA<EmarVerificationOutcomeUnconfirmed>()),
      );
      expect(posts, 1);
    },
  );

  test(
    'session invalidation in flight cannot return confirmed result',
    () async {
      final pending = Completer<http.Response>();
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return http.Response(jsonEncode({'administration': row()}), 200);
        }
        posts++;
        return pending.future;
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final adapter = source(client, guard);
      final future = invoke(adapter);
      for (var i = 0; i < 50 && posts == 0; i++) {
        await Future<void>.delayed(const Duration(milliseconds: 1));
      }
      expect(posts, 1);
      guard.invalidate();
      pending.complete(
        http.Response(jsonEncode({'verification': event()}), 200),
      );
      await expectLater(
        future,
        throwsA(isA<EmarVerificationOutcomeUnconfirmed>()),
      );
    },
  );

  test(
    'cross-clinic verified server event cannot pass even with HTTP 200',
    () async {
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return http.Response(jsonEncode({'administration': row()}), 200);
        }
        return http.Response(
          jsonEncode({
            'verification': {...event(), 'clinic_id': 999},
          }),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        invoke(source(client, guard)),
        throwsA(isA<EmarVerificationOutcomeUnconfirmed>()),
      );
    },
  );
}
