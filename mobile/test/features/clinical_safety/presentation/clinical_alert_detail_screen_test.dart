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

const _timestamp = '2026-11-04T12:30:00+00:00';

Map<String, dynamic> _alert({
  int id = 19,
  String title = 'Medication warning',
  String severity = 'critical',
  String status = 'open',
  String action = 'block',
  String message = 'Clinical medication review required.',
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
  'message': message,
  'context': <String, dynamic>{
    'private_note': 'SENSITIVE_CONTEXT_VALUE_MUST_NOT_RENDER',
  },
  'source_type': 'prescription',
  'source_id': 9,
  'deduplication_key': 'sensitive-deduplication-key',
  'generated_at': _timestamp,
  'resolved_at': status == 'resolved' ? _timestamp : null,
  'created_at': _timestamp,
  'updated_at': _timestamp,
};

http.Response _ok([Map<String, dynamic>? alert]) => http.Response(
  jsonEncode(<String, dynamic>{'alert': alert ?? _alert()}),
  200,
);

http.Response _failure(int status) => http.Response(
  jsonEncode(<String, dynamic>{'message': 'SENSITIVE_SERVER_ERROR'}),
  status,
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
  );
  api.onAuthenticationRejected = onRejected;
  return ClinicalSafetyRemoteDataSource(
    apiClient: api,
    loadAccessToken: tokenLoader ?? () async => 'staff-token',
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

void main() {
  testWidgets('detail sends one authenticated GET and shows backend fields', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/clinical-safety/alerts/19');
      expect(request.url.hasQuery, isFalse);
      expect(request.headers['authorization'], 'Bearer staff-token');
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      return _ok();
    });
    addTearDown(client.close);

    await _mount(tester, _source(client));
    expect(calls, 1);
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-list')),
      findsOneWidget,
    );
    expect(find.text('Medication warning'), findsOneWidget);
    expect(find.text('Patient ID: 7'), findsOneWidget);
    expect(find.text('Severity: Critical'), findsOneWidget);
    expect(find.text('Status: Open'), findsOneWidget);
    expect(find.text('Action: Block'), findsOneWidget);
    expect(
      find.text('Message: Clinical medication review required.'),
      findsOneWidget,
    );
  });

  testWidgets('pending detail GET shows loading and no clinical content', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    final client = MockClient((_) => pending.future);
    addTearDown(client.close);

    await tester.pumpWidget(
      MaterialApp(
        home: ClinicalAlertDetailScreen(
          dataSource: _source(client),
          alertId: 19,
        ),
      ),
    );
    await tester.pump();
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-loading')),
      findsOneWidget,
    );
    expect(find.text('Medication warning'), findsNothing);

    pending.complete(_ok());
    await tester.pumpAndSettle();
    expect(find.text('Medication warning'), findsOneWidget);
  });

  testWidgets('critical acknowledged alert is still visibly unresolved', (
    tester,
  ) async {
    final client = MockClient((_) async => _ok(_alert(status: 'acknowledged')));
    addTearDown(client.close);
    await _mount(tester, _source(client));
    expect(find.text('Status: Acknowledged'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-critical-warning')),
      findsOneWidget,
    );
  });

  for (final status in <String>['resolved', 'expired']) {
    testWidgets('$status critical alert is not shown as unresolved', (
      tester,
    ) async {
      final client = MockClient((_) async => _ok(_alert(status: status)));
      addTearDown(client.close);
      await _mount(tester, _source(client));
      expect(
        find.byKey(const ValueKey('clinical-alert-detail-critical-warning')),
        findsNothing,
      );
    });
  }

  testWidgets('backend block action is displayed without a client override', (
    tester,
  ) async {
    final client = MockClient((_) async => _ok(_alert(severity: 'high')));
    addTearDown(client.close);
    await _mount(tester, _source(client));
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-block-warning')),
      findsOneWidget,
    );
    expect(find.text('Severity: High'), findsOneWidget);
    // P21.S04B adds server-evaluated acknowledgement controls, but no
    // client-side resolution or permission bypass.
    expect(find.text('Resolve'), findsNothing);
  });

  testWidgets('does not render raw context or deduplication metadata', (
    tester,
  ) async {
    final client = MockClient((_) async => _ok());
    addTearDown(client.close);
    await _mount(tester, _source(client));
    expect(
      find.textContaining('SENSITIVE_CONTEXT_VALUE_MUST_NOT_RENDER'),
      findsNothing,
    );
    expect(find.textContaining('sensitive-deduplication-key'), findsNothing);
  });

  testWidgets('initial HTTP 503 is unconfirmed and retry can recover', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return calls == 1 ? _failure(503) : _ok();
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-unconfirmed')),
      findsOneWidget,
    );
    expect(find.textContaining('HTTP 503'), findsOneWidget);
    expect(find.textContaining('SENSITIVE_SERVER_ERROR'), findsNothing);
    expect(find.text('Medication warning'), findsNothing);

    await tester.tap(find.byKey(const ValueKey('clinical-alert-detail-retry')));
    await tester.pumpAndSettle();
    expect(calls, 2);
    expect(find.text('Medication warning'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-unconfirmed')),
      findsNothing,
    );
  });

  testWidgets('refresh clears prior clinical content immediately', (
    tester,
  ) async {
    var calls = 0;
    final pending = Completer<http.Response>();
    final client = MockClient((_) async {
      calls++;
      if (calls == 1) return _ok();
      return pending.future;
    });
    addTearDown(client.close);
    await _mount(tester, _source(client));
    expect(find.text('Medication warning'), findsOneWidget);

    await tester.tap(
      find.byKey(const ValueKey('clinical-alert-detail-refresh')),
    );
    await tester.pump();
    expect(find.text('Medication warning'), findsNothing);
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-loading')),
      findsOneWidget,
    );

    pending.complete(
      _ok(_alert(title: 'Updated safety status', status: 'resolved')),
    );
    await tester.pumpAndSettle();
    expect(find.text('Updated safety status'), findsOneWidget);
    expect(find.text('Medication warning'), findsNothing);
    expect(calls, 2);
  });

  for (final status in <int>[403, 404]) {
    testWidgets('refresh HTTP $status drops old data and hides server detail', (
      tester,
    ) async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return calls == 1 ? _ok() : _failure(status);
      });
      addTearDown(client.close);
      await _mount(tester, _source(client));
      expect(find.text('Medication warning'), findsOneWidget);

      await tester.tap(
        find.byKey(const ValueKey('clinical-alert-detail-refresh')),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('clinical-alert-detail-unconfirmed')),
        findsOneWidget,
      );
      expect(find.text('Medication warning'), findsNothing);
      expect(find.textContaining('SENSITIVE_SERVER_ERROR'), findsNothing);
      expect(
        find.text('This clinical alert is not available to your account.'),
        findsOneWidget,
      );
    });
  }

  testWidgets('HTTP 401 removes details and triggers session invalidation', (
    tester,
  ) async {
    var calls = 0;
    var invalidations = 0;
    final client = MockClient((_) async {
      calls++;
      return calls == 1 ? _ok() : _failure(401);
    });
    addTearDown(client.close);
    await _mount(tester, _source(client, onRejected: () => invalidations++));
    expect(find.text('Medication warning'), findsOneWidget);

    await tester.tap(
      find.byKey(const ValueKey('clinical-alert-detail-refresh')),
    );
    await tester.pumpAndSettle();
    expect(invalidations, 1);
    expect(find.text('Medication warning'), findsNothing);
    expect(find.textContaining('HTTP 401'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-retry')),
      findsNothing,
    );
  });

  for (final failure in <AuthenticatedSessionAccessFailure>[
    AuthenticatedSessionAccessFailure.signedOut,
    AuthenticatedSessionAccessFailure.recoveryRequired,
  ]) {
    testWidgets('${failure.name} session stops before HTTP', (tester) async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return _ok();
      });
      addTearDown(client.close);
      await _mount(
        tester,
        _source(
          client,
          tokenLoader: () async {
            throw AuthenticatedSessionUnavailableException(failure);
          },
        ),
      );
      expect(calls, 0);
      expect(
        find.byKey(const ValueKey('clinical-alert-detail-unconfirmed')),
        findsOneWidget,
      );
      expect(find.text('Medication warning'), findsNothing);
      expect(
        find.byKey(const ValueKey('clinical-alert-detail-retry')),
        findsNothing,
      );
    });
  }

  testWidgets('invalid alert ID fails without token lookup or HTTP', (
    tester,
  ) async {
    var calls = 0;
    var tokenLoads = 0;
    final client = MockClient((_) async {
      calls++;
      return _ok();
    });
    addTearDown(client.close);
    await _mount(
      tester,
      _source(
        client,
        tokenLoader: () async {
          tokenLoads++;
          return 'staff-token';
        },
      ),
      alertId: 0,
    );
    expect(calls, 0);
    expect(tokenLoads, 0);
    expect(find.text('The clinical alert ID is invalid.'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-retry')),
      findsNothing,
    );
  });

  testWidgets(
    'mismatched response ID fails closed instead of showing wrong alert',
    (tester) async {
      final client = MockClient(
        (_) async => _ok(_alert(id: 20, title: 'Wrong patient alert')),
      );
      addTearDown(client.close);
      await _mount(tester, _source(client));
      expect(
        find.byKey(const ValueKey('clinical-alert-detail-unconfirmed')),
        findsOneWidget,
      );
      expect(find.text('Wrong patient alert'), findsNothing);
    },
  );

  testWidgets('malformed detail envelope fails closed', (tester) async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode(<String, dynamic>{'success': true, 'data': _alert()}),
        200,
      ),
    );
    addTearDown(client.close);
    await _mount(tester, _source(client));
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-unconfirmed')),
      findsOneWidget,
    );
    expect(find.text('Medication warning'), findsNothing);
  });

  testWidgets('late old alert response cannot replace new alert ID', (
    tester,
  ) async {
    final pendingOld = Completer<http.Response>();
    final oldClient = MockClient((request) => pendingOld.future);
    addTearDown(oldClient.close);
    final source = _source(oldClient);

    await tester.pumpWidget(
      MaterialApp(
        home: ClinicalAlertDetailScreen(dataSource: source, alertId: 19),
      ),
    );
    await tester.pump();
    expect(
      find.byKey(const ValueKey('clinical-alert-detail-loading')),
      findsOneWidget,
    );

    // Use a new source and different ID to mimic context/navigation changes.
    final newClient = MockClient((request) async {
      expect(request.url.path, '/api/v1/clinical-safety/alerts/20');
      return _ok(_alert(id: 20, title: 'Current alert for patient'));
    });
    addTearDown(newClient.close);
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicalAlertDetailScreen(
          dataSource: _source(newClient),
          alertId: 20,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Current alert for patient'), findsOneWidget);

    pendingOld.complete(_ok(_alert(id: 19, title: 'Old session alert')));
    await tester.pumpAndSettle();
    expect(find.text('Current alert for patient'), findsOneWidget);
    expect(find.text('Old session alert'), findsNothing);
  });
}
