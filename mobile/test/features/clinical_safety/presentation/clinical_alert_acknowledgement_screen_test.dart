import 'dart:async';
import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/presentation/clinical_alert_detail_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _ts = '2026-11-05T10:00:00+00:00';

Map<String, dynamic> _alert({
  int id = 19,
  String status = 'open',
  String severity = 'critical',
  String action = 'block',
  String title = 'Medication warning',
}) => <String, dynamic>{
  'id': id,
  'clinic_id': 3,
  'patient_id': 7,
  'rule_id': 12,
  'rule_version': 2,
  'severity': severity,
  'action': action,
  'status': status,
  'title': title,
  'message': 'Medication safety review required.',
  'context': <String, dynamic>{'private_note': 'NEVER_RENDER_THIS'},
  'source_type': 'prescription',
  'source_id': 8,
  'deduplication_key': 'NEVER_RENDER_DEDUP',
  'generated_at': _ts,
  'resolved_at': status == 'resolved' ? _ts : null,
  'created_at': _ts,
  'updated_at': _ts,
};

Map<String, dynamic> _ack({
  String type = 'acknowledged',
  int alertId = 19,
  String? justification,
}) => <String, dynamic>{
  'id': 41,
  'alert_id': alertId,
  'acknowledged_by_user_id': 42,
  'acknowledgement_type': type,
  'justification': justification,
  'acknowledged_at': _ts,
  'created_at': _ts,
  'updated_at': _ts,
};

http.Response _detail([Map<String, dynamic>? value]) => http.Response(
  jsonEncode(<String, dynamic>{'alert': value ?? _alert()}),
  200,
);

http.Response _posted({String type = 'acknowledged', String? justification}) =>
    http.Response(
      jsonEncode(<String, dynamic>{
        'message': 'Clinical alert acknowledgement recorded',
        'acknowledgement': _ack(type: type, justification: justification),
      }),
      201,
    );

http.Response _httpError(int code) => http.Response(
  jsonEncode(<String, dynamic>{'message': 'SERVER_PRIVATE_INFORMATION'}),
  code,
);

ClinicalSafetyRemoteDataSource _source(
  MockClient client, {
  AuthenticatedAccessTokenLoader? tokenLoader,
  void Function()? onRejected,
}) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  )..onAuthenticationRejected = onRejected;
  return ClinicalSafetyRemoteDataSource(
    apiClient: api,
    loadAccessToken: tokenLoader ?? () async => 'staff-test-token',
  );
}

Future<void> _mount(
  WidgetTester tester,
  ClinicalSafetyRemoteDataSource source, {
  int alertId = 19,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: ClinicalAlertDetailScreen(dataSource: source, alertId: alertId),
    ),
  );
  await tester.pumpAndSettle();
}

Future<void> _toButton(WidgetTester tester, String key) async {
  await tester.scrollUntilVisible(
    find.byKey(ValueKey(key)),
    180,
    scrollable: find.byType(Scrollable).first,
  );
  await tester.pump();
}

Future<void> _submitAck(WidgetTester tester) async {
  const key = 'clinical-alert-acknowledge-submit';
  await _toButton(tester, key);
  await tester.tap(find.byKey(const ValueKey(key)));
  await tester.pump();
}

Future<void> _submitOverride(WidgetTester tester, {String? reason}) async {
  const input = 'clinical-alert-acknowledgement-justification';
  await _toButton(tester, input);
  if (reason != null) {
    await tester.enterText(find.byKey(const ValueKey(input)), reason);
  }
  const key = 'clinical-alert-override-submit';
  await _toButton(tester, key);
  await tester.tap(find.byKey(const ValueKey(key)));
  await tester.pump();
}

void main() {
  testWidgets('initial detail GET never writes automatically', (tester) async {
    var gets = 0;
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        gets++;
        return _detail();
      }
      posts++;
      return _posted();
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    expect(gets, 1);
    expect(posts, 0);
    expect(find.text('Status: Open'), findsOneWidget);
  });

  testWidgets('confirmed acknowledgement uses one POST and a fresh GET', (
    tester,
  ) async {
    var gets = 0;
    var posts = 0;
    final client = MockClient((request) async {
      expect(request.headers['authorization'], 'Bearer staff-test-token');
      if (request.method == 'GET') {
        gets++;
        return _detail(_alert(status: gets == 1 ? 'open' : 'acknowledged'));
      }
      posts++;
      expect(request.url.path, '/api/v1/clinical-safety/alerts/19/acknowledge');
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      expect(jsonDecode(request.body), <String, dynamic>{
        'acknowledgement_type': 'acknowledged',
      });
      return _posted();
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    await _submitAck(tester);
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(gets, 2);
    expect(find.text('Status: Acknowledged'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-critical-warning')),
      findsOneWidget,
    );
    expect(find.text('Status: Resolved'), findsNothing);
    await tester.tap(
      find.byKey(const ValueKey('clinical-alert-detail-refresh')),
    );
    await tester.pumpAndSettle();
    expect(posts, 1, reason: 'Refresh must never repeat a clinical write.');
  });

  testWidgets('override refuses blank justification before POST', (
    tester,
  ) async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') return _detail();
      posts++;
      return _posted(type: 'overridden');
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    await _submitOverride(tester, reason: '   ');
    await tester.pumpAndSettle();
    expect(posts, 0);
    expect(find.textContaining('Justification is required'), findsOneWidget);
  });

  testWidgets('override sends trimmed justification once and refreshes', (
    tester,
  ) async {
    var posts = 0;
    var gets = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        gets++;
        return _detail(_alert(status: gets == 1 ? 'open' : 'overridden'));
      }
      posts++;
      expect(jsonDecode(request.body), <String, dynamic>{
        'acknowledgement_type': 'overridden',
        'justification': 'Documented clinical rationale',
      });
      return _posted(
        type: 'overridden',
        justification: 'Documented clinical rationale',
      );
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    await _submitOverride(tester, reason: '  Documented clinical rationale  ');
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(gets, 2);
    expect(find.text('Status: Overridden'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-critical-warning')),
      findsOneWidget,
    );
  });

  testWidgets('pending POST disables duplicate tap', (tester) async {
    final pending = Completer<http.Response>();
    var posts = 0;
    var gets = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        gets++;
        return _detail(_alert(status: gets == 1 ? 'open' : 'acknowledged'));
      }
      posts++;
      return pending.future;
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    await _submitAck(tester);
    expect(posts, 1);
    final button = tester.widget<ElevatedButton>(
      find.byKey(const ValueKey('clinical-alert-acknowledge-submit')),
    );
    expect(button.onPressed, isNull);
    pending.complete(_posted());
    await tester.pumpAndSettle();
    expect(posts, 1);
  });

  for (final code in <int>[500, 503]) {
    testWidgets('HTTP $code leaves write outcome unknown and blocks retry', (
      tester,
    ) async {
      var posts = 0;
      var gets = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          gets++;
          return _detail();
        }
        posts++;
        return _httpError(code);
      });
      addTearDown(client.close);
      await _mount(tester, _source(client));
      await _submitAck(tester);
      await tester.pumpAndSettle();
      expect(posts, 1);
      expect(
        find.byKey(const ValueKey('clinical-alert-detail-write-uncertain')),
        findsOneWidget,
      );
      expect(find.text('Medication warning'), findsNothing);
      await tester.tap(
        find.byKey(const ValueKey('clinical-alert-detail-refresh')),
      );
      await tester.pumpAndSettle();
      expect(gets, 2);
      expect(posts, 1);
      expect(
        find.byKey(const ValueKey('clinical-alert-detail-write-uncertain')),
        findsOneWidget,
      );
      expect(find.textContaining('cannot establish whether'), findsOneWidget);
    });
  }

  testWidgets('connection failure does not automatically repeat POST', (
    tester,
  ) async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') return _detail();
      posts++;
      throw http.ClientException('simulated transport disconnect');
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    await _submitAck(tester);
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-write-uncertain')),
      findsOneWidget,
    );
  });

  testWidgets('malformed successful POST is unknown, not success', (
    tester,
  ) async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') return _detail();
      posts++;
      return http.Response(jsonEncode(<String, dynamic>{'message': 'ok'}), 201);
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    await _submitAck(tester);
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-write-uncertain')),
      findsOneWidget,
    );
  });

  testWidgets('HTTP 409 conflict requires review, not a blind retry', (
    tester,
  ) async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') return _detail();
      posts++;
      return _httpError(409);
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    await _submitAck(tester);
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.textContaining('conflict (HTTP 409)'), findsOneWidget);
    await tester.tap(
      find.byKey(const ValueKey('clinical-alert-detail-refresh')),
    );
    await tester.pumpAndSettle();
    expect(posts, 1);
  });

  testWidgets('HTTP 422 server refusal is never treated as override success', (
    tester,
  ) async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') return _detail();
      posts++;
      return _httpError(422);
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    await _submitOverride(tester, reason: 'Documented rationale');
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(
      find.textContaining('denied the request (HTTP 422)'),
      findsOneWidget,
    );
    expect(find.text('Status: Overridden'), findsNothing);
  });

  for (final code in <int>[401, 403]) {
    testWidgets('HTTP $code on POST removes clinical data and controls', (
      tester,
    ) async {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') return _detail();
        posts++;
        return _httpError(code);
      });
      addTearDown(client.close);
      await _mount(tester, _source(client));
      await _submitAck(tester);
      await tester.pumpAndSettle();
      expect(posts, 1);
      expect(find.text('Medication warning'), findsNothing);
      expect(find.text('SERVER_PRIVATE_INFORMATION'), findsNothing);
      expect(
        find.byKey(const ValueKey('clinical-alert-acknowledge-submit')),
        findsNothing,
      );
    });
  }

  testWidgets('session loss before POST does not send a write', (tester) async {
    var loads = 0;
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') return _detail();
      posts++;
      return _posted();
    });
    addTearDown(client.close);
    final source = _source(
      client,
      tokenLoader: () async {
        loads++;
        if (loads == 1) return 'staff-test-token';
        throw const AuthenticatedSessionUnavailableException(
          AuthenticatedSessionAccessFailure.signedOut,
        );
      },
    );
    await _mount(tester, source);
    await _submitAck(tester);
    await tester.pumpAndSettle();
    expect(posts, 0);
    expect(find.text('Medication warning'), findsNothing);
  });

  for (final status in <String>['resolved', 'expired']) {
    testWidgets('$status alert has no acknowledgement actions', (tester) async {
      final client = MockClient(
        (request) async => _detail(_alert(status: status)),
      );
      addTearDown(client.close);
      await _mount(tester, _source(client));
      expect(
        find.text('Status: ${status == 'resolved' ? 'Resolved' : 'Expired'}'),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('clinical-alert-acknowledge-submit')),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('clinical-alert-override-submit')),
        findsNothing,
      );
    });
  }

  testWidgets('failed post-success GET never unlocks acknowledgement', (
    tester,
  ) async {
    var gets = 0;
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        gets++;
        if (gets == 2) return _httpError(503);
        return _detail(_alert(status: gets == 1 ? 'open' : 'acknowledged'));
      }
      posts++;
      return _posted();
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    await _submitAck(tester);
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(gets, 2);
    expect(find.text('Medication warning'), findsNothing);
    await tester.tap(
      find.byKey(const ValueKey('clinical-alert-detail-refresh')),
    );
    await tester.pumpAndSettle();
    expect(gets, 3);
    expect(posts, 1);
    expect(find.text('Status: Acknowledged'), findsOneWidget);
  });

  testWidgets('old in-flight write cannot replace a switched alert context', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
        return pending.future;
      }
      if (request.url.path.endsWith('/20')) {
        return _detail(_alert(id: 20, title: 'New context alert'));
      }
      return _detail();
    });
    addTearDown(client.close);
    final source = _source(client);
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicalAlertDetailScreen(
          key: const ValueKey('shared-detail-state'),
          dataSource: source,
          alertId: 19,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await _submitAck(tester);
    expect(posts, 1);
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicalAlertDetailScreen(
          key: const ValueKey('shared-detail-state'),
          dataSource: source,
          alertId: 20,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('New context alert'), findsOneWidget);
    pending.complete(_posted());
    await tester.pumpAndSettle();
    expect(find.text('New context alert'), findsOneWidget);
    expect(find.text('Medication warning'), findsNothing);
    expect(posts, 1);
  });
}
