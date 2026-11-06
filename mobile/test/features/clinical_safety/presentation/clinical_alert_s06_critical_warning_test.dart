import 'dart:async';
import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/models/clinical_alert_acknowledgement.dart';
import 'package:clinic_system_pro/features/clinical_safety/presentation/clinical_alert_detail_screen.dart';
import 'package:clinic_system_pro/features/clinical_safety/presentation/clinical_alert_inbox_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _ts = '2026-11-05T12:00:00+00:00';
Map<String, dynamic> _alert({String status = 'open'}) => <String, dynamic>{
  'id': 19,
  'clinic_id': 3,
  'patient_id': 7,
  'rule_id': 12,
  'rule_version': 2,
  'severity': 'critical',
  'action': 'block',
  'status': status,
  'title': 'SENSITIVE_ALERT_TITLE',
  'message': 'Clinical safety review',
  'context': <String, dynamic>{},
  'source_type': 'prescription',
  'source_id': 7,
  'deduplication_key': 'private-key',
  'generated_at': _ts,
  'resolved_at': status == 'resolved' ? _ts : null,
  'created_at': _ts,
  'updated_at': _ts,
};
http.Response _inbox({String status = 'open'}) => http.Response(
  jsonEncode(<String, dynamic>{
    'items': <Map<String, dynamic>>[_alert(status: status)],
    'total': 1,
    'page': 1,
    'per_page': 50,
  }),
  200,
);
http.Response _detail({String status = 'open'}) => http.Response(
  jsonEncode(<String, dynamic>{'alert': _alert(status: status)}),
  200,
);

ClinicalSafetyRemoteDataSource _source(http.Client client) =>
    ClinicalSafetyRemoteDataSource(
      apiClient: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      ),
      loadAccessToken: () async => 'staff-token',
    );

void main() {
  testWidgets('S06.G05 inbox retains generic critical caution across 503', (
    tester,
  ) async {
    var calls = 0;
    final pending = Completer<http.Response>();
    final client = MockClient((_) {
      calls++;
      if (calls == 1) return Future<http.Response>.value(_inbox());
      if (calls == 2) return pending.future;
      return Future<http.Response>.value(_inbox(status: 'resolved'));
    });
    addTearDown(client.close);
    await tester.pumpWidget(MaterialApp(
      home: ClinicalAlertInboxScreen(dataSource: _source(client)),
    ));
    await tester.pumpAndSettle();
    expect(find.text('SENSITIVE_ALERT_TITLE'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('clinical-alert-inbox-refresh')));
    await tester.pump();
    expect(find.text('SENSITIVE_ALERT_TITLE'), findsNothing);
    expect(find.byKey(const ValueKey('clinical-alert-inbox-prior-critical-unconfirmed')), findsOneWidget);
    pending.complete(http.Response('{"message":"private"}', 503));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('clinical-alert-inbox-prior-critical-unconfirmed')), findsOneWidget);
    expect(find.text('SENSITIVE_ALERT_TITLE'), findsNothing);
    await tester.tap(find.byKey(const ValueKey('clinical-alert-inbox-retry')));
    await tester.pumpAndSettle();
    expect(find.text('Status: Resolved'), findsOneWidget);
    expect(find.byKey(const ValueKey('clinical-alert-inbox-prior-critical-unconfirmed')), findsNothing);
    expect(calls, 3);
  });

  testWidgets('S06.G05 detail retains generic caution then clears on resolution', (
    tester,
  ) async {
    var calls = 0;
    final pending = Completer<http.Response>();
    final client = MockClient((_) {
      calls++;
      if (calls == 1) return Future<http.Response>.value(_detail());
      if (calls == 2) return pending.future;
      return Future<http.Response>.value(_detail(status: 'resolved'));
    });
    addTearDown(client.close);
    await tester.pumpWidget(MaterialApp(
      home: ClinicalAlertDetailScreen(dataSource: _source(client), alertId: 19),
    ));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('clinical-alert-detail-refresh')));
    await tester.pump();
    expect(find.byKey(const ValueKey('clinical-alert-detail-prior-critical-unconfirmed')), findsOneWidget);
    expect(find.text('SENSITIVE_ALERT_TITLE'), findsNothing);
    pending.complete(http.Response('{"error":"private"}', 503));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('clinical-alert-detail-prior-critical-unconfirmed')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('clinical-alert-detail-retry')));
    await tester.pumpAndSettle();
    expect(find.text('Status: Resolved'), findsOneWidget);
    expect(find.byKey(const ValueKey('clinical-alert-detail-prior-critical-unconfirmed')), findsNothing);
  });

  testWidgets('S06.G02/G05 logout deletes same-session critical observation', (
    tester,
  ) async {
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    final pending = Completer<http.Response>();
    var calls = 0;
    final client = MockClient((_) {
      calls++;
      if (calls == 1) return Future<http.Response>.value(_inbox());
      return pending.future;
    });
    addTearDown(client.close);
    await tester.pumpWidget(MaterialApp(
      home: ClinicalAlertInboxScreen(
        dataSource: _source(client),
        sessionNavigationGuard: guard,
      ),
    ));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('clinical-alert-inbox-refresh')));
    await tester.pump();
    expect(find.byKey(const ValueKey('clinical-alert-inbox-prior-critical-unconfirmed')), findsOneWidget);
    guard.markSignedOut();
    await tester.pump();
    expect(find.byKey(const ValueKey('clinical-alert-inbox-prior-critical-unconfirmed')), findsNothing);
    pending.complete(_inbox());
    await tester.pumpAndSettle();
    expect(find.text('SENSITIVE_ALERT_TITLE'), findsNothing);
  });

  test('S06.G06 clinical mutation errors never trigger automatic resend', () async {
    var posts = 0;
    final client = MockClient((request) async {
      expect(request.method, 'POST');
      posts++;
      throw http.ClientException('offline', request.url);
    });
    addTearDown(client.close);
    final source = _source(client);
    await expectLater(
      source.acknowledgeAlert(
        alertId: 19,
        type: ClinicalAlertAcknowledgementType.acknowledged,
      ),
      throwsA(isA<ApiException>().having(
        (error) => error.kind,
        'failure kind',
        ApiFailureKind.connection,
      )),
    );
    await expectLater(
      source.resolveAlert(alertId: 19),
      throwsA(isA<ApiException>().having(
        (error) => error.kind,
        'failure kind',
        ApiFailureKind.connection,
      )),
    );
    expect(posts, 2, reason: 'One request for each distinct operation, no retries.');
  });
}
