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
import 'package:clinic_system_pro/features/patients/data/patient_clinical_alert_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_clinical_alerts_card.dart';

AuthSessionState _ready({String role = 'doctor'}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 's04-alert-token',
    refreshToken: 's04-alert-refresh',
    userId: 42,
    clinicContextId: 3,
    role: role,
  ),
);

Map<String, dynamic> _alert({
  int id = 11,
  int patientId = 7,
  String status = 'open',
}) => {
  'id': id,
  'clinic_id': 3,
  'patient_id': patientId,
  'rule_id': 20,
  'rule_version': 2,
  'severity': 'critical',
  'action': 'block',
  'status': status,
  'title': 'Clinical safety alert',
  'message': 'Server-reported clinical risk.',
  'context': <String, dynamic>{},
  'source_type': 'prescription',
  'source_id': 41,
  'deduplication_key': null,
  'generated_at': '2026-11-06T09:00:00+00:00',
  'resolved_at': status == 'resolved' ? '2026-11-06T10:00:00+00:00' : null,
  'created_at': '2026-11-06T09:00:00+00:00',
  'updated_at': '2026-11-06T09:00:00+00:00',
};

Map<String, dynamic> _page({
  List<Map<String, dynamic>>? items,
  int? total,
  int page = 1,
  int perPage = 20,
}) {
  final rows = items ?? [_alert()];
  return {
    'items': rows,
    'total': total ?? rows.length,
    'page': page,
    'per_page': perPage,
  };
}

PatientClinicalAlertRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
}) => PatientClinicalAlertRemoteDataSource(
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
  PatientClinicalAlertRemoteDataSource source, {
  int patientId = 7,
  int pageSize = 20,
}) => MaterialApp(
  home: Scaffold(
    body: SingleChildScrollView(
      child: PatientClinicalAlertsCard(
        patientId: patientId,
        pageSize: pageSize,
        dataSource: source,
      ),
    ),
  ),
);

void main() {
  testWidgets('critical alert retains server status and blocking action', (
    tester,
  ) async {
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.url.queryParameters['patient_id'], '7');
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.text('Severity: Critical'), findsOneWidget);
    expect(find.text('Action: Block'), findsOneWidget);
    expect(find.text('Status: Open'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('patient-alert-critical-warning')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-alerts-no-clearance-warning')),
      findsOneWidget,
    );
  });

  testWidgets('empty alert page never implies clinical clearance', (
    tester,
  ) async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page(items: [], total: 0)), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('patient-alerts-empty')), findsOneWidget);
    expect(
      find.byKey(const ValueKey('patient-alerts-no-clearance-warning')),
      findsOneWidget,
    );
  });

  testWidgets('pagination preserves both patient-scoped alert rows', (
    tester,
  ) async {
    final pages = <String>[];
    final client = MockClient((request) async {
      final page = request.url.queryParameters['page']!;
      pages.add(page);
      return http.Response(
        jsonEncode(
          _page(
            items: [page == '1' ? _alert() : _alert(id: 12)],
            total: 2,
            page: int.parse(page),
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

    final more = find.byKey(const ValueKey('patient-alerts-load-more'));
    await tester.ensureVisible(more);
    await tester.pumpAndSettle();
    await tester.tap(more);
    await tester.pumpAndSettle();

    expect(pages, ['1', '2']);
    expect(
      find.byKey(const ValueKey('patient-clinical-alert-11')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-clinical-alert-12')),
      findsOneWidget,
    );
    expect(more, findsNothing);
  });

  testWidgets('403 is an access error and hides alert data', (tester) async {
    final client = MockClient(
      (_) async => http.Response('{"error":"Forbidden"}', 403),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('patient-alerts-error')), findsOneWidget);
    expect(find.text('Server-reported clinical risk.'), findsNothing);
  });

  testWidgets('unauthorized role sends no clinical alert GET', (tester) async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard, role: 'patient')));
    await tester.pumpAndSettle();

    expect(requests, 0);
    expect(find.byKey(const ValueKey('patient-alerts-error')), findsOneWidget);
  });

  testWidgets('session invalidation clears clinical alert PHI', (tester) async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page()), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.text('Server-reported clinical risk.'), findsOneWidget);
    guard.invalidate();
    await tester.pump();

    expect(find.text('Server-reported clinical risk.'), findsNothing);
    expect(find.byKey(const ValueKey('patient-alerts-error')), findsOneWidget);
  });

  testWidgets('failed alert refresh clears previously visible data', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      if (calls == 2) return http.Response('{"error":"Unavailable"}', 503);
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    final refresh = find.byKey(const ValueKey('patient-alerts-refresh'));
    await tester.ensureVisible(refresh);
    await tester.pumpAndSettle();
    await tester.tap(refresh);
    await tester.pumpAndSettle();

    expect(calls, 2);
    expect(find.text('Server-reported clinical risk.'), findsNothing);
    expect(find.byKey(const ValueKey('patient-alerts-error')), findsOneWidget);
  });

  testWidgets('wrong patient alert record fails closed', (tester) async {
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode(_page(items: [_alert(patientId: 99)])), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('patient-alerts-error')), findsOneWidget);
    expect(find.text('Server-reported clinical risk.'), findsNothing);
  });

  testWidgets('patient switch rejects a pending old alert page', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    final client = MockClient((request) {
      if (request.url.queryParameters['patient_id'] == '7') {
        return pending.future;
      }
      return Future.value(
        http.Response(
          jsonEncode(_page(items: [_alert(id: 21, patientId: 8)])),
          200,
        ),
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);

    await tester.pumpWidget(_host(source));
    await tester.pump();
    await tester.pumpWidget(_host(source, patientId: 8));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-clinical-alert-21')),
      findsOneWidget,
    );

    pending.complete(http.Response(jsonEncode(_page()), 200));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-clinical-alert-21')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-clinical-alert-11')),
      findsNothing,
    );
  });
}
