import 'dart:async';
import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_session_scope.dart';
import 'package:clinic_system_pro/features/clinical_safety/presentation/clinical_alert_detail_screen.dart';
import 'package:clinic_system_pro/features/clinical_safety/presentation/clinical_alert_inbox_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _ts = '2026-11-05T12:00:00+00:00';

Map<String, dynamic> _alert({String title = 'PREVIOUS_CLINICAL_PHI'}) =>
    <String, dynamic>{
      'id': 19,
      'clinic_id': 3,
      'patient_id': 7,
      'rule_id': 12,
      'rule_version': 2,
      'severity': 'critical',
      'action': 'block',
      'status': 'open',
      'title': title,
      'message': 'Clinical review needed',
      'context': <String, dynamic>{},
      'source_type': 'prescription',
      'source_id': 8,
      'deduplication_key': 'private-dedupe',
      'generated_at': _ts,
      'resolved_at': null,
      'created_at': _ts,
      'updated_at': _ts,
    };

http.Response _inbox() => http.Response(
  jsonEncode(<String, dynamic>{
    'items': <Map<String, dynamic>>[_alert()],
    'total': 1,
    'page': 1,
    'per_page': 50,
  }),
  200,
);

http.Response _detail() => http.Response(
  jsonEncode(<String, dynamic>{'alert': _alert()}),
  200,
);

AuthSessionState _session({int userId = 42, int? clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'token-$userId-$clinicId',
        refreshToken: 'refresh-$userId',
        userId: userId,
        role: 'nurse',
        clinicContextId: clinicId,
      ),
    );

ApiClient _api(http.Client client) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: client,
);

void main() {
  test('S06.G01 missing clinic context fails closed before HTTP', () async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return _inbox();
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    final scope = ClinicalSafetySessionScope(
      restoreSession: () async => _session(clinicId: null),
      navigationGuard: guard,
    );
    final source = ClinicalSafetyRemoteDataSource(
      apiClient: _api(client),
      loadAccessToken: () async => 'token-42-null',
      loadSessionStamp: scope.snapshot,
    );
    await expectLater(
      source.fetchAlerts(),
      throwsA(isA<AuthenticatedSessionUnavailableException>()),
    );
    expect(requests, 0);
  });

  test('S06.G02 mismatched clinic in HTTP 200 response fails closed', () async {
    final client = MockClient((_) async => _inbox());
    addTearDown(client.close);
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    final scope = ClinicalSafetySessionScope(
      restoreSession: () async => _session(clinicId: 6),
      navigationGuard: guard,
    );
    final source = ClinicalSafetyRemoteDataSource(
      apiClient: _api(client),
      loadAccessToken: () async => 'token-42-6',
      loadSessionStamp: scope.snapshot,
    );
    await expectLater(source.fetchAlerts(), throwsA(isA<FormatException>()));
  });

  test('S06.G01 signed-out state blocks session scope before HTTP', () async {
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSignedOut();
    addTearDown(guard.dispose);
    final scope = ClinicalSafetySessionScope(
      restoreSession: () async => _session(),
      navigationGuard: guard,
    );
    await expectLater(
      scope.snapshot(),
      throwsA(isA<AuthenticatedSessionUnavailableException>()),
    );
  });

  test('S06.G02/G03 clinic switch during GET rejects old response', () async {
    final pending = Completer<http.Response>();
    var requests = 0;
    var state = _session();
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    final client = MockClient((_) {
      requests++;
      return pending.future;
    });
    addTearDown(client.close);
    final scope = ClinicalSafetySessionScope(
      restoreSession: () async => state,
      navigationGuard: guard,
    );
    final source = ClinicalSafetyRemoteDataSource(
      apiClient: _api(client),
      loadAccessToken: () async => state.session!.accessToken,
      loadSessionStamp: scope.snapshot,
    );
    final expectation = expectLater(
      source.fetchAlerts(),
      throwsA(isA<AuthenticatedSessionUnavailableException>()),
    );
    await Future<void>.delayed(Duration.zero);
    expect(requests, 1);
    state = _session(userId: 57, clinicId: 6);
    pending.complete(_inbox());
    await expectation;
    expect(requests, 1);
  });

  testWidgets('S06.G02/G03 logout clears displayed alert and ignores late GET', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    var requests = 0;
    final client = MockClient((_) {
      requests++;
      if (requests == 1) return Future<http.Response>.value(_inbox());
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(MaterialApp(
      home: ClinicalAlertInboxScreen(
        dataSource: ClinicalSafetyRemoteDataSource(
          apiClient: _api(client),
          loadAccessToken: () async => 'staff-token',
        ),
        sessionNavigationGuard: guard,
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('PREVIOUS_CLINICAL_PHI'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('clinical-alert-inbox-refresh')));
    await tester.pump();
    guard.markSignedOut();
    await tester.pump();
    expect(find.text('PREVIOUS_CLINICAL_PHI'), findsNothing);
    pending.complete(_inbox());
    await tester.pumpAndSettle();
    expect(find.text('PREVIOUS_CLINICAL_PHI'), findsNothing);
    expect(find.byKey(const ValueKey('clinical-alert-inbox-unconfirmed')), findsOneWidget);
  });

  testWidgets('S06.G02 detail clears PHI immediately on session rejection', (
    tester,
  ) async {
    final client = MockClient((_) async => _detail());
    addTearDown(client.close);
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/clinical-safety'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(MaterialApp(
      home: ClinicalAlertDetailScreen(
        alertId: 19,
        dataSource: ClinicalSafetyRemoteDataSource(
          apiClient: _api(client),
          loadAccessToken: () async => 'staff-token',
        ),
        sessionNavigationGuard: guard,
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('PREVIOUS_CLINICAL_PHI'), findsOneWidget);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('PREVIOUS_CLINICAL_PHI'), findsNothing);
    expect(find.byKey(const ValueKey('clinical-alert-detail-unconfirmed')), findsOneWidget);
  });
}
