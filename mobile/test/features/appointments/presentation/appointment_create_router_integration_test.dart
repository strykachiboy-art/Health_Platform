import 'dart:async';

import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/core/sync/appointment_create_queue_producer.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';
import 'package:clinic_system_pro/core/sync/operation_queue_store.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_scheduler.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_worker.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_create_screen.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';

AuthSessionState _ready({String role = 'doctor', int? clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 's03b-test-token',
        refreshToken: 's03b-test-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinic,
      ),
    );

class _Storage implements SecureKeyValueStorage {
  final Map<String, String> _values = <String, String>{};

  @override
  Future<void> write({required String key, required String value}) async {
    _values[key] = value;
  }

  @override
  Future<String?> read({required String key}) async => _values[key];

  @override
  Future<void> delete({required String key}) async {
    _values.remove(key);
  }
}

class _OfflineNetwork implements DeviceConnectivitySource {
  @override
  Future<DeviceNetworkStatus> checkStatus() async =>
      DeviceNetworkStatus.disconnected;

  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

class _QueueHarness {
  _QueueHarness() : database = AppDatabase(NativeDatabase.memory()) {
    store = OperationQueueStore(
      database,
      payloadCrypto: OperationPayloadCrypto(secureStorage: _Storage()),
    );
    scheduler = OperationReplayScheduler(
      connectivitySource: _OfflineNetwork(),
      runReplay: () async =>
          OperationReplayRunResult(items: const <OperationReplayItemResult>[]),
    );
    producer = AppointmentCreateQueueProducer(
      queueStore: store,
      replayScheduler: scheduler,
      loadContext: () async =>
          OperationPayloadContext(userId: '42', clinicContextId: '3'),
    );
  }

  final AppDatabase database;
  late final OperationQueueStore store;
  late final OperationReplayScheduler scheduler;
  late final AppointmentCreateQueueProducer producer;

  Widget wrap(Widget child) => AppointmentCreateQueueScope(
    producer: producer,
    reconcileOperation: (id) async => OperationReconciliationResult(
      operationId: id,
      disposition: OperationReconciliationDisposition.notReconciliationRequired,
    ),
    child: child,
  );

  Future<void> close() async {
    await scheduler.dispose();
    await database.close();
  }
}

SessionNavigationGuard _guard({bool ready = true}) {
  final guard = SessionNavigationGuard(
    protectedRoutePrefixes: const <String>['/appointments', '/home'],
  );
  if (ready) {
    guard.markSessionAvailable();
  } else {
    guard.markSignedOut();
  }
  return guard;
}

ApiClient _api(MockClient client) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: client,
);

GoRouter _router({
  required ApiClient api,
  required SessionNavigationGuard guard,
  Future<AuthSessionState> Function()? loadSession,
  bool includeSource = true,
  bool includeLoader = true,
  SessionNavigationGuard? sourceGuard,
}) {
  final load = loadSession ?? () async => _ready();
  return createAppRouter(
    healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
    loginScreen: const Scaffold(body: Center(child: Text('Login sentinel'))),
    loadHomeSession: includeLoader ? load : null,
    appointmentDataSource: includeSource
        ? AppointmentRemoteDataSource(
            apiClient: api,
            loadSession: load,
            sessionNavigationGuard: sourceGuard ?? guard,
          )
        : null,
    sessionNavigationGuard: guard,
  );
}

void main() {
  testWidgets('create route opens authorized form using current queue scope', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      throw StateError('Unexpected HTTP: ${request.method} ${request.url}');
    });
    addTearDown(client.close);
    final queue = _QueueHarness();
    addTearDown(queue.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);

    await tester.pumpWidget(
      queue.wrap(MaterialApp.router(routerConfig: router)),
    );
    router.go('/appointments/create');
    await tester.pumpAndSettle();
    expect(find.byType(AppointmentCreateScreen), findsOneWidget);
    expect(
      find.byKey(const ValueKey('appointment-create-ready')),
      findsOneWidget,
    );
    expect(find.textContaining('not booked until'), findsOneWidget);
    expect(calls, 0);
  });

  testWidgets('hub exposes create action only when producer is provided', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final queue = _QueueHarness();
    addTearDown(queue.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);

    await tester.pumpWidget(
      queue.wrap(MaterialApp.router(routerConfig: router)),
    );
    router.go('/appointments');
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointments-open-create')),
      findsOneWidget,
    );
    await tester.tap(find.byKey(const ValueKey('appointments-open-create')));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointment-create-ready')),
      findsOneWidget,
    );
    expect(calls, 0);
  });

  testWidgets('hub keeps create action hidden without queue scope', (
    tester,
  ) async {
    final client = MockClient((_) async => http.Response('{}', 500));
    addTearDown(client.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);

    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/appointments');
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointments-entry-ready')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('appointments-open-create')),
      findsNothing,
    );
    router.go('/appointments/create');
    await tester.pumpAndSettle();
    expect(find.text('Appointment route is unavailable.'), findsOneWidget);
    expect(find.byType(AppointmentCreateScreen), findsNothing);
  });

  testWidgets('production route delegates to the existing encrypted producer', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final queue = _QueueHarness();
    addTearDown(queue.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);

    await tester.pumpWidget(
      queue.wrap(MaterialApp.router(routerConfig: router)),
    );
    router.go('/appointments/create');
    await tester.pumpAndSettle();

    final screen = tester.widget<AppointmentCreateScreen>(
      find.byType(AppointmentCreateScreen),
    );
    final command = AppointmentCreateCommand(
      patientId: 7,
      staffId: 8,
      scheduledStart: DateTime.utc(2030, 1, 14, 9),
      scheduledEnd: DateTime.utc(2030, 1, 14, 9, 30),
      appointmentType: 'in_person',
    );
    final first = await screen.enqueue(command);
    final second = await screen.enqueue(command);

    expect(first.status, AppointmentCreateEnqueueStatus.queued);
    expect(second.status, AppointmentCreateEnqueueStatus.alreadyQueued);
    expect(second.operationId, first.operationId);
    expect(second.idempotencyKey, first.idempotencyKey);
    final stored = await queue.store.findByOperationId(command.operationId);
    expect(stored, isNotNull);
    final persisted = stored!;
    expect(persisted.idempotencyKey, command.idempotencyKey);
    expect(
      persisted.operationType,
      OperationReplayWorker.appointmentCreateType,
    );
    expect(calls, 0);
  });

  for (final role in <String>[
    'patient',
    'pharmacist',
    'super_admin',
    'accountant',
  ]) {
    testWidgets('role $role cannot operate the create form', (tester) async {
      final client = MockClient((_) async => http.Response('{}', 500));
      addTearDown(client.close);
      final queue = _QueueHarness();
      addTearDown(queue.close);
      final guard = _guard();
      addTearDown(guard.dispose);
      final router = _router(
        api: _api(client),
        guard: guard,
        loadSession: () async => _ready(role: role),
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(
        queue.wrap(MaterialApp.router(routerConfig: router)),
      );
      router.go('/appointments/create');
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('appointment-create-denied')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('appointment-create-submit')),
        findsNothing,
      );
    });
  }

  testWidgets('no clinic context denies create form', (tester) async {
    final client = MockClient((_) async => http.Response('{}', 500));
    addTearDown(client.close);
    final queue = _QueueHarness();
    addTearDown(queue.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(
      api: _api(client),
      guard: guard,
      loadSession: () async => _ready(clinic: null),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(
      queue.wrap(MaterialApp.router(routerConfig: router)),
    );
    router.go('/appointments/create');
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointment-create-denied')),
      findsOneWidget,
    );
  });

  testWidgets('missing read source fails closed even with queue scope', (
    tester,
  ) async {
    final client = MockClient((_) async => http.Response('{}', 500));
    addTearDown(client.close);
    final queue = _QueueHarness();
    addTearDown(queue.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(
      api: _api(client),
      guard: guard,
      includeSource: false,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(
      queue.wrap(MaterialApp.router(routerConfig: router)),
    );
    router.go('/appointments/create');
    await tester.pumpAndSettle();
    expect(find.text('Appointment route is unavailable.'), findsOneWidget);
    expect(find.byType(AppointmentCreateScreen), findsNothing);
  });

  testWidgets('missing session loader fails closed even with queue scope', (
    tester,
  ) async {
    final client = MockClient((_) async => http.Response('{}', 500));
    addTearDown(client.close);
    final queue = _QueueHarness();
    addTearDown(queue.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(
      api: _api(client),
      guard: guard,
      includeLoader: false,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(
      queue.wrap(MaterialApp.router(routerConfig: router)),
    );
    router.go('/appointments/create');
    await tester.pumpAndSettle();
    expect(find.text('Appointment route is unavailable.'), findsOneWidget);
  });

  testWidgets('source bound to another guard fails closed', (tester) async {
    final client = MockClient((_) async => http.Response('{}', 500));
    addTearDown(client.close);
    final queue = _QueueHarness();
    addTearDown(queue.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final otherGuard = _guard();
    addTearDown(otherGuard.dispose);
    final router = _router(
      api: _api(client),
      guard: guard,
      sourceGuard: otherGuard,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(
      queue.wrap(MaterialApp.router(routerConfig: router)),
    );
    router.go('/appointments/create');
    await tester.pumpAndSettle();
    expect(find.text('Appointment route is unavailable.'), findsOneWidget);
  });

  testWidgets('signed-out direct deep link redirects before create route', (
    tester,
  ) async {
    final client = MockClient((_) async => http.Response('{}', 500));
    addTearDown(client.close);
    final queue = _QueueHarness();
    addTearDown(queue.close);
    final guard = _guard(ready: false);
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);
    await tester.pumpWidget(
      queue.wrap(MaterialApp.router(routerConfig: router)),
    );
    router.go('/appointments/create');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(find.byType(AppointmentCreateScreen), findsNothing);
  });

  testWidgets('logout removes create form and protected draft', (tester) async {
    final client = MockClient((_) async => http.Response('{}', 500));
    addTearDown(client.close);
    final queue = _QueueHarness();
    addTearDown(queue.close);
    final guard = _guard();
    addTearDown(guard.dispose);
    final router = _router(api: _api(client), guard: guard);
    addTearDown(router.dispose);
    await tester.pumpWidget(
      queue.wrap(MaterialApp.router(routerConfig: router)),
    );
    router.go('/appointments/create');
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointment-create-ready')),
      findsOneWidget,
    );
    guard.markSignedOut();
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(find.byType(AppointmentCreateScreen), findsNothing);
  });
}
