import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/blood_bank/data/blood_bank_contract.dart';
import 'package:clinic_system_pro/features/blood_bank/data/blood_bank_source.dart';

import 'blood_bank_contract_test.dart'
    show orderRow, policyRow, transfusionRow, unitRow;

AuthSessionState ready({
  String role = 'doctor',
  String tenant = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'token',
    refreshToken: 'refresh',
    userId: 41,
    role: role,
    clinicContextId: 3,
    tenantId: tenant,
  ),
);
http.Response response(Object j, {int status = 200}) =>
    http.Response(jsonEncode(j), status);
BloodBankSource source(
  MockClient client,
  SessionNavigationGuard guard,
  Future<AuthSessionState> Function() load,
) => BloodBankSource(
  api: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: load,
  guard: guard,
);
void main() {
  test('signed out does not call backend', () async {
    final g = SessionNavigationGuard();
    addTearDown(g.dispose);
    final c = MockClient((_) async => throw StateError('unexpected HTTP'));
    addTearDown(c.close);
    await expectLater(
      source(c, g, () async => ready()).open(),
      throwsA(isA<BbUnavailable>()),
    );
  });
  test('super admin cannot open clinical workspace', () async {
    final g = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(g.dispose);
    final c = MockClient((_) async => throw StateError('unexpected HTTP'));
    addTearDown(c.close);
    await expectLater(
      source(c, g, () async => ready(role: 'super_admin')).open(),
      throwsA(isA<BbUnavailable>()),
    );
  });
  test('server policy failure blocks workspace', () async {
    final g = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(g.dispose);
    final c = MockClient(
      (_) async =>
          response(policyRow()..['automatic_replay_authorizations'] = 1),
    );
    addTearDown(c.close);
    await expectLater(
      source(c, g, () async => ready()).open(),
      throwsFormatException,
    );
  });
  test('patient cannot read donor registry', () async {
    final g = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(g.dispose);
    final c = MockClient((_) async => throw StateError('unexpected HTTP'));
    addTearDown(c.close);
    final s = await source(c, g, () async => ready(role: 'patient')).open();
    await expectLater(s.read(BbAction.donors), throwsA(isA<BbUnavailable>()));
  });
  test('tenant changes invalidate source response', () async {
    final g = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(g.dispose);
    var a = ready();
    final c = MockClient((r) async {
      if (r.url.path.endsWith('/offline-policy')) return response(policyRow());
      a = ready(tenant: 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb');
      return response(orderRow());
    });
    addTearDown(c.close);
    final s = await source(c, g, () async => a).open();
    await expectLater(
      s.read(BbAction.order, id: 19),
      throwsA(isA<BbUnavailable>()),
    );
  });
  test(
    'unknown outcome prevents a second mutation across route reopen',
    () async {
      final g = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(g.dispose);
      var posts = 0;
      final c = MockClient((r) async {
        if (r.method == 'POST') {
          posts++;
          return response({'error': 'uncertain'}, status: 500);
        }
        return response(policyRow());
      });
      addTearDown(c.close);
      final src = source(c, g, () async => ready(role: 'lab_technician'));
      final a = await src.open();
      await expectLater(
        a.submit(BbAction.expire, {}),
        throwsA(isA<BbUnknownOutcome>()),
      );
      final b = await src.open();
      await expectLater(
        b.submit(BbAction.expire, {}),
        throwsA(isA<BbUnknownOutcome>()),
      );
      expect(posts, 1);
    },
  );
  test('wrong patient order read fails closed', () async {
    final g = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(g.dispose);
    final c = MockClient(
      (r) async => response(
        r.url.path.endsWith('/offline-policy') ? policyRow() : orderRow(),
      ),
    );
    addTearDown(c.close);
    final s = await source(c, g, () async => ready()).open();
    await expectLater(
      s.read(BbAction.order, id: 19, patient: 8),
      throwsFormatException,
    );
  });
  test('duplicate reservation rejected before write', () async {
    final g = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(g.dispose);
    var posts = 0;
    final c = MockClient((r) async {
      if (r.method == 'POST') posts++;
      return response(
        r.url.path.endsWith('/offline-policy') ? policyRow() : orderRow(),
      );
    });
    addTearDown(c.close);
    final s = await source(
      c,
      g,
      () async => ready(role: 'lab_technician'),
    ).open();
    await expectLater(
      s.reserve(
        order: orderRow(issued: 0),
        units: [
          {'blood_unit_id': 11, 'expected_version': 4},
          {'blood_unit_id': 11, 'expected_version': 4},
        ],
      ),
      throwsFormatException,
    );
    expect(posts, 0);
  });
  test('server denial cannot produce verified scan', () async {
    final g = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(g.dispose);
    final c = MockClient((r) async {
      if (r.method == 'GET') {
        return response(
          r.url.path.endsWith('/offline-policy') ? policyRow() : orderRow(),
        );
      }
      final j = jsonDecode(r.body) as Map<String, dynamic>;
      return response({
        'clinic_id': 3,
        'id': 42,
        'scan_device': j['scan_device'],
        'operation_identity': j['operation_identity'],
        'requested_order_id': 19,
        'patient_barcode': j['patient_barcode'],
        'unit_barcode': j['unit_barcode'],
        'verification_policy_version': 2,
        'verification_result': 'not_barcode_verified',
        'failure_code': 'wrong_patient',
      });
    });
    addTearDown(c.close);
    final s = await source(c, g, () async => ready(role: 'nurse')).open();
    expect(
      await s.verify(
        order: orderRow(),
        patientBarcode: 'WRONG',
        unitBarcode: 'DIN-1',
        capturedAt: DateTime.now().toUtc(),
      ),
      isNull,
    );
  });
  test('verification and single-use start are separate requests', () async {
    final g = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(g.dispose);
    final posts = <String>[];
    final c = MockClient((r) async {
      if (r.method == 'GET') {
        return response(
          r.url.path.endsWith('/offline-policy') ? policyRow() : orderRow(),
        );
      }
      posts.add(r.url.path);
      final j = jsonDecode(r.body) as Map<String, dynamic>;
      if (r.url.path.endsWith('/barcode/verify')) {
        return response({
          'clinic_id': 3,
          'id': 42,
          'scan_device': j['scan_device'],
          'operation_identity': j['operation_identity'],
          'requested_order_id': 19,
          'patient_barcode': j['patient_barcode'],
          'unit_barcode': j['unit_barcode'],
          'verification_policy_version': 2,
          'verification_result': 'barcode_verified',
          'failure_code': null,
          'failure_reason': null,
          'patient_match': true,
          'unit_match': true,
          'blood_type_match': true,
          'expiry_match': true,
          'order_status_match': true,
          'patient_id': 7,
          'scanned_patient_id': 7,
          'admission_id': 9,
          'blood_product_order_id': 19,
          'source_order_version': 3,
          'blood_unit_id': 11,
          'scanned_unit_id': 11,
          'issued_unit_evidence_id': 21,
          'source_unit_version': 4,
          'actor_staff_id': 5,
          'policy_reference': 'P1',
          'scan_timestamp': j['scan_timestamp'],
        });
      }
      expect(j['barcode_verification_event_id'], 42);
      return response(transfusionRow());
    });
    addTearDown(c.close);
    final s = await source(c, g, () async => ready(role: 'nurse')).open();
    final scan = await s.verify(
      order: orderRow(),
      patientBarcode: 'P7',
      unitBarcode: 'DIN-1',
      capturedAt: DateTime.now().toUtc(),
    );
    expect(scan, isNotNull);
    expect(posts.length, 1);
    final proof = scan!;
    final t = await s.start(proof, {'pulse_bpm': 80});
    expect(t['status'], 'in_progress');
    await expectLater(
      s.start(proof, {'pulse_bpm': 80}),
      throwsA(isA<BbUnavailable>()),
    );
    expect(posts.length, 2);
  });
  test('exact reservation set is required before issue', () async {
    final g = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(g.dispose);
    var posts = 0;
    final c = MockClient((r) async {
      if (r.method == 'POST') posts++;
      if (r.url.path.endsWith('/offline-policy')) return response(policyRow());
      if (r.url.path.endsWith('/units')) {
        return response({
          'items': [unitRow(status: 'reserved')],
          'total': 1,
        });
      }
      return response(orderRow(issued: 0));
    });
    addTearDown(c.close);
    final s = await source(
      c,
      g,
      () async => ready(role: 'lab_technician'),
    ).open();
    await expectLater(
      s.issue(order: orderRow(issued: 0), displayed: [], note: 'Record'),
      throwsFormatException,
    );
    expect(posts, 0);
  });
  test('detail admission is a response scope, not a query filter', () async {
    final g = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(g.dispose);
    var detailReads = 0;
    var posts = 0;
    final c = MockClient((r) async {
      if (r.method == 'POST') {
        posts++;
      }
      if (r.url.path.endsWith('/offline-policy')) {
        return response(policyRow());
      }
      detailReads++;
      expect(r.method, 'GET');
      expect(r.url.path, '/api/v1/clinical-orders/blood-products/19');
      expect(r.url.hasQuery, isFalse);
      return response(orderRow());
    });
    addTearDown(c.close);
    final s = await source(c, g, () async => ready()).open();
    final row = await s.read(BbAction.order, id: 19, patient: 7, admission: 9);
    expect(row['admission_id'], 9);
    await expectLater(
      s.read(BbAction.order, id: 19, patient: 7, admission: 10),
      throwsFormatException,
    );
    expect(detailReads, 2);
    expect(posts, 0);
  });
}
