import 'dart:async';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_create_queue_state_read_model.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_create_sync_status_panel.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_create_sync_status_route.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

AuthSessionState _session({
  String role = 'DOCTOR',
  int? clinic = 3,
  int userId = 42,
  String token = 's05c2-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 's05c2-refresh',
    userId: userId,
    role: role,
    clinicContextId: clinic,
  ),
);

AppointmentCreateQueuePage _page({bool pending = true}) =>
    AppointmentCreateQueuePage.ready(
      items: pending
          ? <AppointmentCreateQueueSummary>[
              AppointmentCreateQueueSummary(
                operationId: 'private-queue-id',
                state: AppointmentCreateQueueDisplayState.pending,
                createdAt: DateTime.utc(2026, 11, 7),
                attemptsCompleted: 0,
              ),
            ]
          : <AppointmentCreateQueueSummary>[],
      page: 1,
      hasNext: false,
    );

class _Rig {
  _Rig({
    this.includeLoader = true,
    this.includePageLoader = true,
    this.includeSource = true,
    this.signedOut = false,
    this.role = 'DOCTOR',
    this.clinic = 3,
    this.token = 's05c2-token',
    this.pendingPage,
    this.pendingSession,
  }) : guard = SessionNavigationGuard(
         protectedRoutePrefixes: const <String>['/appointments'],
       ) {
    client = MockClient((request) async {
      if (request.method == 'POST') postCalls++;
      return http.Response('unexpected request', 500);
    });
    if (signedOut) {
      guard.markSignedOut();
    } else {
      guard.markSessionAvailable();
    }
    api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    Future<AuthSessionState> loadSession() {
      sessionLoads++;
      final pending = pendingSession;
      return pending ??
          Future<AuthSessionState>.value(
            _session(role: role, clinic: clinic, token: token),
          );
    }

    Future<AppointmentCreateQueuePage> loadPage({
      required int page,
      required int pageSize,
    }) {
      pageReads++;
      if (page != 1 || pageSize != 20) {
        throw StateError('unexpected paging contract');
      }
      return pendingPage ?? Future<AppointmentCreateQueuePage>.value(_page());
    }

    router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Center(child: Text('Login sentinel'))),
      loadHomeSession: includeLoader ? loadSession : null,
      sessionNavigationGuard: guard,
      appointmentDataSource: includeSource
          ? AppointmentRemoteDataSource(
              apiClient: api,
              loadSession: loadSession,
              sessionNavigationGuard: guard,
            )
          : null,
      appointmentCreateSyncPageLoader: includePageLoader ? loadPage : null,
    );
  }

  final bool includeLoader;
  final bool includePageLoader;
  final bool includeSource;
  final bool signedOut;
  final String role;
  final int? clinic;
  final String token;
  final Future<AppointmentCreateQueuePage>? pendingPage;
  final Future<AuthSessionState>? pendingSession;
  final SessionNavigationGuard guard;
  late final MockClient client;
  late final ApiClient api;
  late final GoRouter router;
  int pageReads = 0;
  int sessionLoads = 0;
  int postCalls = 0;

  Widget app() => MaterialApp.router(routerConfig: router);

  void dispose() {
    router.dispose();
    guard.dispose();
    client.close();
  }
}

void main() {
  testWidgets('verified staff can view pending sync metadata without POST', (
    tester,
  ) async {
    final rig = _Rig();
    addTearDown(rig.dispose);
    await tester.pumpWidget(rig.app());
    rig.router.go('/appointments/sync');
    await tester.pumpAndSettle();
    expect(find.byType(AppointmentCreateSyncStatusRoute), findsOneWidget);
    expect(find.byType(AppointmentCreateSyncStatusPanel), findsOneWidget);
    expect(find.textContaining('Queued locally'), findsOneWidget);
    expect(find.textContaining('not yet confirmed'), findsWidgets);
    expect(find.textContaining('private-queue-id'), findsNothing);
    expect(rig.pageReads, 1);
    expect(rig.postCalls, 0);
  });

  testWidgets('direct sync deep link without bound reader fails closed', (
    tester,
  ) async {
    final rig = _Rig(includePageLoader: false);
    addTearDown(rig.dispose);
    await tester.pumpWidget(rig.app());
    rig.router.go('/appointments/sync');
    await tester.pumpAndSettle();
    expect(find.byType(AppointmentCreateSyncStatusPanel), findsNothing);
    expect(find.text('Appointment route is unavailable.'), findsOneWidget);
    expect(rig.pageReads, 0);
  });

  testWidgets('direct sync deep link without auth loader fails closed', (
    tester,
  ) async {
    final rig = _Rig(includeLoader: false);
    addTearDown(rig.dispose);
    await tester.pumpWidget(rig.app());
    rig.router.go('/appointments/sync');
    await tester.pumpAndSettle();
    expect(find.byType(AppointmentCreateSyncStatusPanel), findsNothing);
    expect(find.text('Appointment route is unavailable.'), findsOneWidget);
    expect(rig.pageReads, 0);
  });

  testWidgets('direct sync deep link without appointment source fails closed', (
    tester,
  ) async {
    final rig = _Rig(includeSource: false);
    addTearDown(rig.dispose);
    await tester.pumpWidget(rig.app());
    rig.router.go('/appointments/sync');
    await tester.pumpAndSettle();
    expect(find.byType(AppointmentCreateSyncStatusPanel), findsNothing);
    expect(find.text('Appointment route is unavailable.'), findsOneWidget);
    expect(rig.pageReads, 0);
  });

  testWidgets('signed-out deep link redirects without local queue reads', (
    tester,
  ) async {
    final rig = _Rig(signedOut: true);
    addTearDown(rig.dispose);
    await tester.pumpWidget(rig.app());
    rig.router.go('/appointments/sync');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(rig.pageReads, 0);
  });

  for (final role in <String>['PATIENT', 'SUPER_ADMIN', 'PHARMACIST']) {
    testWidgets('unapproved role $role cannot see queue metadata', (
      tester,
    ) async {
      final rig = _Rig(role: role);
      addTearDown(rig.dispose);
      await tester.pumpWidget(rig.app());
      rig.router.go('/appointments/sync');
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('s05c2-session-denied')),
        findsOneWidget,
      );
      expect(find.byType(AppointmentCreateSyncStatusPanel), findsNothing);
      expect(rig.pageReads, 0);
    });
  }

  testWidgets('missing clinic context cannot access sync status', (
    tester,
  ) async {
    final rig = _Rig(clinic: null);
    addTearDown(rig.dispose);
    await tester.pumpWidget(rig.app());
    rig.router.go('/appointments/sync');
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('s05c2-session-denied')), findsOneWidget);
    expect(rig.pageReads, 0);
  });

  testWidgets('missing access token cannot access sync status', (tester) async {
    final rig = _Rig(token: '');
    addTearDown(rig.dispose);
    await tester.pumpWidget(rig.app());
    rig.router.go('/appointments/sync');
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('s05c2-session-denied')), findsOneWidget);
    expect(rig.pageReads, 0);
  });

  testWidgets('hub shows sync status action and opens protected route', (
    tester,
  ) async {
    final rig = _Rig();
    addTearDown(rig.dispose);
    await tester.pumpWidget(rig.app());
    rig.router.go('/appointments');
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointments-open-sync-status')),
      findsOneWidget,
    );
    await tester.tap(
      find.byKey(const ValueKey('appointments-open-sync-status')),
    );
    await tester.pumpAndSettle();
    expect(find.byType(AppointmentCreateSyncStatusPanel), findsOneWidget);
    expect(rig.pageReads, 1);
    expect(rig.postCalls, 0);
  });

  testWidgets('hub omits sync action without reader', (tester) async {
    final rig = _Rig(includePageLoader: false);
    addTearDown(rig.dispose);
    await tester.pumpWidget(rig.app());
    rig.router.go('/appointments');
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointments-open-sync-status')),
      findsNothing,
    );
    expect(rig.pageReads, 0);
  });

  testWidgets('logout during a pending page read clears old metadata', (
    tester,
  ) async {
    final pending = Completer<AppointmentCreateQueuePage>();
    final rig = _Rig(pendingPage: pending.future);
    addTearDown(rig.dispose);
    await tester.pumpWidget(rig.app());
    rig.router.go('/appointments/sync');
    // Wait for the actual first read across nested auth/route microtasks.
    for (var frame = 0; frame < 40 && rig.pageReads == 0; frame++) {
      await tester.pump(const Duration(milliseconds: 20));
    }
    expect(rig.pageReads, 1);
    rig.guard.markSignedOut();
    await tester.pump();
    pending.complete(_page());
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(find.byType(AppointmentCreateSyncStatusPanel), findsNothing);
  });

  testWidgets('logout while session validation awaits cannot show metadata', (
    tester,
  ) async {
    final pendingSession = Completer<AuthSessionState>();
    final rig = _Rig(pendingSession: pendingSession.future);
    addTearDown(rig.dispose);
    await tester.pumpWidget(rig.app());
    rig.router.go('/appointments/sync');
    await tester.pump();
    rig.guard.markSignedOut();
    pendingSession.complete(_session());
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(find.byType(AppointmentCreateSyncStatusPanel), findsNothing);
    expect(rig.pageReads, 0);
  });

  testWidgets('reader cannot navigate through route into resend or replay', (
    tester,
  ) async {
    final rig = _Rig();
    addTearDown(rig.dispose);
    await tester.pumpWidget(rig.app());
    rig.router.go('/appointments/sync');
    await tester.pumpAndSettle();
    expect(find.textContaining('Send again'), findsNothing);
    expect(find.textContaining('Retry now'), findsNothing);
    expect(find.textContaining('Confirm appointment'), findsNothing);
    expect(find.byKey(const ValueKey('s05c-refresh')), findsOneWidget);
  });
}
