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

Map<String, dynamic> _patient(int id, {String? firstName}) => {
  'id': id,
  'clinic_id': 3,
  'patient_number': 'PT-$id',
  'first_name': firstName ?? (id == 7 ? 'Ada' : 'Grace'),
  'last_name': id == 7 ? 'Lovelace' : 'Hopper',
  'date_of_birth': '1815-12-10',
  'gender': 'FEMALE',
  'blood_type': 'O_POSITIVE',
  'phone': '+2348000000000',
  'email': 'patient@example.test',
  'address': 'Test address',
  'allergies': 'Peanuts',
  'chronic_conditions': 'None',
  'emirates_id': null,
  'umrn': 'UMRN-$id',
  'is_active': true,
  'created_at': '2026-01-01T10:00:00+00:00',
  'updated_at': '2026-01-02T10:00:00+00:00',
};

EmergencyAccessGrant _grant({String expiresAt = '2026-11-03T13:00:00+00:00'}) =>
    EmergencyAccessGrant.fromJson({
      'id': 17,
      'clinic_id': 3,
      'patient_id': 7,
      'requester_user_id': 42,
      'requester_role': 'DOCTOR',
      'reason': 'Emergency treatment',
      'purpose': 'Immediate clinical review',
      'scope': ['patient:read'],
      'status': 'active',
      'requested_at': '2026-11-03T12:00:00+00:00',
      'granted_at': '2026-11-03T12:05:00+00:00',
      'expires_at': expiresAt,
      'revoked_at': null,
      'reviewed_at': '2026-11-03T12:05:00+00:00',
      'reviewed_by_user_id': 99,
      'review_notes': 'Approved',
    });

EmergencyAccessContext _context() =>
    EmergencyAccessContext(nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30));

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

Widget _host(
  PatientRemoteDataSource source,
  EmergencyAccessContext context, {
  int patientId = 7,
}) => MaterialApp(
  home: PatientDetailScreen(
    dataSource: source,
    patientId: patientId,
    emergencyAccessContext: context,
  ),
);

http.Response _success(int patientId, {String? firstName}) => http.Response(
  jsonEncode({
    'success': true,
    'data': _patient(patientId, firstName: firstName),
  }),
  200,
);

void main() {
  testWidgets('idle expiry removes displayed patient PHI', (tester) async {
    final context = _context()
      ..activate(_grant(expiresAt: '2026-11-03T12:30:01+00:00'));
    addTearDown(context.dispose);

    final client = MockClient((request) async {
      expect(request.headers['x-emergency-access-id'], '17');
      return _success(7);
    });
    addTearDown(client.close);

    await tester.pumpWidget(_host(_source(client), context));
    await tester.pumpAndSettle();

    expect(find.text('Ada Lovelace'), findsOneWidget);

    await tester.pump(const Duration(milliseconds: 1100));

    expect(context.activeEmergencyAccessId, isNull);
    expect(find.text('Ada Lovelace'), findsNothing);
    expect(find.textContaining('Emergency access changed'), findsOneWidget);
  });

  testWidgets('expiry discards in-flight emergency response', (tester) async {
    final context = _context()
      ..activate(_grant(expiresAt: '2026-11-03T12:30:01+00:00'));
    addTearDown(context.dispose);

    final started = Completer<void>();
    final pending = Completer<http.Response>();

    final client = MockClient((request) {
      expect(request.headers['x-emergency-access-id'], '17');
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);

    await tester.pumpWidget(_host(_source(client), context));
    await tester.pump();
    await started.future;

    await tester.pump(const Duration(milliseconds: 1100));

    pending.complete(_success(7));
    await tester.pumpAndSettle();

    expect(context.activeEmergencyAccessId, isNull);
    expect(find.text('Ada Lovelace'), findsNothing);
  });

  testWidgets('grant renewal replaces old expiry deadline', (tester) async {
    final context = _context()
      ..activate(_grant(expiresAt: '2026-11-03T12:30:01+00:00'));
    addTearDown(context.dispose);

    context.reconcile(_grant(expiresAt: '2026-11-03T12:30:03+00:00'));

    final client = MockClient((_) async => _success(7));
    addTearDown(client.close);

    await tester.pumpWidget(_host(_source(client), context));
    await tester.pumpAndSettle();

    await tester.pump(const Duration(milliseconds: 1200));

    expect(context.activeEmergencyAccessId, 17);
    expect(find.text('Ada Lovelace'), findsOneWidget);

    await tester.pump(const Duration(seconds: 2));

    expect(context.activeEmergencyAccessId, isNull);
    expect(find.text('Ada Lovelace'), findsNothing);
  });

  testWidgets('patient switch discards old pending response', (tester) async {
    final context = _context()..activate(_grant());
    addTearDown(context.dispose);

    final started = Completer<void>();
    final pending = Completer<http.Response>();
    final paths = <String>[];

    final client = MockClient((request) {
      paths.add(request.url.path);

      if (request.url.path == '/api/v1/patients/7') {
        expect(request.headers['x-emergency-access-id'], '17');
        started.complete();
        return pending.future;
      }

      expect(request.url.path, '/api/v1/patients/8');
      expect(request.headers['x-emergency-access-id'], isNull);
      return Future.value(_success(8));
    });
    addTearDown(client.close);

    final source = _source(client);

    await tester.pumpWidget(_host(source, context));
    await tester.pump();
    await started.future;

    await tester.pumpWidget(_host(source, context, patientId: 8));
    await tester.pumpAndSettle();

    expect(find.text('Grace Hopper'), findsOneWidget);

    pending.complete(_success(7));
    await tester.pumpAndSettle();

    expect(find.text('Grace Hopper'), findsOneWidget);
    expect(find.text('Ada Lovelace'), findsNothing);
    expect(paths, containsAll(['/api/v1/patients/7', '/api/v1/patients/8']));
    context.clear();
  });

  testWidgets('data source replacement rejects stale result', (tester) async {
    final context = _context();
    addTearDown(context.dispose);

    final started = Completer<void>();
    final pending = Completer<http.Response>();

    final oldClient = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(oldClient.close);

    final newClient = MockClient((_) async => _success(7, firstName: 'New'));
    addTearDown(newClient.close);

    await tester.pumpWidget(_host(_source(oldClient), context));
    await tester.pump();
    await started.future;

    await tester.pumpWidget(_host(_source(newClient), context));
    await tester.pumpAndSettle();

    expect(find.text('New Lovelace'), findsOneWidget);

    pending.complete(_success(7));
    await tester.pumpAndSettle();

    expect(find.text('New Lovelace'), findsOneWidget);
    expect(find.text('Ada Lovelace'), findsNothing);
  });

  testWidgets('new context cannot inherit an equal grant ID', (tester) async {
    final first = _context()..activate(_grant());
    final second = _context()..activate(_grant());

    addTearDown(first.dispose);
    addTearDown(second.dispose);

    var requests = 0;

    final client = MockClient((_) async {
      requests++;
      return _success(7);
    });
    addTearDown(client.close);

    final source = _source(client);

    await tester.pumpWidget(_host(source, first));
    await tester.pumpAndSettle();

    expect(find.text('Ada Lovelace'), findsOneWidget);

    await tester.pumpWidget(_host(source, second));
    await tester.pump();

    expect(find.text('Ada Lovelace'), findsNothing);
    expect(find.textContaining('Emergency access changed'), findsOneWidget);
    expect(requests, 1);
    first.clear();
    second.clear();
  });
}
