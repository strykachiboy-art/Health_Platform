import 'dart:async';
import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/surgery/data/surgery_read_remote_data_source.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

AuthSessionState _ready(String role, {int clinicId = 5}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'read-only-surgery-token',
        refreshToken: 'refresh',
        userId: 26,
        role: role,
        clinicContextId: clinicId,
      ),
    );

Map<String, dynamic> _case(int id) => {
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
};

Map<String, dynamic> _worklist({
  int clinicId = 5,
  String scope = 'assigned_cases',
}) => {
  'clinic_id': clinicId,
  'scope': scope,
  'total': 1,
  'limit': 20,
  'offset': 0,
  'metrics': {
    'planned': 1,
    'on_hold': 0,
    'cancelled': 0,
    'sealed_documentation': 0,
    'reviewed_pacu_preparations': 0,
    'dual_signed_reports': 0,
  },
  'cases': [_case(9)],
  'documentation_only': true,
  'operative_completion_authorized': false,
  'clinical_execution_authorized': false,
};

SurgeryReadRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? load,
}) {
  return SurgeryReadRemoteDataSource(
    dashboardSource: DashboardRemoteDataSource(
      apiClient: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      ),
      loadSession: load ?? () async => _ready('doctor'),
      sessionNavigationGuard: guard,
    ),
  );
}

void main() {
  test('worklist uses authenticated GET only; no queue or POST', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/dashboard/surgery');
      expect(request.url.queryParameters, {'limit': '20', 'offset': '0'});
      expect(
        request.headers['authorization'],
        'Bearer read-only-surgery-token',
      );
      expect(request.headers.containsKey('idempotency-key'), false);
      return http.Response(
        jsonEncode({'success': true, 'data': _worklist()}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final result = await _source(client, guard).fetchWorklist();
    expect(result.cases.single.caseId, 9);
    expect(calls, 1);
  });

  test(
    'direct reads reject patient, receptionist and other staff before HTTP',
    () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        throw StateError('Forbidden HTTP request');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      for (final role in const ['patient', 'receptionist', 'pharmacist']) {
        await expectLater(
          _source(
            client,
            guard,
            load: () async => _ready(role),
          ).fetchWorklist(),
          throwsA(isA<SurgeryReadDenied>()),
        );
      }
      expect(calls, 0);
    },
  );

  test(
    'missing selected clinic blocks requests including super admin',
    () async {
      final client = MockClient((request) async {
        throw StateError('No request permitted');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(
          client,
          guard,
          load: () async => _ready('super_admin', clinicId: 0),
        ).fetchWorklist(),
        throwsA(isA<SurgeryReadDenied>()),
      );
    },
  );

  test('session invalidation during GET drops the old response', () async {
    final pending = Completer<http.Response>();
    final client = MockClient((request) => pending.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final operation = _source(client, guard).fetchWorklist();
    await Future<void>.delayed(Duration.zero);
    guard.invalidate();
    pending.complete(
      http.Response(jsonEncode({'success': true, 'data': _worklist()}), 200),
    );
    await expectLater(operation, throwsA(isA<SurgeryReadDenied>()));
  });

  test('foreign clinic response never appears in worklist', () async {
    final client = MockClient(
      (request) async => http.Response(
        jsonEncode({'success': true, 'data': _worklist(clinicId: 99)}),
        200,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).fetchWorklist(),
      throwsFormatException,
    );
  });

  test(
    'case details are id-only GET and reject changed case identity',
    () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/dashboard/surgery/cases/9');
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              ..._case(10),
              'clinic_id': 5,
              'patient_id': 2,
              'admission_id': 3,
              'procedure_order_id': 4,
              'case_version': 1,
            },
          }),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(client, guard).fetchCase(9),
        throwsFormatException,
      );
      expect(calls, 1);
      await expectLater(
        _source(client, guard).fetchCase(0),
        throwsArgumentError,
      );
      expect(calls, 1);
    },
  );

  test('signed-out guard blocks Surgery GET before HTTP', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      throw StateError('No HTTP');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).fetchWorklist(),
      throwsA(isA<SurgeryReadDenied>()),
    );
    expect(calls, 0);
  });
}
