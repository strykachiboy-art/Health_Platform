import 'dart:async';
import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/presentation/clinical_alert_detail_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _ts = '2026-11-05T12:00:00+00:00';
const _resolveButton = ValueKey('clinical-alert-resolve-submit');
const _confirmButton = ValueKey('clinical-alert-resolve-confirm');
const _cancelButton = ValueKey('clinical-alert-resolve-cancel');

Map<String, dynamic> _alert({String status = 'open', int clinic = 3}) =>
    <String, dynamic>{
      'id': 19,
      'clinic_id': clinic,
      'patient_id': 7,
      'rule_id': 12,
      'rule_version': 2,
      'severity': 'critical',
      'action': 'block',
      'status': status,
      'title': 'Prescription safety warning',
      'message': 'Review the clinical issue before acting.',
      'context': <String, dynamic>{'secret': 'DO_NOT_RENDER'},
      'source_type': 'prescription',
      'source_id': 8,
      'deduplication_key': 'SECRET_DEDUP',
      'generated_at': _ts,
      'resolved_at': status == 'resolved' ? _ts : null,
      'created_at': _ts,
      'updated_at': _ts,
    };

http.Response _detail({String status = 'open'}) => http.Response(
  jsonEncode(<String, dynamic>{'alert': _alert(status: status)}),
  200,
);

http.Response _resolved() => http.Response(
  jsonEncode(<String, dynamic>{'alert': _alert(status: 'resolved')}),
  200,
);

http.Response _error(int status) => http.Response(
  jsonEncode(<String, dynamic>{'message': 'SERVER_SECRET'}),
  status,
);

class _Device implements DeviceConnectivitySource {
  _Device(this.current);
  final DeviceNetworkStatus current;

  @override
  Future<DeviceNetworkStatus> checkStatus() async => current;

  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

ClinicalSafetyRemoteDataSource _source(
  http.Client client, {
  DeviceConnectivitySource? device,
}) => ClinicalSafetyRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadAccessToken: () async => 'staff-test-token',
  deviceConnectivitySource: device,
);

Future<void> _mount(
  WidgetTester tester,
  ClinicalSafetyRemoteDataSource source, {
  SessionNavigationGuard? guard,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: ClinicalAlertDetailScreen(
        dataSource: source,
        sessionNavigationGuard: guard,
        alertId: 19,
      ),
    ),
  );
  await tester.pumpAndSettle();
  expect(
    find.byKey(const ValueKey('clinical-alert-detail-list')),
    findsOneWidget,
  );
}

Future<void> _scrollToResolve(WidgetTester tester) async {
  await tester.scrollUntilVisible(
    find.byKey(_resolveButton),
    200,
    scrollable: find.byType(Scrollable).first,
    maxScrolls: 35,
  );
  await tester.pump();
}

Future<void> _openConfirm(WidgetTester tester) async {
  await _scrollToResolve(tester);
  await tester.tap(find.byKey(_resolveButton));
  await tester.pumpAndSettle();
  expect(find.byKey(_confirmButton), findsOneWidget);
  expect(find.byKey(_cancelButton), findsOneWidget);
}

Future<void> _confirm(WidgetTester tester) async {
  await tester.tap(find.byKey(_confirmButton));
  await tester.pump();
}

void main() {
  testWidgets('S05.R01 live detail GET shows distinct Resolve but never POSTs', (
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
      return _resolved();
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    await _scrollToResolve(tester);
    expect(find.byKey(_resolveButton), findsOneWidget);
    expect(find.text('Separate clinical resolution'), findsOneWidget);
    expect(gets, 1);
    expect(posts, 0);
    expect(find.text('DO_NOT_RENDER'), findsNothing);
    expect(find.text('SECRET_DEDUP'), findsNothing);
  });

  testWidgets('S05.R02 cancelling the explicit confirmation sends zero POSTs', (
    tester,
  ) async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') return _detail();
      posts++;
      return _resolved();
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    await _openConfirm(tester);
    expect(find.textContaining('blocked clinical'), findsWidgets);
    await tester.tap(find.byKey(_cancelButton));
    await tester.pumpAndSettle();
    expect(posts, 0);
    expect(find.byKey(_confirmButton), findsNothing);
  });

  testWidgets('S05.R03 confirmed resolution POSTs once and then reads status', (
    tester,
  ) async {
    var posts = 0;
    var gets = 0;
    final client = MockClient((request) async {
      expect(request.headers['authorization'], 'Bearer staff-test-token');
      if (request.method == 'GET') {
        gets++;
        return _detail(status: gets == 1 ? 'open' : 'resolved');
      }
      posts++;
      expect(request.url.path, '/api/v1/clinical-safety/alerts/19/resolve');
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      expect(jsonDecode(request.body), isEmpty);
      return _resolved();
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    await _openConfirm(tester);
    await _confirm(tester);
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(gets, 2);
    expect(find.text('Status: Resolved'), findsOneWidget);
    expect(find.textContaining('Resolution does not grant permission'),
        findsOneWidget);
    expect(find.byKey(_resolveButton), findsNothing);
    expect(find.byKey(const ValueKey('clinical-alert-acknowledge-submit')),
        findsNothing);
    await tester.tap(find.byKey(const ValueKey('clinical-alert-detail-refresh')));
    await tester.pumpAndSettle();
    expect(posts, 1, reason: 'Read refresh must never replay resolution POST.');
  });

  testWidgets('S05.R04 double-submit is blocked while first POST is pending', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    var posts = 0;
    var gets = 0;
    final client = MockClient((request) {
      if (request.method == 'GET') {
        gets++;
        return Future.value(_detail(status: gets == 1 ? 'open' : 'resolved'));
      }
      posts++;
      return pending.future;
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    await _openConfirm(tester);
    await _confirm(tester);
    for (var i = 0; i < 35 && posts == 0; i++) {
      await tester.pump(const Duration(milliseconds: 20));
    }
    expect(posts, 1);
    final button = tester.widget<FilledButton>(find.byKey(_resolveButton));
    expect(button.onPressed, isNull);
    pending.complete(_resolved());
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(gets, 2);
  });

  for (final networkStatus in <DeviceNetworkStatus>[
    DeviceNetworkStatus.disconnected,
    DeviceNetworkStatus.unknown,
  ]) {
    testWidgets('S05.R05 $networkStatus disables Resolve and sends zero POSTs', (
      tester,
    ) async {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') return _detail();
        posts++;
        return _resolved();
      });
      addTearDown(client.close);
      await _mount(tester, _source(client, device: _Device(networkStatus)));
      await _scrollToResolve(tester);
      expect(find.byKey(const ValueKey('clinical-alert-resolve-offline-warning')),
          findsOneWidget);
      expect(tester.widget<FilledButton>(find.byKey(_resolveButton)).onPressed,
          isNull);
      expect(posts, 0);
    });
  }

  for (final status in <String>['resolved', 'expired']) {
    testWidgets('S05.R06 $status alert exposes no Resolve control', (
      tester,
    ) async {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') return _detail(status: status);
        posts++;
        return _resolved();
      });
      addTearDown(client.close);
      await _mount(tester, _source(client));
      expect(find.byKey(_resolveButton, skipOffstage: false), findsNothing);
      expect(posts, 0);
    });
  }

  for (final status in <int>[401, 403, 404, 409, 422, 500, 503]) {
    testWidgets('S05.R07 HTTP $status blocks repeat resolution', (
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
        return _error(status);
      });
      addTearDown(client.close);
      await _mount(tester, _source(client));
      await _openConfirm(tester);
      await _confirm(tester);
      await tester.pumpAndSettle();
      expect(posts, 1);
      expect(find.byKey(const ValueKey('clinical-alert-detail-unconfirmed')),
          findsOneWidget);
      expect(find.byKey(_resolveButton), findsNothing);
      expect(find.text('SERVER_SECRET'), findsNothing);
      // Read-only refresh may be allowed; it cannot unlock a repeat POST.
      if (status != 401) {
        await tester.tap(find.byKey(const ValueKey('clinical-alert-detail-retry')));
        await tester.pumpAndSettle();
        expect(gets, 2);
        expect(posts, 1);
        expect(find.byKey(_resolveButton), findsNothing);
      }
    });
  }

  testWidgets('S05.R08 malformed success is UNKNOWN, never retried', (
    tester,
  ) async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') return _detail();
      posts++;
      return http.Response('{"message":"not-a-resolution"}', 200);
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    await _openConfirm(tester);
    await _confirm(tester);
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.byKey(const ValueKey('clinical-alert-detail-write-uncertain')),
        findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('clinical-alert-detail-refresh')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.byKey(_resolveButton), findsNothing);
  });

  testWidgets('S05.R09 successful POST but unconfirmed GET remains blocked', (
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
      return _resolved();
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    await _openConfirm(tester);
    await _confirm(tester);
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(gets, 2);
    expect(find.byKey(const ValueKey('clinical-alert-detail-write-uncertain')),
        findsOneWidget);
    expect(find.byKey(_resolveButton), findsNothing);
  });

  testWidgets('S05.R10 session invalidation during dialog sends zero POSTs', (
    tester,
  ) async {
    var posts = 0;
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    final client = MockClient((request) async {
      if (request.method == 'GET') return _detail();
      posts++;
      return _resolved();
    });
    addTearDown(client.close);
    await _mount(tester, _source(client), guard: guard);
    await _openConfirm(tester);
    guard.invalidate();
    await tester.pump();
    await _confirm(tester);
    await tester.pumpAndSettle();
    expect(posts, 0);
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-unconfirmed')),
      findsOneWidget,
    );
    expect(find.text('Prescription safety warning'), findsNothing);
  });
}
