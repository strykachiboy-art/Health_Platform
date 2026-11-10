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
import 'package:clinic_system_pro/features/emar/data/emar_medication_administration_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/emar_barcode_verification.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';

AuthSessionState ready({String role = 'nurse', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 's04-token',
        refreshToken: 's04-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> row({
  String status = 'due',
  int? eventId,
  int version = 2,
  int patient = 5,
}) => {
  'id': 11,
  'clinic_id': 3,
  'patient_id': patient,
  'admission_id': 7,
  'prescription_id': 17,
  'prescription_item_id': 19,
  'status': status,
  'record_status': status == 'administered' ? 'final' : 'draft',
  'record_version': 1,
  'source_order_version': version,
  'dose_value': '0.125',
  'dose_unit': 'mg',
  'route_code': 'oral',
  'scheduled_at': '2026-11-09T11:00:00Z',
  'administered_at': status == 'administered' ? '2026-11-09T11:01:00Z' : null,
  'barcode_verification_event_id': status == 'administered' ? 41 : eventId,
  'verification_result': 'barcode_verified',
};

EmarAdministration selected() => EmarAdministration.fromJson(
  row(),
  expectedClinicId: 3,
  expectedPatientId: 5,
  expectedAdmissionId: 7,
  expectedId: 11,
);

EmarBarcodeVerification verified({bool success = true, int id = 41}) =>
    EmarBarcodeVerification(
      id: id,
      clinicId: 3,
      administrationId: success ? 11 : null,
      patientId: success ? 5 : null,
      admissionId: success ? 7 : null,
      result: success
          ? EmarBarcodeResult.barcodeVerified
          : EmarBarcodeResult.notBarcodeVerified,
      failureCode: success ? null : EmarBarcodeFailure.wrongPatient,
      patientMatch: success,
      medicationMatch: success,
      doseMatch: success,
      routeMatch: success,
      timeWindowMatch: success,
      sourceOrderVersion: success ? 2 : null,
      policyVersion: 1,
    );

Map<String, dynamic> history({int latest = 41, bool success = true}) => {
  'history': {
    'total': 1,
    'items': [
      {
        'id': latest,
        'clinic_id': 3,
        'medication_administration_id': 11,
        'actor_staff_id': 17,
        'verification_result': success
            ? 'barcode_verified'
            : 'not_barcode_verified',
        'failure_code': success ? null : 'wrong_patient',
        'failure_reason': success ? null : 'Wrong patient',
        'patient_match': success,
        'medication_match': success,
        'dose_match': success,
        'route_match': success,
        'time_window_match': success,
        'scan_timestamp': '2026-11-09T11:00:00Z',
        'verification_policy_version': 1,
      },
    ],
  },
};

EmarMedicationAdministrationRemoteDataSource adapter(
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
  return EmarMedicationAdministrationRemoteDataSource(
    apiClient: api,
    readSource: EmarReadRemoteDataSource(
      apiClient: api,
      loadSession: load,
      sessionNavigationGuard: guard,
    ),
    loadSession: load,
    sessionNavigationGuard: guard,
  );
}

Future<EmarAdministration> submit(
  EmarMedicationAdministrationRemoteDataSource a, {
  EmarBarcodeVerification? event,
}) => a.administerOnce(
  reviewed: selected(),
  verified: event ?? verified(),
  operationKey: 'admin-once',
);

void main() {
  test('one bound POST then authenticated server readback', () async {
    var postCount = 0;
    var detailCount = 0;
    final client = MockClient((request) async {
      expect(request.headers['authorization'], 'Bearer s04-token');
      if (request.url.path == '/api/v1/emar/11/barcode-events') {
        expect(request.method, 'GET');
        return http.Response(jsonEncode(history()), 200);
      }
      if (request.method == 'GET') {
        detailCount++;
        return http.Response(
          jsonEncode({
            'administration': row(
              status: detailCount == 1 ? 'due' : 'administered',
            ),
          }),
          200,
        );
      }
      postCount++;
      expect(request.method, 'POST');
      expect(request.url.path, '/api/v1/emar/11/administer');
      expect(request.headers['idempotency-key'], 'admin-once');
      expect(jsonDecode(request.body), {'barcode_verification_event_id': 41});
      return http.Response(
        jsonEncode({'administration': row(status: 'administered')}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final a = adapter(client, guard);
    final result = await submit(a);
    expect(result.status, EmarAdministrationStatus.administered);
    expect(result.verificationEventId, 41);
    expect(postCount, 1);
    expect(detailCount, 2);
    expect(a.attemptStarted, isTrue);
    await expectLater(
      submit(a),
      throwsA(isA<EmarAdministerOutcomeUnconfirmed>()),
    );
    expect(postCount, 1);
  });

  test('failed barcode verification never submits', () async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final a = adapter(client, guard);
    await expectLater(
      submit(a, event: verified(success: false)),
      throwsA(isA<EmarAdministerEvidenceChanged>()),
    );
    expect(requests, 0);
    expect(a.attemptStarted, isFalse);
  });

  test('doctor cannot administer before any API request', () async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final a = adapter(client, guard, loader: () async => ready(role: 'doctor'));
    await expectLater(
      submit(a),
      throwsA(isA<EmarAdministerScopeUnavailable>()),
    );
    expect(requests, 0);
  });

  test('wrong clinic cannot administer', () async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final a = adapter(client, guard, loader: () async => ready(clinic: 7));
    await expectLater(
      submit(a),
      throwsA(isA<EmarAdministerScopeUnavailable>()),
    );
    expect(requests, 0);
  });

  test('newer failed scan prevents administration', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') posts++;
      if (request.url.path.endsWith('barcode-events')) {
        return http.Response(
          jsonEncode(history(latest: 42, success: false)),
          200,
        );
      }
      return http.Response(jsonEncode({'administration': row()}), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final a = adapter(client, guard);
    await expectLater(submit(a), throwsA(isA<EmarAdministerEvidenceChanged>()));
    expect(posts, 0);
  });

  test('stale source order blocks POST', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') posts++;
      return http.Response(
        jsonEncode({'administration': row(version: 3)}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      submit(adapter(client, guard)),
      throwsA(isA<EmarAdministerEvidenceChanged>()),
    );
    expect(posts, 0);
  });

  test('timed out/uncertain POST is never retried', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.url.path.endsWith('barcode-events')) {
        return http.Response(jsonEncode(history()), 200);
      }
      if (request.method == 'GET') {
        return http.Response(jsonEncode({'administration': row()}), 200);
      }
      posts++;
      throw http.ClientException('uncertain POST transport');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final a = adapter(client, guard);
    await expectLater(
      submit(a),
      throwsA(isA<EmarAdministerOutcomeUnconfirmed>()),
    );
    await expectLater(
      submit(a),
      throwsA(isA<EmarAdministerOutcomeUnconfirmed>()),
    );
    expect(posts, 1);
  });

  test('sign out during POST clears authority and locks attempt', () async {
    final pending = Completer<http.Response>();
    var posts = 0;
    final client = MockClient((request) async {
      if (request.url.path.endsWith('barcode-events')) {
        return http.Response(jsonEncode(history()), 200);
      }
      if (request.method == 'GET') {
        return http.Response(jsonEncode({'administration': row()}), 200);
      }
      posts++;
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final a = adapter(client, guard);
    final result = submit(a);
    for (var i = 0; i < 30 && posts == 0; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 1));
    }
    expect(posts, 1);
    guard.markSignedOut();
    pending.complete(
      http.Response(
        jsonEncode({'administration': row(status: 'administered')}),
        200,
      ),
    );
    await expectLater(result, throwsA(isA<EmarAdministerOutcomeUnconfirmed>()));
    expect(posts, 1);
  });
}
