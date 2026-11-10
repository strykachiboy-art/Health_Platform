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
import 'package:clinic_system_pro/features/lab/data/lab_order_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/data/models/lab_order_read.dart';

AuthSessionState _ready({String role = 'doctor', int clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 's04b-token',
        refreshToken: 's04b-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinicId,
      ),
    );

Map<String, dynamic> _test({
  int id = 10,
  int clinic = 3,
  bool active = true,
  String name = 'Hemoglobin',
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'name': name,
  'sample_type': 'blood',
  'is_active': active,
};

Map<String, dynamic> _order({
  int id = 31,
  int patient = 7,
  int clinic = 3,
  int testId = 10,
  String status = 'ordered',
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'consultation_id': null,
  'ordered_by_id': 18,
  'status': status,
  'created_at': '2026-11-10T08:00:00+00:00',
  'items': <Map<String, dynamic>>[
    <String, dynamic>{
      'id': id + 100,
      'order_id': id,
      'test_id': testId,
      'test': _test(id: testId, clinic: clinic),
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

LabOrderMutationRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) => LabOrderMutationRemoteDataSource(
  reader: LabOrderReadRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: loader ?? () async => _ready(),
    sessionNavigationGuard: guard,
  ),
);

void main() {
  test(
    'doctor: reviews active catalog and creates once with GET readback',
    () async {
      final requests = <String>[];
      var posts = 0;
      final client = MockClient((request) async {
        requests.add('${request.method} ${request.url.path}');
        expect(request.headers['authorization'], 'Bearer s04b-token');
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        if (request.url.path == '/api/v1/lab/tests/10') {
          return http.Response(jsonEncode(_ok(_test())), 200);
        }
        if (request.method == 'POST') {
          posts++;
          expect(request.url.path, '/api/v1/lab/orders');
          expect(jsonDecode(request.body), <String, dynamic>{
            'patient_id': 7,
            'test_ids': <int>[10],
          });
          return http.Response(jsonEncode(_ok(_order())), 201);
        }
        if (request.url.path == '/api/v1/lab/orders/31') {
          return http.Response(jsonEncode(_ok(_order())), 200);
        }
        return http.Response('{}', 404);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final review = await source.reviewCreate(
        patientId: 7,
        testIds: <int>[10],
      );
      expect(review.testNames, <String>['Hemoglobin']);
      final result = await source.createOnce(reviewed: review);
      expect(result.id, 31);
      expect(result.status, LabOrderStatus.ordered);
      expect(posts, 1);
      expect(requests, <String>[
        'GET /api/v1/lab/tests/10',
        'GET /api/v1/lab/tests/10',
        'POST /api/v1/lab/orders',
        'GET /api/v1/lab/orders/31',
      ]);
      await expectLater(
        source.createOnce(reviewed: review),
        throwsA(isA<LabMutationAlreadyAttempted>()),
      );
      expect(posts, 1);
    },
  );

  test(
    'nurse is permitted to create; admin, technician, patient are denied',
    () async {
      for (final role in <String>['admin', 'lab_technician', 'patient']) {
        var calls = 0;
        final client = MockClient((_) async {
          calls++;
          return http.Response('{}', 200);
        });
        final guard = SessionNavigationGuard()..markSessionAvailable();
        await expectLater(
          _source(
            client,
            guard,
            loader: () async => _ready(role: role),
          ).reviewCreate(patientId: 7, testIds: <int>[10]),
          throwsA(isA<LabMutationScopeUnavailable>()),
        );
        expect(calls, 0);
        client.close();
        guard.dispose();
      }
      final client = MockClient(
        (request) async => http.Response(jsonEncode(_ok(_test())), 200),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final review = await _source(
        client,
        guard,
        loader: () async => _ready(role: 'nurse'),
      ).reviewCreate(patientId: 7, testIds: <int>[10]);
      expect(review.testIds, <int>[10]);
    },
  );

  test('invalid IDs and duplicates never send a request', () async {
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
      source.reviewCreate(patientId: 0, testIds: <int>[10]),
      throwsArgumentError,
    );
    await expectLater(
      source.reviewCreate(patientId: 7, testIds: <int>[]),
      throwsA(isA<FormatException>()),
    );
    await expectLater(
      source.reviewCreate(patientId: 7, testIds: <int>[10, 10]),
      throwsA(isA<FormatException>()),
    );
    await expectLater(
      source.reviewCreate(patientId: 7, testIds: <int>[0]),
      throwsArgumentError,
    );
    expect(calls, 0);
  });

  test('inactive, wrong-clinic, and changed catalog block create', () async {
    for (final wrong in <Map<String, dynamic>>[
      _test(active: false),
      _test(clinic: 4),
    ]) {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'POST') {
          posts++;
        }
        return http.Response(jsonEncode(_ok(wrong)), 200);
      });
      final guard = SessionNavigationGuard()..markSessionAvailable();
      final source = _source(client, guard);
      await expectLater(
        source.reviewCreate(patientId: 7, testIds: <int>[10]),
        throwsA(
          anyOf(isA<LabMutationEvidenceChanged>(), isA<FormatException>()),
        ),
      );
      expect(posts, 0);
      client.close();
      guard.dispose();
    }
    var catalogCalls = 0;
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
      }
      catalogCalls++;
      return http.Response(
        jsonEncode(
          _ok(_test(name: catalogCalls == 1 ? 'Hemoglobin' : 'Different test')),
        ),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final review = await source.reviewCreate(patientId: 7, testIds: <int>[10]);
    await expectLater(
      source.createOnce(reviewed: review),
      throwsA(isA<LabMutationEvidenceChanged>()),
    );
    expect(posts, 0);
  });

  test('lost POST response locks adapter and never replays', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.url.path.endsWith('/tests/10')) {
        return http.Response(jsonEncode(_ok(_test())), 200);
      }
      posts++;
      throw http.ClientException('Connection dropped after POST');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final review = await source.reviewCreate(patientId: 7, testIds: <int>[10]);
    await expectLater(
      source.createOnce(reviewed: review),
      throwsA(isA<LabMutationOutcomeUnconfirmed>()),
    );
    await expectLater(
      source.createOnce(reviewed: review),
      throwsA(isA<LabMutationAlreadyAttempted>()),
    );
    expect(posts, 1);
  });

  test(
    'wrong order on POST response is unconfirmed and cannot repeat',
    () async {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.url.path.endsWith('/tests/10')) {
          return http.Response(jsonEncode(_ok(_test())), 200);
        }
        if (request.method == 'POST') {
          posts++;
          return http.Response(jsonEncode(_ok(_order(patient: 99))), 201);
        }
        return http.Response('{}', 404);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final review = await source.reviewCreate(
        patientId: 7,
        testIds: <int>[10],
      );
      await expectLater(
        source.createOnce(reviewed: review),
        throwsA(isA<LabMutationOutcomeUnconfirmed>()),
      );
      expect(source.writeAttempted, isTrue);
      expect(posts, 1);
    },
  );

  test(
    'doctor cancels only after fresh eligible order and verifies GET',
    () async {
      final routes = <String>[];
      final client = MockClient((request) async {
        routes.add('${request.method} ${request.url.path}');
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        if (request.method == 'POST') {
          expect(jsonDecode(request.body), <String, dynamic>{
            'reason': 'Wrong test',
          });
          return http.Response(
            jsonEncode(_ok(_order(status: 'cancelled'))),
            200,
          );
        }
        if (routes.length == 4) {
          return http.Response(
            jsonEncode(_ok(_order(status: 'cancelled'))),
            200,
          );
        }
        return http.Response(jsonEncode(_ok(_order())), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final review = await source.reviewCancel(patientId: 7, orderId: 31);
      final cancelled = await source.cancelOnce(
        reviewed: review,
        reason: 'Wrong test',
      );
      expect(cancelled.status, LabOrderStatus.cancelled);
      expect(routes, <String>[
        'GET /api/v1/lab/orders/31',
        'GET /api/v1/lab/orders/31',
        'POST /api/v1/lab/orders/31/cancel',
        'GET /api/v1/lab/orders/31',
      ]);
    },
  );

  test(
    'completed order cannot be cancelled; invalid reason sends no POST',
    () async {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'POST') {
          posts++;
        }
        return http.Response(jsonEncode(_ok(_order(status: 'completed'))), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(client, guard).reviewCancel(patientId: 7, orderId: 31),
        throwsA(isA<LabMutationEvidenceChanged>()),
      );
      expect(posts, 0);
    },
  );

  test('logout during preflight blocks the write', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
    var posts = 0;
    final client = MockClient((request) {
      if (request.method == 'POST') {
        posts++;
        return Future.value(http.Response('{}', 200));
      }
      if (!started.isCompleted) {
        started.complete();
      }
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final future = source.reviewCreate(patientId: 7, testIds: <int>[10]);
    await started.future;
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_ok(_test())), 200));
    await expectLater(future, throwsA(isA<LabMutationScopeUnavailable>()));
    expect(posts, 0);
  });
}
