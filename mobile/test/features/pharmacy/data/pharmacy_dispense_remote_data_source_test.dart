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
import 'package:clinic_system_pro/features/pharmacy/data/pharmacy_inventory_remote_data_source.dart';
import 'package:clinic_system_pro/features/pharmacy/data/pharmacy_dispense_remote_data_source.dart';

AuthSessionState ready({String role = 'pharmacist', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 's03b-token',
        refreshToken: 's03b-refresh',
        userId: 42,
        clinicContextId: clinic,
        role: role,
      ),
    );

Map<String, dynamic> prescription({
  int clinicId = 3,
  String status = 'active',
  int patientId = 7,
  int itemId = 111,
}) => <String, dynamic>{
  'id': 11,
  'clinic_id': clinicId,
  'patient_id': patientId,
  'consultation_id': null,
  'prescribed_by_id': 8,
  'status': status,
  'notes': null,
  'issued_at': '2026-11-10T12:00:00+00:00',
  'expires_at': null,
  'items': <Map<String, dynamic>>[
    <String, dynamic>{
      'id': itemId,
      'prescription_id': 11,
      'drug_id': 55,
      'dosage': '500 mg',
      'frequency': 'twice daily',
      'duration': '7 days',
      'quantity': 14,
      'instructions': null,
      'dose_value': null,
      'dose_unit': null,
      'route_code': 'PO',
      'route_display': 'Oral',
      'frequency_code': null,
      'frequency_interval': null,
      'frequency_interval_unit': null,
      'scheduled_times': <String>[],
      'start_at': null,
      'end_at': null,
      'is_prn': false,
      'prn_reason': null,
      'max_single_dose': null,
      'max_daily_dose': null,
      'administration_instructions': null,
      'order_priority': 'routine',
      'order_type': 'outpatient',
      'ordered_at': null,
    },
  ],
};

Map<String, dynamic> drug({bool active = true}) => <String, dynamic>{
  'id': 55,
  'clinic_id': 3,
  'name': 'Verified drug',
  'category': 'analgesic',
  'strength': '500 mg',
  'is_controlled': false,
  'is_active': active,
};

Map<String, dynamic> batch({
  int clinic = 3,
  int qty = 10,
  String expiry = '2099-12-31',
}) => <String, dynamic>{
  'id': 501,
  'clinic_id': clinic,
  'drug_id': 55,
  'batch_number': 'SAFE-501',
  'quantity_on_hand': qty,
  'reorder_level': 2,
  'expiry_date': expiry,
};

Map<String, dynamic> receipt({
  int recordId = 301,
  int batchId = 501,
  int quantity = 2,
  int prescriptionId = 11,
}) => <String, dynamic>{
  'id': recordId,
  'prescription_id': prescriptionId,
  'dispensed_by_id': 23,
  'status': 'partially_dispensed',
  'notes': null,
  'items': <Map<String, dynamic>>[
    <String, dynamic>{
      'id': 401,
      'dispense_record_id': recordId,
      'prescription_item_id': 111,
      'batch_id': batchId,
      'quantity_dispensed': quantity,
    },
  ],
};

http.Response success(Map<String, dynamic> value) => http.Response(
  jsonEncode(<String, dynamic>{'success': true, 'data': value}),
  200,
);

http.Response respond(
  http.Request request, {
  Map<String, dynamic>? rx,
  Map<String, dynamic>? stock,
  Map<String, dynamic>? medicine,
  Map<String, dynamic>? dispensed,
}) {
  switch (request.url.path) {
    case '/api/v1/prescriptions/11':
      return success(rx ?? prescription());
    case '/api/v1/pharmacy/drugs/55':
      return success(medicine ?? drug());
    case '/api/v1/pharmacy/batches/501':
      return success(stock ?? batch());
    case '/api/v1/pharmacy/dispense/301':
      return success(dispensed ?? receipt());
    case '/api/v1/pharmacy/dispense':
      if (request.method == 'POST') {
        return success(dispensed ?? receipt());
      }
      return http.Response('{}', 405);
  }
  return http.Response('{}', 404);
}

PharmacyDispenseRemoteDataSource source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) => PharmacyDispenseRemoteDataSource(
  inventory: PharmacyInventoryRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: loader ?? () async => ready(),
    sessionNavigationGuard: guard,
  ),
);

Future<PharmacyDispenseReview> review(PharmacyDispenseRemoteDataSource s) =>
    s.review(
      patientId: 7,
      prescriptionId: 11,
      prescriptionItemId: 111,
      batchId: 501,
      quantity: 2,
    );

void main() {
  test(
    'pharmacist reviews evidence, POSTs once, and reads back server record',
    () async {
      final calls = <String>[];
      final client = MockClient((request) async {
        calls.add('${request.method} ${request.url.path}');
        expect(request.headers['authorization'], 'Bearer s03b-token');
        if (request.method == 'POST') {
          expect(request.headers['idempotency-key'], 'dispense-unique-301');
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          expect(body['prescription_id'], 11);
          expect(body.containsKey('clinic_id'), isFalse);
          expect(body.containsKey('dispensed_by_id'), isFalse);
          final items = body['items'] as List<dynamic>;
          expect(items.single, {
            'prescription_item_id': 111,
            'batch_id': 501,
            'quantity': 2,
          });
        }
        return respond(request);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final s = source(client, guard);
      final reviewed = await review(s);
      expect(reviewed.batchQuantity, 10);
      expect(s.writeAttempted, isFalse);
      final confirmed = await s.dispenseOnce(
        reviewed: reviewed,
        idempotencyKey: 'dispense-unique-301',
      );
      expect(confirmed.id, 301);
      expect(confirmed.status, 'partially_dispensed');
      expect(confirmed.lines.single.quantityDispensed, 2);
      expect(s.writeAttempted, isTrue);
      expect(calls, <String>[
        'GET /api/v1/prescriptions/11',
        'GET /api/v1/pharmacy/drugs/55',
        'GET /api/v1/pharmacy/batches/501',
        'GET /api/v1/prescriptions/11',
        'GET /api/v1/pharmacy/drugs/55',
        'GET /api/v1/pharmacy/batches/501',
        'POST /api/v1/pharmacy/dispense',
        'GET /api/v1/pharmacy/dispense/301',
      ]);
      await expectLater(
        s.dispenseOnce(reviewed: reviewed, idempotencyKey: 'new-key'),
        throwsA(isA<PharmacyDispenseAlreadyAttempted>()),
      );
      expect(calls.where((x) => x.startsWith('POST')).length, 1);
    },
  );

  for (final role in <String>['admin', 'doctor', 'nurse', 'patient']) {
    test('role $role dispatches no dispensing preflight request', () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return respond(request);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final s = source(client, guard, loader: () async => ready(role: role));
      await expectLater(
        review(s),
        throwsA(isA<PharmacyDispenseScopeUnavailable>()),
      );
      expect(calls, 0);
      expect(s.writeAttempted, isFalse);
    });
  }

  for (final changed in <String>[
    'wrong clinic',
    'expired',
    'no stock',
    'inactive drug',
    'wrong patient',
    'completed prescription',
  ]) {
    test('$changed preflight stops before POST', () async {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'POST') {
          posts++;
        }
        return respond(
          request,
          stock: changed == 'wrong clinic'
              ? batch(clinic: 9)
              : changed == 'expired'
              ? batch(expiry: '2020-01-01')
              : changed == 'no stock'
              ? batch(qty: 1)
              : null,
          medicine: changed == 'inactive drug' ? drug(active: false) : null,
          rx: changed == 'wrong patient'
              ? prescription(patientId: 99)
              : changed == 'completed prescription'
              ? prescription(status: 'completed')
              : null,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final s = source(client, guard);
      await expectLater(
        review(s),
        throwsA(
          changed == 'wrong clinic' || changed == 'wrong patient'
              ? isA<FormatException>()
              : isA<PharmacyDispenseEvidenceChanged>(),
        ),
      );
      expect(posts, 0);
      expect(s.writeAttempted, isFalse);
    });
  }

  test('server stock changes before POST; no mutation dispatched', () async {
    var batchCalls = 0;
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
      }
      if (request.url.path == '/api/v1/pharmacy/batches/501') {
        batchCalls++;
        return success(batch(qty: batchCalls == 1 ? 10 : 6));
      }
      return respond(request);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = source(client, guard);
    final reviewed = await review(s);
    await expectLater(
      s.dispenseOnce(reviewed: reviewed, idempotencyKey: 'key-3'),
      throwsA(isA<PharmacyDispenseEvidenceChanged>()),
    );
    expect(posts, 0);
    expect(s.writeAttempted, isFalse);
  });

  test(
    'mismatched dispense GET readback locks attempt, never replays',
    () async {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'POST') {
          posts++;
        }
        return respond(
          request,
          dispensed: request.url.path == '/api/v1/pharmacy/dispense/301'
              ? receipt(batchId: 99)
              : null,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final s = source(client, guard);
      final reviewed = await review(s);
      await expectLater(
        s.dispenseOnce(reviewed: reviewed, idempotencyKey: 'id-1'),
        throwsA(isA<PharmacyDispenseOutcomeUnconfirmed>()),
      );
      expect(posts, 1);
      expect(s.writeAttempted, isTrue);
      await expectLater(
        s.dispenseOnce(reviewed: reviewed, idempotencyKey: 'id-2'),
        throwsA(isA<PharmacyDispenseAlreadyAttempted>()),
      );
      expect(posts, 1);
    },
  );

  test('transport fails after POST start; no subsequent POST', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
        return http.Response('{"error":"Upstream timed out"}', 503);
      }
      return respond(request);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = source(client, guard);
    final reviewed = await review(s);
    await expectLater(
      s.dispenseOnce(reviewed: reviewed, idempotencyKey: 'request-a'),
      throwsA(isA<PharmacyDispenseOutcomeUnconfirmed>()),
    );
    expect(posts, 1);
    await expectLater(
      s.dispenseOnce(reviewed: reviewed, idempotencyKey: 'request-b'),
      throwsA(isA<PharmacyDispenseAlreadyAttempted>()),
    );
    expect(posts, 1);
  });

  test('session invalidation during POST discards receipt', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
    final client = MockClient((request) {
      if (request.method == 'POST') {
        started.complete();
        return pending.future;
      }
      return Future.value(respond(request));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = source(client, guard);
    final reviewed = await review(s);
    final future = s.dispenseOnce(
      reviewed: reviewed,
      idempotencyKey: 'one-request',
    );
    await started.future;
    guard.invalidate();
    pending.complete(success(receipt()));
    await expectLater(future, throwsA(isA<PharmacyDispenseScopeUnavailable>()));
    expect(s.writeAttempted, isTrue);
  });
}
