import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/problems/presentation/problem_workspace.dart';
import 'package:clinic_system_pro/features/problems/presentation/problem_context_card.dart';

import 'problem_contract_test.dart' as f;

void large(WidgetTester tester) {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(1200, 2600);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

Map<String, dynamic> patient() => {
  'id': 7,
  'clinic_id': 3,
  'patient_number': 'PT-7',
  'first_name': 'Synthetic',
  'last_name': 'Patient',
  'date_of_birth': '1980-01-01',
  'gender': 'FEMALE',
  'blood_type': 'O_POSITIVE',
  'phone': null,
  'email': null,
  'address': null,
  'allergies': 'Separate allergy',
  'chronic_conditions': 'Legacy note',
  'emirates_id': null,
  'umrn': 'TEST-7',
  'is_active': true,
  'created_at': f.stamp,
  'updated_at': f.stamp,
};

void main() {
  testWidgets(
    'doctor workspace loads list, detail and history; clears PHI on clinic switch',
    (tester) async {
      large(tester);
      final calls = <String>[];
      final h = f.Harness(
        MockClient((r) async {
          calls.add(r.url.path);
          if (r.url.path.endsWith('/history')) {
            return f.ok({'original': f.record(), 'amendments': []});
          }
          if (r.url.path.endsWith('/9')) {
            return f.ok(f.record(links: []));
          }
          return f.page([f.record()]);
        }),
      );
      final session = await h.source.open();
      await tester.pumpWidget(
        MaterialApp(
          home: ProblemWorkspaceScreen(
            source: h.source,
            session: session,
            patientId: 7,
            consultationId: 11,
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Synthetic hypertension'), findsOneWidget);
      expect(find.byKey(const ValueKey('problem-create')), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('problem-row-9')));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('problem-action-associate')),
        findsOneWidget,
      );
      await tester.tap(find.text('Review amendment history'));
      await tester.pumpAndSettle();
      expect(find.text('Original clinical record'), findsOneWidget);
      expect(calls, [f.root, '${f.root}/9', '${f.root}/9/history']);
      h.guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.text('Synthetic hypertension'), findsNothing);
      expect(
        find.text('Session changed. Clinical problem information cleared.'),
        findsOneWidget,
      );
      h.guard.markSessionAvailable();
      await tester.pumpAndSettle();
      expect(find.text('Synthetic hypertension'), findsNothing);
    },
  );
  testWidgets(
    'nurse gets read-only screen and never sees clinical mutation controls',
    (tester) async {
      large(tester);
      final h = f.Harness(
        MockClient((r) async {
          expect(r.method, 'GET');
          return r.url.path.endsWith('/9')
              ? f.ok(f.record())
              : f.page([f.record()]);
        }),
        auth: f.ready(role: 'nurse'),
      );
      final session = await h.source.open();
      await tester.pumpWidget(
        MaterialApp(
          home: ProblemWorkspaceScreen(
            source: h.source,
            session: session,
            patientId: 7,
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Read-only problem list.'), findsOneWidget);
      expect(find.byKey(const ValueKey('problem-create')), findsNothing);
      await tester.tap(find.byKey(const ValueKey('problem-row-9')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('problem-action-review')), findsNothing);
      expect(find.text('Review amendment history'), findsOneWidget);
    },
  );
  testWidgets(
    'doctor review requires explicit reason and confirmation; one PATCH only',
    (tester) async {
      large(tester);
      var patches = 0;
      var version = 1;
      final h = f.Harness(
        MockClient((r) async {
          if (r.method == 'PATCH') {
            patches++;
            expect(jsonDecode(r.body), {
              'expected_version': 1,
              'action': 'review',
              'reason': 'Reviewed at encounter',
            });
            version = 2;
            return f.ok(f.record(version: 2));
          }
          return r.url.path.endsWith('/9')
              ? f.ok(f.record(version: version))
              : f.page([f.record(version: version)]);
        }),
      );
      final session = await h.source.open();
      await tester.pumpWidget(
        MaterialApp(
          home: ProblemWorkspaceScreen(
            source: h.source,
            session: session,
            patientId: 7,
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('problem-row-9')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('problem-action-review')));
      await tester.pumpAndSettle();
      final submit = find.byKey(const ValueKey('problem-submit'));
      expect(tester.widget<FilledButton>(submit).onPressed, isNull);
      expect(patches, 0);
      final reason = find.byType(TextFormField);
      expect(reason, findsOneWidget);
      await tester.enterText(reason, 'Reviewed at encounter');
      await tester.tap(
        find.text(
          'I have reviewed the clinical information and intend this operation',
        ),
      );
      await tester.pumpAndSettle();
      await tester.ensureVisible(submit);
      await tester.tap(submit);
      await tester.pumpAndSettle();
      expect(patches, 1);
      expect(
        find.text('Server confirmed the clinical problem operation.'),
        findsOneWidget,
      );
    },
  );
  testWidgets(
    'resource-bound review card does not write and clears after session invalidation',
    (tester) async {
      large(tester);
      var requests = 0;
      final h = f.Harness(
        MockClient((r) async {
          requests++;
          expect(r.method, 'GET');
          expect(
            r.url.path,
            '/api/v1/medication-reconciliations/12/problem-context',
          );
          return f.ok(f.projection(medication: true));
        }),
        auth: f.ready(role: 'pharmacist'),
      );
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ProblemContextCard(
              source: h.source,
              patientId: 7,
              resourceId: 12,
              medication: true,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(requests, 0);
      await tester.tap(find.byKey(const ValueKey('problem-context-load')));
      await tester.pumpAndSettle();
      expect(find.text('Synthetic hypertension'), findsOneWidget);
      expect(requests, 1);
      h.guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.text('Synthetic hypertension'), findsNothing);
    },
  );
  testWidgets(
    'admin entry and pharmacist admission review are hidden without HTTP',
    (tester) async {
      final h = f.Harness(
        MockClient((_) async => throw StateError('Unexpected transport')),
        auth: f.ready(role: 'admin'),
      );
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ProblemWorkspaceEntry(source: h.source, patientId: 7),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('problem-open-workspace')),
        findsNothing,
      );
      h.state = f.ready(role: 'pharmacist');
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ProblemContextCard(
              source: h.source,
              patientId: 7,
              resourceId: 12,
              medication: false,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('problem-context-load')), findsNothing);
    },
  );
  testWidgets(
    'canonical router reaches verified patient overview and new workspace',
    (tester) async {
      large(tester);
      final requests = <String>[];
      final h = f.Harness(
        MockClient((r) async {
          requests.add(r.url.path);
          expect(r.headers['authorization'], 'Bearer bound-token');
          if (r.url.path == '/api/v1/patients/7') {
            return f.ok(patient());
          }
          if (r.url.path == f.root) {
            return f.page([f.record()]);
          }
          throw StateError('Unexpected route');
        }),
      );
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(
          apiClient: h.source.apiClient,
        ),
        loginScreen: const Scaffold(body: Text('Login sentinel')),
        dashboardDataSource: DashboardRemoteDataSource(
          apiClient: h.source.apiClient,
          loadSession: () => Future.value(h.state),
          sessionNavigationGuard: h.guard,
        ),
        patientDataSource: PatientRemoteDataSource(
          apiClient: h.source.apiClient,
          loadAccessToken: () async => 'bound-token',
          loadSession: () => Future.value(h.state),
          sessionNavigationGuard: h.guard,
        ),
        sessionNavigationGuard: h.guard,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/patients/7');
      await tester.pumpAndSettle();
      expect(find.text('Structured clinical problems'), findsOneWidget);
      expect(
        find.textContaining('Legacy chronic condition notes'),
        findsOneWidget,
      );
      expect(find.textContaining('Synthetic hypertension'), findsOneWidget);
      expect(requests.first, '/api/v1/patients/7');
      final open = find.byKey(const ValueKey('problem-open-workspace'));
      await tester.ensureVisible(open);
      await tester.tap(open);
      await tester.pumpAndSettle();
      expect(find.text('Clinical problem list'), findsOneWidget);
      expect(find.text('Synthetic hypertension'), findsOneWidget);
      h.guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.text('Synthetic hypertension'), findsNothing);
    },
  );
}
