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
import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/presentation/clinical_orders_workspace_screen.dart';

AuthSessionState _ready({String role = 'doctor'}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'ui-orders-token',
    refreshToken: 'refresh',
    userId: 42,
    role: role,
    clinicContextId: 3,
  ),
);

ClinicalOrdersRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
}) => ClinicalOrdersRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => _ready(role: role),
  sessionNavigationGuard: guard,
);

Map<String, dynamic> _record() => <String, dynamic>{
  'order_type': 'imaging',
  'source_entity_type': 'ImagingOrder',
  'source_entity_id': 11,
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 23,
  'consultation_id': 34,
  'ordered_by_staff_id': 8,
  'ordered_at': '2026-11-07T10:00:00+00:00',
  'priority': 'routine',
  'status': 'active',
  'source_status': 'active',
  'display_name': 'Chest X-ray',
  'instructions': null,
  'scheduled_at': null,
  'due_at': null,
  'completed_at': null,
  'cancelled_at': null,
  'details': <String, dynamic>{},
};

void _tall(WidgetTester tester) {
  tester.view.devicePixelRatio = 1.0;
  tester.view.physicalSize = const Size(1200, 2000);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

void main() {
  testWidgets(
    'entry validates patient ID and opens scoped route without HTTP',
    (tester) async {
      _tall(tester);
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response('{}', 500);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var selected = 0;
      await tester.pumpWidget(
        MaterialApp(
          home: ClinicalOrdersWorkspaceScreen(
            dataSource: _source(client, guard),
            onPatientSelected: (id) => selected = id,
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p23d-orders-entry')), findsOneWidget);
      await tester.enterText(
        find.byKey(const ValueKey('p23d-orders-patient-id')),
        '0',
      );
      await tester.tap(find.byKey(const ValueKey('p23d-orders-open-patient')));
      await tester.pumpAndSettle();
      expect(selected, 0);
      await tester.enterText(
        find.byKey(const ValueKey('p23d-orders-patient-id')),
        '7',
      );
      await tester.tap(find.byKey(const ValueKey('p23d-orders-open-patient')));
      expect(selected, 7);
      expect(calls, 0);
    },
  );

  testWidgets(
    'patient timeline is read-only and can open linked consultation',
    (tester) async {
      _tall(tester);
      var calls = 0;
      final client = MockClient((req) async {
        calls++;
        expect(req.method, 'GET');
        return http.Response(
          jsonEncode({
            'items': [_record()],
            'total': 1,
          }),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var linked = 0;
      await tester.pumpWidget(
        MaterialApp(
          home: ClinicalOrdersWorkspaceScreen(
            dataSource: _source(client, guard),
            patientId: 7,
            onPatientSelected: (_) {},
            onConsultationSelected: (patient, consultation) {
              expect(patient, 7);
              linked = consultation;
            },
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p23d-orders-loaded')), findsOneWidget);
      expect(find.text('Chest X-ray'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('p23d-order-imaging-11')),
        findsOneWidget,
      );
      await tester.tap(
        find.byKey(const ValueKey('p23d-linked-consultation-11')),
      );
      expect(linked, 34);
      expect(calls, 1);
    },
  );

  testWidgets('sign-out clears order data and hides patient timeline', (
    tester,
  ) async {
    _tall(tester);
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode({
          'items': [_record()],
          'total': 1,
        }),
        200,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicalOrdersWorkspaceScreen(
          dataSource: _source(client, guard),
          patientId: 7,
          onPatientSelected: (_) {},
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Chest X-ray'), findsOneWidget);
    guard.markSignedOut();
    await tester.pumpAndSettle();
    expect(find.text('Chest X-ray'), findsNothing);
    expect(
      find.byKey(const ValueKey('p23d-orders-unavailable')),
      findsOneWidget,
    );
  });

  testWidgets('unsupported role cannot open patient timeline or read PHI', (
    tester,
  ) async {
    _tall(tester);
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicalOrdersWorkspaceScreen(
          dataSource: _source(client, guard, role: 'patient'),
          patientId: 7,
          onPatientSelected: (_) {},
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('p23d-orders-unavailable')),
      findsOneWidget,
    );
    expect(calls, 0);
  });
}
