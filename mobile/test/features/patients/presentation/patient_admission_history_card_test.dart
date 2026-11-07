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
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_admission_history_card.dart';

AuthSessionState _ready({String role = 'doctor'}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 's03-admission-token',
    refreshToken: 's03-admission-refresh',
    userId: 42,
    clinicContextId: 3,
    role: role,
  ),
);

Map<String, dynamic> _admission({
  int id = 11,
  int patientId = 7,
  String status = 'admitted',
}) => {
  'id': id,
  'patient_id': patientId,
  'bed_id': 5,
  'admitted_by_id': 14,
  'reservation_id': null,
  'status': status,
  'reason': 'Clinical observation',
  'admitted_at': '2026-11-06T09:00:00+00:00',
  'discharged_at': status == 'discharged' ? '2026-11-07T09:00:00+00:00' : null,
  'created_at': '2026-11-06T08:59:00+00:00',
  'updated_at': '2026-11-06T09:00:00+00:00',
};

Map<String, dynamic> _page({
  List<Map<String, dynamic>>? items,
  int? total,
  int page = 1,
  int perPage = 20,
}) {
  final rows = items ?? [_admission()];

  return {
    'items': rows,
    'total': total ?? rows.length,
    'page': page,
    'per_page': perPage,
  };
}

PatientAdmissionRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
}) => PatientAdmissionRemoteDataSource(
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
  PatientAdmissionRemoteDataSource source, {
  int patientId = 7,
  int pageSize = 20,
  double scale = 1,
}) => MaterialApp(
  builder: (context, child) => MediaQuery(
    data: MediaQuery.of(context).copyWith(textScaler: TextScaler.linear(scale)),
    child: child!,
  ),
  home: Scaffold(
    body: SingleChildScrollView(
      child: PatientAdmissionHistoryCard(
        patientId: patientId,
        dataSource: source,
        pageSize: pageSize,
      ),
    ),
  ),
);

void main() {
  testWidgets('current and history use separate authenticated GETs', (
    tester,
  ) async {
    final paths = <String>[];

    final client = MockClient((request) async {
      paths.add(request.url.path);

      expect(request.method, 'GET');
      expect(request.headers['authorization'], 'Bearer s03-admission-token');

      if (request.url.path.endsWith('/current-admission')) {
        return http.Response(jsonEncode(_admission()), 200);
      }

      expect(request.url.queryParameters, {'page': '1', 'per_page': '20'});

      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(paths, [
      '/api/v1/wards/patients/7/current-admission',
      '/api/v1/wards/patients/7/admissions',
    ]);

    expect(
      find.byKey(const ValueKey('patient-current-admission-record')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-admission-history-11')),
      findsOneWidget,
    );
    expect(find.text('Status: Admitted'), findsNWidgets(2));
    expect(find.text('Bed ID: 5'), findsNWidgets(2));
    expect(tester.takeException(), isNull);
  });

  testWidgets('exact no-active 404 is distinct from empty history', (
    tester,
  ) async {
    final client = MockClient((request) async {
      if (request.url.path.endsWith('/current-admission')) {
        return http.Response('{"message":"No active admission found"}', 404);
      }

      return http.Response(jsonEncode(_page(items: [], total: 0)), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-admissions-current-empty')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-admissions-history-empty')),
      findsOneWidget,
    );
    expect(find.textContaining('does not prove'), findsOneWidget);
  });

  testWidgets('unrelated current-admission 404 remains an error', (
    tester,
  ) async {
    var historyCalls = 0;

    final client = MockClient((request) async {
      if (request.url.path.endsWith('/admissions')) {
        historyCalls++;
        return http.Response(jsonEncode(_page()), 200);
      }

      return http.Response('{"error":"Patient not found"}', 404);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(historyCalls, 0);
    expect(
      find.byKey(const ValueKey('patient-admissions-error')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-admissions-current-empty')),
      findsNothing,
    );
  });

  testWidgets('403 denies admission access without exposing records', (
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
      find.byKey(const ValueKey('patient-admissions-error')),
      findsOneWidget,
    );
    expect(find.text('Bed ID: 5'), findsNothing);
  });

  testWidgets('pagination loads page two without refreshing current', (
    tester,
  ) async {
    var currentCalls = 0;
    final pages = <String>[];

    final client = MockClient((request) async {
      if (request.url.path.endsWith('/current-admission')) {
        currentCalls++;
        return http.Response(jsonEncode(_admission()), 200);
      }

      final page = request.url.queryParameters['page']!;
      pages.add(page);
      expect(request.url.queryParameters['per_page'], '1');

      final record = page == '1'
          ? _admission()
          : _admission(id: 12, status: 'discharged');

      return http.Response(
        jsonEncode(
          _page(items: [record], total: 2, page: int.parse(page), perPage: 1),
        ),
        200,
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard), pageSize: 1));
    await tester.pumpAndSettle();

    final loadMore = find.byKey(const ValueKey('patient-admissions-load-more'));

    expect(loadMore, findsOneWidget);

    // Prevent the offscreen tap failure encountered in S03-A.
    await tester.ensureVisible(loadMore);
    await tester.pumpAndSettle();

    await tester.tap(loadMore);
    await tester.pumpAndSettle();

    expect(currentCalls, 1);
    expect(pages, ['1', '2']);

    expect(
      find.byKey(const ValueKey('patient-admission-history-11')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-admission-history-12')),
      findsOneWidget,
    );
    expect(find.text('Status: Discharged'), findsOneWidget);
    expect(loadMore, findsNothing);
  });

  testWidgets('history distinguishes admitted, discharged, transferred', (
    tester,
  ) async {
    final client = MockClient((request) async {
      if (request.url.path.endsWith('/current-admission')) {
        return http.Response(jsonEncode(_admission()), 200);
      }

      return http.Response(
        jsonEncode(
          _page(
            items: [
              _admission(id: 11),
              _admission(id: 12, status: 'discharged'),
              _admission(id: 13, status: 'transferred'),
            ],
          ),
        ),
        200,
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.text('Status: Admitted'), findsNWidgets(2));
    expect(find.text('Status: Discharged'), findsOneWidget);
    expect(find.text('Status: Transferred'), findsOneWidget);

    expect(find.textContaining('Discharged (UTC)'), findsOneWidget);
  });

  testWidgets('patient role cannot request admission data', (tester) async {
    var requests = 0;

    final client = MockClient((_) async {
      requests++;
      return http.Response(jsonEncode(_admission()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard, role: 'patient')));
    await tester.pumpAndSettle();

    expect(requests, 0);
    expect(
      find.byKey(const ValueKey('patient-admissions-error')),
      findsOneWidget,
    );
  });

  testWidgets('session invalidation clears admission information', (
    tester,
  ) async {
    final client = MockClient((request) async {
      if (request.url.path.endsWith('/current-admission')) {
        return http.Response(jsonEncode(_admission()), 200);
      }
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.text('Bed ID: 5'), findsNWidgets(2));

    guard.invalidate();
    await tester.pump();

    expect(find.text('Bed ID: 5'), findsNothing);
    expect(
      find.byKey(const ValueKey('patient-admissions-error')),
      findsOneWidget,
    );
  });

  testWidgets('failed refresh removes previously visible admissions', (
    tester,
  ) async {
    var currentCalls = 0;

    final client = MockClient((request) async {
      if (request.url.path.endsWith('/current-admission')) {
        currentCalls++;

        if (currentCalls == 2) {
          return http.Response('{"error":"Unavailable"}', 503);
        }

        return http.Response(jsonEncode(_admission()), 200);
      }

      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.text('Bed ID: 5'), findsNWidgets(2));

    final refresh = find.byKey(const ValueKey('patient-admissions-refresh'));

    await tester.ensureVisible(refresh);
    await tester.pumpAndSettle();

    await tester.tap(refresh);
    await tester.pumpAndSettle();

    expect(currentCalls, 2);
    expect(find.text('Bed ID: 5'), findsNothing);
    expect(
      find.byKey(const ValueKey('patient-admissions-error')),
      findsOneWidget,
    );
  });

  testWidgets('switching patient discards an old pending response', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    var oldStarted = false;

    final client = MockClient((request) {
      final path = request.url.path;

      if (path == '/api/v1/wards/patients/7/current-admission') {
        oldStarted = true;
        return pending.future;
      }

      if (path == '/api/v1/wards/patients/8/current-admission') {
        return Future.value(
          http.Response(jsonEncode(_admission(id: 21, patientId: 8)), 200),
        );
      }

      if (path == '/api/v1/wards/patients/8/admissions') {
        return Future.value(
          http.Response(
            jsonEncode(_page(items: [_admission(id: 21, patientId: 8)])),
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

    expect(find.text('Admission ID: 21'), findsNWidgets(2));
    expect(find.text('Admission ID: 11'), findsNothing);

    pending.complete(http.Response(jsonEncode(_admission()), 200));

    await tester.pumpAndSettle();

    expect(find.text('Admission ID: 21'), findsNWidgets(2));
    expect(find.text('Admission ID: 11'), findsNothing);
  });

  for (final width in <double>[390, 1100]) {
    testWidgets('${width.toInt()}px admissions layout supports enlarged text', (
      tester,
    ) async {
      tester.view.physicalSize = Size(width, 1000);
      tester.view.devicePixelRatio = 1;

      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      final client = MockClient((request) async {
        if (request.url.path.endsWith('/current-admission')) {
          return http.Response(jsonEncode(_admission()), 200);
        }

        return http.Response(jsonEncode(_page()), 200);
      });
      addTearDown(client.close);

      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);

      await tester.pumpWidget(_host(_source(client, guard), scale: 1.25));
      await tester.pumpAndSettle();

      expect(
        find.byKey(const ValueKey('patient-admissions-card')),
        findsOneWidget,
      );

      expect(find.byTooltip('Refresh patient admissions'), findsOneWidget);

      expect(tester.takeException(), isNull);
    });
  }
}
