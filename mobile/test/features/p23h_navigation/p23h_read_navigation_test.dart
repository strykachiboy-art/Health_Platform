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
import 'package:clinic_system_pro/features/lab/presentation/lab_patient_orders_screen.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_prescription_detail_screen.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_prescription_history_card.dart';
import 'package:clinic_system_pro/features/pharmacy/presentation/pharmacy_stock_screen.dart';

const _token = 'p23h-s06a-test-token';

AuthSessionState _session(String role) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: _token,
    refreshToken: 'p23h-s06a-test-refresh',
    userId: 42,
    clinicContextId: 3,
    role: role,
  ),
);

ApiClient _api(MockClient client) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: client,
);

void main() {
  test('home pharmacy stock entry requires admin/pharmacist and clinic', () {
    bool hasStock(String role, int? clinicId) =>
        homeDestinationsFor(role: role, clinicContextId: clinicId).any(
          (entry) =>
              entry.id == 'pharmacy_inventory' &&
              entry.path == '/pharmacy/stock',
        );

    expect(hasStock('ADMIN', 3), isTrue);
    expect(hasStock('pharmacist', 3), isTrue);
    expect(hasStock('PHARMACIST', null), isFalse);
    expect(hasStock('doctor', 3), isFalse);
    expect(hasStock('lab_technician', 3), isFalse);
    expect(hasStock('patient', 3), isFalse);
  });

  testWidgets('new direct routes fail closed on sign-out and invalid IDs', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return http.Response('{"success":false}', 403);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
    final api = _api(client);
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('P23H sign-in required')),
      sessionNavigationGuard: guard,
      patientPrescriptionDataSource: PatientPrescriptionRemoteDataSource(
        apiClient: api,
        loadSession: () async => _session('pharmacist'),
        sessionNavigationGuard: guard,
      ),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    await tester.pumpAndSettle();

    // Pharmacy is NOT part of the guard's default /patients prefix.
    // P23H's explicit router redirect must protect this direct link.
    router.go('/pharmacy/stock');
    await tester.pumpAndSettle();
    expect(find.text('P23H sign-in required'), findsOneWidget);
    expect(requests, 0);

    guard.markSessionAvailable();
    router.go('/patients/7/prescriptions/0');
    await tester.pumpAndSettle();
    expect(
      find.text('Prescription detail navigation unavailable.'),
      findsOneWidget,
    );
    expect(requests, 0);

    router.go('/patients/0/lab-orders');
    await tester.pumpAndSettle();
    expect(find.text('Laboratory navigation unavailable.'), findsOneWidget);
    expect(requests, 0);

    guard.invalidate();
    router.go('/patients/7/lab-orders');
    await tester.pumpAndSettle();
    expect(find.text('P23H sign-in required'), findsOneWidget);
    expect(requests, 0);
  });

  testWidgets('read routes call backend GET only; no clinical POST', (
    tester,
  ) async {
    final requested = <String>[];
    var role = 'doctor';
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.headers['authorization'], 'Bearer $_token');
      requested.add(request.url.path);
      return http.Response('{"success":false}', 403);
    });
    addTearDown(client.close);
    final api = _api(client);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final reader = PatientPrescriptionRemoteDataSource(
      apiClient: api,
      loadSession: () async => _session(role),
      sessionNavigationGuard: guard,
    );
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('P23H sign-in required')),
      sessionNavigationGuard: guard,
      patientPrescriptionDataSource: reader,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    await tester.pumpAndSettle();

    router.go('/patients/7/prescriptions/11');
    await tester.pumpAndSettle();
    expect(find.byType(PatientPrescriptionDetailScreen), findsOneWidget);
    expect(requested, ['/api/v1/prescriptions/11']);

    router.go('/patients/7/lab-orders');
    await tester.pumpAndSettle();
    expect(find.byType(LabPatientOrdersScreen), findsOneWidget);
    expect(requested.last, '/api/v1/lab/orders');

    role = 'pharmacist';
    router.go('/pharmacy/stock');
    await tester.pumpAndSettle();
    expect(find.byType(PharmacyStockScreen), findsOneWidget);
    expect(requested.last, '/api/v1/pharmacy/drugs');
    expect(requested, hasLength(3));
  });

  testWidgets('history only emits selected server-owned prescription ID', (
    tester,
  ) async {
    var chosen = 0;
    var postCount = 0;
    final client = MockClient((request) async {
      if (request.method != 'GET') postCount++;
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {
            'items': [
              {
                'id': 11,
                'clinic_id': 3,
                'patient_id': 7,
                'consultation_id': null,
                'prescribed_by_id': 8,
                'status': 'active',
                'notes': null,
                'issued_at': '2026-11-06T09:00:00',
                'expires_at': null,
                'items': [
                  {
                    'id': 111,
                    'prescription_id': 11,
                    'drug_id': 15,
                    'dosage': '500 mg',
                    'frequency': 'twice daily',
                    'duration': '5 days',
                    'quantity': 10,
                    'instructions': 'Use as prescribed',
                  },
                ],
              },
            ],
            'total': 1,
            'page': 1,
            'per_page': 20,
          },
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = PatientPrescriptionRemoteDataSource(
      apiClient: _api(client),
      loadSession: () async => _session('doctor'),
      sessionNavigationGuard: guard,
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: PatientPrescriptionHistoryCard(
              patientId: 7,
              dataSource: source,
              onPrescriptionDetailRequested: (id) => chosen = id,
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    final link = find.byKey(const ValueKey('p23h-prescription-detail-link-11'));
    expect(link, findsOneWidget);
    await tester.ensureVisible(link);
    await tester.tap(link);
    expect(chosen, 11);
    expect(postCount, 0);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(link, findsNothing);
  });
}
