import 'dart:async';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/core/sync/operation_lifecycle.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';
import 'package:clinic_system_pro/core/sync/operation_queue_store.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_create_queue_state_read_model.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_create_sync_status_panel.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:drift/drift.dart' show driftRuntimeOptions;
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// Synthetic S07 cross-flow checks: encrypted create queue -> scoped metadata
/// -> authenticated route -> read-only status UI. No live backend or PHI.
class _MemoryKeys implements SecureKeyValueStorage {
  final Map<String, String> values = <String, String>{};

  @override
  Future<String?> read({required String key}) async => values[key];

  @override
  Future<void> write({required String key, required String value}) async {
    values[key] = value;
  }

  @override
  Future<void> delete({required String key}) async {
    values.remove(key);
  }
}

class _Rig {
  _Rig()
    : database = AppDatabase(NativeDatabase.memory()),
      guard = SessionNavigationGuard(
        protectedRoutePrefixes: const <String>['/appointments'],
      ) {
    guard.markSessionAvailable();
    queue = OperationQueueStore(
      database,
      payloadCrypto: OperationPayloadCrypto(secureStorage: _MemoryKeys()),
    );
    reader = AppointmentCreateQueueStateReadModel(
      queueStore: queue,
      loadSession: _readSession,
    );
    client = MockClient((request) async {
      httpRequests++;
      return http.Response('Unexpected network request', 500);
    });
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(
        body: Center(child: Text('S07 login sentinel')),
      ),
      loadHomeSession: _loadSession,
      sessionNavigationGuard: guard,
      appointmentDataSource: AppointmentRemoteDataSource(
        apiClient: api,
        loadSession: _loadSession,
        sessionNavigationGuard: guard,
      ),
      appointmentCreateSyncPageLoader: _loadPage,
    );
  }

  final AppDatabase database;
  final SessionNavigationGuard guard;
  late final OperationQueueStore queue;
  late final AppointmentCreateQueueStateReadModel reader;
  late final MockClient client;
  late final GoRouter router;
  int httpRequests = 0;
  int userId = 41;
  int clinicId = 7;
  String role = 'DOCTOR';
  bool storageAvailable = true;

  OperationPayloadContext context({int? user, int? clinic}) =>
      OperationPayloadContext(
        userId: (user ?? userId).toString(),
        clinicContextId: (clinic ?? clinicId).toString(),
      );

  Future<AuthSessionState> _loadSession() async {
    if (!storageAvailable || !guard.canAccessProtectedRoutes) {
      return const AuthSessionState.signedOut();
    }
    return AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 's07-synthetic-token',
        refreshToken: 's07-synthetic-refresh',
        userId: userId,
        role: role,
        clinicContextId: clinicId,
      ),
    );
  }

  Future<AppointmentCreateQueueReadSession?> _readSession() async {
    final revision = guard.revision;
    final state = await _loadSession();
    final session = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        session == null ||
        session.userId <= 0 ||
        session.clinicContextId == null ||
        session.clinicContextId! <= 0 ||
        !guard.canAccessProtectedRoutes ||
        guard.revision != revision) {
      return null;
    }
    return AppointmentCreateQueueReadSession(
      context: OperationPayloadContext(
        userId: session.userId.toString(),
        clinicContextId: session.clinicContextId.toString(),
      ),
      revision: revision,
    );
  }

  Future<AppointmentCreateQueuePage> _loadPage({
    required int page,
    required int pageSize,
  }) => reader.loadPage(page: page, pageSize: pageSize);

  Future<OperationQueueEnqueueResult> add(
    String id, {
    int? user,
    int? clinic,
    String operationType = 'appointment.create',
    DateTime? at,
  }) => queue.enqueueEncrypted(
    OperationIdentity(
      operationId: id,
      operationType: operationType,
      idempotencyKey: 's07-key-$id',
    ),
    context: context(user: user, clinic: clinic),
    payload: <String, dynamic>{
      'synthetic': id,
      'private_note': 'S07_PRIVATE_CONTENT_NEVER_RENDER',
    },
    now: at ?? DateTime.utc(2026, 11, 7, 8),
  );

  Future<void> start(String id) async {
    await queue.transition(
      operationId: id,
      expectedState: OperationLifecycleState.queued,
      nextState: OperationLifecycleState.inFlight,
      context: context(),
      now: DateTime.utc(2026, 11, 7, 9),
    );
  }

  Widget app() => MaterialApp.router(routerConfig: router);

  Future<void> close() async {
    router.dispose();
    guard.dispose();
    client.close();
    await database.close();
  }
}

Future<void> _open(WidgetTester tester, _Rig rig) async {
  await tester.pumpWidget(rig.app());
  rig.router.go('/appointments/sync');
  await tester.pumpAndSettle();
  expect(find.byType(AppointmentCreateSyncStatusPanel), findsOneWidget);
}

Future<void> _refresh(WidgetTester tester) async {
  await tester.tap(find.byKey(const ValueKey('s05c-refresh')));
  await tester.pumpAndSettle();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  driftRuntimeOptions.dontWarnAboutMultipleDatabases = true;

  testWidgets(
    'encrypted create appears as unconfirmed status on authenticated route',
    (tester) async {
      final rig = _Rig();
      addTearDown(rig.close);
      expect(
        await rig.add('s07-create-1'),
        OperationQueueEnqueueResult.inserted,
      );

      await _open(tester, rig);
      expect(find.textContaining('Queued locally'), findsOneWidget);
      expect(find.textContaining('Not booking confirmation'), findsOneWidget);
      expect(
        find.textContaining('S07_PRIVATE_CONTENT_NEVER_RENDER'),
        findsNothing,
      );
      expect(find.textContaining('s07-create-1'), findsNothing);
      expect(find.textContaining('s07-key-'), findsNothing);
      expect(rig.httpRequests, 0);
    },
  );

  testWidgets(
    'duplicate durable create remains one visible pending operation',
    (tester) async {
      final rig = _Rig();
      addTearDown(rig.close);
      expect(await rig.add('s07-once'), OperationQueueEnqueueResult.inserted);
      expect(
        await rig.add('s07-once'),
        OperationQueueEnqueueResult.alreadyPresent,
      );

      await _open(tester, rig);
      expect(find.byKey(const ValueKey('s05c-entry-0')), findsOneWidget);
      expect(find.byKey(const ValueKey('s05c-entry-1')), findsNothing);
      expect(rig.httpRequests, 0);
    },
  );

  testWidgets(
    'unknown outcome moves from pending to reconciliation after refresh',
    (tester) async {
      final rig = _Rig();
      addTearDown(rig.close);
      await rig.add('s07-unknown');
      await _open(tester, rig);
      expect(find.textContaining('Queued locally'), findsOneWidget);

      await rig.start('s07-unknown');
      final result = await rig.queue.recordFailure(
        operationId: 's07-unknown',
        failure: OperationAttemptFailure.outcomeUnknown,
        now: DateTime.utc(2026, 11, 7, 9),
      );
      expect(
        result.lifecycleState,
        OperationLifecycleState.reconciliationRequired.name,
      );
      await _refresh(tester);
      expect(find.textContaining('reconciliation required'), findsOneWidget);
      expect(find.textContaining('Queued locally'), findsNothing);
      expect(find.textContaining('Retry now'), findsNothing);
      expect(find.textContaining('Send again'), findsNothing);
      expect(rig.httpRequests, 0);
    },
  );

  testWidgets(
    'patient schedule conflict requires review, never second submission',
    (tester) async {
      final rig = _Rig();
      addTearDown(rig.close);
      await rig.add('s07-conflict');
      await rig.start('s07-conflict');
      await rig.queue.recordFailure(
        operationId: 's07-conflict',
        failure: OperationAttemptFailure.appointmentPatientScheduleConflict,
        now: DateTime.utc(2026, 11, 7, 9),
      );
      await _open(tester, rig);
      expect(find.textContaining('Conflict requires review'), findsOneWidget);
      expect(find.textContaining('Server acknowledgement'), findsNothing);
      expect(find.textContaining('Retry now'), findsNothing);
      expect(rig.httpRequests, 0);
    },
  );

  testWidgets('acknowledgement shown as local receipt, not confirmed booking', (
    tester,
  ) async {
    final rig = _Rig();
    addTearDown(rig.close);
    await rig.add('s07-ack');
    await rig.start('s07-ack');
    await rig.queue.transition(
      operationId: 's07-ack',
      expectedState: OperationLifecycleState.inFlight,
      nextState: OperationLifecycleState.acknowledged,
      now: DateTime.utc(2026, 11, 7, 10),
    );
    await _open(tester, rig);
    expect(
      find.textContaining('Server acknowledgement recorded'),
      findsOneWidget,
    );
    expect(find.textContaining('check appointment list'), findsOneWidget);
    expect(find.textContaining('Booking confirmed'), findsNothing);
    expect(rig.httpRequests, 0);
  });

  testWidgets(
    'same-clinic foreign owner and unverified status operation remain hidden',
    (tester) async {
      final rig = _Rig();
      addTearDown(rig.close);
      await rig.add('s07-own');
      await rig.add('s07-foreign', user: 42);
      await rig.add('s07-status', operationType: 'appointment.cancel');
      await _open(tester, rig);
      expect(find.byKey(const ValueKey('s05c-entry-0')), findsOneWidget);
      expect(find.byKey(const ValueKey('s05c-entry-1')), findsNothing);
      expect(find.textContaining('s07-foreign'), findsNothing);
      expect(find.textContaining('s07-status'), findsNothing);
      expect(rig.httpRequests, 0);
    },
  );

  testWidgets('other-clinic create never appears for current clinic', (
    tester,
  ) async {
    final rig = _Rig();
    addTearDown(rig.close);
    await rig.add('s07-own');
    await rig.add('s07-other-clinic', clinic: 8);
    await _open(tester, rig);
    expect(find.byKey(const ValueKey('s05c-entry-0')), findsOneWidget);
    expect(find.byKey(const ValueKey('s05c-entry-1')), findsNothing);
    expect(rig.httpRequests, 0);
  });

  testWidgets(
    'sign-out immediately removes pending queue UI and redirects to login',
    (tester) async {
      final rig = _Rig();
      addTearDown(rig.close);
      await rig.add('s07-visible-before-signout');
      await _open(tester, rig);
      expect(find.byKey(const ValueKey('s05c-entry-0')), findsOneWidget);
      rig.guard.markSignedOut();
      await tester.pumpAndSettle();
      expect(find.text('S07 login sentinel'), findsOneWidget);
      expect(find.byType(AppointmentCreateSyncStatusPanel), findsNothing);
      expect(rig.httpRequests, 0);
    },
  );

  testWidgets('invalid role rejects real queued metadata at route boundary', (
    tester,
  ) async {
    final rig = _Rig();
    addTearDown(rig.close);
    rig.role = 'PATIENT';
    await rig.add('s07-private');
    await tester.pumpWidget(rig.app());
    rig.router.go('/appointments/sync');
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('s05c2-session-denied')), findsOneWidget);
    expect(find.byType(AppointmentCreateSyncStatusPanel), findsNothing);
    expect(rig.httpRequests, 0);
  });

  testWidgets(
    'verified transient create retry is status-only, with no manual send action',
    (tester) async {
      final rig = _Rig();
      addTearDown(rig.close);
      await rig.add('s07-retry');
      await rig.start('s07-retry');
      await rig.queue.recordFailure(
        operationId: 's07-retry',
        failure: OperationAttemptFailure.confirmedTransientNoEffect,
        now: DateTime.utc(2026, 11, 7, 9),
        jitterSample: 0.5,
      );
      await _open(tester, rig);
      expect(
        find.textContaining('Retry scheduled for verified create request'),
        findsOneWidget,
      );
      expect(find.textContaining('Retry now'), findsNothing);
      expect(rig.httpRequests, 0);
    },
  );

  testWidgets('aged queued create is still pending, never silently confirmed', (
    tester,
  ) async {
    final rig = _Rig();
    addTearDown(rig.close);
    await rig.add('s07-aged', at: DateTime.utc(2021, 1, 1));
    await _open(tester, rig);
    expect(find.textContaining('Queued locally'), findsOneWidget);
    expect(
      find.textContaining('Server acknowledgement recorded'),
      findsNothing,
    );
    expect(rig.httpRequests, 0);
  });

  test('scoped read rejects session revision race even when owner and clinic match', () async {
    final rig = _Rig();
    try {
      await rig.add('s07-stale');
      final before = rig.guard.revision;
      final completer = Completer<AppointmentCreateQueueReadSession?>();
      final pending = AppointmentCreateQueueStateReadModel(
        queueStore: rig.queue,
        loadSession: () async {
          final stamp = await rig._readSession();
          if (stamp == null) return null;
          if (!completer.isCompleted) {
            completer.complete(stamp);
            return completer.future;
          }
          return AppointmentCreateQueueReadSession(
            context: stamp.context,
            revision: before + 1,
          );
        },
      ).loadPage();
      final result = await pending;
      expect(result.available, isFalse);
      expect(result.items, isEmpty);
    } finally {
      await rig.close();
    }
  });
}
