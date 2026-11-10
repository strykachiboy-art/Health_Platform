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
import 'package:clinic_system_pro/features/lab/data/lab_result_finalization_remote_data_source.dart';

AuthSessionState _ready({String role = 'lab_technician', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'result-token',
        refreshToken: 'result-refresh',
        userId: 41,
        clinicContextId: clinic,
        role: role,
      ),
    );

Map<String, dynamic> _order({
  int clinic = 3,
  int patient = 7,
  bool processed = true,
  bool resulted = false,
  bool verified = false,
  bool completed = false,
  String status = 'in_progress',
}) => <String, dynamic>{
  'id': 31,
  'clinic_id': clinic,
  'patient_id': patient,
  'consultation_id': null,
  'ordered_by_id': 18,
  'status': completed ? 'completed' : status,
  'qr_code': 'QR-31',
  'collected_by_id': 12,
  'sample_collected_at': '2026-11-10T09:05:00+00:00',
  'processed_by_id': processed ? 13 : null,
  'processed_at': processed ? '2026-11-10T09:10:00+00:00' : null,
  'verified_by_id': verified ? 14 : null,
  'verified_at': verified ? '2026-11-10T09:30:00+00:00' : null,
  'completed_at': completed ? '2026-11-10T09:40:00+00:00' : null,
  'equipment_reference_id': null,
  'created_at': '2026-11-10T09:00:00+00:00',
  'items': <Map<String, dynamic>>[
    <String, dynamic>{
      'id': 131,
      'order_id': 31,
      'test_id': 10,
      'test': <String, dynamic>{
        'id': 10,
        'clinic_id': clinic,
        'name': 'Hemoglobin',
        'critical_low': '5.0',
        'sample_type': 'blood',
        'is_active': true,
      },
      'result_value': resulted ? '4.2' : null,
      'flag': resulted ? 'critical' : null,
      'result_notes': null,
      'resulted_at': resulted ? '2026-11-10T09:20:00+00:00' : null,
      'result_file_url': null,
    },
  ],
};

Map<String, dynamic> _ok(Map<String, dynamic> data) => <String, dynamic>{
  'success': true,
  'data': data,
};

LabResultFinalizationRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? session,
}) => LabResultFinalizationRemoteDataSource(
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
  test(
    'result POST once, receipt and GET confirm server critical flag',
    () async {
      final paths = <String>[];
      var posts = 0;
      final client = MockClient((request) async {
        paths.add('${request.method} ${request.url.path}');
        if (request.method == 'POST') {
          posts++;
          expect(request.url.path, '/api/v1/lab/order-items/131/result');
          expect(jsonDecode(request.body), {'result_value': '4.2'});
          expect(request.headers.containsKey('idempotency-key'), isFalse);
          return http.Response(
            jsonEncode(
              _ok(_order(resulted: true)['items'][0] as Map<String, dynamic>),
            ),
            200,
          );
        }
        return http.Response(jsonEncode(_ok(_order(resulted: posts > 0))), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final review = await source.review(
        patientId: 7,
        orderId: 31,
        action: LabResultAction.enterResult,
        orderItemId: 131,
        resultValue: '4.2',
      );
      expect(posts, 0);
      final confirmed = await source.submitOnce(reviewed: review);
      expect(confirmed.items[131]?['flag'], 'critical');
      expect(paths, <String>[
        'GET /api/v1/lab/orders/31',
        'GET /api/v1/lab/orders/31',
        'POST /api/v1/lab/order-items/131/result',
        'GET /api/v1/lab/orders/31',
      ]);
      await expectLater(
        source.submitOnce(reviewed: review),
        throwsA(isA<LabResultAlreadyAttempted>()),
      );
      expect(posts, 1);
    },
  );

  test('verification requires every result and confirms fresh GET', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
        expect(request.url.path, '/api/v1/lab/orders/31/verify');
        expect(jsonDecode(request.body), <String, dynamic>{});
      }
      return http.Response(
        jsonEncode(_ok(_order(resulted: true, verified: posts > 0))),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final review = await source.review(
      patientId: 7,
      orderId: 31,
      action: LabResultAction.verify,
    );
    final confirmed = await source.submitOnce(reviewed: review);
    expect(confirmed.verifiedById, 14);
    expect(confirmed.order.status.name, 'inProgress');
    expect(posts, 1);
  });

  test(
    'completion needs prior verification and confirms completed state',
    () async {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'POST') {
          posts++;
          expect(request.url.path, '/api/v1/lab/orders/31/complete');
        }
        return http.Response(
          jsonEncode(
            _ok(_order(resulted: true, verified: true, completed: posts > 0)),
          ),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final review = await source.review(
        patientId: 7,
        orderId: 31,
        action: LabResultAction.complete,
      );
      expect(
        (await source.submitOnce(reviewed: review)).order.status.name,
        'completed',
      );
      expect(posts, 1);
    },
  );

  test('unauthorized roles cannot read or write', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final role in <String>['doctor', 'nurse', 'admin', 'patient']) {
      await expectLater(
        _source(
          client,
          guard,
          session: () async => _ready(role: role),
        ).review(patientId: 7, orderId: 31, action: LabResultAction.verify),
        throwsA(isA<LabResultScopeUnavailable>()),
      );
    }
    expect(calls, 0);
  });

  test('cross-clinic and cross-patient response blocked', () async {
    for (final order in <Map<String, dynamic>>[
      _order(clinic: 8),
      _order(patient: 99),
    ]) {
      final client = MockClient(
        (_) async => http.Response(jsonEncode(_ok(order)), 200),
      );
      final guard = SessionNavigationGuard()..markSessionAvailable();
      await expectLater(
        _source(client, guard).review(
          patientId: 7,
          orderId: 31,
          action: LabResultAction.enterResult,
          orderItemId: 131,
          resultValue: '4.2',
        ),
        throwsA(isA<FormatException>()),
      );
      guard.dispose();
      client.close();
    }
  });

  test('verification without results fails before POST', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
      }
      return http.Response(jsonEncode(_ok(_order())), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(
        client,
        guard,
      ).review(patientId: 7, orderId: 31, action: LabResultAction.verify),
      throwsA(isA<LabResultEvidenceChanged>()),
    );
    expect(posts, 0);
  });

  test('completion without verification fails before POST', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
      }
      return http.Response(jsonEncode(_ok(_order(resulted: true))), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(
        client,
        guard,
      ).review(patientId: 7, orderId: 31, action: LabResultAction.complete),
      throwsA(isA<LabResultEvidenceChanged>()),
    );
    expect(posts, 0);
  });

  test('result after verification rejected before POST', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
      }
      return http.Response(
        jsonEncode(_ok(_order(resulted: true, verified: true))),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).review(
        patientId: 7,
        orderId: 31,
        action: LabResultAction.enterResult,
        orderItemId: 131,
        resultValue: '4.2',
      ),
      throwsA(isA<LabResultEvidenceChanged>()),
    );
    expect(posts, 0);
  });

  test('changed pre-POST evidence stops write', () async {
    var reads = 0;
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
      }
      reads++;
      return http.Response(jsonEncode(_ok(_order(resulted: reads > 1))), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final review = await source.review(
      patientId: 7,
      orderId: 31,
      action: LabResultAction.enterResult,
      orderItemId: 131,
      resultValue: '4.2',
    );
    await expectLater(
      source.submitOnce(reviewed: review),
      throwsA(isA<LabResultEvidenceChanged>()),
    );
    expect(posts, 0);
    expect(source.writeAttempted, isFalse);
  });

  test('dropped POST response remains locked, never replayed', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
        throw http.ClientException('Response lost after clinical write');
      }
      return http.Response(jsonEncode(_ok(_order())), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final review = await source.review(
      patientId: 7,
      orderId: 31,
      action: LabResultAction.enterResult,
      orderItemId: 131,
      resultValue: '4.2',
    );
    await expectLater(
      source.submitOnce(reviewed: review),
      throwsA(isA<LabResultOutcomeUnconfirmed>()),
    );
    await expectLater(
      source.submitOnce(reviewed: review),
      throwsA(isA<LabResultAlreadyAttempted>()),
    );
    expect(posts, 1);
  });

  test('logout between review and submit blocks POST', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
      }
      return http.Response(jsonEncode(_ok(_order(resulted: true))), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final review = await source.review(
      patientId: 7,
      orderId: 31,
      action: LabResultAction.verify,
    );
    guard.invalidate();
    await expectLater(
      source.submitOnce(reviewed: review),
      throwsA(isA<LabResultEvidenceChanged>()),
    );
    expect(posts, 0);
  });

  test('mismatched result receipt is unconfirmed, no retry', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
        final wrong = Map<String, dynamic>.from(
          _order(resulted: true)['items'][0] as Map<String, dynamic>,
        )..['id'] = 999;
        return http.Response(jsonEncode(_ok(wrong)), 200);
      }
      return http.Response(jsonEncode(_ok(_order(resulted: posts > 0))), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final review = await source.review(
      patientId: 7,
      orderId: 31,
      action: LabResultAction.enterResult,
      orderItemId: 131,
      resultValue: '4.2',
    );
    await expectLater(
      source.submitOnce(reviewed: review),
      throwsA(isA<LabResultOutcomeUnconfirmed>()),
    );
    expect(source.writeAttempted, isTrue);
    expect(posts, 1);
  });

  test('invalid result value never reaches server', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).review(
        patientId: 7,
        orderId: 31,
        action: LabResultAction.enterResult,
        orderItemId: 131,
        resultValue: '   ',
      ),
      throwsA(isA<FormatException>()),
    );
    expect(calls, 0);
  });
}
