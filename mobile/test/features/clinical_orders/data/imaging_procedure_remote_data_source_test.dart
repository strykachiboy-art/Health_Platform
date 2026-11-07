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
import 'package:clinic_system_pro/features/clinical_orders/data/imaging_procedure_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/imaging_procedure_order.dart';

AuthSessionState _ready({
  String role = 'doctor',
  int clinic = 3,
  String token = 'token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'refresh',
    userId: 42,
    clinicContextId: clinic,
    role: role,
  ),
);

Map<String, dynamic> _row({
  String kind = 'imaging',
  int clinic = 3,
  int patient = 7,
  int admission = 9,
  int id = 17,
  String status = 'active',
  bool consent = false,
  bool verified = false,
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'admission_id': admission,
  'ordered_by_staff_id': 8,
  'priority': 'routine',
  'status': status,
  'version': 1,
  'ordered_at': '2026-11-07T08:00:00+00:00',
  'completed_at': status == 'completed' ? '2026-11-07T09:00:00+00:00' : null,
  'cancelled_at': status == 'cancelled' ? '2026-11-07T09:00:00+00:00' : null,
  'indication': 'Clinical indication',
  if (kind == 'imaging') ...<String, dynamic>{
    'study_name': 'Chest X-ray',
    'modality': 'xray',
    'body_site': 'Chest',
  } else ...<String, dynamic>{
    'procedure_name': 'Wound repair',
    'procedure_type': 'bedside',
    'consent_required': consent,
    'consent_verified_at': verified ? '2026-11-07T08:10:00+00:00' : null,
  },
};

ImagingProcedureRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? session,
}) {
  return ImagingProcedureRemoteDataSource(
    unifiedSource: ClinicalOrdersRemoteDataSource(
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
}

void main() {
  test('accepts scoped imaging, procedure and terminal-state records', () {
    for (final kind in <String>['imaging', 'procedures']) {
      final record = ImagingProcedureOrder.fromJson(
        _row(kind: kind),
        kind: kind,
        expectedClinicId: 3,
        expectedPatientId: 7,
        expectedAdmissionId: 9,
      );
      expect(record.id, 17);
      expect(record.isOpen, isTrue);
      final completed = ImagingProcedureOrder.fromJson(
        _row(kind: kind, status: 'completed'),
        kind: kind,
        expectedClinicId: 3,
        expectedPatientId: 7,
        expectedAdmissionId: 9,
      );
      expect(completed.isOpen, isFalse);
    }
  });

  test('rejects cross-clinic, patient, admission and wrong order ID', () {
    for (final row in <Map<String, dynamic>>[
      _row(clinic: 4),
      _row(patient: 8),
      _row(admission: 10),
    ]) {
      expect(
        () => ImagingProcedureOrder.fromJson(
          row,
          kind: 'imaging',
          expectedClinicId: 3,
          expectedPatientId: 7,
          expectedAdmissionId: 9,
        ),
        throwsFormatException,
      );
    }
    expect(
      () => ImagingProcedureOrder.fromJson(
        _row(),
        kind: 'imaging',
        expectedClinicId: 3,
        expectedPatientId: 7,
        expectedAdmissionId: 9,
        expectedId: 18,
      ),
      throwsFormatException,
    );
  });

  test('rejects invalid status, inconsistent terminal timestamps and unknown enums', () {
    final invalidStatus = _row()..['status'] = 'untrusted';
    final invalidTimestamp = _row(status: 'completed')..['completed_at'] = null;
    final invalidPriority = _row()..['priority'] = 'immediate';
    final invalidModality = _row()..['modality'] = 'unknown';
    for (final row in <Map<String, dynamic>>[
      invalidStatus,
      invalidTimestamp,
      invalidPriority,
      invalidModality,
    ]) {
      expect(
        () => ImagingProcedureOrder.fromJson(
          row,
          kind: 'imaging',
          expectedClinicId: 3,
          expectedPatientId: 7,
          expectedAdmissionId: 9,
        ),
        throwsFormatException,
      );
    }
  });

  test('list rejects duplicate IDs, wrong totals and mismatched scope', () {
    for (final response in <Map<String, dynamic>>[
      {
        'items': [_row(), _row()],
        'total': 2,
      },
      {
        'items': [_row()],
        'total': 2,
      },
      {
        'items': [_row(clinic: 4)],
        'total': 1,
      },
    ]) {
      expect(
        () => ImagingProcedureOrderList.fromJson(
          response,
          kind: 'imaging',
          expectedClinicId: 3,
          expectedPatientId: 7,
          expectedAdmissionId: 9,
        ),
        throwsFormatException,
      );
    }
  });

  test(
    'imaging GET uses admission scope and returns verified records',
    () async {
      var calls = 0;
      final client = MockClient((req) async {
        calls++;
        expect(req.method, 'GET');
        expect(req.url.path, '/api/v1/clinical-orders/imaging');
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
      final rows = await _source(
        client,
        guard,
      ).list(kind: 'imaging', patientId: 7, admissionId: 9);
      expect(rows.items.single.name, 'Chest X-ray');
      expect(calls, 1);
    },
  );

  test(
    'doctor imaging creation submits one POST without idempotency or retry',
    () async {
      var posts = 0;
      final client = MockClient((req) async {
        posts++;
        expect(req.method, 'POST');
        expect(req.url.path, '/api/v1/clinical-orders/imaging');
        expect(
          req.headers.keys.map((s) => s.toLowerCase()),
          isNot(contains('idempotency-key')),
        );
        final body = jsonDecode(req.body) as Map<String, dynamic>;
        expect(body['admission_id'], 9);
        expect(body['body_site'], 'Chest');
        expect(body['study_name'], 'Chest X-ray');
        return http.Response(
          jsonEncode({'order': _row(), 'message': 'created'}),
          201,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final created = await _source(client, guard).create(
        kind: 'imaging',
        patientId: 7,
        admissionId: 9,
        name: 'Chest X-ray',
        category: 'xray',
        bodySite: 'Chest',
        indication: 'Clinical indication',
      );
      expect(created.id, 17);
      expect(posts, 1);
    },
  );

  test('procedure create includes consent and verify-consent uses POST with empty body', () async {
    var posts = 0;
    final client = MockClient((req) async {
      posts++;
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      if (req.url.path.endsWith('/verify-consent')) {
        expect(body, isEmpty);
        return http.Response(
          jsonEncode({
            'order': _row(kind: 'procedures', consent: true, verified: true),
            'message': 'verified',
          }),
          200,
        );
      }
      expect(req.url.path, '/api/v1/clinical-orders/procedures');
      expect(body['consent_required'], true);
      return http.Response(
        jsonEncode({
          'order': _row(kind: 'procedures', consent: true),
          'message': 'created',
        }),
        201,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await source.create(
      kind: 'procedures',
      patientId: 7,
      admissionId: 9,
      name: 'Wound repair',
      category: 'bedside',
      indication: 'Clinical indication',
      consentRequired: true,
    );
    final verified = await source.act(
      kind: 'procedures',
      patientId: 7,
      admissionId: 9,
      orderId: 17,
      action: 'verify-consent',
    );
    expect(verified.consentVerifiedAt, isNotNull);
    expect(posts, 2);
  });

  test('admin or nurse cannot issue doctor-only writes', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final role in <String>['admin', 'nurse', 'patient']) {
      final source = _source(
        client,
        guard,
        session: () async => _ready(role: role),
      );
      await expectLater(
        source.create(
          kind: 'imaging',
          patientId: 7,
          admissionId: 9,
          name: 'Chest X-ray',
          category: 'xray',
          bodySite: 'Chest',
          indication: 'Clinical indication',
        ),
        throwsA(isA<ImagingProcedureSessionException>()),
      );
    }
    expect(calls, 0);
  });

  test('session change while GET is pending fails closed', () async {
    final pending = Completer<http.Response>();
    final client = MockClient((_) => pending.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final future = _source(
      client,
      guard,
    ).list(kind: 'imaging', patientId: 7, admissionId: 9);
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
    await expectLater(future, throwsA(isA<ImagingProcedureSessionException>()));
  });

  test('transport failure on write never retries by itself', () async {
    var posts = 0;
    final client = MockClient((_) async {
      posts++;
      throw http.ClientException('connection dropped');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).create(
        kind: 'imaging',
        patientId: 7,
        admissionId: 9,
        name: 'Chest X-ray',
        category: 'xray',
        bodySite: 'Chest',
        indication: 'Clinical indication',
      ),
      throwsException,
    );
    expect(posts, 1);
  });
}
