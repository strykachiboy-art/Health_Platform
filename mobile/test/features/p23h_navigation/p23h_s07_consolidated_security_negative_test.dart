import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/p23h_navigation/p23h_clinical_action_hub.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';

const _token = 'p23h-s07-inert-widget-test-token';

AuthSessionState _session(String role) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: _token,
    refreshToken: 'p23h-s07-inert-widget-test-refresh',
    userId: 41,
    role: role,
    clinicContextId: 3,
  ),
);

ApiClient _api(MockClient mock) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: mock,
);

Map<String, dynamic> _rx({int patientId = 7, int clinicId = 3}) => {
  'id': 11,
  'clinic_id': clinicId,
  'patient_id': patientId,
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

Map<String, dynamic> _page(List<Map<String, dynamic>> rows) => {
  'success': true,
  'data': {'items': rows, 'total': rows.length, 'page': 1, 'per_page': 50},
};

Widget _hub(
  ApiClient api,
  SessionNavigationGuard guard,
  String role,
  List<P23HClinicalActionIntent> intents,
) {
  Future<AuthSessionState> loader() async => _session(role);
  return MaterialApp(
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
  );
}

void main() {
  test('S07: invalid patient IDs, cross-resource identifiers and missing item IDs never form grants', () {
    for (final kind in P23HClinicalActionKind.values) {
      expect(
        P23HClinicalActionIntent(kind: kind, patientId: 0).isValid,
        isFalse,
      );
      expect(
        P23HClinicalActionIntent(kind: kind, patientId: 2147483648).isValid,
        isFalse,
      );
      expect(
        P23HClinicalActionIntent(
          kind: kind,
          patientId: 7,
          prescriptionId: 11,
          orderId: 31,
          orderItemId: 131,
        ).isValid,
        isFalse,
      );
    }
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
        prescriptionId: -1,
      ).isValid,
      isFalse,
    );
  });

  test('S07: pharmacy home destination never exposes unauthorized or clinicless paths', () {
    bool exposed(String role, int? clinicId) => homeDestinationsFor(
      role: role,
      clinicContextId: clinicId,
    ).any((destination) => destination.path == '/pharmacy/stock');

    expect(exposed('pharmacist', 3), isTrue);
    expect(exposed('ADMIN', 3), isTrue);
    for (final role in <String>[
      'patient',
      'nurse',
      'doctor',
      'receptionist',
      'lab_technician',
    ]) {
      expect(exposed(role, 3), isFalse);
    }
    expect(exposed('pharmacist', null), isFalse);
    expect(exposed('admin', 0), isFalse);
  });

  testWidgets(
    'S07: query-string and direct URL cannot forge a clinical write grant',
    (tester) async {
      final requests = <String>[];
      final client = MockClient((request) async {
        requests.add('${request.method} ${request.url.path}');
        return http.Response(jsonEncode({'success': false}), 403);
      });
      addTearDown(client.close);
      final api = _api(client);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Center(child: Text('S07 sign-in'))),
        sessionNavigationGuard: guard,
        patientPrescriptionDataSource: PatientPrescriptionRemoteDataSource(
          apiClient: api,
          loadSession: () async => _session('pharmacist'),
          sessionNavigationGuard: guard,
        ),
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/patients/7/clinical-action?kind=dispense&prescriptionId=11');
      await tester.pumpAndSettle();
      expect(find.text('Clinical action grant unavailable.'), findsOneWidget);
      expect(requests, isEmpty);

      router.go('/patients/7/clinical-action');
      await tester.pumpAndSettle();
      expect(find.text('Clinical action grant unavailable.'), findsOneWidget);
      expect(requests, isEmpty);

      guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.text('S07 sign-in'), findsOneWidget);
      expect(requests, isEmpty);
    },
  );

  testWidgets('S07: signed-out pharmacy stock route cannot issue a request', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      throw StateError('Unexpected ${request.method} ${request.url.path}');
    });
    addTearDown(client.close);
    final api = _api(client);
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Center(child: Text('S07 sign-in'))),
      sessionNavigationGuard: guard,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/pharmacy/stock');
    await tester.pumpAndSettle();
    expect(find.text('S07 sign-in'), findsOneWidget);
    expect(requests, 0);
  });

  testWidgets(
    'S07: non-clinical patient role cannot fetch or expose mutation actions',
    (tester) async {
      final requests = <String>[];
      final intents = <P23HClinicalActionIntent>[];
      final client = MockClient((request) async {
        requests.add('${request.method} ${request.url.path}');
        throw StateError('Unauthorized request');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(_hub(_api(client), guard, 'patient', intents));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('p23h-clinical-hub-error')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('p23h-hub-prescribe')), findsNothing);
      expect(find.byKey(const ValueKey('p23h-hub-dispense-11')), findsNothing);
      expect(find.byKey(const ValueKey('p23h-hub-collect-31')), findsNothing);
      expect(requests, isEmpty);
      expect(intents, isEmpty);
    },
  );

  testWidgets(
    'S07: prescription from another patient cannot grant action or leak into hub',
    (tester) async {
      final requests = <String>[];
      final intents = <P23HClinicalActionIntent>[];
      final client = MockClient((request) async {
        requests.add('${request.method} ${request.url.path}');
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/prescriptions/patients/7');
        return http.Response(jsonEncode(_page([_rx(patientId: 8)])), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(_hub(_api(client), guard, 'doctor', intents));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('p23h-clinical-hub-error')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('p23h-hub-rx-cancel-11')), findsNothing);
      expect(find.byKey(const ValueKey('p23h-hub-prescribe')), findsNothing);
      expect(requests, ['GET /api/v1/prescriptions/patients/7']);
      expect(intents, isEmpty);
    },
  );

  testWidgets(
    'S07: cross-clinic prescription record cannot create a dispense intent',
    (tester) async {
      final requests = <String>[];
      final intents = <P23HClinicalActionIntent>[];
      final client = MockClient((request) async {
        requests.add('${request.method} ${request.url.path}');
        expect(request.method, 'GET');
        return http.Response(jsonEncode(_page([_rx(clinicId: 9)])), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(_hub(_api(client), guard, 'pharmacist', intents));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('p23h-clinical-hub-error')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('p23h-hub-dispense-11')), findsNothing);
      expect(requests, ['GET /api/v1/prescriptions/patients/7']);
      expect(intents, isEmpty);
    },
  );

  testWidgets(
    'S07: technician lab access denial removes actions and performs no POST',
    (tester) async {
      final requests = <String>[];
      final intents = <P23HClinicalActionIntent>[];
      final client = MockClient((request) async {
        requests.add('${request.method} ${request.url.path}');
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/lab/orders');
        return http.Response(jsonEncode({'success': false}), 403);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(
        _hub(_api(client), guard, 'lab_technician', intents),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('p23h-clinical-hub-error')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('p23h-hub-collect-31')), findsNothing);
      expect(find.byKey(const ValueKey('p23h-hub-result-131')), findsNothing);
      expect(requests, ['GET /api/v1/lab/orders']);
      expect(intents, isEmpty);
    },
  );
}
