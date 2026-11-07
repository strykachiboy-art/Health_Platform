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
import 'package:clinic_system_pro/features/patients/data/patient_vitals_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_vitals_history_card.dart';

AuthSessionState _ready({String role = 'doctor', int clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 's03-vitals-token',
        refreshToken: 's03-vitals-refresh',
        userId: 42,
        clinicContextId: clinicId,
        role: role,
      ),
    );

Map<String, dynamic> _vitals({
  int id = 11,
  int patientId = 7,
  String temperature = '36.70',
}) => {
  'id': id,
  'patient_id': patientId,
  'consultation_id': null,
  'recorded_by_id': 14,
  'temperature': temperature,
  'blood_pressure_systolic': 120,
  'blood_pressure_diastolic': 80,
  'heart_rate': 72,
  'respiratory_rate': 16,
  'oxygen_saturation': '98.00',
  'weight': '70.50',
  'height': '175.00',
  'recorded_at': '2026-11-06T09:00:00+00:00',
};

Map<String, dynamic> _history({
  List<Map<String, dynamic>>? items,
  int? total,
  int page = 1,
  int perPage = 20,
}) {
  final rows = items ?? [_vitals()];

  return {
    'success': true,
    'data': {
      'items': rows,
      'total': total ?? rows.length,
      'page': page,
      'per_page': perPage,
    },
  };
}

PatientVitalsRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
}) => PatientVitalsRemoteDataSource(
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
  PatientVitalsRemoteDataSource source, {
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
      child: PatientVitalsHistoryCard(
        patientId: patientId,
        dataSource: source,
        pageSize: pageSize,
      ),
    ),
  ),
);

void main() {
  testWidgets('latest and history use separate GET contracts', (tester) async {
    final paths = <String>[];

    final client = MockClient((request) async {
      paths.add(request.url.path);
      expect(request.method, 'GET');
      expect(request.headers['authorization'], 'Bearer s03-vitals-token');

      if (request.url.path.endsWith('/latest')) {
        return http.Response(
          jsonEncode({'success': true, 'data': _vitals()}),
          200,
        );
      }

      expect(request.url.queryParameters, {'page': '1', 'per_page': '20'});

      return http.Response(jsonEncode(_history()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(paths, [
      '/api/v1/patients/7/vitals/latest',
      '/api/v1/patients/7/vitals',
    ]);

    expect(
      find.byKey(const ValueKey('patient-vitals-latest-observation')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-vitals-history-11')),
      findsOneWidget,
    );

    expect(find.text('Temperature: 36.70 °C'), findsNWidgets(2));
    expect(find.text('Oxygen saturation: 98.00%'), findsNWidgets(2));
    expect(find.text('Blood pressure: 120/80 mmHg'), findsNWidgets(2));
    expect(tester.takeException(), isNull);
  });

  testWidgets('explicit null latest and empty history are distinct', (
    tester,
  ) async {
    final client = MockClient((request) async {
      if (request.url.path.endsWith('/latest')) {
        return http.Response('{"success":true,"data":null}', 200);
      }

      return http.Response(jsonEncode(_history(items: [], total: 0)), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-vitals-latest-empty')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-vitals-history-empty')),
      findsOneWidget,
    );
    expect(find.textContaining('not proof'), findsOneWidget);
  });

  testWidgets('pagination loads a second page without another latest GET', (
    tester,
  ) async {
    var latestGets = 0;
    final requestedPages = <String>[];

    final client = MockClient((request) async {
      if (request.url.path.endsWith('/latest')) {
        latestGets++;
        return http.Response(
          jsonEncode({'success': true, 'data': _vitals()}),
          200,
        );
      }

      final page = request.url.queryParameters['page']!;
      requestedPages.add(page);
      expect(request.url.queryParameters['per_page'], '1');

      final row = page == '1'
          ? _vitals()
          : _vitals(id: 12, temperature: '37.10');

      return http.Response(
        jsonEncode(
          _history(items: [row], total: 2, page: int.parse(page), perPage: 1),
        ),
        200,
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard), pageSize: 1));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-vitals-load-more')),
      findsOneWidget,
    );

    // Scroll to the button before a real pointer tap.
    final loadMore = find.byKey(const ValueKey('patient-vitals-load-more'));

    await tester.ensureVisible(loadMore);
    await tester.pumpAndSettle();

    await tester.tap(loadMore);
    await tester.pumpAndSettle();

    expect(latestGets, 1);
    expect(requestedPages, ['1', '2']);
    expect(
      find.byKey(const ValueKey('patient-vitals-history-11')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-vitals-history-12')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-vitals-load-more')),
      findsNothing,
    );
  });

  testWidgets('403 clears vitals and displays an access failure', (
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

    expect(find.byKey(const ValueKey('patient-vitals-error')), findsOneWidget);
    expect(
      find.byKey(const ValueKey('patient-vitals-latest-observation')),
      findsNothing,
    );
  });

  testWidgets('patient role makes no vitals requests', (tester) async {
    var requests = 0;

    final client = MockClient((_) async {
      requests++;
      return http.Response(
        jsonEncode({'success': true, 'data': _vitals()}),
        200,
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard, role: 'patient')));
    await tester.pumpAndSettle();

    expect(requests, 0);
    expect(find.byKey(const ValueKey('patient-vitals-error')), findsOneWidget);
  });

  testWidgets('session invalidation clears displayed vitals', (tester) async {
    final client = MockClient((request) async {
      if (request.url.path.endsWith('/latest')) {
        return http.Response(
          jsonEncode({'success': true, 'data': _vitals()}),
          200,
        );
      }

      return http.Response(jsonEncode(_history()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.text('Temperature: 36.70 °C'), findsNWidgets(2));

    guard.invalidate();
    await tester.pump();

    expect(find.text('Temperature: 36.70 °C'), findsNothing);
    expect(find.byKey(const ValueKey('patient-vitals-error')), findsOneWidget);
  });

  testWidgets('failed refresh discards previously displayed vitals', (
    tester,
  ) async {
    var latestGets = 0;

    final client = MockClient((request) async {
      if (request.url.path.endsWith('/latest')) {
        latestGets++;

        if (latestGets == 2) {
          return http.Response('{"error":"Unavailable"}', 503);
        }

        return http.Response(
          jsonEncode({'success': true, 'data': _vitals()}),
          200,
        );
      }

      return http.Response(jsonEncode(_history()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.text('Temperature: 36.70 °C'), findsNWidgets(2));

    await tester.tap(find.byKey(const ValueKey('patient-vitals-refresh')));
    await tester.pumpAndSettle();

    expect(latestGets, 2);
    expect(find.text('Temperature: 36.70 °C'), findsNothing);
    expect(find.byKey(const ValueKey('patient-vitals-error')), findsOneWidget);
  });

  testWidgets('switching patient discards an in-flight latest record', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    var oldStarted = false;

    final client = MockClient((request) {
      if (request.url.path == '/api/v1/patients/7/vitals/latest') {
        oldStarted = true;
        return pending.future;
      }

      if (request.url.path == '/api/v1/patients/8/vitals/latest') {
        return Future.value(
          http.Response(
            jsonEncode({
              'success': true,
              'data': _vitals(patientId: 8, id: 21, temperature: '37.20'),
            }),
            200,
          ),
        );
      }

      if (request.url.path == '/api/v1/patients/8/vitals') {
        return Future.value(
          http.Response(
            jsonEncode(
              _history(
                items: [_vitals(patientId: 8, id: 21, temperature: '37.20')],
              ),
            ),
            200,
          ),
        );
      }

      throw StateError('Unexpected request: ${request.url}');
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

    expect(find.text('Temperature: 37.20 °C'), findsNWidgets(2));
    expect(find.text('Temperature: 36.70 °C'), findsNothing);

    pending.complete(
      http.Response(jsonEncode({'success': true, 'data': _vitals()}), 200),
    );

    await tester.pumpAndSettle();

    expect(find.text('Temperature: 37.20 °C'), findsNWidgets(2));
    expect(find.text('Temperature: 36.70 °C'), findsNothing);
  });

  for (final width in <double>[390, 1100]) {
    testWidgets('${width.toInt()}px vitals layout supports enlarged text', (
      tester,
    ) async {
      tester.view.physicalSize = Size(width, 1000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      final client = MockClient((request) async {
        if (request.url.path.endsWith('/latest')) {
          return http.Response(
            jsonEncode({'success': true, 'data': _vitals()}),
            200,
          );
        }

        return http.Response(jsonEncode(_history()), 200);
      });
      addTearDown(client.close);

      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);

      await tester.pumpWidget(_host(_source(client, guard), scale: 1.25));
      await tester.pumpAndSettle();

      expect(find.byKey(const ValueKey('patient-vitals-card')), findsOneWidget);
      expect(find.byTooltip('Refresh patient vitals'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }
}
