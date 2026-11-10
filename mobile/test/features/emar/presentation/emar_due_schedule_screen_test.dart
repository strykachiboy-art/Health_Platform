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
import 'package:clinic_system_pro/features/emar/data/emar_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/presentation/emar_due_schedule_screen.dart';

AuthSessionState session({String role = 'nurse', int clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'test-emar-token',
        refreshToken: 'test-emar-refresh',
        userId: 43,
        role: role,
        clinicContextId: clinicId,
      ),
    );

Map<String, dynamic> row({
  int id = 11,
  int clinicId = 3,
  int patientId = 5,
  int admissionId = 7,
  String status = 'due',
}) => {
  'id': id,
  'clinic_id': clinicId,
  'patient_id': patientId,
  'admission_id': admissionId,
  'prescription_id': 17,
  'prescription_item_id': id == 11 ? 19 : 20,
  'status': status,
  'record_status': 'draft',
  'record_version': 1,
  'dose_value': '0.125',
  'dose_unit': 'mg',
  'route_code': 'oral',
  'scheduled_at': '2026-11-09T11:00:00Z',
};

Map<String, dynamic> page({
  List<Map<String, dynamic>>? items,
  int total = 1,
  int pageNumber = 1,
}) => {
  'items': items ?? [row()],
  'total': total,
  'page': pageNumber,
  'per_page': 20,
};

EmarReadRemoteDataSource source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) => EmarReadRemoteDataSource(
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
  EmarReadRemoteDataSource remote, {
  int patientId = 5,
  int admissionId = 7,
}) => MaterialApp(
  home: EmarDueScheduleScreen(
    dataSource: remote,
    patientId: patientId,
    admissionId: admissionId,
  ),
);

void largeScreen(WidgetTester tester) {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(1000, 1800);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

void main() {
  testWidgets('due schedule is server filtered, scoped and GET-only', (
    tester,
  ) async {
    largeScreen(tester);
    var calls = 0;
    var writes = 0;
    final client = MockClient((request) async {
      calls++;
      if (request.method != 'GET') writes++;
      expect(request.url.path, '/api/v1/emar/admissions/7');
      expect(request.url.queryParameters, {
        'page': '1',
        'per_page': '20',
        'status': 'due',
      });
      expect(request.headers['authorization'], 'Bearer test-emar-token');
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      return http.Response(jsonEncode(page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(app(source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('p23f-s02-row-11')), findsOneWidget);
    expect(find.text('Prescription item #19'), findsOneWidget);
    expect(find.textContaining('Server status: due'), findsOneWidget);
    expect(find.textContaining('Dose: 0.125 mg'), findsOneWidget);
    expect(
      find.textContaining(
        'No scanning or medication administration is enabled.',
      ),
      findsOneWidget,
    );
    expect(find.textContaining('Administer now'), findsNothing);
    expect(calls, 1);
    expect(writes, 0);
  });

  testWidgets('scheduled filter changes backend query, not local due rules', (
    tester,
  ) async {
    largeScreen(tester);
    final filters = <String?>[];
    final client = MockClient((request) async {
      final filter = request.url.queryParameters['status'];
      filters.add(filter);
      return http.Response(
        jsonEncode(
          page(
            items: [row(status: filter == 'scheduled' ? 'scheduled' : 'due')],
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

    await tester.tap(find.byKey(const ValueKey('p23f-s02-filter')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Scheduled (not due)').last);
    await tester.pumpAndSettle();

    expect(filters, ['due', 'scheduled']);
    expect(find.textContaining('Server status: scheduled'), findsOneWidget);
    expect(find.textContaining('Server status: due'), findsNothing);
  });

  testWidgets('pagination remains server page based and GET-only', (
    tester,
  ) async {
    largeScreen(tester);
    final pages = <String>[];
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      final which = request.url.queryParameters['page']!;
      pages.add(which);
      return http.Response(
        jsonEncode(
          page(
            pageNumber: int.parse(which),
            total: 21,
            items: [row(id: which == '1' ? 11 : 12)],
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

    await tester.tap(find.byKey(const ValueKey('p23f-s02-next')));
    await tester.pumpAndSettle();
    expect(pages, ['1', '2']);
    expect(find.byKey(const ValueKey('p23f-s02-row-11')), findsNothing);
    expect(find.byKey(const ValueKey('p23f-s02-row-12')), findsOneWidget);
    expect(find.textContaining('Page 2'), findsOneWidget);
    final next = tester.widget<IconButton>(
      find.byKey(const ValueKey('p23f-s02-next')),
    );
    expect(next.onPressed, isNull);
  });

  testWidgets(
    'detail readback is server owned and never exposes mutation controls',
    (tester) async {
      largeScreen(tester);
      var detailReads = 0;
      final client = MockClient((request) async {
        expect(request.method, 'GET');
        if (request.url.path == '/api/v1/emar/11') {
          detailReads++;
          return http.Response(jsonEncode({'administration': row()}), 200);
        }
        return http.Response(jsonEncode(page()), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(app(source(client, guard)));
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const ValueKey('p23f-s02-row-11')));
      await tester.pumpAndSettle();
      expect(detailReads, 1);
      expect(find.byKey(const ValueKey('p23f-s02-detail')), findsOneWidget);
      expect(find.text('Server detail: administration #11'), findsOneWidget);
      expect(
        find.textContaining('not available in this read-only workspace.'),
        findsOneWidget,
      );
    },
  );

  testWidgets('wrong patient detail is denied and not rendered', (
    tester,
  ) async {
    largeScreen(tester);
    final client = MockClient((request) async {
      if (request.url.path.endsWith('/11')) {
        return http.Response(
          jsonEncode({'administration': row(patientId: 99)}),
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

    await tester.tap(find.byKey(const ValueKey('p23f-s02-row-11')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23f-s02-detail')), findsNothing);
    expect(find.byKey(const ValueKey('p23f-s02-detail-error')), findsOneWidget);
  });

  testWidgets(
    'session invalidation clears PHI and cannot refresh old context',
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
      expect(find.text('Prescription item #19'), findsOneWidget);

      guard.markSignedOut();
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('p23f-s02-session-unavailable')),
        findsOneWidget,
      );
      expect(find.text('Prescription item #19'), findsNothing);
      expect(find.byKey(const ValueKey('p23f-s02-refresh')), findsOneWidget);
      expect(
        tester
            .widget<IconButton>(find.byKey(const ValueKey('p23f-s02-refresh')))
            .onPressed,
        isNull,
      );
      expect(calls, 1);
    },
  );

  testWidgets('pending GET completing after invalidation never displays PHI', (
    tester,
  ) async {
    largeScreen(tester);
    final pending = Completer<http.Response>();
    final client = MockClient((_) => pending.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(source(client, guard)));
    await tester.pump();
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(page()), 200));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('p23f-s02-session-unavailable')),
      findsOneWidget,
    );
    expect(find.text('Prescription item #19'), findsNothing);
  });

  testWidgets('HTTP denial is error, not an empty medication schedule', (
    tester,
  ) async {
    largeScreen(tester);
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode({'error': 'private backend error'}), 403),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(source(client, guard)));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23f-s02-error')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23f-s02-empty')), findsNothing);
    expect(find.textContaining('private backend error'), findsNothing);
  });

  testWidgets('cross-patient list response is fail-closed', (tester) async {
    largeScreen(tester);
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode(page(items: [row(patientId: 900)])), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(source(client, guard)));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23f-s02-error')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23f-s02-row-11')), findsNothing);
  });
}
