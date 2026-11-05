import 'dart:async';
import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/presentation/clinical_alert_inbox_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _timestamp = '2026-11-04T12:30:00+00:00';

Map<String, dynamic> _alert({
  int id = 19,
  String severity = 'critical',
  String status = 'open',
  String title = 'Medication warning',
}) => <String, dynamic>{
  'id': id,
  'clinic_id': 3,
  'patient_id': 7,
  'rule_id': 12,
  'rule_version': 2,
  'severity': severity,
  'action': 'block',
  'status': status,
  'title': title,
  'message': 'Clinical medication review is required.',
  'context': <String, dynamic>{'medication_id': 5},
  'source_type': 'prescription',
  'source_id': 9,
  'deduplication_key': 'alert-$id',
  'generated_at': _timestamp,
  'resolved_at': status == 'resolved' ? _timestamp : null,
  'created_at': _timestamp,
  'updated_at': _timestamp,
};

Map<String, dynamic> _page({List<Map<String, dynamic>>? items, int? total}) {
  final results = items ?? <Map<String, dynamic>>[_alert()];
  return <String, dynamic>{
    'items': results,
    'total': total ?? results.length,
    'page': 1,
    'per_page': 50,
  };
}

http.Response _response(Map<String, dynamic> json, {int status = 200}) =>
    http.Response(jsonEncode(json), status);

ClinicalSafetyRemoteDataSource _source(
  MockClient client, {
  AuthenticatedAccessTokenLoader? tokenLoader,
}) => ClinicalSafetyRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadAccessToken: tokenLoader ?? () async => 'staff-test-token',
);

Future<void> _mount(
  WidgetTester tester,
  ClinicalSafetyRemoteDataSource source,
) async {
  await tester.pumpWidget(
    MaterialApp(home: ClinicalAlertInboxScreen(dataSource: source)),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('inbox loads GET-only data with bearer session and no query', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/clinical-safety/alerts');
      expect(request.url.hasQuery, isFalse);
      expect(request.headers['authorization'], 'Bearer staff-test-token');
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      return _response(_page());
    });
    addTearDown(client.close);

    await _mount(tester, _source(client));
    expect(calls, 1);
    expect(
      find.byKey(const ValueKey('clinical-alert-inbox-list')),
      findsOneWidget,
    );
    expect(find.text('Medication warning'), findsOneWidget);
    expect(find.text('Patient ID: 7'), findsOneWidget);
    expect(find.text('Status: Open'), findsOneWidget);
    expect(find.text('Critical'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('clinical-alert-inbox-critical-warning')),
      findsOneWidget,
    );
  });

  testWidgets('in-flight initial request displays loading indicator', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    final client = MockClient((request) => pending.future);
    addTearDown(client.close);

    await tester.pumpWidget(
      MaterialApp(home: ClinicalAlertInboxScreen(dataSource: _source(client))),
    );
    await tester.pump();
    expect(
      find.byKey(const ValueKey('clinical-alert-inbox-loading')),
      findsOneWidget,
    );
    expect(find.text('Medication warning'), findsNothing);

    pending.complete(_response(_page()));
    await tester.pumpAndSettle();
    expect(find.text('Medication warning'), findsOneWidget);
  });

  testWidgets('empty first page has explicit backend-confirmed empty state', (
    tester,
  ) async {
    final client = MockClient(
      (request) async => _response(_page(items: <Map<String, dynamic>>[])),
    );
    addTearDown(client.close);

    await _mount(tester, _source(client));
    expect(
      find.text('No clinical alerts returned by the backend.'),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('clinical-alert-inbox-list')),
      findsNothing,
    );
  });

  testWidgets('inconsistent empty page does not claim there are no alerts', (
    tester,
  ) async {
    final client = MockClient(
      (request) async =>
          _response(_page(items: <Map<String, dynamic>>[], total: 10)),
    );
    addTearDown(client.close);

    await _mount(tester, _source(client));
    expect(find.textContaining('reports 10 alerts'), findsOneWidget);
  });

  testWidgets('inbox warns about undisplayed additional backend pages', (
    tester,
  ) async {
    final client = MockClient((request) async => _response(_page(total: 62)));
    addTearDown(client.close);

    await _mount(tester, _source(client));
    expect(find.text('Showing 1 of 62 alerts (first page)'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('clinical-alert-inbox-partial-warning')),
      findsOneWidget,
    );
  });

  testWidgets('acknowledged critical alert is not misrepresented as resolved', (
    tester,
  ) async {
    final client = MockClient(
      (request) async => _response(
        _page(items: <Map<String, dynamic>>[_alert(status: 'acknowledged')]),
      ),
    );
    addTearDown(client.close);

    await _mount(tester, _source(client));
    expect(find.text('Status: Acknowledged'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('clinical-alert-inbox-critical-warning')),
      findsOneWidget,
    );
  });

  testWidgets('resolved critical alert is not presented as unresolved', (
    tester,
  ) async {
    final client = MockClient(
      (request) async => _response(
        _page(items: <Map<String, dynamic>>[_alert(status: 'resolved')]),
      ),
    );
    addTearDown(client.close);

    await _mount(tester, _source(client));
    expect(find.text('Status: Resolved'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('clinical-alert-inbox-critical-warning')),
      findsNothing,
    );
  });

  testWidgets('refresh clears clinical details immediately until confirmed', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      if (calls == 1) return _response(_page());
      return pending.future;
    });
    addTearDown(client.close);

    await _mount(tester, _source(client));
    expect(find.text('Medication warning'), findsOneWidget);

    await tester.tap(
      find.byKey(const ValueKey('clinical-alert-inbox-refresh')),
    );
    await tester.pump();
    expect(find.text('Medication warning'), findsNothing);
    expect(
      find.byKey(const ValueKey('clinical-alert-inbox-loading')),
      findsOneWidget,
    );

    pending.complete(
      _response(
        _page(
          items: <Map<String, dynamic>>[
            _alert(id: 20, severity: 'high', title: 'Updated warning'),
          ],
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Medication warning'), findsNothing);
    expect(find.text('Updated warning'), findsOneWidget);
    expect(calls, 2);
  });

  testWidgets('503 refresh clears old data and online retry recovers', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      if (calls == 2) {
        return _response(<String, dynamic>{
          'error': 'unavailable',
        }, status: 503);
      }
      return _response(_page());
    });
    addTearDown(client.close);

    await _mount(tester, _source(client));
    expect(find.text('Medication warning'), findsOneWidget);
    await tester.tap(
      find.byKey(const ValueKey('clinical-alert-inbox-refresh')),
    );
    await tester.pumpAndSettle();

    expect(find.text('Medication warning'), findsNothing);
    expect(find.textContaining('HTTP 503'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('clinical-alert-inbox-unconfirmed')),
      findsOneWidget,
    );

    await tester.tap(find.byKey(const ValueKey('clinical-alert-inbox-retry')));
    await tester.pumpAndSettle();
    expect(find.text('Medication warning'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('clinical-alert-inbox-unconfirmed')),
      findsNothing,
    );
    expect(calls, 3);
  });

  for (final code in <int>[401, 403]) {
    testWidgets('HTTP $code refresh does not retain protected alerts', (
      tester,
    ) async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        if (calls == 1) return _response(_page());
        return _response(<String, dynamic>{
          'error': 'access rejected',
        }, status: code);
      });
      addTearDown(client.close);

      await _mount(tester, _source(client));
      await tester.tap(
        find.byKey(const ValueKey('clinical-alert-inbox-refresh')),
      );
      await tester.pumpAndSettle();

      expect(find.text('Medication warning'), findsNothing);
      expect(find.textContaining('HTTP $code'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('clinical-alert-inbox-unconfirmed')),
        findsOneWidget,
      );
      if (code == 401) {
        expect(
          find.byKey(const ValueKey('clinical-alert-inbox-retry')),
          findsNothing,
        );
      }
    });
  }

  testWidgets('signed-out session fails before HTTP and displays no data', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      return _response(_page());
    });
    addTearDown(client.close);

    await _mount(
      tester,
      _source(
        client,
        tokenLoader: () async {
          throw const AuthenticatedSessionUnavailableException(
            AuthenticatedSessionAccessFailure.signedOut,
          );
        },
      ),
    );

    expect(calls, 0);
    expect(find.textContaining('Sign in again'), findsOneWidget);
    expect(find.text('Medication warning'), findsNothing);
    expect(
      find.byKey(const ValueKey('clinical-alert-inbox-retry')),
      findsNothing,
    );
  });

  testWidgets('malformed backend response fails closed without alert rows', (
    tester,
  ) async {
    final client = MockClient(
      (request) async =>
          _response(<String, dynamic>{'success': true, 'data': _page()}),
    );
    addTearDown(client.close);

    await _mount(tester, _source(client));
    expect(
      find.byKey(const ValueKey('clinical-alert-inbox-unconfirmed')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('clinical-alert-inbox-list')),
      findsNothing,
    );
    expect(find.text('Medication warning'), findsNothing);
  });

  testWidgets('late result from old data source cannot replace new data', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    final oldClient = MockClient((request) => pending.future);
    final newClient = MockClient(
      (request) async => _response(
        _page(
          items: <Map<String, dynamic>>[
            _alert(id: 21, severity: 'high', title: 'Current session alert'),
          ],
        ),
      ),
    );
    addTearDown(oldClient.close);
    addTearDown(newClient.close);

    await tester.pumpWidget(
      MaterialApp(
        home: ClinicalAlertInboxScreen(dataSource: _source(oldClient)),
      ),
    );
    await tester.pump();

    await tester.pumpWidget(
      MaterialApp(
        home: ClinicalAlertInboxScreen(dataSource: _source(newClient)),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Current session alert'), findsOneWidget);

    pending.complete(_response(_page()));
    await tester.pumpAndSettle();
    expect(find.text('Current session alert'), findsOneWidget);
    expect(find.text('Medication warning'), findsNothing);
  });
}
