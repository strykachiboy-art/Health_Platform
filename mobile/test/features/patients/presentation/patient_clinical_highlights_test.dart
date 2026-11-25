import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_clinical_highlights.dart';

import '../../clinical_timeline/timeline_contract_test.dart' as timeline;
import '../../problems/problem_contract_test.dart' as fixtures;

void main() {
  testWidgets('current order summaries reuse scoped lab and CPOE readers', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1000, 1400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final paths = <String>[];
    final h = timeline.Harness(
      MockClient((request) async {
        paths.add(request.url.path);
        if (request.url.path == timeline.timelineRoot) {
          return fixtures.ok(timeline.page([]));
        }
        if (request.url.path == '/api/v1/lab/orders') {
          expect(request.url.queryParameters, {
            'patient_id': '7',
            'page': '1',
            'per_page': '5',
          });
          return fixtures.ok({
            'items': [
              {
                'id': 12,
                'clinic_id': 3,
                'patient_id': 7,
                'consultation_id': null,
                'ordered_by_id': 18,
                'status': 'completed',
                'created_at': '2026-11-10T08:00:00+00:00',
                'items': [
                  {
                    'id': 112,
                    'order_id': 12,
                    'test_id': 10,
                    'test': null,
                    'result_value': '5.4',
                    'flag': 'normal',
                    'result_notes': null,
                  },
                ],
              },
            ],
            'total': 1,
            'page': 1,
            'per_page': 5,
          });
        }
        if (request.url.path == '/api/v1/clinical-orders/patients/7') {
          expect(request.url.queryParameters['order_type'], 'imaging');
          return http.Response(
            jsonEncode({
              'total': 1,
              'items': [
                {
                  'order_type': 'imaging',
                  'source_entity_type': 'imaging_order',
                  'source_entity_id': 11,
                  'clinic_id': 3,
                  'patient_id': 7,
                  'admission_id': 23,
                  'consultation_id': 34,
                  'ordered_by_staff_id': 8,
                  'ordered_at': '2026-11-07T10:00:00+00:00',
                  'priority': 'routine',
                  'status': 'active',
                  'source_status': 'active',
                  'display_name': 'Synthetic imaging order',
                  'instructions': null,
                  'scheduled_at': null,
                  'due_at': null,
                  'completed_at': null,
                  'cancelled_at': null,
                },
              ],
            }),
            200,
          );
        }
        throw StateError('Unexpected request');
      }),
    );
    final lab = LabOrderReadRemoteDataSource(
      apiClient: h.source.apiClient,
      loadSession: h.source.loadSession,
      sessionNavigationGuard: h.guard,
    );
    final orders = ClinicalOrdersRemoteDataSource(
      apiClient: h.source.apiClient,
      loadSession: h.source.loadSession,
      sessionNavigationGuard: h.guard,
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: PatientClinicalHighlights(
              source: h.source,
              patientId: 7,
              clinicId: 3,
              labSource: lab,
              ordersSource: orders,
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(paths, isEmpty);
    await tester.tap(find.byKey(const ValueKey('patient-highlights-refresh')));
    await tester.pumpAndSettle();
    expect(paths.length, 5);
    expect(find.text('Laboratory order #12 · completed'), findsOneWidget);
    expect(find.text('Synthetic imaging order'), findsOneWidget);
    h.guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Synthetic imaging order'), findsNothing);
  });

  test('medication highlight skips schedule and refusal, continues with opaque cursor', () async {
    var calls = 0;
    final h = timeline.Harness(
      MockClient((request) async {
        calls++;
        expect(
          request.url.queryParameters['categories'],
          'medication_administration',
        );
        if (calls == 1) {
          return fixtures.ok(
            timeline.page(
              List.generate(
                25,
                (i) => timeline.event(
                  row: 50 - i,
                  type: 'medication_current_slot',
                ),
              ),
              next: 'opaque-next',
            ),
          );
        }
        expect(request.url.queryParameters['cursor'], 'opaque-next');
        return fixtures.ok(
          timeline.page([
            timeline.event(
              row: 2,
              type: 'medication_refused',
              date: '2000-01-01T09:00:00Z',
            ),
            timeline.event(
              row: 1,
              type: 'medication_administered',
              date: '2000-01-01T08:00:00Z',
            ),
          ]),
        );
      }),
    );
    final session = await h.source.open(7, 3);
    final result = await findClinicalHighlight(
      session,
      'medication_administration',
      {'medication_administered'},
    );
    expect(calls, 2);
    expect(result.event?.sourceId, 1);
    expect(result.event?.type, 'medication_administered');
  });
  test(
    'bounded history with more pages is not an empty clinical history',
    () async {
      final h = timeline.Harness(
        MockClient(
          (_) async => fixtures.ok(
            timeline.page(
              List.generate(
                25,
                (i) => timeline.event(
                  row: 50 - i,
                  type: 'medication_current_slot',
                ),
              ),
              next: 'more',
            ),
          ),
        ),
      );
      final result = await findClinicalHighlight(
        await h.source.open(7, 3),
        'medication_administration',
        {'medication_administered'},
        maxPages: 1,
      );
      expect(result.event, isNull);
      expect(result.exhausted, isFalse);
    },
  );
  test('exhausted authorized history may report no matching event', () async {
    final h = timeline.Harness(
      MockClient((_) async => fixtures.ok(timeline.page([]))),
    );
    final result = await findClinicalHighlight(
      await h.source.open(7, 3),
      'medication_administration',
      {'medication_administered'},
    );
    expect(result.event, isNull);
    expect(result.exhausted, isTrue);
  });
  testWidgets(
    'on-demand summaries use actual events and clear on session loss',
    (tester) async {
      var calls = 0;
      final h = timeline.Harness(
        MockClient((request) async {
          calls++;
          final type = switch (request.url.queryParameters['categories']) {
            'medication_administration' => 'medication_administered',
            'laboratory' => 'lab_results_verified',
            'imaging' => 'imaging_order_completed',
            _ => throw StateError('Unexpected category'),
          };
          return fixtures.ok(timeline.page([timeline.event(type: type)]));
        }),
      );
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: PatientClinicalHighlights(
                source: h.source,
                patientId: 7,
                clinicId: 3,
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(calls, 0);
      await tester.tap(
        find.byKey(const ValueKey('patient-highlights-refresh')),
      );
      await tester.pumpAndSettle();
      expect(calls, 3);
      expect(find.textContaining('medication administered ·'), findsOneWidget);
      expect(find.textContaining('lab results verified ·'), findsOneWidget);
      h.guard.invalidate();
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('patient-clinical-highlights')),
        findsNothing,
      );
    },
  );
}
