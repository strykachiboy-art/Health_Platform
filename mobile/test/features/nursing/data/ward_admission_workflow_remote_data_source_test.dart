import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/nursing/data/models/ward_admission_workflow.dart';
import 'package:clinic_system_pro/features/nursing/data/ward_admission_workflow_remote_data_source.dart';

AuthSessionState _ready({String role = 'nurse', int clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'clinical-token',
        refreshToken: 'refresh',
        userId: 42,
        role: role,
        clinicContextId: clinicId,
      ),
    );

Map<String, dynamic> _admission({int bed = 9, int? reservation}) => {
  'id': 11,
  'patient_id': 7,
  'bed_id': bed,
  'admitted_by_id': 8,
  'reservation_id': reservation,
  'status': 'admitted',
  'reason': null,
  'admitted_at': '2026-11-08T10:00:00+00:00',
  'discharged_at': null,
  'created_at': null,
  'updated_at': null,
};

Map<String, dynamic> _reservation() => {
  'id': 33,
  'patient_id': 7,
  'bed_id': 9,
  'reserved_by_id': 8,
  'status': 'pending',
  'reason': null,
  'reserved_at': '2026-11-08T09:00:00+00:00',
  'expires_at': null,
};

Map<String, dynamic> _bed({String status = 'available'}) => {
  'id': 9,
  'ward_id': 3,
  'bed_number': '09',
  'status': status,
};

Map<String, dynamic> _ward({int clinic = 3}) => {
  'id': 3,
  'clinic_id': clinic,
  'name': 'General',
  'ward_type': 'general',
  'capacity': 20,
};

WardAdmissionWorkflowRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'nurse',
  int clinic = 3,
}) => WardAdmissionWorkflowRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => _ready(role: role, clinicId: clinic),
  sessionNavigationGuard: guard,
);

void main() {
  test('review reuses current-admission GET and never sends a POST', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      expect(req.method, 'GET');
      expect(req.headers['authorization'], 'Bearer clinical-token');
      if (req.url.path.endsWith('/current-admission')) {
        return http.Response('{"message":"No active admission found"}', 404);
      }
      if (req.url.path.endsWith('/reservation')) {
        return http.Response('{"message":"No active reservation found"}', 404);
      }
      if (req.url.path.endsWith('/beds/9')) {
        return http.Response(jsonEncode(_bed()), 200);
      }
      if (req.url.path.endsWith('/wards/3')) {
        return http.Response(jsonEncode(_ward()), 200);
      }
      throw StateError('Unexpected route');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final snapshot = await source.inspect(patientId: 7, bedId: 9);
    expect(snapshot.canStartNew, isTrue);
    expect(snapshot.allows(WardAdmissionAction.admitDirect), isTrue);
    expect(source.writeAttempted, isFalse);
    expect(calls, 4);
  });

  test('direct admission confirms exactly once with GET readback', () async {
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        writes++;
        expect(req.url.path, '/api/v1/wards/admissions');
        expect(req.headers.containsKey('idempotency-key'), isFalse);
        expect(jsonDecode(req.body), {'patient_id': 7, 'bed_id': 9});
        return http.Response(
          jsonEncode({
            'message': 'Patient admitted',
            'admission': _admission(),
          }),
          201,
        );
      }
      if (req.url.path.endsWith('/current-admission')) {
        return writes > 0
            ? http.Response(jsonEncode(_admission()), 200)
            : http.Response('{"message":"No active admission found"}', 404);
      }
      if (req.url.path.endsWith('/reservation')) {
        return http.Response('{"message":"No active reservation found"}', 404);
      }
      if (req.url.path.endsWith('/beds/9')) {
        return http.Response(
          jsonEncode(_bed(status: writes == 0 ? 'available' : 'occupied')),
          200,
        );
      }
      if (req.url.path.endsWith('/wards/3')) {
        return http.Response(jsonEncode(_ward()), 200);
      }
      throw StateError('Unknown route');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final reviewed = await source.inspect(patientId: 7, bedId: 9);
    final result = await source.perform(
      reviewed: reviewed,
      action: WardAdmissionAction.admitDirect,
    );
    expect(result.referenceId, 11);
    expect(source.writeAttempted, isTrue);
    expect(writes, 1);
    await expectLater(
      source.perform(
        reviewed: reviewed,
        action: WardAdmissionAction.admitDirect,
      ),
      throwsA(isA<WardAdmissionOutcomeUnconfirmed>()),
    );
    expect(writes, 1);
  });

  test(
    'reservation to admission needs live pending reservation and reserved bed',
    () async {
      var writes = 0;
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          writes++;
          expect(req.url.path, '/api/v1/wards/reservations/33/admit');
          return http.Response(
            jsonEncode({'admission': _admission(reservation: 33)}),
            201,
          );
        }
        if (req.url.path.endsWith('/current-admission')) {
          return writes > 0
              ? http.Response(jsonEncode(_admission(reservation: 33)), 200)
              : http.Response('{"message":"No active admission found"}', 404);
        }
        if (req.url.path.endsWith('/reservation')) {
          return writes > 0
              ? http.Response('{"message":"No active reservation found"}', 404)
              : http.Response(jsonEncode(_reservation()), 200);
        }
        if (req.url.path.endsWith('/beds/9')) {
          return http.Response(
            jsonEncode(_bed(status: writes > 0 ? 'occupied' : 'reserved')),
            200,
          );
        }
        if (req.url.path.endsWith('/wards/3')) {
          return http.Response(jsonEncode(_ward()), 200);
        }
        throw StateError('Unknown route');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final before = await source.inspect(patientId: 7, bedId: 9);
      expect(before.canAdmitReservation, isTrue);
      final receipt = await source.perform(
        reviewed: before,
        action: WardAdmissionAction.admitFromReservation,
      );
      expect(receipt.referenceId, 11);
      expect(writes, 1);
    },
  );

  test(
    'transfer requires original bed and verifies subsequent current admission',
    () async {
      var writes = 0;
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          writes++;
          expect(req.url.path, '/api/v1/wards/admissions/11/transfer');
          expect(jsonDecode(req.body), {'to_bed_id': 9});
          return http.Response(
            jsonEncode({
              'transfer': {
                'id': 44,
                'admission_id': 11,
                'from_bed_id': 5,
                'to_bed_id': 9,
              },
            }),
            201,
          );
        }
        if (req.url.path.endsWith('/current-admission')) {
          return http.Response(
            jsonEncode(_admission(bed: writes > 0 ? 9 : 5)),
            200,
          );
        }
        if (req.url.path.endsWith('/reservation')) {
          return http.Response(
            '{"message":"No active reservation found"}',
            404,
          );
        }
        if (req.url.path.endsWith('/beds/9')) {
          return http.Response(
            jsonEncode(_bed(status: writes > 0 ? 'occupied' : 'available')),
            200,
          );
        }
        if (req.url.path.endsWith('/wards/3')) {
          return http.Response(jsonEncode(_ward()), 200);
        }
        throw StateError('Unknown route');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final reviewed = await source.inspect(patientId: 7, bedId: 9);
      expect(reviewed.canTransfer, isTrue);
      final receipt = await source.perform(
        reviewed: reviewed,
        action: WardAdmissionAction.transfer,
      );
      expect(receipt.referenceId, 44);
      expect(writes, 1);
    },
  );

  test('unknown POST transport outcome locks any replay', () async {
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        writes++;
        throw http.ClientException('lost response');
      }
      if (req.url.path.endsWith('/current-admission')) {
        return http.Response('{"message":"No active admission found"}', 404);
      }
      if (req.url.path.endsWith('/reservation')) {
        return http.Response('{"message":"No active reservation found"}', 404);
      }
      if (req.url.path.endsWith('/beds/9')) {
        return http.Response(jsonEncode(_bed()), 200);
      }
      return http.Response(jsonEncode(_ward()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final reviewed = await source.inspect(patientId: 7, bedId: 9);
    await expectLater(
      source.perform(
        reviewed: reviewed,
        action: WardAdmissionAction.admitDirect,
      ),
      throwsA(isA<WardAdmissionOutcomeUnconfirmed>()),
    );
    await expectLater(
      source.perform(
        reviewed: reviewed,
        action: WardAdmissionAction.admitDirect,
      ),
      throwsA(isA<WardAdmissionOutcomeUnconfirmed>()),
    );
    expect(source.writeAttempted, isTrue);
    expect(writes, 1);
  });

  test(
    'receptionist cannot send clinical writes; clinic mismatch fails closed',
    () async {
      var writes = 0;
      final client = MockClient((req) async {
        if (req.method != 'GET') writes++;
        if (req.url.path.endsWith('/current-admission')) {
          return http.Response('{"message":"No active admission found"}', 404);
        }
        if (req.url.path.endsWith('/reservation')) {
          return http.Response(
            '{"message":"No active reservation found"}',
            404,
          );
        }
        if (req.url.path.endsWith('/beds/9')) {
          return http.Response(jsonEncode(_bed()), 200);
        }
        return http.Response(jsonEncode(_ward()), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final receptionist = _source(client, guard, role: 'receptionist');
      final reviewed = await receptionist.inspect(patientId: 7, bedId: 9);
      expect(reviewed.mayWrite, isFalse);
      await expectLater(
        receptionist.perform(
          reviewed: reviewed,
          action: WardAdmissionAction.admitDirect,
        ),
        throwsA(isA<WardAdmissionPreflightException>()),
      );
      expect(writes, 0);
      final wrongClinic = _source(client, guard, clinic: 4);
      await expectLater(
        wrongClinic.inspect(patientId: 7, bedId: 9),
        throwsFormatException,
      );
    },
  );

  test('fresh preflight rejects changed bed without POST', () async {
    var reads = 0;
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        writes++;
        return http.Response('{}', 500);
      }
      if (req.url.path.endsWith('/current-admission')) {
        return http.Response('{"message":"No active admission found"}', 404);
      }
      if (req.url.path.endsWith('/reservation')) {
        return http.Response('{"message":"No active reservation found"}', 404);
      }
      if (req.url.path.endsWith('/beds/9')) {
        reads++;
        return http.Response(
          jsonEncode(_bed(status: reads == 1 ? 'available' : 'occupied')),
          200,
        );
      }
      return http.Response(jsonEncode(_ward()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final reviewed = await source.inspect(patientId: 7, bedId: 9);
    await expectLater(
      source.perform(
        reviewed: reviewed,
        action: WardAdmissionAction.admitDirect,
      ),
      throwsA(isA<WardAdmissionPreflightException>()),
    );
    expect(source.writeAttempted, isFalse);
    expect(writes, 0);
  });
}
