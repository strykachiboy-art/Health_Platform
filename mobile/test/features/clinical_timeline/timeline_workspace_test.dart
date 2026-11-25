import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_timeline/presentation/timeline_workspace.dart';
import 'package:clinic_system_pro/features/problems/data/problem_source.dart';

import '../problems/problem_contract_test.dart' as f;
import '../problems/problem_workspace_test.dart' as w;
import 'timeline_contract_test.dart' as t;

void main() {
  testWidgets(
    'entry makes no HTTP request before opening; displayed events distinguish scheduled and historical',
    (tester) async {
      w.large(tester);
      var requests = 0;
      final h = t.Harness(
        MockClient((r) async {
          requests++;
          expect(r.method, 'GET');
          expect(r.url.path, t.timelineRoot);
          return f.ok(
            t.page([
              t.event(type: 'appointment_current_slot'),
              t.event(type: 'problem_review_or_change_recorded', version: 2),
            ]),
          );
        }),
      );
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: TimelineEntry(source: h.source, patientId: 7, clinicId: 3),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(requests, 0);
      await tester.tap(find.byKey(const ValueKey('clinical-timeline-open')));
      await tester.pumpAndSettle();
      expect(requests, 1);
      expect(
        find.text('Scheduled slot — not proof of completed care.'),
        findsOneWidget,
      );
      expect(
        find.text('Historical amendment event — current source may differ.'),
        findsOneWidget,
      );
      expect(
        find.text(
          'Recorded version 2 · original and amended records are distinct.',
        ),
        findsOneWidget,
      );
      expect(
        find.text('Patient check-in timestamps are not available.'),
        findsOneWidget,
      );
      h.guard.invalidate();
      await tester.pumpAndSettle();
      expect(
        find.text('Session changed. Clinical timeline information cleared.'),
        findsOneWidget,
      );
      expect(find.text('Appointment scheduled slot'), findsNothing);
      h.guard.markSessionAvailable();
      await tester.pumpAndSettle();
      expect(find.text('Appointment scheduled slot'), findsNothing);
    },
  );
  testWidgets('denied role and wrong clinic hide entry without network', (
    tester,
  ) async {
    final h = t.Harness(
      MockClient((_) async => throw StateError('Unexpected request')),
      auth: f.ready(role: 'admin'),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: TimelineEntry(source: h.source, patientId: 7, clinicId: 3),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('clinical-timeline-open')), findsNothing);
    h.state = f.ready();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: TimelineEntry(source: h.source, patientId: 7, clinicId: 4),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('clinical-timeline-open')), findsNothing);
  });
  testWidgets(
    'category filter refreshes and original record navigation uses exact identifier',
    (tester) async {
      w.large(tester);
      final calls = <String?>[];
      final routes = <String>[];
      final h = t.Harness(
        MockClient((r) async {
          calls.add(r.url.queryParameters['categories']);
          return f.ok(t.page([t.event(row: 9, sourceId: 23)]));
        }),
      );
      final s = await h.source.open(7, 3);
      await tester.pumpWidget(
        MaterialApp(
          home: TimelineWorkspace(
            session: s,
            onOpenSource: (path) async {
              routes.add(path);
            },
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('timeline-category')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Consultations').last);
      await tester.pumpAndSettle();
      expect(calls, [null, 'consultations']);
      await tester.tap(
        find.byKey(const ValueKey('timeline-source-consultation_started:9')),
      );
      await tester.pumpAndSettle();
      expect(routes, ['/consultations/patient/7/23']);
      expect(find.text('Open authorized source record'), findsOneWidget);
    },
  );
  testWidgets(
    'problem link reuses exact existing detail and history in read-only mode',
    (tester) async {
      w.large(tester);
      final calls = <String>[];
      final h = t.Harness(
        MockClient((r) async {
          calls.add(r.url.path);
          expect(r.method, 'GET');
          if (r.url.path == t.timelineRoot) {
            return f.ok(
              t.page([
                t.event(type: 'problem_added', sourceId: 23, version: 1),
              ]),
            );
          }
          if (r.url.path == '${f.root}/23') {
            return f.ok(f.record(id: 23));
          }
          if (r.url.path == '${f.root}/23/history') {
            return f.ok({'original': f.record(id: 23), 'amendments': []});
          }
          throw StateError('Unexpected request');
        }),
      );
      final source = ProblemSource(
        apiClient: h.source.apiClient,
        loadSession: h.source.loadSession,
        guard: h.guard,
        connectivity: f.Net(),
      );
      final s = await h.source.open(7, 3);
      await tester.pumpWidget(
        MaterialApp(
          home: TimelineWorkspace(session: s, problemSource: source),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(
        find.byKey(const ValueKey('timeline-source-problem_added:9')),
      );
      await tester.pumpAndSettle();
      expect(find.text('Synthetic hypertension'), findsOneWidget);
      expect(find.text('Read-only problem list.'), findsOneWidget);
      expect(find.byKey(const ValueKey('problem-create')), findsNothing);
      await tester.tap(find.text('Review amendment history'));
      await tester.pumpAndSettle();
      expect(find.text('Original clinical record'), findsOneWidget);
      expect(calls, [t.timelineRoot, '${f.root}/23', '${f.root}/23/history']);
    },
  );
  testWidgets('pending request cannot reveal data after guard invalidation', (
    tester,
  ) async {
    final response = Completer<Map<String, dynamic>>();
    final h = t.Harness(MockClient((_) async => f.ok(await response.future)));
    final s = await h.source.open(7, 3);
    await tester.pumpWidget(MaterialApp(home: TimelineWorkspace(session: s)));
    await tester.pump();
    h.guard.invalidate();
    response.complete(t.page([t.event()]));
    await tester.pumpAndSettle();
    expect(
      find.text('Session changed. Clinical timeline information cleared.'),
      findsOneWidget,
    );
    expect(find.text('Consultation started'), findsNothing);
  });
  testWidgets('held feature never displays synthetic data', (tester) async {
    final h = t.Harness(MockClient((_) async => f.denied(404)));
    final s = await h.source.open(7, 3);
    await tester.pumpWidget(MaterialApp(home: TimelineWorkspace(session: s)));
    await tester.pumpAndSettle();
    expect(
      find.text(
        'Clinical timeline or patient unavailable. The staged feature may be disabled.',
      ),
      findsOneWidget,
    );
    expect(
      find.text('No authorized events returned for this filter.'),
      findsNothing,
    );
  });
  testWidgets(
    'canonical patient router exposes timeline after verified chart only',
    (tester) async {
      w.large(tester);
      final calls = <String>[];
      final h = t.Harness(
        MockClient((r) async {
          calls.add(r.url.path);
          if (r.url.path == '/api/v1/patients/7') {
            return f.ok(w.patient());
          }
          if (r.url.path == f.root) {
            return f.page([f.record()]);
          }
          if (r.url.path == t.timelineRoot) {
            return f.ok(t.page([t.event()]));
          }
          throw StateError('Unexpected route');
        }),
      );
      final api = h.source.apiClient;
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Text('Login sentinel')),
        dashboardDataSource: DashboardRemoteDataSource(
          apiClient: api,
          loadSession: () async => h.state,
          sessionNavigationGuard: h.guard,
        ),
        patientDataSource: PatientRemoteDataSource(
          apiClient: api,
          loadAccessToken: () async => 'bound-token',
          loadSession: () async => h.state,
          sessionNavigationGuard: h.guard,
        ),
        sessionNavigationGuard: h.guard,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/patients/7');
      await tester.pumpAndSettle();
      expect(calls.first, '/api/v1/patients/7');
      expect(calls.contains(t.timelineRoot), false);
      final open = find.byKey(const ValueKey('clinical-timeline-open'));
      await tester.ensureVisible(open);
      await tester.tap(open);
      await tester.pumpAndSettle();
      expect(calls.contains(t.timelineRoot), true);
      expect(find.text('Consultation started'), findsOneWidget);
      h.guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.text('Consultation started'), findsNothing);
    },
  );
}
