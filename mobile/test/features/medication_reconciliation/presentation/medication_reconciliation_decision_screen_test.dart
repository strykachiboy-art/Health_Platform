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
import 'package:clinic_system_pro/features/medication_reconciliation/data/medication_reconciliation_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/medication_reconciliation_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/models/medication_reconciliation.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/presentation/medication_reconciliation_decision_screen.dart';

const previous = '2026-11-10T09:00:00Z';
const updated = '2026-11-10T09:01:00Z';

AuthSessionState ready({String role = 'doctor', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'p23g-s03d-token',
        refreshToken: 'p23g-s03d-refresh',
        userId: 51,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> medication({String decision = 'verify'}) => {
  'source_prescription_item_id': 21,
  'medication_name': 'Metformin',
  'decision': decision,
  'replacement_medication_name': decision == 'replace' ? 'Replacement' : null,
  'dose_text': '500 mg',
  'route_text': 'oral',
  'frequency_text': 'twice daily',
  'instructions': null,
  'rationale': 'History requires verification',
};

Map<String, dynamic> record({
  int patient = 5,
  String event = 'admission',
  int? transfer,
  String status = 'draft',
  String stamp = previous,
  bool noMeds = false,
  List<Map<String, dynamic>>? medications,
  String? notes = 'Initial review',
}) => {
  'id': 31,
  'clinic_id': 3,
  'patient_id': patient,
  'admission_id': 7,
  'ward_transfer_id': transfer,
  'event_type': event,
  'event_identity': event == 'transfer' ? 'transfer:$transfer' : '$event:7',
  'created_by_staff_id': 9,
  'no_medications_reported': noMeds,
  'notes': notes,
  'items': medications ?? (noMeds ? <Map<String, dynamic>>[] : [medication()]),
  'record_status': status,
  'record_version': 1,
  'finalized_by_staff_id': status == 'draft' ? null : 9,
  'finalized_at': status == 'draft' ? null : previous,
  'latest_amendment_id': null,
  'created_at': previous,
  'updated_at': stamp,
};

ApiClient apiClient(MockClient client) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: client,
);

Widget app(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
  MedicationReconciliationEvent event = MedicationReconciliationEvent.admission,
  int? transfer,
  int? recordId,
}) {
  final api = apiClient(client);
  Future<AuthSessionState> loadSession() async => ready(role: role);
  return MaterialApp(
    home: MedicationReconciliationDecisionScreen(
      readSource: MedicationReconciliationReadRemoteDataSource(
        apiClient: api,
        loadSession: loadSession,
        sessionNavigationGuard: guard,
      ),
      mutationSource: MedicationReconciliationMutationRemoteDataSource(
        apiClient: api,
        loadSession: loadSession,
        sessionNavigationGuard: guard,
      ),
      admissionId: 7,
      patientId: 5,
      event: event,
      wardTransferId: transfer,
      reconciliationId: recordId,
    ),
  );
}

void largeScreen(WidgetTester tester) {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(1100, 3200);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

void main() {
  testWidgets(
    'new event requires explicit declaration and sends one POST plus readback',
    (tester) async {
      largeScreen(tester);
      final methods = <String>[];
      final client = MockClient((request) async {
        methods.add(request.method);
        if (request.method == 'POST') {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          expect(body['event_type'], 'admission');
          expect(body['no_medications_reported'], true);
          expect(body['items'], isEmpty);
          return http.Response(
            jsonEncode({
              'reconciliation': record(
                noMeds: true,
                medications: [],
                notes: null,
              ),
            }),
            201,
          );
        }
        if (request.url.path.endsWith('/31')) {
          return http.Response(
            jsonEncode({
              'reconciliation': record(
                noMeds: true,
                medications: [],
                notes: null,
              ),
            }),
            200,
          );
        }
        expect(request.url.queryParameters['event_type'], 'admission');
        expect(request.url.queryParameters['per_page'], '100');
        return http.Response(
          jsonEncode({
            'items': <dynamic>[],
            'total': 0,
            'page': 1,
            'per_page': 100,
          }),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(app(client, guard));
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<FilledButton>(find.byKey(const ValueKey('p23g-s03d-save')))
            .onPressed,
        isNull,
      );
      expect(find.text('Finalize reconciliation'), findsNothing);
      await tester.tap(find.byKey(const ValueKey('p23g-s03d-no-medications')));
      await tester.pump();
      await tester.tap(find.byKey(const ValueKey('p23g-s03d-save')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p23g-s03d-saved')), findsOneWidget);
      expect(methods, <String>['GET', 'POST', 'GET']);
    },
  );

  testWidgets('existing draft uses timestamp CAS and confirms readback', (
    tester,
  ) async {
    largeScreen(tester);
    final calls = <String>[];
    final client = MockClient((request) async {
      calls.add(request.method);
      if (request.method == 'PATCH') {
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(
          DateTime.parse(body['expected_updated_at'] as String),
          DateTime.parse(previous),
        );
        expect(body['notes'], 'Reviewed again');
        expect(body['items'], hasLength(1));
        return http.Response(
          jsonEncode({
            'reconciliation': record(stamp: updated, notes: 'Reviewed again'),
          }),
          200,
        );
      }
      return http.Response(
        jsonEncode({
          'reconciliation': record(
            stamp: calls.length == 1 ? previous : updated,
            notes: calls.length == 1 ? 'Initial review' : 'Reviewed again',
          ),
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard, recordId: 31));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('p23g-s03d-notes')),
      'Reviewed again',
    );
    FocusManager.instance.primaryFocus?.unfocus();
    await tester.pumpAndSettle();
    final saveButton = find.byKey(const ValueKey('p23g-s03d-save'));
    await tester.ensureVisible(saveButton);
    await tester.pumpAndSettle();
    expect(
      tester.widget<FilledButton>(saveButton).onPressed,
      isNotNull,
      reason: 'Notes edits must enable Save before any clinical write.',
    );
    await tester.tap(saveButton);
    await tester.pumpAndSettle();
    expect(calls, ['GET', 'PATCH', 'GET']);
    expect(find.byKey(const ValueKey('p23g-s03d-saved')), findsOneWidget);
  });

  testWidgets('409 prevents replay and requires explicit refresh', (
    tester,
  ) async {
    largeScreen(tester);
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method == 'PATCH') {
        writes++;
        return http.Response(jsonEncode({'error': 'draft changed'}), 409);
      }
      return http.Response(jsonEncode({'reconciliation': record()}), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard, recordId: 31));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('p23g-s03d-notes')),
      'Stale attempt',
    );
    FocusManager.instance.primaryFocus?.unfocus();
    await tester.pumpAndSettle();
    final saveButton = find.byKey(const ValueKey('p23g-s03d-save'));
    await tester.ensureVisible(saveButton);
    await tester.pumpAndSettle();
    expect(
      tester.widget<FilledButton>(saveButton).onPressed,
      isNotNull,
      reason: 'Notes edits must enable Save before any clinical write.',
    );
    await tester.tap(saveButton);
    await tester.pumpAndSettle();
    expect(writes, 1);
    expect(
      find.byKey(const ValueKey('p23g-s03d-recheck-required')),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('p23g-s03d-save')), findsNothing);
    await tester.tap(find.byKey(const ValueKey('p23g-s03d-recheck')));
    await tester.pumpAndSettle();
    expect(writes, 1);
  });

  testWidgets('nurse cannot enter mutation editor or send any requests', (
    tester,
  ) async {
    largeScreen(tester);
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard, role: 'nurse'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23g-s03d-error')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23g-s03d-save')), findsNothing);
    expect(calls, 0);
  });

  testWidgets('finalized reconciliation is immutable in the draft editor', (
    tester,
  ) async {
    largeScreen(tester);
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode({
          'reconciliation': record(
            status: 'final',
            medications: [medication(decision: 'continue')],
          ),
        }),
        200,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard, recordId: 31));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23g-s03d-non-draft')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23g-s03d-save')), findsNothing);
  });

  testWidgets('cross-patient response is not editable', (tester) async {
    largeScreen(tester);
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode({'reconciliation': record(patient: 99)}),
        200,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard, recordId: 31));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23g-s03d-error')), findsOneWidget);
    expect(find.text('Metformin'), findsNothing);
    expect(find.byKey(const ValueKey('p23g-s03d-save')), findsNothing);
  });

  testWidgets('session invalidation clears clinical inputs and late response', (
    tester,
  ) async {
    largeScreen(tester);
    final pending = Completer<http.Response>();
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        return pending.future;
      }
      return http.Response(
        jsonEncode({
          'items': <dynamic>[],
          'total': 0,
          'page': 1,
          'per_page': 100,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23g-s03d-no-medications')));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('p23g-s03d-save')));
    await tester.pump();
    guard.markSignedOut();
    await tester.pump();
    pending.complete(
      http.Response(
        jsonEncode({
          'reconciliation': record(noMeds: true, medications: [], notes: null),
        }),
        201,
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('p23g-s03d-session-locked')),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('p23g-s03d-saved')), findsNothing);
    expect(find.text('Metformin'), findsNothing);
  });

  testWidgets('transfer event draft requires and sends correct transfer ID', (
    tester,
  ) async {
    largeScreen(tester);
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body['event_type'], 'transfer');
        expect(body['ward_transfer_id'], 41);
        return http.Response(
          jsonEncode({
            'reconciliation': record(
              event: 'transfer',
              transfer: 41,
              noMeds: true,
              medications: [],
              notes: null,
            ),
          }),
          201,
        );
      }
      if (request.url.path.endsWith('/31')) {
        return http.Response(
          jsonEncode({
            'reconciliation': record(
              event: 'transfer',
              transfer: 41,
              noMeds: true,
              medications: [],
              notes: null,
            ),
          }),
          200,
        );
      }
      return http.Response(
        jsonEncode({
          'items': <dynamic>[],
          'total': 0,
          'page': 1,
          'per_page': 100,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      app(
        client,
        guard,
        event: MedicationReconciliationEvent.transfer,
        transfer: 41,
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23g-s03d-no-medications')));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('p23g-s03d-save')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23g-s03d-saved')), findsOneWidget);
  });

  testWidgets('invalid replacement decision never sends a mutation', (
    tester,
  ) async {
    largeScreen(tester);
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method != 'GET') {
        writes++;
      }
      return http.Response(
        jsonEncode({
          'items': <dynamic>[],
          'total': 0,
          'page': 1,
          'per_page': 100,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23g-s03d-add-medication')));
    await tester.pump();
    await tester.enterText(
      find.byKey(const ValueKey('p23g-s03d-medication-name-0')),
      'Metformin',
    );
    await tester.tap(find.byKey(const ValueKey('p23g-s03d-decision-0')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Replace').last);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23g-s03d-save')));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('p23g-s03d-validation-error')),
      findsOneWidget,
    );
    expect(writes, 0);
  });
}
