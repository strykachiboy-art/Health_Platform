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
import 'package:clinic_system_pro/features/clinical_orders/data/diet_nursing_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/diet_nursing_order.dart';

AuthSessionState _ready({
  String role = 'doctor',
  int clinic = 3,
  String token = 'token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'refresh',
    userId: 42,
    role: role,
    clinicContextId: clinic,
  ),
);

Map<String, dynamic> _row({
  String kind = 'diets',
  int clinic = 3,
  int patient = 7,
  int admission = 9,
  int id = 17,
  int version = 1,
  String status = 'active',
  String category = '',
  String frequency = 'once',
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'admission_id': admission,
  'ordered_by_staff_id': 8,
  'version': version,
  'priority': 'routine',
  'status': status,
  'ordered_at': '2026-11-07T08:00:00+00:00',
  'completed_at': status == 'completed' ? '2026-11-07T09:00:00+00:00' : null,
  'cancelled_at': status == 'cancelled' ? '2026-11-07T09:00:00+00:00' : null,
  if (kind == 'diets') ...<String, dynamic>{
    'diet_type': category.isEmpty ? 'regular' : category,
    'feeding_route': category == 'npo' ? null : 'oral',
    'custom_description': category == 'custom' ? 'Custom diet' : null,
  } else ...<String, dynamic>{
    'category': category.isEmpty ? 'monitoring' : category,
    'order_text': 'Hourly neuro observations',
    'frequency': frequency,
    'custom_frequency': frequency == 'custom' ? 'every 45 minutes' : null,
    'assigned_to_staff_id': null,
  },
};

DietNursingRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? load,
}) => DietNursingRemoteDataSource(
  unifiedSource: ClinicalOrdersRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: load ?? () async => _ready(),
    sessionNavigationGuard: guard,
  ),
);

void main() {
  test('accepts scoped diet, NPO and nursing records', () {
    for (final kind in <String>['diets', 'nursing-orders']) {
      final order = DietNursingOrder.fromJson(
        _row(kind: kind),
        kind: kind,
        expectedClinicId: 3,
        expectedPatientId: 7,
        expectedAdmissionId: 9,
      );
      expect(order.id, 17);
      expect(order.isOpen, isTrue);
    }
    final npo = DietNursingOrder.fromJson(
      _row(category: 'npo'),
      kind: 'diets',
      expectedClinicId: 3,
      expectedPatientId: 7,
      expectedAdmissionId: 9,
    );
    expect(npo.feedingRoute, isNull);
    final once = DietNursingOrder.fromJson(
      _row(kind: 'nursing-orders'),
      kind: 'nursing-orders',
      expectedClinicId: 3,
      expectedPatientId: 7,
      expectedAdmissionId: 9,
    );
    expect(once.canDoctorComplete, isFalse);
    expect(once.canExecute, isTrue);
  });

  test('rejects cross-clinic, cross-patient, admission and ID mismatch', () {
    final items = <Map<String, dynamic>>[
      _row(clinic: 4),
      _row(patient: 8),
      _row(admission: 10),
    ];
    for (final item in items) {
      expect(
        () => DietNursingOrder.fromJson(
          item,
          kind: 'diets',
          expectedClinicId: 3,
          expectedPatientId: 7,
          expectedAdmissionId: 9,
        ),
        throwsFormatException,
      );
    }
    expect(
      () => DietNursingOrder.fromJson(
        _row(),
        kind: 'diets',
        expectedClinicId: 3,
        expectedPatientId: 7,
        expectedAdmissionId: 9,
        expectedId: 18,
      ),
      throwsFormatException,
    );
  });

  test('rejects invalid lifecycle, enum, NPO route and frequencies', () {
    final invalidStatus = _row()..['status'] = 'unknown';
    final terminal = _row(status: 'completed')..['completed_at'] = null;
    final npoRoute = _row(category: 'npo')..['feeding_route'] = 'oral';
    final frequency = _row(kind: 'nursing-orders', frequency: 'custom')
      ..['custom_frequency'] = null;
    for (final row in <Map<String, dynamic>>[
      invalidStatus,
      terminal,
      npoRoute,
    ]) {
      expect(
        () => DietNursingOrder.fromJson(
          row,
          kind: 'diets',
          expectedClinicId: 3,
          expectedPatientId: 7,
          expectedAdmissionId: 9,
        ),
        throwsFormatException,
      );
    }
    expect(
      () => DietNursingOrder.fromJson(
        frequency,
        kind: 'nursing-orders',
        expectedClinicId: 3,
        expectedPatientId: 7,
        expectedAdmissionId: 9,
      ),
      throwsFormatException,
    );
  });

  test('list enforces count, deduplication and row scope', () {
    for (final json in <Map<String, dynamic>>[
      {
        'items': [_row(), _row()],
        'total': 2,
      },
      {
        'items': [_row()],
        'total': 2,
      },
      {
        'items': [_row(patient: 8)],
        'total': 1,
      },
    ]) {
      expect(
        () => DietNursingOrderList.fromJson(
          json,
          kind: 'diets',
          expectedClinicId: 3,
          expectedPatientId: 7,
          expectedAdmissionId: 9,
        ),
        throwsFormatException,
      );
    }
  });

  test('admission-scoped diets GET returns strict list', () async {
    var gets = 0;
    final client = MockClient((req) async {
      gets++;
      expect(req.method, 'GET');
      expect(req.url.path, '/api/v1/clinical-orders/diets');
      expect(req.url.queryParameters['admission_id'], '9');
      return http.Response(
        jsonEncode({
          'items': [_row()],
          'total': 1,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final page = await _source(
      client,
      guard,
    ).list(kind: 'diets', patientId: 7, admissionId: 9);
    expect(page.items.single.category, 'regular');
    expect(gets, 1);
  });

  test(
    'NPO creation supplies explicit null feeding route exactly once',
    () async {
      var posts = 0;
      final client = MockClient((req) async {
        posts++;
        expect(req.method, 'POST');
        expect(req.url.path, '/api/v1/clinical-orders/diets');
        expect(
          req.headers.keys.map((e) => e.toLowerCase()),
          isNot(contains('idempotency-key')),
        );
        final body = jsonDecode(req.body) as Map<String, dynamic>;
        expect(body['diet_type'], 'npo');
        expect(body.containsKey('feeding_route'), isTrue);
        expect(body['feeding_route'], isNull);
        return http.Response(
          jsonEncode({'order': _row(category: 'npo'), 'message': 'created'}),
          201,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final result = await _source(client, guard).createDiet(
        patientId: 7,
        admissionId: 9,
        dietType: 'npo',
        feedingRoute: null,
      );
      expect(result.category, 'npo');
      expect(posts, 1);
    },
  );

  test('custom diet requires description before HTTP', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    expect(
      () => source.createDiet(patientId: 7, admissionId: 9, dietType: 'custom'),
      throwsArgumentError,
    );
    expect(calls, 0);
  });

  test('doctor creates nursing order and verifies custom frequency', () async {
    var posts = 0;
    final client = MockClient((req) async {
      posts++;
      expect(req.url.path, '/api/v1/clinical-orders/nursing-orders');
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      expect(body['category'], 'monitoring');
      expect(body['frequency'], 'custom');
      expect(body['custom_frequency'], 'every 45 minutes');
      expect(body['assigned_to_staff_id'], 34);
      return http.Response(
        jsonEncode({
          'order': _row(kind: 'nursing-orders', frequency: 'custom'),
          'message': 'created',
        }),
        201,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final result = await _source(client, guard).createNursing(
      patientId: 7,
      admissionId: 9,
      category: 'monitoring',
      orderText: 'Hourly neuro observations',
      frequency: 'custom',
      customFrequency: 'every 45 minutes',
      assignedToStaffId: 34,
    );
    expect(result.frequency, 'custom');
    expect(posts, 1);
  });

  test(
    'nurse executes one-time nursing order and server marks completed',
    () async {
      var posts = 0;
      final client = MockClient((req) async {
        posts++;
        expect(req.method, 'POST');
        expect(
          req.url.path,
          '/api/v1/clinical-orders/nursing-orders/17/execute',
        );
        final body = jsonDecode(req.body) as Map<String, dynamic>;
        expect(body['result_note'], 'Observations recorded');
        return http.Response(
          jsonEncode({
            'order': _row(
              kind: 'nursing-orders',
              status: 'completed',
              version: 2,
            ),
            'execution': {
              'id': 90,
              'nursing_order_id': 17,
              'clinic_id': 3,
              'patient_id': 7,
              'admission_id': 9,
              'performed_by_staff_id': 34,
              'performed_at': '2026-11-07T09:00:00+00:00',
              'result_note': 'Observations recorded',
            },
            'message': 'executed',
          }),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(
        client,
        guard,
        load: () async => _ready(role: 'nurse'),
      );
      final order = DietNursingOrder.fromJson(
        _row(kind: 'nursing-orders'),
        kind: 'nursing-orders',
        expectedClinicId: 3,
        expectedPatientId: 7,
        expectedAdmissionId: 9,
      );
      final result = await source.executeNursing(
        patientId: 7,
        admissionId: 9,
        order: order,
        resultNote: 'Observations recorded',
      );
      expect(result.status, 'completed');
      expect(posts, 1);
    },
  );

  test(
    'nursing execution history is read-only and rejects mismatches',
    () async {
      final client = MockClient((req) async {
        expect(req.method, 'GET');
        expect(
          req.url.path,
          '/api/v1/clinical-orders/nursing-orders/17/executions',
        );
        return http.Response(
          jsonEncode({
            'items': [
              {
                'id': 90,
                'nursing_order_id': 17,
                'clinic_id': 3,
                'patient_id': 7,
                'admission_id': 9,
                'performed_by_staff_id': 34,
                'performed_at': '2026-11-07T09:00:00+00:00',
                'result_note': 'Completed observations',
              },
            ],
            'total': 1,
          }),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final list = await _source(
        client,
        guard,
        load: () async => _ready(role: 'nurse'),
      ).executions(patientId: 7, admissionId: 9, orderId: 17);
      expect(list.items.single.resultNote, 'Completed observations');
      expect(
        () => NursingOrderExecution.fromJson(
          {
            'id': 90,
            'nursing_order_id': 18,
            'clinic_id': 3,
            'patient_id': 7,
            'admission_id': 9,
            'performed_by_staff_id': 34,
            'performed_at': '2026-11-07T09:00:00+00:00',
            'result_note': 'Completed observations',
          },
          expectedOrderId: 17,
          expectedClinicId: 3,
          expectedPatientId: 7,
          expectedAdmissionId: 9,
        ),
        throwsFormatException,
      );
    },
  );

  test(
    'roles enforce doctor writes, nurse execution and admin read-only',
    () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response('{}', 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final nursing = DietNursingOrder.fromJson(
        _row(kind: 'nursing-orders'),
        kind: 'nursing-orders',
        expectedClinicId: 3,
        expectedPatientId: 7,
        expectedAdmissionId: 9,
      );
      for (final role in <String>['admin', 'nurse', 'patient']) {
        final source = _source(
          client,
          guard,
          load: () async => _ready(role: role),
        );
        await expectLater(
          source.createNursing(
            patientId: 7,
            admissionId: 9,
            category: 'monitoring',
            orderText: 'Observations',
            frequency: 'once',
          ),
          throwsA(isA<DietNursingSessionException>()),
        );
      }
      for (final role in <String>['admin', 'doctor', 'patient']) {
        final source = _source(
          client,
          guard,
          load: () async => _ready(role: role),
        );
        await expectLater(
          source.executeNursing(
            patientId: 7,
            admissionId: 9,
            order: nursing,
            resultNote: 'Completed',
          ),
          throwsA(isA<DietNursingSessionException>()),
        );
      }
      expect(calls, 0);
    },
  );

  test('session invalidation while GET pending discards response', () async {
    final pending = Completer<http.Response>();
    final client = MockClient((_) => pending.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final future = _source(
      client,
      guard,
    ).list(kind: 'diets', patientId: 7, admissionId: 9);
    await Future<void>.delayed(Duration.zero);
    guard.invalidate();
    pending.complete(
      http.Response(
        jsonEncode({
          'items': [_row()],
          'total': 1,
        }),
        200,
      ),
    );
    await expectLater(future, throwsA(isA<DietNursingSessionException>()));
  });

  test('unknown nursing POST outcome sends exactly one request', () async {
    var posts = 0;
    final client = MockClient((req) async {
      posts++;
      throw http.ClientException('no response');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).createNursing(
        patientId: 7,
        admissionId: 9,
        category: 'monitoring',
        orderText: 'Observations',
        frequency: 'once',
      ),
      throwsException,
    );
    expect(posts, 1);
  });
}
