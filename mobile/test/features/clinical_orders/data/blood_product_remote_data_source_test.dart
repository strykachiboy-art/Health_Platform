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
import 'package:clinic_system_pro/features/clinical_orders/data/blood_product_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/blood_product_order.dart';

AuthSessionState _ready({
  String role = 'doctor',
  int clinic = 3,
  String token = 'token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'refresh',
    userId: 41,
    role: role,
    clinicContextId: clinic,
  ),
);

Map<String, dynamic> _row({
  int id = 17,
  int clinic = 3,
  int patient = 7,
  int admission = 9,
  String compatibility = 'pending',
  String status = 'active',
  int version = 1,
  int issued = 0,
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'admission_id': admission,
  'ordered_by_staff_id': 8,
  'product_type': 'packed_red_blood_cells',
  'units_requested': 2,
  'units_issued': issued,
  'indication': 'Symptomatic bleeding',
  'priority': 'stat',
  'status': status,
  'version': version,
  'patient_blood_type_at_order': 'O+',
  'compatibility_status': compatibility,
  'compatibility_verified_by_staff_id': compatibility == 'pending' ? null : 11,
  'compatibility_verified_at': compatibility == 'pending'
      ? null
      : '2026-11-07T09:00:00+00:00',
  'patient_blood_type_verified': compatibility == 'pending' ? null : 'O+',
  'crossmatch_reference': compatibility == 'compatible' ? 'CROSS-88' : null,
  'emergency_release_reason': compatibility == 'emergency_release'
      ? 'Emergency release approved'
      : null,
  'product_blood_type': null,
  'ordered_at': '2026-11-07T08:00:00+00:00',
  'fulfilled_by_staff_id': status == 'completed' ? 11 : null,
  'cancelled_by_staff_id': status == 'cancelled' ? 8 : null,
  'completed_at': status == 'completed' ? '2026-11-07T09:30:00+00:00' : null,
  'cancelled_at': status == 'cancelled' ? '2026-11-07T09:30:00+00:00' : null,
};

BloodProductOrder _order({
  String compatibility = 'pending',
  String status = 'active',
  int version = 1,
}) => BloodProductOrder.fromJson(
  _row(
    compatibility: compatibility,
    status: status,
    version: version,
    issued: status == 'completed' ? 1 : 0,
  ),
  expectedClinicId: 3,
  expectedPatientId: 7,
  expectedAdmissionId: 9,
);

BloodProductRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? load,
}) => BloodProductRemoteDataSource(
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
  test('strict scoped blood-product response accepts pending and verified', () {
    final pending = _order();
    expect(pending.canVerify, isTrue);
    expect(pending.canFulfill, isFalse);
    final verified = _order(compatibility: 'compatible', version: 2);
    expect(verified.canFulfill, isTrue);
    final emergency = _order(compatibility: 'emergency_release', version: 2);
    expect(emergency.compatibilityStatus, 'emergency_release');
    expect(
      _order(
        compatibility: 'compatible',
        status: 'completed',
        version: 3,
      ).isOpen,
      isFalse,
    );
  });

  test('denies foreign clinic, patient, admission and response ID', () {
    for (final row in <Map<String, dynamic>>[
      _row(clinic: 4),
      _row(patient: 8),
      _row(admission: 10),
    ]) {
      expect(
        () => BloodProductOrder.fromJson(
          row,
          expectedClinicId: 3,
          expectedPatientId: 7,
          expectedAdmissionId: 9,
        ),
        throwsFormatException,
      );
    }
    expect(
      () => BloodProductOrder.fromJson(
        _row(),
        expectedClinicId: 3,
        expectedId: 18,
      ),
      throwsFormatException,
    );
  });

  test('rejects unknown types, negative units and inconsistent lifecycle', () {
    final invalid = <Map<String, dynamic>>[
      _row()..['product_type'] = 'unregistered',
      _row()..['units_requested'] = 0,
      _row()..['units_issued'] = 3,
      _row(status: 'completed', compatibility: 'compatible')
        ..['completed_at'] = null,
      _row(compatibility: 'compatible')..['crossmatch_reference'] = null,
      _row(compatibility: 'pending')
        ..['compatibility_verified_at'] = '2026-11-07T09:00:00+00:00',
      _row()..['patient_blood_type_at_order'] = 'Z+',
      _row()..['version'] = 0,
    ];
    for (final item in invalid) {
      expect(
        () => BloodProductOrder.fromJson(item, expectedClinicId: 3),
        throwsFormatException,
      );
    }
  });

  test('list rejects duplicate IDs, bad count and foreign patient row', () {
    for (final envelope in <Map<String, dynamic>>[
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
        () => BloodProductOrderList.fromJson(
          envelope,
          expectedClinicId: 3,
          expectedPatientId: 7,
          expectedAdmissionId: 9,
        ),
        throwsFormatException,
      );
    }
  });

  test('scoped blood-product admission list is GET only', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      expect(req.method, 'GET');
      expect(req.url.path, '/api/v1/clinical-orders/blood-products');
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
    final result = await _source(
      client,
      guard,
    ).list(patientId: 7, admissionId: 9);
    expect(result.items.single.id, 17);
    expect(calls, 1);
  });

  test('lab technician gets order by ID without admission API', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      expect(req.method, 'GET');
      expect(req.url.path, '/api/v1/clinical-orders/blood-products/17');
      return http.Response(jsonEncode(_row()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final order = await _source(
      client,
      guard,
      load: () async => _ready(role: 'lab_technician'),
    ).get(orderId: 17);
    expect(order.patientId, 7);
    expect(order.admissionId, 9);
    expect(calls, 1);
  });

  test('doctor creates once with no idempotency or replay header', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      expect(req.method, 'POST');
      expect(req.url.path, '/api/v1/clinical-orders/blood-products');
      expect(
        req.headers.keys.map((value) => value.toLowerCase()),
        isNot(contains('idempotency-key')),
      );
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      expect(body, {
        'admission_id': 9,
        'product_type': 'packed_red_blood_cells',
        'units_requested': 2,
        'indication': 'Symptomatic bleeding',
      });
      return http.Response(
        jsonEncode({'order': _row(), 'message': 'created'}),
        201,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final order = await _source(client, guard).create(
      patientId: 7,
      admissionId: 9,
      productType: 'packed_red_blood_cells',
      unitsRequested: 2,
      indication: 'Symptomatic bleeding',
    );
    expect(order.id, 17);
    expect(calls, 1);
  });

  test(
    'cannot create with invalid quantity or indication before transport',
    () {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response('{}', 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      expect(
        () => source.create(
          patientId: 7,
          admissionId: 9,
          productType: 'platelets',
          unitsRequested: 101,
          indication: 'Required',
        ),
        throwsArgumentError,
      );
      expect(
        () => source.create(
          patientId: 7,
          admissionId: 9,
          productType: 'platelets',
          unitsRequested: 2,
          indication: '',
        ),
        throwsArgumentError,
      );
      expect(calls, 0);
    },
  );

  test('doctor cannot verify; lab must explicitly attest crossmatch', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final doctor = _source(client, guard);
    expect(
      () => doctor.verifyStandard(
        order: _order(),
        crossmatchReference: 'CROSS-88',
        laboratoryAttested: false,
      ),
      throwsArgumentError,
    );
    await expectLater(
      doctor.verifyStandard(
        order: _order(),
        crossmatchReference: 'CROSS-88',
        laboratoryAttested: true,
      ),
      throwsA(isA<BloodProductSessionException>()),
    );
    expect(calls, 0);
  });

  test('laboratory verification records crossmatch once', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      expect(req.method, 'POST');
      expect(
        req.url.path,
        '/api/v1/clinical-orders/blood-products/17/verify-compatibility',
      );
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      expect(body['emergency_release'], false);
      expect(body['crossmatch_reference'], 'CROSS-88');
      expect(body.containsKey('emergency_release_reason'), isFalse);
      return http.Response(
        jsonEncode({
          'order': _row(compatibility: 'compatible', version: 2),
          'message': 'verified',
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final order =
        await _source(
          client,
          guard,
          load: () async => _ready(role: 'lab_technician'),
        ).verifyStandard(
          order: _order(),
          crossmatchReference: 'CROSS-88',
          laboratoryAttested: true,
        );
    expect(order.compatibilityStatus, 'compatible');
    expect(order.version, 2);
    expect(calls, 1);
  });

  test('laboratory cannot verify terminal order or verify twice', () {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final lab = _source(
      client,
      guard,
      load: () async => _ready(role: 'lab_technician'),
    );
    expect(
      () => lab.verifyStandard(
        order: _order(compatibility: 'compatible', version: 2),
        crossmatchReference: 'CROSS-88',
        laboratoryAttested: true,
      ),
      throwsArgumentError,
    );
    expect(calls, 0);
  });

  test('fulfillment requires verified compatibility and valid quantity', () {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final lab = _source(
      client,
      guard,
      load: () async => _ready(role: 'lab_technician'),
    );
    expect(
      () => lab.fulfill(
        order: _order(),
        unitsIssued: 1,
        note: 'Issued',
        issuanceAttested: true,
      ),
      throwsArgumentError,
    );
    expect(
      () => lab.fulfill(
        order: _order(compatibility: 'compatible', version: 2),
        unitsIssued: 3,
        note: 'Issued',
        issuanceAttested: true,
      ),
      throwsArgumentError,
    );
    expect(
      () => lab.fulfill(
        order: _order(compatibility: 'emergency_release', version: 2),
        unitsIssued: 1,
        note: 'Issued',
        issuanceAttested: true,
      ),
      throwsArgumentError,
    );
    expect(calls, 0);
  });

  test(
    'verified laboratory order issues once and checks confirmed version',
    () async {
      var calls = 0;
      final client = MockClient((req) async {
        calls++;
        expect(
          req.url.path,
          '/api/v1/clinical-orders/blood-products/17/fulfill',
        );
        expect(
          (jsonDecode(req.body) as Map<String, dynamic>)['units_issued'],
          1,
        );
        return http.Response(
          jsonEncode({
            'order': _row(
              compatibility: 'compatible',
              status: 'completed',
              version: 3,
              issued: 1,
            ),
            'message': 'fulfilled',
          }),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final order =
          await _source(
            client,
            guard,
            load: () async => _ready(role: 'lab_technician'),
          ).fulfill(
            order: _order(compatibility: 'compatible', version: 2),
            unitsIssued: 1,
            note: 'Issued by lab',
            issuanceAttested: true,
          );
      expect(order.status, 'completed');
      expect(calls, 1);
    },
  );

  test('doctor cancels open order, lab technician cannot cancel', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      return http.Response(
        jsonEncode({
          'order': _row(status: 'cancelled', version: 2),
          'message': 'cancelled',
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final cancelled = await source.cancel(
      order: _order(),
      reason: 'No longer needed',
    );
    expect(cancelled.status, 'cancelled');
    expect(calls, 1);
    await expectLater(
      _source(
        client,
        guard,
        load: () async => _ready(role: 'lab_technician'),
      ).cancel(order: _order(), reason: 'No longer needed'),
      throwsA(isA<BloodProductSessionException>()),
    );
    expect(calls, 1);
  });

  test(
    'admin, nurse, patient, pharmacist cannot mutate blood products',
    () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response('{}', 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      for (final role in <String>['admin', 'nurse', 'patient', 'pharmacist']) {
        final source = _source(
          client,
          guard,
          load: () async => _ready(role: role),
        );
        await expectLater(
          source.create(
            patientId: 7,
            admissionId: 9,
            productType: 'platelets',
            unitsRequested: 1,
            indication: 'Needed',
          ),
          throwsA(isA<BloodProductSessionException>()),
        );
      }
      expect(calls, 0);
    },
  );

  test('session change while GET pending rejects stale result', () async {
    final pending = Completer<http.Response>();
    final client = MockClient((_) => pending.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final future = source.get(orderId: 17);
    await Future<void>.delayed(Duration.zero);
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_row()), 200));
    await expectLater(future, throwsA(isA<BloodProductSessionException>()));
  });

  test(
    'clinic changes while response pending rejects old-clinic data',
    () async {
      final pending = Completer<http.Response>();
      final client = MockClient((_) => pending.future);
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var clinic = 3;
      final source = _source(
        client,
        guard,
        load: () async => _ready(clinic: clinic),
      );
      final future = source.get(orderId: 17);
      await Future<void>.delayed(Duration.zero);
      clinic = 4;
      pending.complete(http.Response(jsonEncode(_row()), 200));
      await expectLater(future, throwsA(isA<BloodProductSessionException>()));
    },
  );

  test('stale or foreign-scope mutation responses fail closed', () async {
    final client = MockClient((req) async {
      return http.Response(
        jsonEncode({'order': _row(compatibility: 'compatible', version: 1)}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(
        client,
        guard,
        load: () async => _ready(role: 'lab_technician'),
      ).verifyStandard(
        order: _order(),
        crossmatchReference: 'CROSS-88',
        laboratoryAttested: true,
      ),
      throwsFormatException,
    );
  });
}
