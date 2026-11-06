import 'dart:async';
import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_session_scope.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/models/clinical_alert_acknowledgement.dart';
import 'package:clinic_system_pro/features/clinical_safety/presentation/clinical_alert_detail_screen.dart';
import 'package:clinic_system_pro/features/clinical_safety/presentation/clinical_alert_inbox_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _time = '2026-11-05T12:00:00+00:00';

Map<String, dynamic> _alert({
  int id = 19,
  int clinic = 3,
  String severity = 'critical',
  String status = 'open',
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'patient_id': 7,
  'rule_id': 12,
  'rule_version': 2,
  'severity': severity,
  'action': 'block',
  'status': status,
  'title': 'S07_PRIVATE_ALERT',
  'message': 'Review required',
  'context': <String, dynamic>{},
  'source_type': 'prescription',
  'source_id': 8,
  'deduplication_key': 'private',
  'generated_at': _time,
  'resolved_at': status == 'resolved' ? _time : null,
  'created_at': _time,
  'updated_at': _time,
};

http.Response _list(List<Map<String, dynamic>> alerts) => http.Response(
  jsonEncode(<String, dynamic>{
    'items': alerts,
    'total': alerts.length,
    'page': 1,
    'per_page': 50,
  }),
  200,
);

http.Response _detail({String status = 'open'}) => http.Response(
  jsonEncode(<String, dynamic>{'alert': _alert(status: status)}),
  200,
);

AuthSessionState _session({int userId = 42, int clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'token-$userId-$clinicId',
        refreshToken: 'refresh-$userId',
        userId: userId,
        role: 'nurse',
        clinicContextId: clinicId,
      ),
    );

class _Network implements DeviceConnectivitySource {
  _Network(this.status);
  DeviceNetworkStatus status;
  @override
  Future<DeviceNetworkStatus> checkStatus() async => status;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

ClinicalSafetyRemoteDataSource _source(
  http.Client client, {
  DeviceConnectivitySource? device,
  Future<ClinicalSafetySessionStamp> Function()? stamp,
  Future<String> Function()? token,
}) => ClinicalSafetyRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadAccessToken: token ?? () async => 'staff-token',
  loadSessionStamp: stamp,
  deviceConnectivitySource: device,
);

/// Wait for an observable widget state, not just for scheduled frames.
/// Session and connectivity preflight can complete in later microtask turns.
/// A genuinely missing widget still fails with a precise diagnostic.
Future<void> _waitForVisible(
  WidgetTester tester,
  Finder finder, {
  int attempts = 40,
}) async {
  for (var i = 0; i < attempts && finder.evaluate().isEmpty; i++) {
    await tester.pump(const Duration(milliseconds: 25));
  }
  expect(finder, findsOneWidget);
}

void main() {
  testWidgets(
    'S07.G01 critical ID absent from refreshed first page remains unconfirmed until same-ID terminal status',
    (tester) async {
      var requests = 0;
      final client = MockClient((_) async {
        requests++;
        if (requests == 1) return _list(<Map<String, dynamic>>[_alert()]);
        if (requests == 2) return _list(<Map<String, dynamic>>[]);
        return _list(<Map<String, dynamic>>[_alert(status: 'resolved')]);
      });
      addTearDown(client.close);
      await tester.pumpWidget(
        MaterialApp(
          home: ClinicalAlertInboxScreen(dataSource: _source(client)),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('S07_PRIVATE_ALERT'), findsOneWidget);
      await tester.tap(
        find.byKey(const ValueKey('clinical-alert-inbox-refresh')),
      );
      await tester.pumpAndSettle();
      expect(find.text('S07_PRIVATE_ALERT'), findsNothing);
      expect(
        find.byKey(
          const ValueKey('clinical-alert-inbox-prior-critical-unconfirmed'),
        ),
        findsOneWidget,
      );
      // The same alert ID's authoritative terminal state is required to clear.
      await tester.tap(find.text('Refresh alerts'));
      await tester.pumpAndSettle();
      expect(find.text('Status: Resolved'), findsOneWidget);
      expect(
        find.byKey(
          const ValueKey('clinical-alert-inbox-prior-critical-unconfirmed'),
        ),
        findsNothing,
      );
      expect(requests, 3);
    },
  );

  test('S07.G02 disconnected device blocks acknowledgement and resolution before any POST', () async {
    var posts = 0;
    final client = MockClient((_) async {
      posts++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final source = _source(
      client,
      device: _Network(DeviceNetworkStatus.disconnected),
    );
    await expectLater(
      source.acknowledgeAlert(
        alertId: 19,
        type: ClinicalAlertAcknowledgementType.acknowledged,
      ),
      throwsA(isA<ClinicalSafetyOfflineWriteBlockedException>()),
    );
    await expectLater(
      source.resolveAlert(alertId: 19),
      throwsA(isA<ClinicalSafetyOfflineWriteBlockedException>()),
    );
    expect(posts, 0);
  });

  test(
    'S07.G03 unknown network blocks clinical writes, not guessed connected',
    () async {
      var posts = 0;
      final client = MockClient((_) async {
        posts++;
        return http.Response('{}', 500);
      });
      addTearDown(client.close);
      final source = _source(
        client,
        device: _Network(DeviceNetworkStatus.unknown),
      );
      await expectLater(
        source.resolveAlert(alertId: 19),
        throwsA(isA<ClinicalSafetyOfflineWriteBlockedException>()),
      );
      expect(posts, 0);
    },
  );

  testWidgets('S07.G04 offline UI disables acknowledgement and override', (
    tester,
  ) async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') posts++;
      return _detail();
    });
    addTearDown(client.close);
    final source = _source(
      client,
      device: _Network(DeviceNetworkStatus.disconnected),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicalAlertDetailScreen(dataSource: source, alertId: 19),
      ),
    );
    await tester.pumpAndSettle();
    // Do not race the chained asynchronous detail GET and network preflight.
    await _waitForVisible(
      tester,
      find.byKey(const ValueKey('clinical-alert-detail-list')),
    );
    // The actionable controls are lazy children near the end of ListView.
    // Waiting for frames never builds them while they remain off-screen.
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('clinical-alert-acknowledge-submit')),
      240.0,
      scrollable: find.byType(Scrollable).first,
      maxScrolls: 30,
    );
    await tester.pump();
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-offline-write-blocked')),
      findsOneWidget,
    );
    final ack = tester.widget<ElevatedButton>(
      find.byKey(const ValueKey('clinical-alert-acknowledge-submit')),
    );
    final override = tester.widget<OutlinedButton>(
      find.byKey(const ValueKey('clinical-alert-override-submit')),
    );
    expect(ack.onPressed, isNull);
    expect(override.onPressed, isNull);
    expect(posts, 0);
  });

  test('S07.G05 session/clinic switches after POST dispatch classify UNKNOWN, never resend', () async {
    var state = _session();
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    final scope = ClinicalSafetySessionScope(
      restoreSession: () async => state,
      navigationGuard: guard,
    );
    var posts = 0;
    final pending = Completer<http.Response>();
    final client = MockClient((request) {
      posts++;
      return pending.future;
    });
    addTearDown(client.close);
    final source = _source(
      client,
      token: () async => state.session!.accessToken,
      stamp: scope.snapshot,
      device: _Network(DeviceNetworkStatus.connected),
    );
    final completion = expectLater(
      source.acknowledgeAlert(
        alertId: 19,
        type: ClinicalAlertAcknowledgementType.acknowledged,
      ),
      throwsA(isA<ClinicalSafetyWriteOutcomeUnknownException>()),
    );
    for (var i = 0; i < 10 && posts == 0; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 1));
    }
    expect(posts, 1);
    state = _session(userId: 99, clinicId: 6);
    pending.complete(http.Response('{"acknowledgement":{}}', 200));
    await completion;
    expect(posts, 1);
  });

  test(
    'S07.G06 independent access token mismatch prevents HTTP dispatch',
    () async {
      final state = _session();
      final guard = SessionNavigationGuard(
        protectedRoutePrefixes: const <String>['/clinical-safety'],
      )..markSessionAvailable();
      addTearDown(guard.dispose);
      final scope = ClinicalSafetySessionScope(
        restoreSession: () async => state,
        navigationGuard: guard,
      );
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return _list(<Map<String, dynamic>>[]);
      });
      addTearDown(client.close);
      final source = _source(
        client,
        token: () async => 'different-token',
        stamp: scope.snapshot,
      );
      await expectLater(
        source.fetchAlerts(),
        throwsA(isA<AuthenticatedSessionUnavailableException>()),
      );
      expect(calls, 0);
    },
  );

  test('S07.G07 wrong-role HTTP 403 and wrong-patient HTTP 404 never reveal alert data', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      return http.Response('{}', calls == 1 ? 403 : 404);
    });
    addTearDown(client.close);
    final source = _source(client);
    await expectLater(
      source.fetchAlerts(),
      throwsA(
        isA<ApiException>().having((e) => e.statusCode, 'statusCode', 403),
      ),
    );
    await expectLater(
      source.fetchAlert(alertId: 19),
      throwsA(
        isA<ApiException>().having((e) => e.statusCode, 'statusCode', 404),
      ),
    );
    expect(calls, 2);
  });

  test(
    'S07.G08 invalid alert IDs and invalid override validation block all POSTs',
    () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response('{}', 500);
      });
      addTearDown(client.close);
      final source = _source(client);
      await expectLater(source.resolveAlert(alertId: 0), throwsArgumentError);
      await expectLater(
        source.acknowledgeAlert(
          alertId: -1,
          type: ClinicalAlertAcknowledgementType.acknowledged,
        ),
        throwsArgumentError,
      );
      await expectLater(
        source.acknowledgeAlert(
          alertId: 19,
          type: ClinicalAlertAcknowledgementType.overridden,
          justification: '  ',
        ),
        throwsArgumentError,
      );
      expect(calls, 0);
    },
  );

  test('S07.G09 backend HTTP 500 permits no automatic resend', () async {
    var posts = 0;
    final client = MockClient((request) async {
      posts++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final source = _source(
      client,
      device: _Network(DeviceNetworkStatus.connected),
    );
    await expectLater(
      source.acknowledgeAlert(
        alertId: 19,
        type: ClinicalAlertAcknowledgementType.acknowledged,
      ),
      throwsA(
        isA<ApiException>().having((e) => e.statusCode, 'statusCode', 500),
      ),
    );
    expect(posts, 1);
  });

  test('S07.G10 session guard blocks clinical routes after invalidation', () {
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>[
        '/patients',
        '/emergency-access',
        '/clinical-safety',
      ],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    expect(guard.redirect('/clinical-safety/alerts/19'), isNull);
    guard.invalidate();
    expect(guard.redirect('/clinical-safety'), '/login');
    expect(guard.redirect('/clinical-safety/alerts/19'), '/login');
  });

  test('S07.G12 clinic-mismatched successful response cannot expose foreign alerts', () async {
    final state = _session(userId: 42, clinicId: 3);
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    final scope = ClinicalSafetySessionScope(
      restoreSession: () async => state,
      navigationGuard: guard,
    );
    final client = MockClient(
      (_) async => _list(<Map<String, dynamic>>[_alert(clinic: 999)]),
    );
    addTearDown(client.close);
    final source = _source(
      client,
      stamp: scope.snapshot,
      token: () async => state.session!.accessToken,
    );
    await expectLater(source.fetchAlerts(), throwsA(isA<FormatException>()));
  });

  test('S07.G13 transport timeout is one request and never retries', () async {
    var posts = 0;
    final client = MockClient((request) async {
      posts++;
      throw TimeoutException('no response');
    });
    addTearDown(client.close);
    final source = _source(
      client,
      device: _Network(DeviceNetworkStatus.connected),
    );
    await expectLater(
      source.acknowledgeAlert(
        alertId: 19,
        type: ClinicalAlertAcknowledgementType.acknowledged,
      ),
      throwsA(
        isA<ApiException>().having(
          (error) => error.kind,
          'failure kind',
          ApiFailureKind.timeout,
        ),
      ),
    );
    expect(posts, 1);
  });

  testWidgets('S07.G14 double-tap sends exactly one acknowledgement POST', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    var posts = 0;
    final client = MockClient((request) {
      if (request.method == 'POST') {
        posts++;
        return pending.future;
      }
      return Future<http.Response>.value(_detail());
    });
    addTearDown(client.close);
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicalAlertDetailScreen(
          dataSource: _source(client),
          alertId: 19,
        ),
      ),
    );
    await tester.pumpAndSettle();
    // Wait for a backend-confirmed, rendered detail before interacting.
    await _waitForVisible(
      tester,
      find.byKey(const ValueKey('clinical-alert-detail-list')),
    );
    // Ensure the lazy acknowledgement controls are built by scrolling.
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('clinical-alert-acknowledge-submit')),
      240.0,
      scrollable: find.byType(Scrollable).first,
      maxScrolls: 30,
    );
    await tester.pump();
    final button = find.byKey(
      const ValueKey('clinical-alert-acknowledge-submit'),
    );
    await tester.tap(button);
    await tester.pump();
    for (var i = 0; i < 30 && posts == 0; i++) {
      await tester.pump(const Duration(milliseconds: 20));
    }
    expect(posts, 1);
    // The pending first POST must disable repeat acknowledgement.
    expect(tester.widget<ElevatedButton>(button).onPressed, isNull);
    await tester.tap(button);
    await tester.pump();
    expect(posts, 1);
    pending.complete(http.Response('{"acknowledgement":{}}', 200));
    await tester.pumpAndSettle();
    expect(posts, 1);
  });

  testWidgets(
    'S07.G11 expired alerts have no actionable clinical mutation controls',
    (tester) async {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'POST') posts++;
        return _detail(status: 'expired');
      });
      addTearDown(client.close);
      await tester.pumpWidget(
        MaterialApp(
          home: ClinicalAlertDetailScreen(
            dataSource: _source(client),
            alertId: 19,
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Status: Expired'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('clinical-alert-acknowledge-submit')),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('clinical-alert-override-submit')),
        findsNothing,
      );
      expect(posts, 0);
    },
  );
}
