import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/core/networking/device_connectivity_cubit.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/status/data/scoped_sync_status_source.dart';
import 'package:clinic_system_pro/features/status/presentation/authenticated_status_strip.dart';

class _FakeConnectivity implements DeviceConnectivitySource {
  _FakeConnectivity(this.status);
  DeviceNetworkStatus status;
  final changes = StreamController<DeviceNetworkStatus>.broadcast();

  @override
  Future<DeviceNetworkStatus> checkStatus() async => status;

  @override
  Stream<DeviceNetworkStatus> get statusChanges => changes.stream;

  void emit(DeviceNetworkStatus next) {
    status = next;
    changes.add(next);
  }

  Future<void> close() => changes.close();
}

AuthSessionState _ready({int? clinic = 3, String token = 'token-a'}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: token,
        refreshToken: 'refresh-a',
        userId: 44,
        role: 'doctor',
        clinicContextId: clinic,
      ),
    );

void main() {
  late _FakeConnectivity network;
  late DeviceConnectivityCubit cubit;
  bool connectivityInitialized = false;
  setUp(() {
    connectivityInitialized = false;
  });
  tearDown(() async {
    if (!connectivityInitialized) return;
    await cubit.close();
    await network.close();
  });

  Future<void> mount(
    WidgetTester tester, {
    required Future<AuthSessionState> Function() loadSession,
    required ScopedSyncCountsLoader counts,
    SessionNavigationGuard? guard,
  }) async {
    if (!connectivityInitialized) {
      network = _FakeConnectivity(DeviceNetworkStatus.connected);
      cubit = DeviceConnectivityCubit(source: network);
      connectivityInitialized = true;
      unawaited(cubit.start());
    }
    await tester.pumpWidget(
      BlocProvider<DeviceConnectivityCubit>.value(
        value: cubit,
        child: MaterialApp(
          home: Scaffold(
            body: AuthenticatedStatusStrip(
              loadSession: loadSession,
              loadSyncCounts: counts,
              sessionNavigationGuard: guard,
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('reports only scoped local queue counts and network signal', (
    tester,
  ) async {
    var checkedUser = 0;
    var checkedClinic = 0;
    Future<ScopedSyncCounts> counts({
      required int userId,
      required int clinicContextId,
    }) async {
      checkedUser = userId;
      checkedClinic = clinicContextId;
      return const ScopedSyncCounts(pending: 2, needsReview: 1);
    }

    Future<AuthSessionState> session() async => _ready();
    await mount(tester, loadSession: session, counts: counts);
    expect(checkedUser, 44);
    expect(checkedClinic, 3);
    expect(find.text('Sync: 2 pending, 1 review'), findsOneWidget);
    expect(find.text('Device network: Connected'), findsOneWidget);
    expect(find.text('Network signal is not server health'), findsOneWidget);
    network.emit(DeviceNetworkStatus.disconnected);
    for (var frame = 0; frame < 8; frame++) {
      await tester.pump(const Duration(milliseconds: 10));
      if (find.text('Device network: Offline').evaluate().isNotEmpty) {
        break;
      }
    }
    expect(find.text('Device network: Offline'), findsOneWidget);

    network.emit(DeviceNetworkStatus.unknown);
    for (var frame = 0; frame < 8; frame++) {
      await tester.pump(const Duration(milliseconds: 10));
      if (find.text('Device network: Unknown').evaluate().isNotEmpty) {
        break;
      }
    }
    expect(find.text('Device network: Unknown'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('no clinic context never triggers a queue query or false zero', (
    tester,
  ) async {
    var reads = 0;
    Future<ScopedSyncCounts> counts({
      required int userId,
      required int clinicContextId,
    }) async {
      reads++;
      return const ScopedSyncCounts(pending: 0, needsReview: 0);
    }

    Future<AuthSessionState> session() async => _ready(clinic: null);
    await mount(tester, loadSession: session, counts: counts);
    expect(reads, 0);
    expect(find.text('Sync: Clinic context required'), findsOneWidget);
    expect(find.textContaining('Sync: 0 pending'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('local DB failure renders unknown, never zero', (tester) async {
    Future<ScopedSyncCounts> counts({
      required int userId,
      required int clinicContextId,
    }) async => throw StateError('private DB failure');
    Future<AuthSessionState> session() async => _ready();
    await mount(tester, loadSession: session, counts: counts);
    expect(find.text('Sync: Unknown'), findsOneWidget);
    expect(find.textContaining('private DB failure'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('guard invalidation removes old scoped values immediately', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    Future<ScopedSyncCounts> counts({
      required int userId,
      required int clinicContextId,
    }) async => const ScopedSyncCounts(pending: 7, needsReview: 3);
    Future<AuthSessionState> session() async => _ready();
    await mount(tester, loadSession: session, counts: counts, guard: guard);
    expect(find.text('Sync: 7 pending, 3 review'), findsOneWidget);
    guard.invalidate();
    await tester.pump();
    expect(find.text('Sync: Unavailable'), findsOneWidget);
    expect(find.textContaining('7 pending'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('old asynchronous count cannot appear after session change', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final pending = Completer<ScopedSyncCounts>();
    Future<ScopedSyncCounts> counts({
      required int userId,
      required int clinicContextId,
    }) => pending.future;
    Future<AuthSessionState> session() async => _ready();
    await tester.pumpWidget(
      BlocProvider<DeviceConnectivityCubit>.value(
        value: cubit,
        child: MaterialApp(
          home: Scaffold(
            body: AuthenticatedStatusStrip(
              loadSession: session,
              loadSyncCounts: counts,
              sessionNavigationGuard: guard,
            ),
          ),
        ),
      ),
    );
    await tester.pump();
    guard.invalidate();
    pending.complete(const ScopedSyncCounts(pending: 90, needsReview: 10));
    await tester.pumpAndSettle();
    expect(find.text('Sync: Unavailable'), findsOneWidget);
    expect(find.textContaining('90 pending'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('token rotation between read and display clears count', (
    tester,
  ) async {
    var calls = 0;
    Future<AuthSessionState> session() async {
      calls++;
      return _ready(token: calls == 1 ? 'token-a' : 'token-b');
    }

    Future<ScopedSyncCounts> counts({
      required int userId,
      required int clinicContextId,
    }) async => const ScopedSyncCounts(pending: 5, needsReview: 0);
    await mount(tester, loadSession: session, counts: counts);
    expect(find.text('Sync: Unavailable'), findsOneWidget);
    expect(find.textContaining('5 pending'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('manual refresh updates summary without reusing stale value', (
    tester,
  ) async {
    var reads = 0;
    Future<AuthSessionState> session() async => _ready();
    Future<ScopedSyncCounts> counts({
      required int userId,
      required int clinicContextId,
    }) async {
      reads++;
      return ScopedSyncCounts(pending: reads, needsReview: 0);
    }

    await mount(tester, loadSession: session, counts: counts);
    expect(find.text('Sync: 1 pending, 0 review'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('p22-status-sync-refresh')));
    await tester.pumpAndSettle();
    expect(find.text('Sync: 2 pending, 0 review'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
