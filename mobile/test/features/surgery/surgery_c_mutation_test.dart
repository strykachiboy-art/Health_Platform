import 'dart:async';
import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/surgery/data/surgery_action_contract.dart';
import 'package:clinic_system_pro/features/surgery/data/surgery_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/surgery/data/surgery_read_remote_data_source.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _Network implements DeviceConnectivitySource {
  const _Network(this.status);
  final DeviceNetworkStatus status;
  @override
  Future<DeviceNetworkStatus> checkStatus() async => status;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

AuthSessionState _ready(String role, {int clinic = 5}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'surgery-c-token',
        refreshToken: 'refresh',
        userId: 26,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> _case(int id, {int clinic = 5}) => {
  'case_id': id,
  'case_status': 'planned',
  'journal_status': 'draft',
  'pacu_preparation_status': 'prepared',
  'operative_report_status': 'draft',
  'signature_roles': <dynamic>[],
  'material_trace_count': 0,
  'clinical_reference_count': 0,
  'documentary_only': true,
  'operative_completion_authorized': false,
  'pacu_arrival_or_transfer_authorized': false,
  'sign_out_authorized': false,
  'medication_administration_authorized': false,
  'billing_clearance_authorized': false,
  'clinic_id': clinic,
  'patient_id': 2,
  'admission_id': 3,
  'procedure_order_id': 4,
  'case_version': 1,
};

Map<String, dynamic> _envelope(Map<String, dynamic> data) => {
  'success': true,
  'data': data,
  'documentation_only': true,
  'operative_completion_authorized': false,
  'anesthesia_induction_authorized': false,
  'medication_administration_authorized': false,
  'nursing_transfer_authorized': false,
  'billing_clearance_authorized': false,
};

SurgeryMutationRemoteDataSource _writer(
  MockClient client,
  SessionNavigationGuard guard, {
  DeviceNetworkStatus network = DeviceNetworkStatus.connected,
  Future<AuthSessionState> Function()? load,
}) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  final dashboard = DashboardRemoteDataSource(
    apiClient: api,
    loadSession: load ?? () async => _ready('doctor'),
    sessionNavigationGuard: guard,
  );
  return SurgeryMutationRemoteDataSource(
    reader: SurgeryReadRemoteDataSource(dashboardSource: dashboard),
    connectivity: _Network(network),
  );
}

const _rawBook = <String, String>{
  'theatre_id': '4',
  'starts_at': '2030-01-11T09:00:00Z',
  'ends_at': '2030-01-11T11:00:00Z',
};

void main() {
  test(
    'signed-out and patient sessions make no clinical HTTP requests',
    () async {
      var calls = 0;
      final client = MockClient((req) async {
        calls++;
        throw StateError('Forbidden request');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard();
      addTearDown(guard.dispose);
      final writer = _writer(client, guard);
      await expectLater(
        writer.submitOnce(
          const SurgeryActionInput(
            caseId: 5,
            kind: SurgeryActionKind.book,
            raw: _rawBook,
          ),
        ),
        throwsA(isA<SurgeryMutationUnavailable>()),
      );
      guard.markSessionAvailable();
      final patientWriter = _writer(
        client,
        guard,
        load: () async => _ready('patient'),
      );
      await expectLater(
        patientWriter.submitOnce(
          const SurgeryActionInput(
            caseId: 5,
            kind: SurgeryActionKind.book,
            raw: _rawBook,
          ),
        ),
        throwsA(isA<SurgeryMutationUnavailable>()),
      );
      expect(calls, 0);
    },
  );

  test('unknown or disconnected network never triggers the POST', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      throw StateError('No HTTP');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final writer = _writer(
      client,
      guard,
      network: DeviceNetworkStatus.disconnected,
    );
    await expectLater(
      writer.submitOnce(
        const SurgeryActionInput(
          caseId: 5,
          kind: SurgeryActionKind.book,
          raw: _rawBook,
        ),
      ),
      throwsA(isA<SurgeryMutationUnavailable>()),
    );
    expect(calls, 0);
  });

  test(
    'exact single POST, no idempotency, successful GET readback and no replay',
    () async {
      var writes = 0;
      var reads = 0;
      final client = MockClient((req) async {
        expect(req.headers['idempotency-key'], isNull);
        if (req.method == 'GET' &&
            req.url.path == '/api/v1/dashboard/surgery/cases/5') {
          reads++;
          return http.Response(
            jsonEncode({'success': true, 'data': _case(5)}),
            200,
          );
        }
        if (req.method == 'POST' &&
            req.url.path == '/api/v1/surgery/bookings') {
          writes++;
          final data = jsonDecode(req.body) as Map<String, dynamic>;
          expect(data, {
            'theatre_id': 4,
            'surgical_case_id': 5,
            'starts_at': '2030-01-11T09:00:00Z',
            'ends_at': '2030-01-11T11:00:00Z',
          });
          return http.Response(
            jsonEncode(
              _envelope({
                'id': 17,
                'clinic_id': 5,
                'surgical_case_id': 5,
                'theatre_id': 4,
                'status': 'scheduled',
                'version': 1,
              }),
            ),
            201,
          );
        }
        if (req.method == 'GET' &&
            req.url.path == '/api/v1/surgery/cases/5/bookings') {
          reads++;
          return http.Response(
            jsonEncode(
              _envelope({
                'case_id': 5,
                'items': [
                  {
                    'id': 17,
                    'clinic_id': 5,
                    'surgical_case_id': 5,
                    'theatre_id': 4,
                    'status': 'scheduled',
                    'version': 1,
                  },
                ],
                'has_more': false,
              }),
            ),
            200,
          );
        }
        throw StateError(
          'Unexpected Surgery request ${req.method} ${req.url.path}',
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final writer = _writer(client, guard);
      final input = const SurgeryActionInput(
        caseId: 5,
        kind: SurgeryActionKind.book,
        raw: _rawBook,
      );
      final accepted = await writer.submitOnce(input);
      expect(accepted.recordId, 17);
      expect(writes, 1);
      expect(reads, 2);
      await expectLater(
        writer.submitOnce(input),
        throwsA(isA<SurgeryMutationAlreadyAttempted>()),
      );
      expect(writes, 1);
    },
  );

  test('foreign clinic case cannot turn into POST', () async {
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') writes++;
      return http.Response(
        jsonEncode({'success': true, 'data': _case(5, clinic: 9)}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final writer = _writer(client, guard);
    await expectLater(
      writer.submitOnce(
        const SurgeryActionInput(
          caseId: 5,
          kind: SurgeryActionKind.book,
          raw: _rawBook,
        ),
      ),
      throwsA(isA<FormatException>()),
    );
    expect(writes, 0);
  });

  test('lost response never retries and exposes unknown outcome', () async {
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method == 'GET') {
        return http.Response(
          jsonEncode({'success': true, 'data': _case(5)}),
          200,
        );
      }
      writes++;
      return http.Response('{"success":false}', 503);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final writer = _writer(client, guard);
    const input = SurgeryActionInput(
      caseId: 5,
      kind: SurgeryActionKind.book,
      raw: _rawBook,
    );
    await expectLater(
      writer.submitOnce(input),
      throwsA(isA<SurgeryMutationUnconfirmed>()),
    );
    await expectLater(
      writer.submitOnce(input),
      throwsA(isA<SurgeryMutationAlreadyAttempted>()),
    );
    expect(writes, 1);
  });

  test('no implicit retry after session invalidates during mutation', () async {
    final pending = Completer<http.Response>();
    var writes = 0;
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final client = MockClient((req) async {
      if (req.method == 'GET') {
        return http.Response(
          jsonEncode({'success': true, 'data': _case(5)}),
          200,
        );
      }
      writes++;
      return pending.future;
    });
    addTearDown(client.close);
    final writer = _writer(client, guard);
    const input = SurgeryActionInput(
      caseId: 5,
      kind: SurgeryActionKind.book,
      raw: _rawBook,
    );
    final result = writer.submitOnce(input);
    for (var i = 0; i < 20 && writes == 0; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 1));
    }
    expect(writes, 1);
    guard.invalidate();
    pending.complete(
      http.Response(
        jsonEncode(
          _envelope({
            'id': 17,
            'clinic_id': 5,
            'surgical_case_id': 5,
            'status': 'scheduled',
            'version': 1,
          }),
        ),
        201,
      ),
    );
    await expectLater(result, throwsA(isA<SurgeryMutationUnconfirmed>()));
    expect(writes, 1);
  });
}
