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

const _config = BackendConfig(
  baseUrl: 'http://127.0.0.1:5000',
  allowInsecureHttp: true,
);

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

EmergencyAccessGrant _grant({int patientId = 7}) {
  return EmergencyAccessGrant.fromJson(<String, dynamic>{
    'id': 17,
    'clinic_id': 3,
    'patient_id': patientId,
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

PatientRemoteDataSource _source(http.Client client) {
  return PatientRemoteDataSource(
    apiClient: ApiClient(config: _config, client: client),
    loadAccessToken: () async => 'test-token',
  );
}

void main() {
  testWidgets('patient mismatch omits emergency access header', (tester) async {
    final client = MockClient((request) async {
      expect(request.url.path, '/api/v1/patients/7');

      expect(request.headers['x-emergency-access-id'], isNull);

      return http.Response(
        jsonEncode(<String, dynamic>{'success': true, 'data': _patientJson()}),
        200,
      );
    });
    addTearDown(client.close);

    final context = EmergencyAccessContext(
      nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
    );
    addTearDown(context.dispose);

    context.activate(_grant(patientId: 8));

    await tester.pumpWidget(
      MaterialApp(
        home: PatientDetailScreen(
          dataSource: _source(client),
          patientId: 7,
          emergencyAccessContext: context,
        ),
      ),
    );

    await tester.pumpAndSettle();

    expect(find.text('Ada Lovelace'), findsOneWidget);
  });

  testWidgets('expired context omits emergency access header', (tester) async {
    var now = DateTime.utc(2026, 11, 3, 12, 30);

    final client = MockClient((request) async {
      expect(request.headers['x-emergency-access-id'], isNull);

      return http.Response(
        jsonEncode(<String, dynamic>{'success': true, 'data': _patientJson()}),
        200,
      );
    });
    addTearDown(client.close);

    final context = EmergencyAccessContext(nowUtc: () => now);
    addTearDown(context.dispose);

    context.activate(_grant());

    now = DateTime.utc(2026, 11, 3, 13);

    await tester.pumpWidget(
      MaterialApp(
        home: PatientDetailScreen(
          dataSource: _source(client),
          patientId: 7,
          emergencyAccessContext: context,
        ),
      ),
    );

    await tester.pumpAndSettle();

    expect(context.activeEmergencyAccessId, isNull);
    expect(find.text('Ada Lovelace'), findsOneWidget);
  });

  testWidgets('definitive backend 403 clears matching emergency context', (
    tester,
  ) async {
    final client = MockClient((request) async {
      expect(request.headers['x-emergency-access-id'], '17');

      return http.Response(
        jsonEncode(<String, dynamic>{
          'success': false,
          'error': 'Emergency access is not authorized for this resource',
        }),
        403,
      );
    });
    addTearDown(client.close);

    final context = EmergencyAccessContext(
      nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
    );
    addTearDown(context.dispose);

    context.activate(_grant());

    await tester.pumpWidget(
      MaterialApp(
        home: PatientDetailScreen(
          dataSource: _source(client),
          patientId: 7,
          emergencyAccessContext: context,
        ),
      ),
    );

    await tester.pumpAndSettle();

    expect(context.activeEmergencyAccessId, isNull);

    expect(find.byKey(const ValueKey('patient-detail-name')), findsNothing);
  });

  testWidgets(
    'transient connection failure does not invent emergency revocation',
    (tester) async {
      final client = MockClient((request) async {
        expect(request.headers['x-emergency-access-id'], '17');

        throw http.ClientException('network unavailable', request.url);
      });
      addTearDown(client.close);

      final context = EmergencyAccessContext(
        nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
      );
      addTearDown(context.dispose);

      context.activate(_grant());

      await tester.pumpWidget(
        MaterialApp(
          home: PatientDetailScreen(
            dataSource: _source(client),
            patientId: 7,
            emergencyAccessContext: context,
          ),
        ),
      );

      await tester.pumpAndSettle();

      expect(context.activeEmergencyAccessId, 17);

      expect(find.byKey(const ValueKey('patient-detail-name')), findsNothing);
    },
  );
}
