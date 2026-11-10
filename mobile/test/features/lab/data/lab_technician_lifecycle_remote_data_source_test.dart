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
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/data/lab_technician_lifecycle_remote_data_source.dart';

AuthSessionState _ready({String role = 'lab_technician', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'tech-token',
        refreshToken: 'tech-refresh',
        userId: 41,
        clinicContextId: clinic,
        role: role,
      ),
    );

Map<String, dynamic> _test() => <String, dynamic>{
  'id': 10,
  'clinic_id': 3,
  'name': 'Hemoglobin',
  'sample_type': 'blood',
  'is_active': true,
};

Map<String, dynamic> _order({
  String status = 'ordered',
  int clinic = 3,
  int patient = 7,
  String? collectedAt,
  int? collectedBy,
  int? processedBy,
  String? processedAt,
  String? equipment,
}) => <String, dynamic>{
  'id': 31,
  'clinic_id': clinic,
  'patient_id': patient,
  'consultation_id': null,
  'ordered_by_id': 18,
  'status': status,
  'qr_code': 'ORDER-QR-31',
  'collected_by_id': collectedBy,
  'sample_collected_at': collectedAt,
  'processed_by_id': processedBy,
  'processed_at': processedAt,
  'equipment_reference_id': equipment,
  'created_at': '2026-11-10T09:00:00+00:00',
  'items': <Map<String, dynamic>>[
    <String, dynamic>{
      'id': 131,
      'order_id': 31,
      'test_id': 10,
      'test': _test(),
      'result_value': null,
      'flag': null,
      'result_notes': null,
    },
  ],
};

Map<String, dynamic> _ok(Map<String, dynamic> data) => <String, dynamic>{
  'success': true,
  'data': data,
};

LabTechnicianLifecycleRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? session,
}) => LabTechnicianLifecycleRemoteDataSource(
  reader: LabOrderReadRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: session ?? () async => _ready(),
    sessionNavigationGuard: guard,
  ),
);

void main() {
  test('collect QR review, one POST, fresh GET, no replay', () async {
    var posts = 0;
    final paths = <String>[];
    final client = MockClient((request) async {
      paths.add('${request.method} ${request.url.path}');
      expect(request.headers['authorization'], 'Bearer tech-token');
      if (request.method == 'POST') {
        posts++;
        expect(request.url.path, '/api/v1/lab/orders/31/collect-sample');
        expect(jsonDecode(request.body), {'scanned_qr_code': 'ORDER-QR-31'});
        expect(request.headers.containsKey('idempotency-key'), isFalse);
      }
      return http.Response(
        jsonEncode(
          _ok(
            posts == 0
                ? _order()
                : _order(
                    status: 'sample_collected',
                    collectedBy: 12,
                    collectedAt: '2026-11-10T09:10:00+00:00',
                  ),
          ),
        ),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final reviewed = await source.review(
      patientId: 7,
      orderId: 31,
      action: LabTechnicianAction.collect,
      scannedQrCode: 'ORDER-QR-31',
    );
    expect(posts, 0);
    final result = await source.submitOnce(reviewed: reviewed);
    expect(result.order.status.name, 'sampleCollected');
    expect(posts, 1);
    expect(paths, <String>[
      'GET /api/v1/lab/orders/31',
      'GET /api/v1/lab/orders/31',
      'POST /api/v1/lab/orders/31/collect-sample',
      'GET /api/v1/lab/orders/31',
    ]);
    await expectLater(
      source.submitOnce(reviewed: reviewed),
      throwsA(isA<LabTechnicianAlreadyAttempted>()),
    );
    expect(posts, 1);
  });

  test('wrong QR stops before clinical POST', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_ok(_order())), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).review(
        patientId: 7,
        orderId: 31,
        action: LabTechnicianAction.collect,
        scannedQrCode: 'WRONG-QR',
      ),
      throwsA(isA<LabTechnicianEvidenceChanged>()),
    );
    expect(calls, 1);
  });

  test('unauthorized doctor and patient issue no GET or POST', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_ok(_order())), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final role in <String>['doctor', 'patient', 'admin', 'nurse']) {
      await expectLater(
        _source(client, guard, session: () async => _ready(role: role)).review(
          patientId: 7,
          orderId: 31,
          action: LabTechnicianAction.collect,
          scannedQrCode: 'ORDER-QR-31',
        ),
        throwsA(isA<LabTechnicianScopeUnavailable>()),
      );
    }
    expect(calls, 0);
  });

  test('wrong clinic or patient from server fails closed', () async {
    for (final order in <Map<String, dynamic>>[
      _order(clinic: 8),
      _order(patient: 19),
    ]) {
      final client = MockClient(
        (_) async => http.Response(jsonEncode(_ok(order)), 200),
      );
      final guard = SessionNavigationGuard()..markSessionAvailable();
      await expectLater(
        _source(client, guard).review(
          patientId: 7,
          orderId: 31,
          action: LabTechnicianAction.collect,
          scannedQrCode: 'ORDER-QR-31',
        ),
        throwsA(isA<FormatException>()),
      );
      client.close();
      guard.dispose();
    }
  });

  test('processing without collection cannot be reviewed', () async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_ok(_order())), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(
        client,
        guard,
      ).review(patientId: 7, orderId: 31, action: LabTechnicianAction.process),
      throwsA(isA<LabTechnicianEvidenceChanged>()),
    );
  });

  test('equipment link needs a reference before any GET', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_ok(_order())), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).review(
        patientId: 7,
        orderId: 31,
        action: LabTechnicianAction.linkEquipment,
      ),
      throwsA(isA<FormatException>()),
    );
    expect(calls, 0);
  });

  test('equipment linkage from collected state is confirmed online', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
        expect(request.url.path, '/api/v1/lab/orders/31/equipment');
        expect(jsonDecode(request.body), {
          'equipment_reference_id': 'MACHINE-7',
        });
      }
      return http.Response(
        jsonEncode(
          _ok(
            _order(
              status: posts == 0 ? 'sample_collected' : 'in_progress',
              collectedBy: 12,
              collectedAt: '2026-11-10T09:10:00+00:00',
              equipment: posts == 0 ? null : 'MACHINE-7',
            ),
          ),
        ),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final reviewed = await source.review(
      patientId: 7,
      orderId: 31,
      action: LabTechnicianAction.linkEquipment,
      equipmentReference: 'MACHINE-7',
    );
    final result = await source.submitOnce(reviewed: reviewed);
    expect(result.equipmentReference, 'MACHINE-7');
    expect(posts, 1);
  });

  test(
    'processing after collection confirms processor and timestamp',
    () async {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'POST') {
          posts++;
          expect(request.url.path, '/api/v1/lab/orders/31/process');
          expect(jsonDecode(request.body), <String, dynamic>{});
        }
        return http.Response(
          jsonEncode(
            _ok(
              _order(
                status: posts == 0 ? 'sample_collected' : 'in_progress',
                collectedBy: 12,
                collectedAt: '2026-11-10T09:10:00+00:00',
                processedBy: posts == 0 ? null : 13,
                processedAt: posts == 0 ? null : '2026-11-10T09:20:00+00:00',
              ),
            ),
          ),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final reviewed = await source.review(
        patientId: 7,
        orderId: 31,
        action: LabTechnicianAction.process,
      );
      final result = await source.submitOnce(reviewed: reviewed);
      expect(result.processedById, 13);
      expect(posts, 1);
    },
  );

  test('server state change before submission prevents POST', () async {
    var gets = 0;
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
      }
      if (request.method == 'GET') {
        gets++;
      }
      return http.Response(
        jsonEncode(_ok(_order(status: gets == 1 ? 'ordered' : 'cancelled'))),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final reviewed = await source.review(
      patientId: 7,
      orderId: 31,
      action: LabTechnicianAction.collect,
      scannedQrCode: 'ORDER-QR-31',
    );
    await expectLater(
      source.submitOnce(reviewed: reviewed),
      throwsA(isA<LabTechnicianEvidenceChanged>()),
    );
    expect(posts, 0);
  });

  test('lost POST remains locked without another clinical request', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
        throw http.ClientException('Transport lost after clinical send');
      }
      return http.Response(jsonEncode(_ok(_order())), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final reviewed = await source.review(
      patientId: 7,
      orderId: 31,
      action: LabTechnicianAction.collect,
      scannedQrCode: 'ORDER-QR-31',
    );
    await expectLater(
      source.submitOnce(reviewed: reviewed),
      throwsA(isA<LabTechnicianOutcomeUnconfirmed>()),
    );
    await expectLater(
      source.submitOnce(reviewed: reviewed),
      throwsA(isA<LabTechnicianAlreadyAttempted>()),
    );
    expect(posts, 1);
  });

  test('logout during order GET discards technician evidence', () async {
    final pending = Completer<http.Response>();
    final started = Completer<void>();
    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final future = source.review(
      patientId: 7,
      orderId: 31,
      action: LabTechnicianAction.collect,
      scannedQrCode: 'ORDER-QR-31',
    );
    await started.future;
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_ok(_order())), 200));
    await expectLater(future, throwsA(isA<LabTechnicianScopeUnavailable>()));
  });
}
