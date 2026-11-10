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
import 'package:clinic_system_pro/features/medication_reconciliation/data/medication_reconciliation_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/models/medication_reconciliation.dart';

AuthSessionState ready({
  String role = 'nurse',
  int clinic = 3,
  String token = 'p23g-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'p23g-refresh',
    userId: 43,
    role: role,
    clinicContextId: clinic,
  ),
);

MedicationReconciliationReadRemoteDataSource source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loadSession,
}) => MedicationReconciliationReadRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: loadSession ?? () async => ready(),
  sessionNavigationGuard: guard,
);

Map<String, dynamic> row({
  int clinic = 3,
  int patient = 5,
  int admission = 7,
  int id = 31,
  String event = 'admission',
  int? transfer,
}) => {
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'admission_id': admission,
  'ward_transfer_id': transfer,
  'event_type': event,
  'event_identity': event == 'transfer'
      ? 'transfer:$transfer'
      : '$event:$admission',
  'created_by_staff_id': 9,
  'no_medications_reported': true,
  'notes': null,
  'items': [],
  'record_status': 'draft',
  'record_version': 1,
  'finalized_by_staff_id': null,
  'finalized_at': null,
  'latest_amendment_id': null,
  'created_at': '2026-11-09T09:00:00Z',
  'updated_at': '2026-11-09T09:00:00Z',
};

void main() {
  test(
    'GET admission page uses exact backend route/filter/role/clinic',
    () async {
      var gets = 0;
      final client = MockClient((request) async {
        gets++;
        expect(request.method, 'GET');
        expect(
          request.url.path,
          '/api/v1/medication-reconciliations/admissions/7',
        );
        expect(request.url.queryParameters, {
          'page': '2',
          'per_page': '20',
          'event_type': 'discharge',
        });
        expect(request.headers['authorization'], 'Bearer p23g-token');
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        return http.Response(
          jsonEncode({
            'items': [row(event: 'discharge')],
            'total': 21,
            'page': 2,
            'per_page': 20,
          }),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final page = await source(client, guard).listForAdmission(
        admissionId: 7,
        expectedPatientId: 5,
        page: 2,
        perPage: 20,
        event: MedicationReconciliationEvent.discharge,
      );
      expect(page.items.single.event, MedicationReconciliationEvent.discharge);
      expect(page.hasNext, isFalse);
      expect(gets, 1);
    },
  );

  test('empty admission is empty, not a fabricated reconciliation', () async {
    final client = MockClient(
      (request) async => http.Response(
        jsonEncode({'items': [], 'total': 0, 'page': 1, 'per_page': 50}),
        200,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final page = await source(client, guard).listForAdmission(admissionId: 7);
    expect(page.items, isEmpty);
    expect(page.total, 0);
  });

  test(
    'exact transfer event GET includes transfer ID, no unrelated query fields',
    () async {
      final client = MockClient((request) async {
        expect(request.method, 'GET');
        expect(
          request.url.path,
          '/api/v1/medication-reconciliations/admissions/7/events/transfer',
        );
        expect(request.url.queryParameters, {'ward_transfer_id': '41'});
        return http.Response(
          jsonEncode({'reconciliation': row(event: 'transfer', transfer: 41)}),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final result = await source(client, guard).getForEvent(
        admissionId: 7,
        event: MedicationReconciliationEvent.transfer,
        wardTransferId: 41,
        expectedPatientId: 5,
      );
      expect(result.wardTransferId, 41);
    },
  );

  test('GET detail by record ID enforces admission and patient', () async {
    var gets = 0;
    final client = MockClient((request) async {
      gets++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/medication-reconciliations/31');
      expect(request.url.queryParameters, isEmpty);
      return http.Response(jsonEncode({'reconciliation': row()}), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final result = await source(client, guard).getById(
      reconciliationId: 31,
      expectedAdmissionId: 7,
      expectedPatientId: 5,
    );
    expect(result.id, 31);
    expect(gets, 1);
  });

  test(
    'nurse, doctor, pharmacist can read; other roles blocked before HTTP',
    () async {
      for (final role in ['nurse', 'doctor', 'pharmacist']) {
        final guard = SessionNavigationGuard()..markSessionAvailable();
        addTearDown(guard.dispose);
        final client = MockClient(
          (request) async => http.Response(
            jsonEncode({'items': [], 'total': 0, 'page': 1, 'per_page': 50}),
            200,
          ),
        );
        addTearDown(client.close);
        final result = await source(
          client,
          guard,
          loadSession: () async => ready(role: role),
        ).listForAdmission(admissionId: 7);
        expect(result.items, isEmpty);
      }
      for (final role in ['admin', 'patient', 'receptionist', 'super_admin']) {
        var gets = 0;
        final client = MockClient((request) async {
          gets++;
          return http.Response('{}', 200);
        });
        addTearDown(client.close);
        final guard = SessionNavigationGuard()..markSessionAvailable();
        addTearDown(guard.dispose);
        await expectLater(
          source(
            client,
            guard,
            loadSession: () async => ready(role: role),
          ).listForAdmission(admissionId: 7),
          throwsA(isA<MedicationReconciliationReadSessionException>()),
        );
        expect(gets, 0);
      }
    },
  );

  test(
    'rejects missing context and invalid identifier before any HTTP',
    () async {
      var gets = 0;
      final client = MockClient((request) async {
        gets++;
        return http.Response('{}', 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final adapter = source(client, guard);
      expect(
        () => adapter.listForAdmission(admissionId: 0),
        throwsArgumentError,
      );
      expect(
        () => adapter.listForAdmission(admissionId: 7, perPage: 101),
        throwsArgumentError,
      );
      expect(
        () => adapter.getForEvent(
          admissionId: 7,
          event: MedicationReconciliationEvent.transfer,
        ),
        throwsArgumentError,
      );
      expect(
        () => adapter.getForEvent(
          admissionId: 7,
          event: MedicationReconciliationEvent.discharge,
          wardTransferId: 41,
        ),
        throwsArgumentError,
      );
      expect(() => adapter.getById(reconciliationId: -1), throwsArgumentError);
      expect(gets, 0);
    },
  );

  test('server 404 is preserved, not treated as a fake record', () async {
    final client = MockClient(
      (request) async => http.Response(
        jsonEncode({
          'error': 'Medication reconciliation not found for clinical event',
        }),
        404,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      source(client, guard).getForEvent(
        admissionId: 7,
        event: MedicationReconciliationEvent.discharge,
      ),
      throwsA(
        isA<ApiException>().having(
          (error) => error.statusCode,
          'statusCode',
          404,
        ),
      ),
    );
  });

  test('cross-clinic or wrong-patient payload fails closed', () async {
    for (final fixture in [
      row(clinic: 9),
      row(patient: 8),
      row(admission: 99),
    ]) {
      final client = MockClient(
        (request) async =>
            http.Response(jsonEncode({'reconciliation': fixture}), 200),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        source(client, guard).getById(
          reconciliationId: 31,
          expectedPatientId: 5,
          expectedAdmissionId: 7,
        ),
        throwsFormatException,
      );
    }
  });

  test('session changed while request pending invalidates response', () async {
    final arrived = Completer<void>();
    final pending = Completer<http.Response>();
    final client = MockClient((request) async {
      arrived.complete();
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final future = source(client, guard).listForAdmission(admissionId: 7);
    await arrived.future;
    guard.invalidate();
    pending.complete(
      http.Response(
        jsonEncode({
          'items': [row()],
          'total': 1,
          'page': 1,
          'per_page': 50,
        }),
        200,
      ),
    );
    await expectLater(
      future,
      throwsA(isA<MedicationReconciliationReadSessionException>()),
    );
  });

  test('token or clinic switch mid-read invalidates successful GET', () async {
    for (final changed in [ready(token: 'rotated'), ready(clinic: 9)]) {
      var reads = 0;
      final client = MockClient(
        (request) async => http.Response(
          jsonEncode({'items': [], 'total': 0, 'page': 1, 'per_page': 50}),
          200,
        ),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final adapter = source(
        client,
        guard,
        loadSession: () async {
          reads++;
          return reads == 1 ? ready() : changed;
        },
      );
      await expectLater(
        adapter.listForAdmission(admissionId: 7),
        throwsA(isA<MedicationReconciliationReadSessionException>()),
      );
    }
  });

  test('signed-out session blocks before HTTP', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSignedOut();
    addTearDown(guard.dispose);
    await expectLater(
      source(client, guard).listForAdmission(admissionId: 7),
      throwsA(isA<MedicationReconciliationReadSessionException>()),
    );
    expect(calls, 0);
  });
}
