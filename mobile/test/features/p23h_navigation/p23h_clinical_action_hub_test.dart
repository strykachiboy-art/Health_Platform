import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';
import 'package:clinic_system_pro/features/p23h_navigation/p23h_clinical_action_hub.dart';

AuthSessionState _session(String role) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 's06b-token',
    refreshToken: 's06b-refresh',
    userId: 41,
    role: role,
    clinicContextId: 3,
  ),
);

Map<String, dynamic> _rx() => {
  'id': 11,
  'clinic_id': 3,
  'patient_id': 7,
  'consultation_id': null,
  'prescribed_by_id': 10,
  'status': 'active',
  'notes': null,
  'issued_at': null,
  'expires_at': null,
  'items': [
    {
      'id': 111,
      'prescription_id': 11,
      'drug_id': 15,
      'dosage': '1 tablet',
      'frequency': 'daily',
      'duration': '5 days',
      'quantity': 5,
      'instructions': null,
    },
  ],
};

Map<String, dynamic> _lab({String status = 'ordered'}) => {
  'id': 31,
  'clinic_id': 3,
  'patient_id': 7,
  'consultation_id': null,
  'ordered_by_id': 9,
  'status': status,
  'created_at': '2026-11-10T12:00:00+00:00',
  'items': [
    {
      'id': 131,
      'order_id': 31,
      'test_id': 10,
      'test': {
        'id': 10,
        'clinic_id': 3,
        'name': 'Hemoglobin',
        'sample_type': 'blood',
        'reference_range': null,
        'unit': null,
        'critical_low': null,
        'critical_high': null,
        'is_active': true,
      },
      'result_value': null,
      'flag': null,
      'result_notes': null,
    },
  ],
};

Map<String, dynamic> _page(List<Map<String, dynamic>> items) => {
  'success': true,
  'data': {'items': items, 'total': items.length, 'page': 1, 'per_page': 50},
};

Future<void> _host(
  WidgetTester tester,
  SessionNavigationGuard guard,
  String role,
  List<P23HClinicalActionIntent> intents,
  List<String> paths, {
  String status = 'ordered',
}) async {
  final client = MockClient((request) async {
    paths.add('${request.method} ${request.url.path}');
    expect(request.method, 'GET');
    expect(request.headers['authorization'], 'Bearer s06b-token');
    expect(request.url.queryParameters['per_page'], '50');
    if (request.url.path == '/api/v1/prescriptions/patients/7') {
      return http.Response(jsonEncode(_page([_rx()])), 200);
    }
    if (request.url.path == '/api/v1/lab/orders') {
      expect(request.url.queryParameters['patient_id'], '7');
      return http.Response(jsonEncode(_page([_lab(status: status)])), 200);
    }
    throw StateError('Unexpected request: ${request.url}');
  });
  addTearDown(client.close);
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  Future<AuthSessionState> loader() async => _session(role);
  await tester.pumpWidget(
    MaterialApp(
      home: P23HClinicalActionHub(
        patientId: 7,
        prescriptionReader: PatientPrescriptionRemoteDataSource(
          apiClient: api,
          loadSession: loader,
          sessionNavigationGuard: guard,
        ),
        labReader: LabOrderReadRemoteDataSource(
          apiClient: api,
          loadSession: loader,
          sessionNavigationGuard: guard,
        ),
        onActionRequested: intents.add,
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  test('clinical intent rejects missing or cross-type identifiers', () {
    expect(
      const P23HClinicalActionIntent(
        kind: P23HClinicalActionKind.enterResult,
        patientId: 7,
        orderId: 31,
        orderItemId: 131,
      ).isValid,
      isTrue,
    );
    expect(
      const P23HClinicalActionIntent(
        kind: P23HClinicalActionKind.enterResult,
        patientId: 7,
        orderId: 31,
      ).isValid,
      isFalse,
    );
    expect(
      const P23HClinicalActionIntent(
        kind: P23HClinicalActionKind.dispense,
        patientId: 7,
        prescriptionId: 11,
        orderId: 31,
      ).isValid,
      isFalse,
    );
  });

  testWidgets(
    'doctor receives server-scoped prescription and lab actions, no POST',
    (tester) async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final intents = <P23HClinicalActionIntent>[];
      final paths = <String>[];
      await _host(tester, guard, 'doctor', intents, paths);
      expect(find.byKey(const ValueKey('p23h-hub-prescribe')), findsOneWidget);
      expect(find.byKey(const ValueKey('p23h-hub-lab-mutate')), findsOneWidget);
      expect(
        find.byKey(const ValueKey('p23h-hub-rx-cancel-11')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('p23h-hub-dispense-11')), findsNothing);
      expect(find.byKey(const ValueKey('p23h-hub-collect-31')), findsNothing);
      await tester.tap(find.byKey(const ValueKey('p23h-hub-prescribe')));
      expect(intents.single.kind, P23HClinicalActionKind.prescribe);
      expect(intents.single.patientId, 7);
      expect(paths, [
        'GET /api/v1/prescriptions/patients/7',
        'GET /api/v1/lab/orders',
      ]);
    },
  );

  testWidgets(
    'pharmacist sees dispense only for server-returned prescription',
    (tester) async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final intents = <P23HClinicalActionIntent>[];
      final paths = <String>[];
      await _host(tester, guard, 'pharmacist', intents, paths);
      expect(
        find.byKey(const ValueKey('p23h-hub-dispense-11')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('p23h-hub-prescribe')), findsNothing);
      expect(find.byKey(const ValueKey('p23h-hub-lab-mutate')), findsNothing);
      await tester.tap(find.byKey(const ValueKey('p23h-hub-dispense-11')));
      expect(intents.single.prescriptionId, 11);
      expect(intents.single.kind, P23HClinicalActionKind.dispense);
      expect(paths, ['GET /api/v1/prescriptions/patients/7']);
    },
  );

  testWidgets(
    'technician sees allowed specimen action; invalidation clears it',
    (tester) async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final intents = <P23HClinicalActionIntent>[];
      final paths = <String>[];
      await _host(tester, guard, 'lab_technician', intents, paths);
      expect(find.byKey(const ValueKey('p23h-hub-collect-31')), findsOneWidget);
      expect(find.byKey(const ValueKey('p23h-hub-prescribe')), findsNothing);
      expect(paths, ['GET /api/v1/lab/orders']);
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p23h-hub-collect-31')), findsNothing);
      expect(
        find.byKey(const ValueKey('p23h-clinical-hub-error')),
        findsOneWidget,
      );
      expect(intents, isEmpty);
    },
  );

  testWidgets(
    'in-progress results can open a single technician action without POST',
    (tester) async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final intents = <P23HClinicalActionIntent>[];
      final paths = <String>[];
      await _host(
        tester,
        guard,
        'lab_technician',
        intents,
        paths,
        status: 'in_progress',
      );
      final target = find.byKey(const ValueKey('p23h-hub-result-131'));
      await tester.scrollUntilVisible(
        target,
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(target);
      expect(intents.single.kind, P23HClinicalActionKind.enterResult);
      expect(intents.single.orderId, 31);
      expect(intents.single.orderItemId, 131);
      expect(paths, ['GET /api/v1/lab/orders']);
    },
  );
}
