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
import 'package:clinic_system_pro/features/patients/data/patient_consultation_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_consultation_history_card.dart';

AuthSessionState _ready({String role = 'doctor'}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 's03-consultation-token',
    refreshToken: 's03-consultation-refresh',
    userId: 42,
    clinicContextId: 3,
    role: role,
  ),
);

Map<String, dynamic> _row({
  int id = 11,
  int patientId = 7,
  int clinicId = 3,
  String type = 'general',
  String workflow = 'in_progress',
  String record = 'draft',
}) {
  final finalized = record == 'final' || record == 'amended';

  return {
    'id': id,
    'clinic_id': clinicId,
    'patient_id': patientId,
    'staff_id': 8,
    'appointment_id': null,
    'icd10_code': null,
    'consultation_type': type,
    'status': workflow,
    'record_status': record,
    'record_version': record == 'amended' ? 2 : 1,
    'finalized_by_staff_id': finalized ? 8 : null,
    'finalized_at': finalized ? '2026-11-06T10:00:00' : null,
    'latest_amendment_id': record == 'amended' ? 25 : null,
    'chief_complaint': 'Fever',
    'symptoms': 'Fatigue',
    'diagnosis': finalized ? 'Clinical finding' : null,
    'treatment_plan': null,
    'notes': null,
    'voice_note_url': null,
    'transcribed_text': null,
    'template_id': null,
    'started_at': '2026-11-06T09:00:00',
    'ended_at': finalized ? '2026-11-06T10:00:00' : null,
    'created_at': '2026-11-06T09:00:00+00:00',
    'updated_at': '2026-11-06T09:00:00+00:00',
  };
}

Map<String, dynamic> _response({
  List<Map<String, dynamic>>? items,
  int? total,
  int page = 1,
  int perPage = 20,
}) {
  final records = items ?? [_row()];
  final count = total ?? records.length;
  final pages = (count + perPage - 1) ~/ perPage;
  final hasNext = page < pages;
  final hasPrev = page > 1;

  return {
    'success': true,
    'data': records,
    'pagination': {
      'total': count,
      'page': page,
      'per_page': perPage,
      'pages': pages,
      'has_next': hasNext,
      'has_prev': hasPrev,
      'next_page': hasNext ? page + 1 : null,
      'prev_page': hasPrev ? page - 1 : null,
    },
  };
}

PatientConsultationRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
}) => PatientConsultationRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => _ready(role: role),
  sessionNavigationGuard: guard,
);

Widget _host(
  PatientConsultationRemoteDataSource source, {
  int patientId = 7,
  int pageSize = 20,
  double textScale = 1,
}) => MaterialApp(
  builder: (context, child) => MediaQuery(
    data: MediaQuery.of(context)
        .copyWith(textScaler: TextScaler.linear(textScale)),
    child: child!,
  ),
  home: Scaffold(
    body: SingleChildScrollView(
      child: PatientConsultationHistoryCard(
        patientId: patientId,
        dataSource: source,
        pageSize: pageSize,
      ),
    ),
  ),
);

void main() {
  testWidgets('history GET displays separate clinical and workflow states', (
    tester,
  ) async {
    final paths = <String>[];

    final client = MockClient((request) async {
      paths.add(request.url.path);
      expect(request.method, 'GET');
      expect(request.headers['authorization'], 'Bearer s03-consultation-token');
      expect(request.url.queryParameters, {'page': '1', 'per_page': '20'});

      return http.Response(jsonEncode(_response()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(paths, ['/api/v1/consultations/patient/7']);
    expect(
      find.byKey(const ValueKey('patient-consultation-history-11')),
      findsOneWidget,
    );
    expect(find.text('Type: General'), findsOneWidget);
    expect(find.text('Workflow: In progress'), findsOneWidget);
    expect(find.text('Clinical record: Draft (not finalized)'), findsOneWidget);
    expect(find.text('Record version: 1'), findsOneWidget);
    expect(find.text('Chief complaint: Fever'), findsOneWidget);
    expect(
      find.text('Started (backend time): 2026-11-06T09:00:00'),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('finalized and amended statuses remain distinguishable', (
    tester,
  ) async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode(
          _response(
            items: [
              _row(
                id: 12,
                type: 'follow_up',
                workflow: 'completed',
                record: 'final',
              ),
              _row(
                id: 13,
                type: 'specialist',
                workflow: 'completed',
                record: 'amended',
              ),
            ],
          ),
        ),
        200,
      ),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.text('Clinical record: Finalized'), findsOneWidget);
    expect(find.text('Clinical record: Amended'), findsOneWidget);
    expect(find.text('Record version: 2'), findsOneWidget);
    expect(find.text('Latest amendment ID: 25'), findsOneWidget);
    expect(find.text('Workflow: Completed'), findsNWidgets(2));
    expect(find.text('Type: Follow-up'), findsOneWidget);
    expect(find.text('Type: Specialist'), findsOneWidget);
    expect(find.text('Diagnosis: Clinical finding'), findsNWidgets(2));
  });

  testWidgets('paginated history loads page two once', (tester) async {
    final pages = <String>[];

    final client = MockClient((request) async {
      final requestedPage = request.url.queryParameters['page']!;
      pages.add(requestedPage);
      expect(request.url.queryParameters['per_page'], '1');

      final item = requestedPage == '1'
          ? _row()
          : _row(id: 12, workflow: 'cancelled');

      return http.Response(
        jsonEncode(
          _response(
            items: [item],
            total: 2,
            page: int.parse(requestedPage),
            perPage: 1,
          ),
        ),
        200,
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard), pageSize: 1));
    await tester.pumpAndSettle();

    final loadMore = find.byKey(
      const ValueKey('patient-consultations-load-more'),
    );

    expect(loadMore, findsOneWidget);

    await tester.ensureVisible(loadMore);
    await tester.pumpAndSettle();
    await tester.tap(loadMore);
    await tester.pumpAndSettle();

    expect(pages, ['1', '2']);
    expect(
      find.byKey(const ValueKey('patient-consultation-history-11')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-consultation-history-12')),
      findsOneWidget,
    );
    expect(find.text('Workflow: Cancelled'), findsOneWidget);
    expect(loadMore, findsNothing);
    expect(find.text('Consultations: 2 of 2'), findsOneWidget);
  });

  testWidgets('empty history is not mistaken for access failure', (
    tester,
  ) async {
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode(_response(items: [], total: 0)), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-consultations-empty')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-consultations-error')),
      findsNothing,
    );
  });

  testWidgets('403 denies consultation records and exposes no PHI', (
    tester,
  ) async {
    final client = MockClient(
      (_) async => http.Response('{"error":"Forbidden"}', 403),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-consultations-error')),
      findsOneWidget,
    );
    expect(find.text('Chief complaint: Fever'), findsNothing);
    expect(
      find.byKey(const ValueKey('patient-consultations-empty')),
      findsNothing,
    );
  });

  testWidgets('patient role cannot fetch consultation history', (tester) async {
    var requests = 0;

    final client = MockClient((_) async {
      requests++;
      return http.Response(jsonEncode(_response()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard, role: 'patient')));
    await tester.pumpAndSettle();

    expect(requests, 0);
    expect(
      find.byKey(const ValueKey('patient-consultations-error')),
      findsOneWidget,
    );
  });

  testWidgets('session invalidation clears consultation PHI', (tester) async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_response()), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.text('Chief complaint: Fever'), findsOneWidget);

    guard.invalidate();
    await tester.pump();

    expect(find.text('Chief complaint: Fever'), findsNothing);
    expect(
      find.byKey(const ValueKey('patient-consultations-error')),
      findsOneWidget,
    );
  });

  testWidgets('failed refresh removes previously displayed records', (
    tester,
  ) async {
    var requests = 0;

    final client = MockClient((_) async {
      requests++;

      if (requests == 2) {
        return http.Response('{"error":"Unavailable"}', 503);
      }

      return http.Response(jsonEncode(_response()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.text('Chief complaint: Fever'), findsOneWidget);

    final refresh = find.byKey(const ValueKey('patient-consultations-refresh'));

    await tester.ensureVisible(refresh);
    await tester.pumpAndSettle();
    await tester.tap(refresh);
    await tester.pumpAndSettle();

    expect(requests, 2);
    expect(find.text('Chief complaint: Fever'), findsNothing);
    expect(
      find.byKey(const ValueKey('patient-consultations-error')),
      findsOneWidget,
    );
  });

  testWidgets('patient switch rejects old in-flight records', (tester) async {
    final pending = Completer<http.Response>();
    var oldStarted = false;

    final client = MockClient((request) {
      final path = request.url.path;

      if (path == '/api/v1/consultations/patient/7') {
        oldStarted = true;
        return pending.future;
      }

      if (path == '/api/v1/consultations/patient/8') {
        return Future.value(
          http.Response(
            jsonEncode(_response(items: [_row(id: 21, patientId: 8)])),
            200,
          ),
        );
      }

      throw StateError('Unexpected path: $path');
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);

    await tester.pumpWidget(_host(source));
    await tester.pump();

    expect(oldStarted, isTrue);

    await tester.pumpWidget(_host(source, patientId: 8));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-consultation-history-21')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-consultation-history-11')),
      findsNothing,
    );

    pending.complete(http.Response(jsonEncode(_response()), 200));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-consultation-history-21')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-consultation-history-11')),
      findsNothing,
    );
  });

  testWidgets('wrong clinic or patient in response fails closed', (
    tester,
  ) async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode(_response(items: [_row(clinicId: 99)])),
        200,
      ),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-consultations-error')),
      findsOneWidget,
    );
    expect(find.text('Chief complaint: Fever'), findsNothing);
  });

  for (final width in <double>[390, 1100]) {
    testWidgets(
      '${width.toInt()}px consultation layout supports enlarged text',
      (tester) async {
        tester.view.physicalSize = Size(width, 1000);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        final client = MockClient(
          (_) async => http.Response(jsonEncode(_response()), 200),
        );
        addTearDown(client.close);

        final guard = SessionNavigationGuard()..markSessionAvailable();
        addTearDown(guard.dispose);

        await tester.pumpWidget(_host(_source(client, guard), textScale: 1.25));
        await tester.pumpAndSettle();

        expect(
          find.byKey(const ValueKey('patient-consultations-card')),
          findsOneWidget,
        );
        expect(find.byTooltip('Refresh consultation history'), findsOneWidget);
        expect(tester.takeException(), isNull);
      },
    );
  }
}
