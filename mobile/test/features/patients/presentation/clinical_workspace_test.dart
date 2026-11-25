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
import 'package:clinic_system_pro/features/patients/data/models/patient_vitals.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_detail_screen.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_vitals_trend.dart';

Map<String, dynamic> _patient(int id) => {
  'id': id,
  'clinic_id': 3,
  'patient_number': 'TEST-$id',
  'first_name': 'Synthetic',
  'last_name': 'Patient $id',
  'date_of_birth': '2000-01-01',
  'gender': null,
  'blood_type': 'O_POSITIVE',
  'phone': null,
  'email': null,
  'address': null,
  'allergies': null,
  'chronic_conditions': null,
  'emirates_id': null,
  'umrn': null,
  'is_active': true,
  'created_at': '2026-01-01T00:00:00Z',
  'updated_at': '2026-01-01T00:00:00Z',
};

http.Response _response(int id) =>
    http.Response(jsonEncode({'success': true, 'data': _patient(id)}), 200);

PatientRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard,
) => PatientRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadAccessToken: () async => 'test-access',
  loadSession: () async => AuthSessionState.ready(
    AuthTokenResponse(
      userId: 42,
      role: 'doctor',
      clinicContextId: 3,
      accessToken: 'test-access',
      refreshToken: 'test-refresh',
    ),
  ),
  sessionNavigationGuard: guard,
);

PatientVitals _vital(
  int id,
  int minute, {
  String? temperature,
  int? systolic,
}) => PatientVitals(
  id: id,
  patientId: 7,
  consultationId: null,
  recordedById: null,
  temperature: temperature,
  bloodPressureSystolic: systolic,
  bloodPressureDiastolic: null,
  heartRate: null,
  respiratoryRate: null,
  oxygenSaturation: null,
  weight: null,
  height: null,
  recordedAt: DateTime.utc(2026, 1, 1, 0, minute),
);

void main() {
  test(
    'trend uses true time order, missing is not zero, exact precision survives',
    () {
      final rows = [
        _vital(3, 10, temperature: '37.10'),
        _vital(1, 0, temperature: '36.70'),
        _vital(2, 2),
      ];
      final points = buildVitalTrend(rows, VitalTrendMetric.temperature);
      expect(points.map((p) => p.raw), ['36.70', '37.10']);
      expect(
        points.last.recordedAt.difference(points.first.recordedAt),
        const Duration(minutes: 10),
      );
      expect(buildVitalTrend(rows, VitalTrendMetric.oxygen), isEmpty);
      expect(rows.first.id, 3);
    },
  );
  test('trend is bounded to newest 50 loaded observations', () {
    final rows = List.generate(70, (i) => _vital(i + 1, i, systolic: 100 + i));
    final points = buildVitalTrend(rows, VitalTrendMetric.systolic);
    expect(points.length, 50);
    expect(points.first.raw, '120');
    expect(points.last.raw, '169');
  });
  test('each blood pressure component remains independent', () {
    final rows = [_vital(1, 1, systolic: 125)];
    expect(buildVitalTrend(rows, VitalTrendMetric.systolic).single.raw, '125');
    expect(buildVitalTrend(rows, VitalTrendMetric.diastolic), isEmpty);
  });
  testWidgets('chart expands and preserves timestamped exact values', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: PatientVitalsTrend(
              observations: [
                _vital(1, 0, systolic: 125),
                _vital(2, 5, systolic: 130),
              ],
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Vital-sign trends'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('clinical-vitals-plot')), findsOneWidget);
    expect(find.text('2026-01-01T00:00:00.000Z: 125 mmHg'), findsOneWidget);
    expect(find.text('2026-01-01T00:05:00.000Z: 130 mmHg'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
  testWidgets(
    'section selection retains identity and existing authorized action',
    (tester) async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var calls = 0;
      final client = MockClient((request) async {
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/patients/7');
        return _response(7);
      });
      addTearDown(client.close);
      await tester.pumpWidget(
        MaterialApp(
          home: PatientDetailScreen(
            dataSource: _source(client, guard),
            patientId: 7,
            onLabOrdersRequested: () => calls++,
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(
        find.byKey(const ValueKey('clinical-workspace-sections')),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Orders and clinical actions'));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('patient-detail-name')), findsOneWidget);
      final orders = find.byKey(const ValueKey('p23h-patient-lab-orders-7'));
      expect(orders, findsOneWidget);
      await tester.ensureVisible(orders);
      await tester.tap(orders);
      expect(calls, 1);
      expect(find.text('Patient information'), findsNothing);
      final safety = find.byKey(
        const ValueKey('clinical-workspace-safety-overview'),
      );
      await tester.ensureVisible(safety);
      await tester.tap(safety);
      await tester.pumpAndSettle();
      expect(find.text('Patient information'), findsOneWidget);
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('patient-detail-name')), findsNothing);
      expect(
        find.byKey(const ValueKey('clinical-workspace-sections')),
        findsNothing,
      );
      expect(
        find.textContaining('Patient details were cleared'),
        findsOneWidget,
      );
    },
  );
  testWidgets('session invalidation rejects in-flight profile response', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final pending = Completer<http.Response>();
    final client = MockClient((_) => pending.future);
    addTearDown(client.close);
    await tester.pumpWidget(
      MaterialApp(
        home: PatientDetailScreen(
          dataSource: _source(client, guard),
          patientId: 7,
        ),
      ),
    );
    await tester.pump();
    guard.invalidate();
    pending.complete(_response(7));
    await tester.pumpAndSettle();
    expect(find.text('Synthetic Patient 7'), findsNothing);
    expect(find.byKey(const ValueKey('patient-detail-name')), findsNothing);
    expect(find.textContaining('Patient details were cleared'), findsOneWidget);
  });
}
