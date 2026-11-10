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
import 'package:clinic_system_pro/features/medication_reconciliation/data/medication_reconciliation_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/presentation/medication_reconciliation_review_screen.dart';

AuthSessionState session({String role = 'nurse', int clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'p23g-s02-token',
        refreshToken: 'p23g-s02-refresh',
        userId: 43,
        role: role,
        clinicContextId: clinicId,
      ),
    );

Map<String, dynamic> medication({String decision = 'continue'}) => {
  'source_prescription_item_id': 19,
  'medication_name': 'Metformin',
  'decision': decision,
  'replacement_medication_name': decision == 'replace' ? 'Alternative' : null,
  'dose_text': '500 mg',
  'route_text': 'oral',
  'frequency_text': 'twice daily',
  'instructions': 'Take with food',
  'rationale': 'Clinician reviewed',
};

Map<String, dynamic> record({
  int id = 31,
  int clinicId = 3,
  int patientId = 5,
  int admissionId = 7,
  String event = 'admission',
  int? transferId,
  String status = 'draft',
  bool noMedicationsReported = false,
  List<Map<String, dynamic>>? items,
}) => {
  'id': id,
  'clinic_id': clinicId,
  'patient_id': patientId,
  'admission_id': admissionId,
  'ward_transfer_id': transferId,
  'event_type': event,
  'event_identity': event == 'transfer'
      ? 'transfer:$transferId'
      : '$event:$admissionId',
  'created_by_staff_id': 9,
  'no_medications_reported': noMedicationsReported,
  'notes': 'Medication history reviewed',
  'items':
      items ??
      (noMedicationsReported ? <Map<String, dynamic>>[] : [medication()]),
  'record_status': status,
  'record_version': status == 'amended' ? 2 : 1,
  'finalized_by_staff_id': status == 'draft' ? null : 9,
  'finalized_at': status == 'draft' ? null : '2026-11-10T09:00:00Z',
  'latest_amendment_id': status == 'amended' ? 35 : null,
  'created_at': '2026-11-09T09:00:00Z',
  'updated_at': '2026-11-10T09:00:00Z',
};

Map<String, dynamic> page({
  List<Map<String, dynamic>>? items,
  int number = 1,
  int total = 1,
}) => {
  'items': items ?? [record()],
  'total': total,
  'page': number,
  'per_page': 20,
};

MedicationReconciliationReadRemoteDataSource source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) => MedicationReconciliationReadRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: loader ?? () async => session(),
  sessionNavigationGuard: guard,
);

Widget app(
  MedicationReconciliationReadRemoteDataSource remote, {
  int patientId = 5,
  int admissionId = 7,
}) => MaterialApp(
  home: MedicationReconciliationReviewScreen(
    dataSource: remote,
    patientId: patientId,
    admissionId: admissionId,
  ),
);

void largeScreen(WidgetTester tester) {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(1100, 2300);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

void main() {
  testWidgets(
    'admission review is GET-only, scoped, and shows unfinalized decisions',
    (tester) async {
      largeScreen(tester);
      final methods = <String>[];
      final client = MockClient((request) async {
        methods.add(request.method);
        expect(request.headers['authorization'], 'Bearer p23g-s02-token');
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        if (request.url.path.endsWith('/31')) {
          return http.Response(
            jsonEncode({
              'reconciliation': record(items: [medication(decision: 'verify')]),
            }),
            200,
          );
        }
        expect(
          request.url.path,
          '/api/v1/medication-reconciliations/admissions/7',
        );
        expect(request.url.queryParameters, {'page': '1', 'per_page': '20'});
        return http.Response(
          jsonEncode(
            page(
              items: [
                record(items: [medication(decision: 'verify')]),
              ],
            ),
          ),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(app(source(client, guard)));
      await tester.pumpAndSettle();
      expect(find.textContaining('Patient #5'), findsOneWidget);
      expect(find.byKey(const ValueKey('p23g-s02-record-31')), findsOneWidget);
      expect(find.textContaining('Draft — not finalized'), findsOneWidget);
      expect(
        find.textContaining(
          'Home-medication history is not independently verified',
        ),
        findsOneWidget,
      );
      await tester.tap(find.byKey(const ValueKey('p23g-s02-record-31')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p23g-s02-detail')), findsOneWidget);
      expect(
        find.byKey(const ValueKey('p23g-s02-draft-warning')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('p23g-s02-unresolved-warning')),
        findsOneWidget,
      );
      expect(find.text('Server decision: Verify — unresolved'), findsOneWidget);
      expect(find.textContaining('Dose (documented): 500 mg'), findsOneWidget);
      expect(
        find.textContaining('Rationale: Clinician reviewed'),
        findsOneWidget,
      );
      expect(find.text('Finalize reconciliation'), findsNothing);
      expect(find.text('Change medication'), findsNothing);
      expect(methods, ['GET', 'GET']);
    },
  );

  testWidgets(
    'event filter sends backend discharge filter; no-medication evidence is explicit',
    (tester) async {
      largeScreen(tester);
      final filters = <String?>[];
      final client = MockClient((request) async {
        expect(request.method, 'GET');
        final filter = request.url.queryParameters['event_type'];
        filters.add(filter);
        final row = filter == 'discharge'
            ? record(
                id: 32,
                event: 'discharge',
                status: 'final',
                noMedicationsReported: true,
              )
            : record();
        return http.Response(jsonEncode(page(items: [row])), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(app(source(client, guard)));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('p23g-s02-filter')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Discharge review').last);
      await tester.pumpAndSettle();
      expect(filters, [null, 'discharge']);
      expect(find.byKey(const ValueKey('p23g-s02-record-32')), findsOneWidget);
      expect(find.textContaining('Finalized by server'), findsOneWidget);
    },
  );

  testWidgets(
    'transfer filter uses backend event and displays actual transfer identity',
    (tester) async {
      largeScreen(tester);
      final queries = <String?>[];
      final client = MockClient((request) async {
        queries.add(request.url.queryParameters['event_type']);
        if (request.url.path.endsWith('/33')) {
          return http.Response(
            jsonEncode({
              'reconciliation': record(
                id: 33,
                event: 'transfer',
                transferId: 41,
              ),
            }),
            200,
          );
        }
        final isTransfer = queries.last == 'transfer';
        return http.Response(
          jsonEncode(
            page(
              items: [
                isTransfer
                    ? record(id: 33, event: 'transfer', transferId: 41)
                    : record(),
              ],
            ),
          ),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(app(source(client, guard)));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('p23g-s02-filter')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Transfer review').last);
      await tester.pumpAndSettle();
      expect(queries, [null, 'transfer']);
      await tester.tap(find.byKey(const ValueKey('p23g-s02-record-33')));
      await tester.pumpAndSettle();
      expect(find.text('Ward transfer #41'), findsOneWidget);
    },
  );

  testWidgets(
    'pagination follows server page metadata and does not infer records',
    (tester) async {
      largeScreen(tester);
      final pages = <String>[];
      final client = MockClient((request) async {
        final number = int.parse(request.url.queryParameters['page']!);
        pages.add('$number');
        return http.Response(
          jsonEncode(
            page(
              number: number,
              total: 21,
              items: [record(id: number == 1 ? 31 : 32)],
            ),
          ),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(app(source(client, guard)));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('p23g-s02-next')));
      await tester.pumpAndSettle();
      expect(pages, ['1', '2']);
      expect(find.byKey(const ValueKey('p23g-s02-record-31')), findsNothing);
      expect(find.byKey(const ValueKey('p23g-s02-record-32')), findsOneWidget);
      expect(
        tester
            .widget<IconButton>(find.byKey(const ValueKey('p23g-s02-next')))
            .onPressed,
        isNull,
      );
      await tester.tap(find.byKey(const ValueKey('p23g-s02-previous')));
      await tester.pumpAndSettle();
      expect(pages, ['1', '2', '1']);
    },
  );

  testWidgets('wrong-patient list response never renders clinical data', (
    tester,
  ) async {
    largeScreen(tester);
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode(page(items: [record(patientId: 99)])), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(source(client, guard)));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23g-s02-error')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23g-s02-record-31')), findsNothing);
    expect(find.textContaining('Metformin'), findsNothing);
  });

  testWidgets('wrong-patient detail is hidden with no guessed reconciliation', (
    tester,
  ) async {
    largeScreen(tester);
    final client = MockClient((request) async {
      if (request.url.path.endsWith('/31')) {
        return http.Response(
          jsonEncode({'reconciliation': record(patientId: 99)}),
          200,
        );
      }
      return http.Response(jsonEncode(page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(source(client, guard)));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23g-s02-record-31')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23g-s02-detail-error')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23g-s02-detail')), findsNothing);
  });

  testWidgets('patient role is rejected before HTTP, including refresh', (
    tester,
  ) async {
    largeScreen(tester);
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      app(source(client, guard, loader: () async => session(role: 'patient'))),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23g-s02-error')), findsOneWidget);
    expect(calls, 0);
    await tester.tap(find.byKey(const ValueKey('p23g-s02-retry')));
    await tester.pumpAndSettle();
    expect(calls, 0);
  });

  testWidgets(
    'session change clears displayed medications and locks stale context',
    (tester) async {
      largeScreen(tester);
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response(jsonEncode(page()), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(app(source(client, guard)));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p23g-s02-record-31')), findsOneWidget);
      guard.invalidate();
      await tester.pump();
      expect(
        find.byKey(const ValueKey('p23g-s02-session-unavailable')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('p23g-s02-record-31')), findsNothing);
      guard.markSessionAvailable();
      await tester.pump();
      expect(
        find.byKey(const ValueKey('p23g-s02-session-unavailable')),
        findsOneWidget,
      );
      expect(calls, 1);
    },
  );

  testWidgets('late HTTP list response is discarded after logout', (
    tester,
  ) async {
    largeScreen(tester);
    final waiting = Completer<http.Response>();
    final client = MockClient((_) => waiting.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(source(client, guard)));
    await tester.pump();
    guard.invalidate();
    await tester.pump();
    waiting.complete(http.Response(jsonEncode(page()), 200));
    await tester.pump();
    await tester.pump();
    expect(
      find.byKey(const ValueKey('p23g-s02-session-unavailable')),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('p23g-s02-record-31')), findsNothing);
  });

  testWidgets('server failure gives retry, not optimistic medication state', (
    tester,
  ) async {
    largeScreen(tester);
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      if (calls == 1) {
        return http.Response('Unavailable', 503);
      }
      return http.Response(jsonEncode(page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(source(client, guard)));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23g-s02-error')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23g-s02-record-31')), findsNothing);
    await tester.tap(find.byKey(const ValueKey('p23g-s02-retry')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23g-s02-record-31')), findsOneWidget);
    expect(calls, 2);
  });

  testWidgets(
    'empty admission list is not treated as signed-off reconciliation',
    (tester) async {
      largeScreen(tester);
      final client = MockClient(
        (_) async => http.Response(
          jsonEncode(page(items: <Map<String, dynamic>>[], total: 0)),
          200,
        ),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(app(source(client, guard)));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p23g-s02-empty')), findsOneWidget);
      expect(find.byKey(const ValueKey('p23g-s02-detail')), findsNothing);
      expect(find.text('Finalized by server'), findsNothing);
    },
  );
}
