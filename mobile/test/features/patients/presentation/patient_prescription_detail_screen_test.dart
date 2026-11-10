import 'dart:async';
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
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_prescription_detail_screen.dart';

AuthSessionState _ready() => const AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'test-detail-token',
    refreshToken: 'test-detail-refresh',
    userId: 5,
    clinicContextId: 3,
    role: 'doctor',
  ),
);

Map<String, dynamic> _response({int patientId = 7}) => {
  'success': true,
  'data': {
    'id': 11,
    'clinic_id': 3,
    'patient_id': patientId,
    'consultation_id': null,
    'prescribed_by_id': 8,
    'status': 'active',
    'notes': null,
    'issued_at': '2026-11-10T12:00:00+00:00',
    'expires_at': null,
    'items': [
      {
        'id': 111,
        'prescription_id': 11,
        'drug_id': 55,
        'dosage': '500 mg',
        'frequency': 'twice daily',
        'duration': '7 days',
        'quantity': 14,
        'instructions': null,
        'dose_value': 500.0,
        'dose_unit': 'mg',
        'route_code': 'PO',
        'route_display': 'Oral',
        'frequency_code': 'BID',
        'frequency_interval': 12,
        'frequency_interval_unit': 'hour',
        'scheduled_times': ['08:00', '20:00'],
        'start_at': null,
        'end_at': null,
        'is_prn': false,
        'prn_reason': null,
        'max_single_dose': 500.0,
        'max_daily_dose': 1000.0,
        'administration_instructions': 'With food',
        'order_priority': 'routine',
        'order_type': 'outpatient',
        'ordered_at': null,
      },
    ],
  },
};

PatientPrescriptionRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard,
) => PatientPrescriptionRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => _ready(),
  sessionNavigationGuard: guard,
);

Widget _host(PatientPrescriptionRemoteDataSource source, {int patientId = 7}) =>
    MaterialApp(
      home: PatientPrescriptionDetailScreen(
        dataSource: source,
        patientId: patientId,
        prescriptionId: 11,
      ),
    );

void main() {
  testWidgets(
    'server detail renders typed fields but has no clinical write actions',
    (tester) async {
      final paths = <String>[];
      final client = MockClient((request) async {
        paths.add(request.url.path);
        expect(request.method, 'GET');
        return http.Response(jsonEncode(_response()), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(_host(_source(client, guard)));
      await tester.pumpAndSettle();
      expect(paths, ['/api/v1/prescriptions/11']);
      expect(
        find.byKey(const ValueKey('p23h-s01-detail-record')),
        findsOneWidget,
      );
      expect(find.text('Server status: active'), findsOneWidget);
      expect(find.text('Priority: routine'), findsOneWidget);
      expect(find.text('Route: Oral'), findsOneWidget);
      expect(find.text('Schedule: 08:00, 20:00'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('p23h-s01-readonly-note')),
        findsOneWidget,
      );
      expect(find.textContaining('Dispense'), findsNothing);
    },
  );

  testWidgets('HTTP 403 shows no prescription content', (tester) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{"error":"Forbidden"}', 403);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23h-s01-detail-error')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23h-s01-detail-record')), findsNothing);
    expect(calls, 1);
  });

  testWidgets('logout clears server detail immediately', (tester) async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_response()), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('p23h-s01-detail-record')),
      findsOneWidget,
    );
    guard.invalidate();
    await tester.pump();
    expect(find.byKey(const ValueKey('p23h-s01-detail-record')), findsNothing);
    expect(find.byKey(const ValueKey('p23h-s01-detail-error')), findsOneWidget);
  });

  testWidgets('wrong patient detail is discarded', (tester) async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_response(patientId: 8)), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23h-s01-detail-error')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23h-s01-detail-record')), findsNothing);
  });

  testWidgets('late response after logout never resurrects PHI', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    final client = MockClient((_) => pending.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pump();
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_response()), 200));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23h-s01-detail-record')), findsNothing);
    expect(find.byKey(const ValueKey('p23h-s01-detail-error')), findsOneWidget);
  });
}
