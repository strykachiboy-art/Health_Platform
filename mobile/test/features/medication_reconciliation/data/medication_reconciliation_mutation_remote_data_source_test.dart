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
import 'package:clinic_system_pro/features/medication_reconciliation/data/models/medication_reconciliation.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/medication_reconciliation_mutation_remote_data_source.dart';

const oldTimestamp = '2026-11-10T09:00:00Z';
const newTimestamp = '2026-11-10T09:01:00Z';

AuthSessionState ready({
  String role = 'doctor',
  int clinic = 3,
  String token = 'write-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'write-refresh',
    userId: 44,
    role: role,
    clinicContextId: clinic,
  ),
);

Map<String, dynamic> item({String decision = 'continue'}) => {
  'source_prescription_item_id': 21,
  'medication_name': 'Metformin',
  'decision': decision,
  'replacement_medication_name': decision == 'replace' ? 'Alternative' : null,
  'dose_text': '500 mg',
  'route_text': 'oral',
  'frequency_text': 'twice daily',
  'instructions': null,
  'rationale': 'Checked by clinician',
};

Map<String, dynamic> row({
  int id = 31,
  int clinic = 3,
  int patient = 5,
  int admission = 7,
  String event = 'admission',
  int? transfer,
  String timestamp = oldTimestamp,
  String status = 'draft',
  List<Map<String, dynamic>>? medications,
  bool noMedicationsReported = true,
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
  'created_by_staff_id': 19,
  'no_medications_reported': noMedicationsReported,
  'notes': null,
  'items': medications ?? <Map<String, dynamic>>[],
  'record_status': status,
  'record_version': 1,
  'finalized_by_staff_id': null,
  'finalized_at': null,
  'latest_amendment_id': null,
  'created_at': oldTimestamp,
  'updated_at': timestamp,
};

MedicationReconciliationRecord draft({
  int clinic = 3,
  String status = 'draft',
}) => MedicationReconciliationRecord.fromJson(
  row(clinic: clinic, status: status),
  expectedClinicId: clinic,
);

MedicationReconciliationDraftDocument noMeds() =>
    const MedicationReconciliationDraftDocument(
      noMedicationsReported: true,
      notes: null,
      items: [],
    );

MedicationReconciliationMutationRemoteDataSource source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loadSession,
}) => MedicationReconciliationMutationRemoteDataSource(
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

void main() {
  test('POST creates draft once, then confirms with GET readback', () async {
    final methods = <String>[];
    final client = MockClient((request) async {
      methods.add(request.method);
      expect(request.headers['authorization'], 'Bearer write-token');
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      if (request.method == 'POST') {
        expect(request.url.path, '/api/v1/medication-reconciliations');
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body, {
          'admission_id': 7,
          'event_type': 'admission',
          'no_medications_reported': true,
          'notes': null,
          'items': <dynamic>[],
        });
        return http.Response(jsonEncode({'reconciliation': row()}), 201);
      }
      expect(request.url.path, '/api/v1/medication-reconciliations/31');
      return http.Response(jsonEncode({'reconciliation': row()}), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final saved = await source(client, guard).createDraft(
      admissionId: 7,
      expectedPatientId: 5,
      event: MedicationReconciliationEvent.admission,
      document: noMeds(),
    );
    expect(saved.status, MedicationReconciliationRecordStatus.draft);
    expect(methods, ['POST', 'GET']);
  });

  test(
    'PATCH carries exact timestamp CAS and reads back updated draft',
    () async {
      final methods = <String>[];
      final client = MockClient((request) async {
        methods.add(request.method);
        if (request.method == 'PATCH') {
          expect(request.url.path, '/api/v1/medication-reconciliations/31');
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          expect(
            DateTime.parse(body['expected_updated_at'] as String),
            DateTime.parse(oldTimestamp),
          );
          expect(body['no_medications_reported'], true);
          return http.Response(
            jsonEncode({'reconciliation': row(timestamp: newTimestamp)}),
            200,
          );
        }
        expect(request.method, 'GET');
        return http.Response(
          jsonEncode({'reconciliation': row(timestamp: newTimestamp)}),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final saved = await source(
        client,
        guard,
      ).updateDraft(current: draft(), document: noMeds());
      expect(saved.updatedAt, DateTime.parse(newTimestamp));
      expect(methods, ['PATCH', 'GET']);
    },
  );

  test('stale PATCH returns 409 and never performs a GET or retry', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.method, 'PATCH');
      return http.Response(jsonEncode({'error': 'draft changed'}), 409);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      source(client, guard).updateDraft(current: draft(), document: noMeds()),
      throwsA(isA<ApiException>().having((e) => e.statusCode, 'status', 409)),
    );
    expect(calls, 1);
  });

  test('offline/uncertain PATCH propagates without replay', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      throw http.ClientException('unreachable');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      source(client, guard).updateDraft(current: draft(), document: noMeds()),
      throwsA(isA<ApiException>()),
    );
    expect(calls, 1);
  });

  test('nurse cannot create or update before network request', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final adapter = source(
      client,
      guard,
      loadSession: () async => ready(role: 'nurse'),
    );
    await expectLater(
      adapter.createDraft(
        admissionId: 7,
        expectedPatientId: 5,
        event: MedicationReconciliationEvent.admission,
        document: noMeds(),
      ),
      throwsA(isA<MedicationReconciliationMutationSessionException>()),
    );
    await expectLater(
      adapter.updateDraft(current: draft(), document: noMeds()),
      throwsA(isA<MedicationReconciliationMutationSessionException>()),
    );
    expect(calls, 0);
  });

  test('wrong-clinic current draft rejected before PATCH', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      source(
        client,
        guard,
      ).updateDraft(current: draft(clinic: 4), document: noMeds()),
      throwsA(isA<MedicationReconciliationMutationSessionException>()),
    );
    expect(calls, 0);
  });

  test(
    'response claiming foreign patient fails without success readback',
    () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return http.Response(
          jsonEncode({'reconciliation': row(patient: 99)}),
          201,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        source(client, guard).createDraft(
          admissionId: 7,
          expectedPatientId: 5,
          event: MedicationReconciliationEvent.admission,
          document: noMeds(),
        ),
        throwsA(isA<FormatException>()),
      );
      expect(calls, 1);
    },
  );

  test(
    'readback timestamp changed by another actor requires recheck',
    () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        if (request.method == 'PATCH') {
          return http.Response(
            jsonEncode({'reconciliation': row(timestamp: newTimestamp)}),
            200,
          );
        }
        return http.Response(
          jsonEncode({
            'reconciliation': row(timestamp: '2026-11-10T09:02:00Z'),
          }),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        source(client, guard).updateDraft(current: draft(), document: noMeds()),
        throwsA(isA<MedicationReconciliationWriteRecheckRequiredException>()),
      );
      expect(calls, 2);
    },
  );

  test('signout during in-flight PATCH discards late response', () async {
    final wait = Completer<http.Response>();
    final client = MockClient((request) => wait.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final operation = source(
      client,
      guard,
    ).updateDraft(current: draft(), document: noMeds());
    await Future<void>.delayed(Duration.zero);
    guard.markSignedOut();
    wait.complete(
      http.Response(
        jsonEncode({'reconciliation': row(timestamp: newTimestamp)}),
        200,
      ),
    );
    await expectLater(
      operation,
      throwsA(isA<MedicationReconciliationMutationSessionException>()),
    );
  });

  test('concurrent writes on one adapter do not double-send', () async {
    final wait = Completer<http.Response>();
    var calls = 0;
    final client = MockClient((request) {
      calls++;
      if (request.method == 'PATCH') {
        return wait.future;
      }
      return Future.value(
        http.Response(
          jsonEncode({'reconciliation': row(timestamp: newTimestamp)}),
          200,
        ),
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final adapter = source(client, guard);
    final first = adapter.updateDraft(current: draft(), document: noMeds());
    await expectLater(
      adapter.updateDraft(current: draft(), document: noMeds()),
      throwsA(isA<MedicationReconciliationMutationInFlightException>()),
    );
    wait.complete(
      http.Response(
        jsonEncode({'reconciliation': row(timestamp: newTimestamp)}),
        200,
      ),
    );
    await first;
    expect(calls, 2);
  });

  test('transfer create sends event-owned ward transfer ID', () async {
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body['event_type'], 'transfer');
        expect(body['ward_transfer_id'], 17);
      }
      return http.Response(
        jsonEncode({'reconciliation': row(event: 'transfer', transfer: 17)}),
        request.method == 'POST' ? 201 : 200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final result = await source(client, guard).createDraft(
      admissionId: 7,
      expectedPatientId: 5,
      event: MedicationReconciliationEvent.transfer,
      wardTransferId: 17,
      document: noMeds(),
    );
    expect(result.wardTransferId, 17);
  });

  test(
    'document rejects contradictory no-medications and invalid replacement',
    () {
      final sourceItem = MedicationReconciliationItem.fromJson(item());
      expect(
        () => MedicationReconciliationDraftDocument(
          noMedicationsReported: true,
          notes: null,
          items: [sourceItem],
        ).toJson(),
        throwsArgumentError,
      );
      final badReplacement = MedicationReconciliationItem(
        sourcePrescriptionItemId: 21,
        medicationName: 'Metformin',
        decision: MedicationReconciliationDecision.replace,
        replacementMedicationName: null,
        doseText: '500 mg',
        routeText: 'oral',
        frequencyText: 'twice daily',
        instructions: null,
        rationale: null,
      );
      expect(
        () => MedicationReconciliationDraftDocument(
          noMedicationsReported: false,
          notes: null,
          items: [badReplacement],
        ).toJson(),
        throwsFormatException,
      );
    },
  );
}
