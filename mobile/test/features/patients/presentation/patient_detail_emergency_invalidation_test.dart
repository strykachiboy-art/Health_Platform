import 'dart:async';
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

Map<String, dynamic> _patient() => {
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

EmergencyAccessGrant _grant({int patientId = 7, String status = 'active'}) =>
    EmergencyAccessGrant.fromJson({
      'id': 17,
      'clinic_id': 3,
      'patient_id': patientId,
      'requester_user_id': 42,
      'requester_role': 'DOCTOR',
      'reason': 'Emergency treatment',
      'purpose': 'Immediate clinical review',
      'scope': ['patient:read'],
      'status': status,
      'requested_at': '2026-11-03T12:00:00+00:00',
      'granted_at': '2026-11-03T12:05:00+00:00',
      'expires_at': '2026-11-03T13:00:00+00:00',
      'revoked_at': status == 'revoked' ? '2026-11-03T12:20:00+00:00' : null,
      'reviewed_at': '2026-11-03T12:05:00+00:00',
      'reviewed_by_user_id': 99,
      'review_notes': 'Approved',
    });

PatientRemoteDataSource _source(MockClient client) => PatientRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadAccessToken: () async => 'test-token',
);

EmergencyAccessContext _context() =>
    EmergencyAccessContext(nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30));

Widget _host(MockClient client, EmergencyAccessContext context) => MaterialApp(
  home: PatientDetailScreen(
    dataSource: _source(client),
    patientId: 7,
    emergencyAccessContext: context,
  ),
);

void main() {
  testWidgets('clearing active grant immediately removes patient PHI', (
    tester,
  ) async {
    final client = MockClient((request) async {
      expect(request.headers['x-emergency-access-id'], '17');
      return http.Response(
        jsonEncode({'success': true, 'data': _patient()}),
        200,
      );
    });
    addTearDown(client.close);

    final context = _context()..activate(_grant());
    addTearDown(context.dispose);

    await tester.pumpWidget(_host(client, context));
    await tester.pumpAndSettle();

    expect(find.text('Ada Lovelace'), findsOneWidget);

    context.clear();
    await tester.pump();

    expect(find.text('Ada Lovelace'), findsNothing);
    expect(find.byKey(const ValueKey('patient-detail-name')), findsNothing);
    expect(find.textContaining('Emergency access changed'), findsOneWidget);
  });

  testWidgets('backend-reconciled revocation clears the patient view', (
    tester,
  ) async {
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode({'success': true, 'data': _patient()}), 200),
    );
    addTearDown(client.close);

    final context = _context()..activate(_grant());
    addTearDown(context.dispose);

    await tester.pumpWidget(_host(client, context));
    await tester.pumpAndSettle();

    expect(find.text('Ada Lovelace'), findsOneWidget);

    context.reconcile(_grant(status: 'revoked'));
    await tester.pump();

    expect(context.activeEmergencyAccessId, isNull);
    expect(find.text('Ada Lovelace'), findsNothing);
  });

  testWidgets('pending emergency patient GET cannot restore cleared PHI', (
    tester,
  ) async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();

    final client = MockClient((request) {
      expect(request.headers['x-emergency-access-id'], '17');
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);

    final context = _context()..activate(_grant());
    addTearDown(context.dispose);

    await tester.pumpWidget(_host(client, context));
    await tester.pump();
    await started.future;

    context.clear();
    await tester.pump();

    pending.complete(
      http.Response(jsonEncode({'success': true, 'data': _patient()}), 200),
    );
    await tester.pumpAndSettle();

    expect(find.text('Ada Lovelace'), findsNothing);
    expect(find.byKey(const ValueKey('patient-detail-name')), findsNothing);
    expect(find.textContaining('Emergency access changed'), findsOneWidget);
  });

  testWidgets('unrelated emergency grant does not erase normal view', (
    tester,
  ) async {
    final client = MockClient((request) async {
      expect(request.headers['x-emergency-access-id'], isNull);
      return http.Response(
        jsonEncode({'success': true, 'data': _patient()}),
        200,
      );
    });
    addTearDown(client.close);

    final context = _context();
    addTearDown(context.dispose);

    await tester.pumpWidget(_host(client, context));
    await tester.pumpAndSettle();

    expect(find.text('Ada Lovelace'), findsOneWidget);

    context.activate(_grant(patientId: 8));
    await tester.pump();

    expect(find.text('Ada Lovelace'), findsOneWidget);

    context.clear();
    await tester.pump();

    expect(find.text('Ada Lovelace'), findsOneWidget);
  });
}
