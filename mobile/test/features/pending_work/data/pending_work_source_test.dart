import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/sync/operation_discard_service.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_worker.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_create_queue_state_read_model.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/pending_work/data/pending_work_source.dart';

AuthSessionState _state({
  String role = 'nurse',
  int user = 9,
  int clinic = 3,
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'p25-test-token',
    refreshToken: 'p25-refresh',
    userId: user,
    role: role,
    clinicContextId: clinic,
  ),
);

AppointmentCreateQueuePage _page() => AppointmentCreateQueuePage.ready(
  items: const <AppointmentCreateQueueSummary>[],
  page: 1,
  hasNext: false,
);

void main() {
  test('read fails closed for an unauthorized or changed session', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var calls = 0;
    var role = 'patient';
    final source = PendingWorkSource(
      guard: guard,
      loadSession: () async => _state(role: role),
      readPage: ({int page = 1, int pageSize = 20}) async {
        calls++;
        return _page();
      },
      reconcileAppointment: (id) async => OperationReconciliationResult(
        operationId: id,
        disposition: OperationReconciliationDisposition.acknowledged,
      ),
      discardAppointment: (id, {bool manualNoEffectConfirmed = false}) async =>
          const OperationDiscardResult(
            OperationDiscardStatus.unsupportedOperation,
          ),
    );
    expect((await source.loadPage()).available, isFalse);
    expect(calls, 0);
    role = 'nurse';
    expect((await source.loadPage()).available, isTrue);
    expect(calls, 1);
    guard.invalidate();
    expect((await source.loadPage()).available, isFalse);
    expect(calls, 1);
  });

  test(
    'unknown reconciliation permanently blocks any further mutation',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var reconciliations = 0;
      var discards = 0;
      final source = PendingWorkSource(
        guard: guard,
        loadSession: () async => _state(),
        readPage: ({int page = 1, int pageSize = 20}) async => _page(),
        reconcileAppointment: (id) async {
          reconciliations++;
          throw StateError('lost response');
        },
        discardAppointment: (id, {bool manualNoEffectConfirmed = false}) async {
          discards++;
          return const OperationDiscardResult(
            OperationDiscardStatus.unsupportedOperation,
          );
        },
      );
      expect(
        (await source.reconcile('operation-1')).outcome,
        PendingWorkActionOutcome.unconfirmed,
      );
      expect(source.actionsLocked, isTrue);
      expect(
        (await source.reconcile('operation-1')).outcome,
        PendingWorkActionOutcome.unavailable,
      );
      expect(
        (await source.discard('operation-1')).outcome,
        PendingWorkActionOutcome.unavailable,
      );
      expect(reconciliations, 1);
      expect(discards, 0);
    },
  );

  test('discard never supplies a manual no-effect attestation', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var manual = true;
    final source = PendingWorkSource(
      guard: guard,
      loadSession: () async => _state(),
      readPage: ({int page = 1, int pageSize = 20}) async => _page(),
      reconcileAppointment: (id) async => OperationReconciliationResult(
        operationId: id,
        disposition: OperationReconciliationDisposition.unresolved,
      ),
      discardAppointment: (id, {bool manualNoEffectConfirmed = false}) async {
        manual = manualNoEffectConfirmed;
        return const OperationDiscardResult(
          OperationDiscardStatus.serverDispositionNotConfirmed,
        );
      },
    );
    expect(
      (await source.discard('operation-1')).outcome,
      PendingWorkActionOutcome.rejected,
    );
    expect(manual, isFalse);
  });

  test('clinic switch while reading hides the prior queue snapshot', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var clinic = 3;
    final source = PendingWorkSource(
      guard: guard,
      loadSession: () async => _state(clinic: clinic),
      readPage: ({int page = 1, int pageSize = 20}) async {
        clinic = 4;
        return _page();
      },
      reconcileAppointment: (id) async => OperationReconciliationResult(
        operationId: id,
        disposition: OperationReconciliationDisposition.unresolved,
      ),
      discardAppointment: (id, {bool manualNoEffectConfirmed = false}) async =>
          const OperationDiscardResult(
            OperationDiscardStatus.unsupportedOperation,
          ),
    );
    expect((await source.loadPage()).available, isFalse);
  });
}
