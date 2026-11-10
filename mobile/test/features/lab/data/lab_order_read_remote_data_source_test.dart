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
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/data/models/lab_order_read.dart';

AuthSessionState _ready({String role = 'doctor', int clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 's04a-token',
        refreshToken: 's04a-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinicId,
      ),
    );

Map<String, dynamic> _testRow({int clinicId = 3, int id = 10}) => {
  'id': id,
  'clinic_id': clinicId,
  'name': 'Hemoglobin',
  'sample_type': 'blood',
  'reference_range': '11-15',
  'unit': 'g/dL',
  'critical_low': '6.0',
  'critical_high': '20.0',
  'is_active': true,
};

Map<String, dynamic> _orderRow({
  int id = 12,
  int patientId = 7,
  int clinicId = 3,
  String status = 'completed',
  String? flag = 'critical',
}) => {
  'id': id,
  'clinic_id': clinicId,
  'patient_id': patientId,
  'consultation_id': null,
  'ordered_by_id': 18,
  'status': status,
  'created_at': '2026-11-10T08:00:00+00:00',
  'items': [
    {
      'id': id + 100,
      'order_id': id,
      'test_id': 10,
      'test': _testRow(clinicId: clinicId),
      'result_value': '5.4',
      'flag': flag,
      'result_notes': 'Verify immediately',
    },
  ],
};

Map<String, dynamic> _page(
  List<Map<String, dynamic>> rows, {
  int? total,
  int page = 1,
  int perPage = 20,
}) => {
  'success': true,
  'data': {
    'items': rows,
    'total': total ?? rows.length,
    'page': page,
    'per_page': perPage,
  },
};

LabOrderReadRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) => LabOrderReadRemoteDataSource(
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
    'patient order GET uses authenticated patient pagination and scope',
    () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/lab/orders');
        expect(request.url.queryParameters, {
          'patient_id': '7',
          'page': '1',
          'per_page': '20',
        });
        expect(request.headers['authorization'], 'Bearer s04a-token');
        expect(request.headers.containsKey('x-emergency-access-id'), isFalse);
        return http.Response(jsonEncode(_page([_orderRow()])), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);

      final result = await _source(
        client,
        guard,
      ).fetchPatientOrders(patientId: 7);
      expect(calls, 1);
      expect(result.items.single.status, LabOrderStatus.completed);
      expect(result.items.single.items.single.flag, LabResultFlag.critical);
      expect(result.items.single.items.single.test!.name, 'Hemoglobin');
    },
  );

  test('catalog GET reads backend test definitions only', () async {
    final client = MockClient((request) async {
      expect(request.url.path, '/api/v1/lab/tests');
      expect(request.url.queryParameters['active_only'], 'true');
      return http.Response(jsonEncode(_page([_testRow()])), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final result = await _source(client, guard).fetchCatalog();
    expect(result.items.single.criticalLow, '6.0');
    expect(result.items.single.sampleType, 'blood');
  });

  test(
    'detail GET confirms exact patient clinic order and nested test',
    () async {
      final client = MockClient((request) async {
        expect(request.url.path, '/api/v1/lab/orders/12');
        return http.Response(
          jsonEncode({'success': true, 'data': _orderRow()}),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final result = await _source(
        client,
        guard,
      ).fetchOrder(patientId: 7, orderId: 12);
      expect(result.items.single.resultValue, '5.4');
    },
  );

  for (final role in <String>[
    'admin',
    'doctor',
    'nurse',
    'receptionist',
    'lab_technician',
  ]) {
    test('$role is a permitted normal lab reader', () async {
      final client = MockClient(
        (_) async => http.Response(jsonEncode(_page([_orderRow()])), 200),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final result = await _source(
        client,
        guard,
        loader: () async => _ready(role: role),
      ).fetchPatientOrders(patientId: 7);
      expect(result.items.single.id, 12);
    });
  }

  test('patient/pharmacist cannot dispatch a lab GET', () async {
    var count = 0;
    final client = MockClient((_) async {
      count++;
      return http.Response(jsonEncode(_page([_orderRow()])), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final role in <String>['patient', 'pharmacist']) {
      await expectLater(
        _source(
          client,
          guard,
          loader: () async => _ready(role: role),
        ).fetchPatientOrders(patientId: 7),
        throwsA(isA<LabReadSessionUnavailable>()),
      );
    }
    expect(count, 0);
  });

  test(
    'cross-patient cross-clinic and unknown result flag fail closed',
    () async {
      for (final row in <Map<String, dynamic>>[
        _orderRow(patientId: 9),
        _orderRow(clinicId: 11),
        _orderRow(flag: 'pending'),
      ]) {
        final client = MockClient(
          (_) async => http.Response(jsonEncode(_page([row])), 200),
        );
        final guard = SessionNavigationGuard()..markSessionAvailable();
        await expectLater(
          _source(client, guard).fetchPatientOrders(patientId: 7),
          throwsA(isA<FormatException>()),
        );
        client.close();
        guard.dispose();
      }
    },
  );

  test('cross-order result item fails closed', () async {
    final row = _orderRow();
    final item = (row['items'] as List<dynamic>).first as Map<String, dynamic>;
    item['order_id'] = 999;
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page([row])), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).fetchPatientOrders(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('invalid page or patient never dispatches HTTP', () async {
    var count = 0;
    final client = MockClient((_) async {
      count++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await expectLater(
      source.fetchPatientOrders(patientId: 0),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchPatientOrders(patientId: 7, page: 0),
      throwsArgumentError,
    );
    await expectLater(source.fetchCatalog(perPage: 501), throwsArgumentError);
    await expectLater(
      source.fetchOrder(patientId: 7, orderId: 0),
      throwsArgumentError,
    );
    expect(count, 0);
  });

  test('clinic switch during response discards old lab records', () async {
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
    ).fetchPatientOrders(patientId: 7);
    await started.future;
    session = _ready(clinicId: 4);
    pending.complete(http.Response(jsonEncode(_page([_orderRow()])), 200));
    await expectLater(future, throwsA(isA<LabReadSessionUnavailable>()));
  });

  test('logout during request discards old lab records', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final future = _source(client, guard).fetchPatientOrders(patientId: 7);
    await started.future;
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_page([_orderRow()])), 200));
    await expectLater(future, throwsA(isA<LabReadSessionUnavailable>()));
  });

  test('HTTP 403 remains one backend rejection without retry', () async {
    var count = 0;
    final client = MockClient((_) async {
      count++;
      return http.Response('{"error":"Forbidden"}', 403);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).fetchPatientOrders(patientId: 7),
      throwsA(
        isA<ApiException>().having((e) => e.statusCode, 'statusCode', 403),
      ),
    );
    expect(count, 1);
  });
}
