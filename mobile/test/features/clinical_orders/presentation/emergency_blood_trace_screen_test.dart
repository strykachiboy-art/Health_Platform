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
import 'package:clinic_system_pro/features/clinical_orders/presentation/blood_product_orders_screen.dart';

Map<String, dynamic> emergencyOrder() => <String, dynamic>{
  'id': 17,
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 9,
  'ordered_by_staff_id': 8,
  'product_type': 'packed_red_blood_cells',
  'units_requested': 1,
  'units_issued': 1,
  'indication': 'Emergency',
  'priority': 'stat',
  'status': 'completed',
  'version': 5,
  'patient_blood_type_at_order': 'O+',
  'compatibility_status': 'emergency_release',
  'compatibility_verified_by_staff_id': 12,
  'compatibility_verified_at': '2026-11-08T08:00:00+00:00',
  'patient_blood_type_verified': 'O+',
  'product_blood_type': 'O-',
  'emergency_release_reason': 'Urgent authorized release',
  'crossmatch_reference': null,
  'ordered_at': '2026-11-08T07:00:00+00:00',
  'completed_at': '2026-11-08T08:15:00+00:00',
  'fulfilled_by_staff_id': 12,
  'cancelled_at': null,
  'emergency_release_requested': true,
  'emergency_request_reason': 'Urgent hemorrhage',
  'emergency_requested_by_staff_id': 8,
  'emergency_requested_at': '2026-11-08T07:30:00+00:00',
  'emergency_review_outcome': null,
  'emergency_reviewed_by_staff_id': null,
  'emergency_reviewed_at': null,
  'emergency_review_note': null,
  'issued_units': <Map<String, dynamic>>[
    {
      'id': 41,
      'clinic_id': 3,
      'patient_id': 7,
      'admission_id': 9,
      'order_id': 17,
      'unit_identifier': 'EM-001',
      'product_blood_type': 'O-',
      'issued_by_staff_id': 12,
      'issued_at': '2026-11-08T08:15:00+00:00',
    },
  ],
  'lab_followups': <Map<String, dynamic>>[
    {
      'id': 51,
      'clinic_id': 3,
      'patient_id': 7,
      'admission_id': 9,
      'order_id': 17,
      'issued_unit_id': 41,
      'status': 'pending',
      'lab_order_item_id': null,
      'recorded_by_staff_id': null,
      'recorded_at': null,
      'evidence_note': null,
      'escalation_note': null,
    },
  ],
};

void main() {
  testWidgets('emergency detail displays pending unit without release writes', (
    tester,
  ) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(1400, 3200);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPhysicalSize);
    var mutationCount = 0;
    final client = MockClient((request) async {
      if (request.method != 'GET') {
        mutationCount++;
        return http.Response('{}', 500);
      }
      if (request.url.path.endsWith('/blood-products/17')) {
        return http.Response(jsonEncode(emergencyOrder()), 200);
      }
      return http.Response('{}', 404);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    final source = ClinicalOrdersRemoteDataSource(
      apiClient: api,
      loadSession: () async => AuthSessionState.ready(
        AuthTokenResponse(
          accessToken: 'token',
          refreshToken: 'refresh',
          userId: 41,
          role: 'lab_technician',
          clinicContextId: 3,
        ),
      ),
      sessionNavigationGuard: guard,
    );
    await tester.pumpWidget(
      MaterialApp(home: BloodProductOrdersScreen(unifiedSource: source)),
    );
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('p23d-d-order-id')), '17');
    await tester.tap(find.byKey(const ValueKey('p23d-d-lookup')));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('p23de-s03c-emergency-panel-17')),
      findsOneWidget,
    );
    expect(find.textContaining('EM-001'), findsOneWidget);
    expect(find.textContaining('Pending laboratory follow-up'), findsOneWidget);
    expect(
      find.textContaining('Not a compatibility clearance'),
      findsOneWidget,
    );
    expect(mutationCount, 0);
  });
}
