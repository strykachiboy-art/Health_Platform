import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_detail_screen.dart';

Map<String, dynamic> _patientJson() => {
  'id': 7,
  'clinic_id': 3,
  'patient_number': 'PT-0007',
  'first_name': 'Ada',
  'last_name': 'Lovelace',
  'date_of_birth': '1815-12-10',
  'gender': 'FEMALE',
  'blood_type': 'O_POSITIVE',
  'phone': '+2348000000000',
  'email': 'ada@example.test',
  'address': 'Private test address',
  'allergies': 'Peanuts',
  'chronic_conditions': 'Asthma',
  'emirates_id': null,
  'umrn': 'UMRN-7',
  'is_active': true,
  'created_at': '2026-01-01T10:00:00+00:00',
  'updated_at': '2026-01-02T10:00:00+00:00',
};

PatientRemoteDataSource _dataSource(MockClient client) =>
    PatientRemoteDataSource(
      apiClient: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      ),
    );

void main() {
  testWidgets('detail screen renders authorized patient details', (
    tester,
  ) async {
    final client = MockClient((request) async {
      expect(request.url.path, '/api/v1/patients/7');
      expect(request.headers['authorization'], 'Bearer test-token');
      return http.Response(
        jsonEncode({'success': true, 'data': _patientJson()}),
        200,
      );
    });
    addTearDown(client.close);

    await tester.pumpWidget(
      MaterialApp(
        home: PatientDetailScreen(
          dataSource: _dataSource(client),
          bearerToken: 'test-token',
          patientId: 7,
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('patient-detail-name')), findsOneWidget);
    expect(find.text('Ada Lovelace'), findsOneWidget);
    expect(find.text('+2348000000000'), findsOneWidget);

    await tester.scrollUntilVisible(find.text('Peanuts'), 250);
    expect(find.text('Peanuts'), findsOneWidget);
    expect(find.text('Asthma'), findsOneWidget);

    await tester.scrollUntilVisible(find.text('UMRN-7'), 250);
    expect(find.text('UMRN-7'), findsOneWidget);
  });

  testWidgets('detail screen exposes unauthorized state without patient data', (
    tester,
  ) async {
    final client = MockClient((request) async {
      return http.Response(jsonEncode({'success': false}), 401);
    });
    addTearDown(client.close);

    await tester.pumpWidget(
      MaterialApp(
        home: PatientDetailScreen(
          dataSource: _dataSource(client),
          bearerToken: 'expired-token',
          patientId: 7,
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('HTTP 401'), findsOneWidget);
    expect(find.text('Return to sign in'), findsOneWidget);
    expect(find.byKey(const ValueKey('patient-detail-name')), findsNothing);
  });
}
