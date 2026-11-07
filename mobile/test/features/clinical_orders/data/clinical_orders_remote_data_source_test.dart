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
import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/clinical_order_summary.dart';

AuthSessionState _ready({
  String role = 'doctor',
  int clinic = 3,
  String token = 'orders-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'orders-refresh',
    userId: 42,
    role: role,
    clinicContextId: clinic,
  ),
);

Map<String, dynamic> _order({
  String type = 'imaging',
  String status = 'active',
  int clinic = 3,
  int patient = 7,
  int source = 11,
}) => <String, dynamic>{
  'order_type': type,
  'source_entity_type': '${type}_order',
  'source_entity_id': source,
  'clinic_id': clinic,
  'patient_id': patient,
  'admission_id': 23,
  'consultation_id': 34,
  'ordered_by_staff_id': 8,
  'ordered_at': '2026-11-07T10:00:00+00:00',
  'priority': 'routine',
  'status': status,
  'source_status': status,
  'display_name': 'Chest X-ray',
  'instructions': 'After review',
  'scheduled_at': null,
  'due_at': null,
  'completed_at': status == 'completed' ? '2026-11-07T10:30:00+00:00' : null,
  'cancelled_at': status == 'cancelled' ? '2026-11-07T10:30:00+00:00' : null,
  'details': <String, dynamic>{'study': 'chest'},
};

Map<String, dynamic> _envelope(List<Map<String, dynamic>> rows) =>
    <String, dynamic>{'items': rows, 'total': rows.length};

ClinicalOrdersRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? session,
}) => ClinicalOrdersRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: session ?? () async => _ready(),
  sessionNavigationGuard: guard,
);

void main() {
  test(
    'unified read uses exact patient URL, bearer, and filter parameters',
    () async {
      var calls = 0;
      final client = MockClient((req) async {
        calls++;
        expect(req.method, 'GET');
        expect(req.url.path, '/api/v1/clinical-orders/patients/7');
        expect(req.url.queryParameters, {
          'order_type': 'imaging',
          'status': 'active',
        });
        expect(req.headers['Authorization'], 'Bearer orders-token');
        return http.Response(jsonEncode(_envelope([_order()])), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final result = await _source(
        client,
        guard,
      ).fetchPatientOrders(patientId: 7, type: 'imaging', status: 'active');
      expect(result.total, 1);
      expect(result.items.single.consultationId, 34);
      expect(calls, 1);
    },
  );

  test('empty list is accepted without inventing records', () async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_envelope([])), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final result = await _source(
      client,
      guard,
    ).fetchPatientOrders(patientId: 7);
    expect(result.items, isEmpty);
    expect(result.total, 0);
  });

  for (final row in <Map<String, dynamic>>[
    _order(clinic: 4),
    _order(patient: 9),
    _order(status: 'completed')..['completed_at'] = null,
    _order()..['order_type'] = 'unsupported',
    _order()..['priority'] = 'unknown',
    _order()..['source_entity_id'] = -1,
    _order()..['ordered_at'] = 'bad-time',
    _order()..['admission_id'] = null,
  ]) {
    test(
      'strict parser rejects malformed/foreign row ${row['clinic_id']} ${row['patient_id']} ${row['order_type']} ${row['source_entity_id']} ${row['priority']} ${row['ordered_at']}',
      () {
        expect(
          () => ClinicalOrderTimeline.fromJson(
            _envelope([row]),
            expectedClinicId: 3,
            expectedPatientId: 7,
          ),
          throwsFormatException,
        );
      },
    );
  }

  test('list rejects duplicate IDs, inconsistent total and wrong filters', () {
    final item = _order();
    expect(
      () => ClinicalOrderTimeline.fromJson(
        {
          'items': [item, item],
          'total': 2,
        },
        expectedClinicId: 3,
        expectedPatientId: 7,
      ),
      throwsFormatException,
    );
    expect(
      () => ClinicalOrderTimeline.fromJson(
        {
          'items': [item],
          'total': 2,
        },
        expectedClinicId: 3,
        expectedPatientId: 7,
      ),
      throwsFormatException,
    );
    expect(
      () => ClinicalOrderTimeline.fromJson(
        _envelope([item]),
        expectedClinicId: 3,
        expectedPatientId: 7,
        type: 'procedure',
      ),
      throwsFormatException,
    );
  });

  test('invalid filters/IDs never send HTTP', () async {
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
      source.fetchPatientOrders(patientId: 0),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchPatientOrders(patientId: 7, type: 'unknown'),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchPatientOrders(patientId: 7, status: 'unknown'),
      throwsArgumentError,
    );
    expect(calls, 0);
  });

  for (final role in <String>[
    'patient',
    'receptionist',
    'lab_technician',
    'super_admin',
  ]) {
    test('role $role fails closed with no HTTP', () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response('{}', 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(
        client,
        guard,
        session: () async => _ready(role: role),
      );
      await expectLater(
        source.fetchPatientOrders(patientId: 7),
        throwsA(isA<ClinicalOrdersSessionException>()),
      );
      expect(calls, 0);
    });
  }

  test('guard invalidation during pending GET discards response', () async {
    final gate = Completer<http.Response>();
    final client = MockClient((_) => gate.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final future = _source(client, guard).fetchPatientOrders(patientId: 7);
    await Future<void>.delayed(Duration.zero);
    guard.invalidate();
    gate.complete(http.Response(jsonEncode(_envelope([_order()])), 200));
    await expectLater(future, throwsA(isA<ClinicalOrdersSessionException>()));
  });

  test('clinic/session switch during pending GET discards response', () async {
    final gate = Completer<http.Response>();
    final client = MockClient((_) => gate.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var clinic = 3;
    final source = _source(
      client,
      guard,
      session: () async => _ready(clinic: clinic),
    );
    final future = source.fetchPatientOrders(patientId: 7);
    await Future<void>.delayed(Duration.zero);
    clinic = 4;
    gate.complete(http.Response(jsonEncode(_envelope([_order()])), 200));
    await expectLater(future, throwsA(isA<ClinicalOrdersSessionException>()));
  });
}
