import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_context.dart';
import 'package:clinic_system_pro/features/emergency_access/data/models/emergency_access_grant.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_detail_screen.dart';

Map<String, dynamic> _patientJson() {
  return <String, dynamic>{
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
    'address': 'Test address',
    'allergies': 'Peanuts',
    'chronic_conditions': 'None',
    'emirates_id': null,
    'umrn': 'UMRN-7',
    'is_active': true,
    'created_at': '2026-01-01T10:00:00+00:00',
    'updated_at': '2026-01-02T10:00:00+00:00',
  };
}

EmergencyAccessGrant _activeGrant() {
  return EmergencyAccessGrant.fromJson(<String, dynamic>{
    'id': 17,
    'clinic_id': 3,
    'patient_id': 7,
    'requester_user_id': 42,
    'requester_role': 'DOCTOR',
    'reason': 'Emergency treatment',
    'purpose': 'Immediate clinical review',
    'scope': <String>['patient:read'],
    'status': 'active',
    'requested_at': '2026-11-03T12:00:00+00:00',
    'granted_at': '2026-11-03T12:05:00+00:00',
    'expires_at': '2026-11-03T13:00:00+00:00',
    'revoked_at': null,
    'reviewed_at': '2026-11-03T12:05:00+00:00',
    'reviewed_by_user_id': 99,
    'review_notes': 'Approved',
  });
}

void main() {
  testWidgets(
    'patient detail propagates active matching emergency access header',
    (tester) async {
      var requests = 0;

      final client = MockClient((request) async {
        requests++;

        expect(request.url.path, '/api/v1/patients/7');
        expect(request.headers['authorization'], 'Bearer test-token');
        expect(request.headers['x-emergency-access-id'], '17');

        return http.Response(
          jsonEncode(<String, dynamic>{
            'success': true,
            'data': _patientJson(),
          }),
          200,
        );
      });
      addTearDown(client.close);

      final source = PatientRemoteDataSource(
        apiClient: ApiClient(
          config: const BackendConfig(
            baseUrl: 'http://127.0.0.1:5000',
            allowInsecureHttp: true,
          ),
          client: client,
        ),
        loadAccessToken: () async => 'test-token',
      );

      final accessContext = EmergencyAccessContext(
        nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
      );
      addTearDown(accessContext.dispose);

      accessContext.activate(_activeGrant());

      await tester.pumpWidget(
        MaterialApp(
          home: PatientDetailScreen(
            dataSource: source,
            patientId: 7,
            emergencyAccessContext: accessContext,
          ),
        ),
      );

      await tester.pumpAndSettle();

      expect(requests, 1);
      expect(find.byKey(const ValueKey('patient-detail-name')), findsOneWidget);
      expect(find.text('Ada Lovelace'), findsOneWidget);
    },
  );
}
