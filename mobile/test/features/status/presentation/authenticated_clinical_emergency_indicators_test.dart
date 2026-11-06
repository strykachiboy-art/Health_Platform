import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_context.dart';
import 'package:clinic_system_pro/features/emergency_access/data/models/emergency_access_grant.dart';
import 'package:clinic_system_pro/features/status/presentation/authenticated_clinical_emergency_indicators.dart';

AuthSessionState _ready({
  int? clinic = 3,
  int user = 44,
  String role = 'doctor',
}) {
  return AuthSessionState.ready(
    AuthTokenResponse(
      accessToken: 'token-a',
      refreshToken: 'refresh-a',
      userId: user,
      role: role,
      clinicContextId: clinic,
    ),
  );
}

EmergencyAccessGrant _grant({
  int clinic = 3,
  int user = 44,
  EmergencyAccessStatus status = EmergencyAccessStatus.active,
}) {
  final now = DateTime.now().toUtc();
  return EmergencyAccessGrant(
    id: 51,
    clinicId: clinic,
    patientId: 73,
    requesterUserId: user,
    requesterRole: 'doctor',
    reason: 'Test fixture',
    purpose: 'Test fixture',
    scope: const ['patient:read'],
    status: status,
    requestedAt: now.subtract(const Duration(minutes: 5)),
    grantedAt: now.subtract(const Duration(minutes: 1)),
    expiresAt: now.add(const Duration(hours: 1)),
    revokedAt: null,
    reviewedAt: now.subtract(const Duration(minutes: 1)),
    reviewedByUserId: 8,
    reviewNotes: null,
  );
}

void main() {
  testWidgets(
    'P22 S04.2 gate 1 confirms zero only from server and fails closed',
    (tester) async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      final contextState = EmergencyAccessContext(
        sessionNavigationGuard: guard,
      );
      addTearDown(() {
        contextState.dispose();
        guard.dispose();
      });
      var reads = 0;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: AuthenticatedClinicalEmergencyIndicators(
              loadSession: () async => _ready(),
              loadOpenAlertCount: () async {
                reads++;
                return 0;
              },
              loadEmergencyRequest: ({required emergencyAccessId}) async {
                throw StateError('No emergency request expected');
              },
              emergencyAccessContext: contextState,
              sessionNavigationGuard: guard,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(reads, 1);
      expect(find.text('Clinical alerts: 0 open'), findsOneWidget);
      expect(find.text('Emergency access: Unknown'), findsOneWidget);
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.text('Clinical alerts: Unavailable'), findsOneWidget);
      expect(find.text('Emergency access: Unavailable'), findsOneWidget);
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );

  testWidgets('P22 S04.2 gate 1 missing clinic never calls an alert reader', (
    tester,
  ) async {
    final contextState = EmergencyAccessContext();
    addTearDown(contextState.dispose);
    var reads = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AuthenticatedClinicalEmergencyIndicators(
            loadSession: () async => _ready(clinic: null),
            loadOpenAlertCount: () async {
              reads++;
              return 0;
            },
            loadEmergencyRequest: ({required emergencyAccessId}) async {
              throw StateError('No emergency request expected');
            },
            emergencyAccessContext: contextState,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(reads, 0);
    expect(find.text('Clinical alerts: Unavailable'), findsOneWidget);
    expect(find.text('Emergency access: Unavailable'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('P22 S04.2 gate 2 requires matching fresh backend active grant', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    final contextState = EmergencyAccessContext(sessionNavigationGuard: guard);
    addTearDown(() {
      contextState.dispose();
      guard.dispose();
    });
    final candidate = _grant();
    contextState.activate(candidate);
    var reads = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AuthenticatedClinicalEmergencyIndicators(
            loadSession: () async => _ready(),
            loadOpenAlertCount: () async => 2,
            loadEmergencyRequest: ({required emergencyAccessId}) async {
              reads++;
              expect(emergencyAccessId, 51);
              return candidate;
            },
            emergencyAccessContext: contextState,
            sessionNavigationGuard: guard,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(reads, 1);
    expect(find.text('Clinical alerts: 2 open'), findsOneWidget);
    expect(
      find.text('Emergency access: Active (server confirmed)'),
      findsOneWidget,
    );
    contextState.clear();
    await tester.pumpAndSettle();
    expect(find.text('Emergency access: Unknown'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('P22 S04.2 gate 2 rejects cross-clinic grant response', (
    tester,
  ) async {
    final contextState = EmergencyAccessContext();
    addTearDown(contextState.dispose);
    contextState.activate(_grant());
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AuthenticatedClinicalEmergencyIndicators(
            loadSession: () async => _ready(),
            loadOpenAlertCount: () async => 1,
            loadEmergencyRequest: ({required emergencyAccessId}) async =>
                _grant(clinic: 9),
            emergencyAccessContext: contextState,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Emergency access: Unknown'), findsOneWidget);
    expect(
      find.text('Emergency access: Active (server confirmed)'),
      findsNothing,
    );
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('P22 S04.2 gate 2 revoked backend grant never shows active', (
    tester,
  ) async {
    final contextState = EmergencyAccessContext();
    addTearDown(contextState.dispose);
    contextState.activate(_grant());
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AuthenticatedClinicalEmergencyIndicators(
            loadSession: () async => _ready(),
            loadOpenAlertCount: () async => 1,
            loadEmergencyRequest: ({required emergencyAccessId}) async =>
                _grant(status: EmergencyAccessStatus.revoked),
            emergencyAccessContext: contextState,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Emergency access: Not active'), findsOneWidget);
    expect(find.textContaining('server confirmed'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('P22 S04.2 gate 2 backend read failure remains unknown', (
    tester,
  ) async {
    final contextState = EmergencyAccessContext();
    addTearDown(contextState.dispose);
    contextState.activate(_grant());
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AuthenticatedClinicalEmergencyIndicators(
            loadSession: () async => _ready(),
            loadOpenAlertCount: () async => 1,
            loadEmergencyRequest: ({required emergencyAccessId}) async {
              throw StateError('private transport failure');
            },
            emergencyAccessContext: contextState,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Emergency access: Unknown'), findsOneWidget);
    expect(find.textContaining('private transport failure'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets(
    'P22 S04.2 gate 2 stale clinical completion cannot restore values',
    (tester) async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      final contextState = EmergencyAccessContext(
        sessionNavigationGuard: guard,
      );
      addTearDown(() {
        contextState.dispose();
        guard.dispose();
      });
      final pending = Completer<int>();
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: AuthenticatedClinicalEmergencyIndicators(
              loadSession: () async => _ready(),
              loadOpenAlertCount: () => pending.future,
              loadEmergencyRequest: ({required emergencyAccessId}) async {
                throw StateError('No emergency request expected');
              },
              emergencyAccessContext: contextState,
              sessionNavigationGuard: guard,
            ),
          ),
        ),
      );
      await tester.pump();
      guard.invalidate();
      pending.complete(500);
      await tester.pumpAndSettle();
      expect(find.text('Clinical alerts: Unavailable'), findsOneWidget);
      expect(find.textContaining('500 open'), findsNothing);
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );

  testWidgets('P22 S04.2 gate 3 uses existing Home and Feature shell seam', (
    tester,
  ) async {
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const ['/home', '/patients'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    final client = MockClient((request) async {
      throw StateError('No HTTP expected');
    });
    addTearDown(client.close);
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    addTearDown(api.close);
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('Login sentinel')),
      loadHomeSession: () async => _ready(),
      statusStripBuilder: () => const Text('P22 S04.2 separate safety row'),
      sessionNavigationGuard: guard,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/home');
    await tester.pumpAndSettle();
    expect(find.text('P22 S04.2 separate safety row'), findsOneWidget);
    router.go('/patients');
    await tester.pumpAndSettle();
    expect(find.text('P22 S04.2 separate safety row'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
