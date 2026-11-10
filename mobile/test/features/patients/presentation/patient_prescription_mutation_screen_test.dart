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
import 'package:clinic_system_pro/features/patients/data/patient_prescription_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_prescription_mutation_screen.dart';

AuthSessionState _ready({String role = 'doctor'}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'ui-token',
    refreshToken: 'ui-refresh',
    userId: 42,
    role: role,
    clinicContextId: 3,
  ),
);

Map<String, dynamic> _record({String status = 'active', int patient = 7}) => {
  'id': 11,
  'clinic_id': 3,
  'patient_id': patient,
  'consultation_id': null,
  'prescribed_by_id': 8,
  'status': status,
  'notes': null,
  'issued_at': '2026-11-10T12:00:00+00:00',
  'expires_at': null,
  'items': [
    <String, dynamic>{
      'id': 111,
      'prescription_id': 11,
      'drug_id': 55,
      'dosage': '500 mg',
      'frequency': 'BID',
      'duration': '7 days',
      'quantity': 14,
      'instructions': null,
      'dose_value': null,
      'dose_unit': null,
      'route_code': 'PO',
      'route_display': 'Oral',
      'frequency_code': null,
      'frequency_interval': null,
      'frequency_interval_unit': null,
      'scheduled_times': <String>[],
      'start_at': null,
      'end_at': null,
      'is_prn': false,
      'prn_reason': null,
      'max_single_dose': null,
      'max_daily_dose': null,
      'administration_instructions': null,
      'order_priority': 'routine',
      'order_type': 'outpatient',
      'ordered_at': null,
    },
  ],
};

http.Response _ok(Map<String, dynamic> row, {bool created = false}) =>
    http.Response(
      jsonEncode(<String, dynamic>{
        'success': true,
        'data': row,
        if (created) 'interaction_warnings': <Map<String, dynamic>>[],
      }),
      200,
    );

PatientPrescriptionMutationRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
}) => PatientPrescriptionMutationRemoteDataSource(
  reader: PatientPrescriptionRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: () async => _ready(role: role),
    sessionNavigationGuard: guard,
  ),
);

Widget _host(
  PatientPrescriptionMutationRemoteDataSource source, {
  PrescriptionMutationAction action = PrescriptionMutationAction.create,
}) => MaterialApp(
  home: PatientPrescriptionMutationScreen(
    source: source,
    patientId: 7,
    action: action,
    prescriptionId: action == PrescriptionMutationAction.create ? null : 11,
  ),
);

void main() {
  testWidgets('empty creation form cannot send clinical POST', (tester) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return _ok(_record());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('p23h-s02-confirm-action')),
      250,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.byKey(const ValueKey('p23h-s02-confirm-action')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23h-s02-message')), findsOneWidget);
    expect(calls, 0);
  });

  testWidgets('cancel dialog leaves prescription unchanged', (tester) async {
    final methods = <String>[];
    final client = MockClient((request) async {
      methods.add(request.method);
      return _ok(_record());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _host(_source(client, guard), action: PrescriptionMutationAction.cancel),
    );
    await tester.pumpAndSettle();
    final reason = find.byKey(const ValueKey('p23h-s02-cancel-reason'));
    await tester.ensureVisible(reason);
    await tester.enterText(reason, 'Clinical decision revised');
    final button = find.byKey(const ValueKey('p23h-s02-confirm-action'));
    await tester.ensureVisible(button);
    await tester.tap(button);
    await tester.pumpAndSettle();
    expect(
      find.text('Confirm server-owned prescription action'),
      findsOneWidget,
    );
    await tester.tap(find.text('Back'));
    await tester.pumpAndSettle();
    expect(methods, ['GET']);
    expect(find.byKey(const ValueKey('p23h-s02-locked')), findsNothing);
  });

  testWidgets(
    'confirmed completion displays backend readback and locks screen',
    (tester) async {
      final methods = <String>[];
      final client = MockClient((request) async {
        methods.add(request.method);
        if (request.method == 'POST') return _ok(_record(status: 'completed'));
        return _ok(
          _record(status: methods.contains('POST') ? 'completed' : 'active'),
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(
        _host(
          _source(client, guard),
          action: PrescriptionMutationAction.complete,
        ),
      );
      await tester.pumpAndSettle();
      final button = find.byKey(const ValueKey('p23h-s02-confirm-action'));
      await tester.ensureVisible(button);
      await tester.tap(button);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Confirm once'));
      await tester.pumpAndSettle();
      expect(methods, ['GET', 'GET', 'POST', 'GET']);
      expect(find.byKey(const ValueKey('p23h-s02-confirmed')), findsOneWidget);
      expect(find.byKey(const ValueKey('p23h-s02-locked')), findsOneWidget);
      expect(tester.widget<FilledButton>(button).onPressed, isNull);
    },
  );

  testWidgets('nurse completion cannot POST or reveal confirmation', (
    tester,
  ) async {
    final methods = <String>[];
    final client = MockClient((request) async {
      methods.add(request.method);
      return _ok(_record());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _host(
        _source(client, guard, role: 'nurse'),
        action: PrescriptionMutationAction.complete,
      ),
    );
    await tester.pumpAndSettle();
    expect(methods, isEmpty);
    expect(find.byKey(const ValueKey('p23h-s02-message')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23h-s02-confirmed')), findsNothing);
  });

  testWidgets('logout clears prescription data and disables clinical actions', (
    tester,
  ) async {
    final client = MockClient((_) async => _ok(_record()));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _host(_source(client, guard), action: PrescriptionMutationAction.cancel),
    );
    await tester.pumpAndSettle();
    expect(find.text('Server prescription #11'), findsOneWidget);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Server prescription #11'), findsNothing);
    expect(find.byKey(const ValueKey('p23h-s02-confirmed')), findsNothing);
    expect(
      find.text('Clinical session unavailable. Reopen workflow.'),
      findsOneWidget,
    );
  });
}
