import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_create_queue_state_read_model.dart';
import 'package:clinic_system_pro/features/pending_work/data/pending_work_source.dart';
import 'package:clinic_system_pro/features/pending_work/presentation/pending_work_center_screen.dart';

class _FakeSource implements PendingWorkAccess {
  _FakeSource(this.guard);
  @override
  final SessionNavigationGuard guard;
  int reconcileCalls = 0;
  int discardCalls = 0;

  @override
  Future<AppointmentCreateQueuePage> loadPage({
    int page = 1,
    int pageSize = 20,
  }) async => AppointmentCreateQueuePage.ready(
    page: page,
    hasNext: false,
    items: [
      AppointmentCreateQueueSummary(
        operationId: 'internal-operation-id-not-rendered',
        state: AppointmentCreateQueueDisplayState.reconciliationRequired,
        createdAt: DateTime.utc(2026, 11, 1),
        attemptsCompleted: 1,
      ),
    ],
  );

  @override
  Future<PendingWorkActionResult> reconcile(String operationId) async {
    reconcileCalls++;
    return const PendingWorkActionResult(
      PendingWorkActionOutcome.reviewRequired,
    );
  }

  @override
  Future<PendingWorkActionResult> discard(String operationId) async {
    discardCalls++;
    return const PendingWorkActionResult(PendingWorkActionOutcome.rejected);
  }
}

void main() {
  testWidgets('P25 hides pending metadata after session invalidation', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _FakeSource(guard);
    await tester.pumpWidget(
      MaterialApp(home: PendingWorkCenterScreen(source: source)),
    );
    await tester.pumpAndSettle();
    expect(find.text('Reconciliation required'), findsOneWidget);
    expect(find.textContaining('internal-operation-id'), findsNothing);
    expect(find.textContaining('idempotency'), findsNothing);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p25-locked')), findsOneWidget);
    expect(find.text('Reconciliation required'), findsNothing);
  });

  testWidgets('P25 review is explicit and does not send duplicates', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _FakeSource(guard);
    await tester.pumpWidget(
      MaterialApp(home: PendingWorkCenterScreen(source: source)),
    );
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byKey(const ValueKey('p25-reconcile-0')));
    await tester.tap(find.byKey(const ValueKey('p25-reconcile-0')));
    await tester.pumpAndSettle();
    expect(source.reconcileCalls, 0);
    await tester.tap(find.byKey(const ValueKey('p25-confirm-action')));
    await tester.pumpAndSettle();
    expect(source.reconcileCalls, 1);
    expect(source.discardCalls, 0);
    expect(find.textContaining('manual review'), findsOneWidget);
    expect(find.byKey(const ValueKey('p25-work-0')), findsNothing);
  });
}
