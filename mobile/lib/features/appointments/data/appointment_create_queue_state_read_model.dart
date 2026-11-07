import '../../../core/sync/operation_lifecycle.dart';
import '../../../core/sync/operation_payload_crypto.dart';
import '../../../core/sync/operation_queue_store.dart';
import '../../../core/sync/operation_replay_worker.dart';

/// A read-only snapshot of the existing authenticated owner/clinic context.
/// Revision must come from the app's existing session/navigation guard.
class AppointmentCreateQueueReadSession {
  const AppointmentCreateQueueReadSession({
    required this.context,
    required this.revision,
  });

  final OperationPayloadContext context;
  final int revision;
}

typedef AppointmentCreateQueueSessionLoader =
    Future<AppointmentCreateQueueReadSession?> Function();

enum AppointmentCreateQueueDisplayState {
  pending,
  sending,
  retryScheduled,
  reconciliationRequired,
  conflictReview,
  acknowledged,
  rejected,
  userInterventionRequired,
  cancelled,
}

/// Safe UI projection: no idempotency key, payload, token, patient or notes.
class AppointmentCreateQueueSummary {
  const AppointmentCreateQueueSummary({
    required this.operationId,
    required this.state,
    required this.createdAt,
    required this.attemptsCompleted,
    this.nextAttemptAt,
  });

  final String operationId;
  final AppointmentCreateQueueDisplayState state;
  final DateTime createdAt;
  final int attemptsCompleted;
  final DateTime? nextAttemptAt;
}

class AppointmentCreateQueuePage {
  AppointmentCreateQueuePage.ready({
    required List<AppointmentCreateQueueSummary> items,
    required this.page,
    required this.hasNext,
  }) : available = true,
       items = List<AppointmentCreateQueueSummary>.unmodifiable(items);

  const AppointmentCreateQueuePage.unavailable({required this.page})
    : available = false,
      items = const <AppointmentCreateQueueSummary>[],
      hasNext = false;

  final bool available;
  final int page;
  final bool hasNext;
  final List<AppointmentCreateQueueSummary> items;
}

/// Read-only queue metadata adapter. It creates no new queue or replay path.
///
/// The UI must not interpret a pending/sending/retry item as a booking.
/// Even an acknowledged entry indicates local durable server acknowledgement,
/// not an independent patient appointment record lookup.
class AppointmentCreateQueueStateReadModel {
  const AppointmentCreateQueueStateReadModel({
    required OperationQueueStore queueStore,
    required AppointmentCreateQueueSessionLoader loadSession,
  }) : this._(queueStore, loadSession);

  const AppointmentCreateQueueStateReadModel._(
    this._queueStore,
    this._loadSession,
  );

  final OperationQueueStore _queueStore;
  final AppointmentCreateQueueSessionLoader _loadSession;

  static final Set<String> _conflictFailures = <String>{
    OperationAttemptFailure.serverConflict.name,
    OperationAttemptFailure.appointmentPatientScheduleConflict.name,
    OperationAttemptFailure.appointmentStaffScheduleConflict.name,
    OperationAttemptFailure.idempotencyKeyPayloadMismatch.name,
    OperationAttemptFailure.idempotencyRecordUnresolved.name,
  };

  /// Returns a bounded page of this session's appointment-create metadata.
  /// The loader must reflect the currently authorized session and guard
  /// revision, not a cached identity. A changed context/revision fails closed.
  Future<AppointmentCreateQueuePage> loadPage({
    int page = 1,
    int pageSize = 25,
  }) async {
    if (page < 1 || page > 10000) {
      throw ArgumentError.value(page, 'page', 'Must be between 1 and 10000.');
    }
    if (pageSize < 1 || pageSize > 50) {
      throw ArgumentError.value(
        pageSize,
        'pageSize',
        'Must be between 1 and 50.',
      );
    }

    try {
      final before = await _loadSession();
      if (before == null) {
        return AppointmentCreateQueuePage.unavailable(page: page);
      }

      final rows = await _queueStore.listScopedOperationPage(
        context: before.context,
        operationType: OperationReplayWorker.appointmentCreateType,
        limit: pageSize + 1,
        offset: (page - 1) * pageSize,
      );

      final after = await _loadSession();
      if (after == null ||
          before.revision != after.revision ||
          before.context.userId != after.context.userId ||
          before.context.clinicContextId != after.context.clinicContextId) {
        return AppointmentCreateQueuePage.unavailable(page: page);
      }

      final summaries = <AppointmentCreateQueueSummary>[];
      for (final row in rows.take(pageSize)) {
        summaries.add(
          AppointmentCreateQueueSummary(
            operationId: row.operationId,
            state: _toState(row.lifecycleState, row.lastFailureCategory),
            createdAt: row.createdAt.toUtc(),
            attemptsCompleted: row.attemptsCompleted,
            nextAttemptAt: row.nextAttemptAt?.toUtc(),
          ),
        );
      }
      return AppointmentCreateQueuePage.ready(
        items: summaries,
        page: page,
        hasNext: rows.length > pageSize,
      );
    } catch (_) {
      // No cross-session data or raw database/crypto exceptions reach the UI.
      return AppointmentCreateQueuePage.unavailable(page: page);
    }
  }

  static AppointmentCreateQueueDisplayState _toState(
    String lifecycleState,
    String? failureCategory,
  ) {
    final state = OperationLifecycleState.values.byName(lifecycleState);
    if ((state == OperationLifecycleState.reconciliationRequired ||
            state == OperationLifecycleState.userInterventionRequired) &&
        _conflictFailures.contains(failureCategory)) {
      return AppointmentCreateQueueDisplayState.conflictReview;
    }

    return switch (state) {
      OperationLifecycleState.queued =>
        AppointmentCreateQueueDisplayState.pending,
      OperationLifecycleState.inFlight =>
        AppointmentCreateQueueDisplayState.sending,
      OperationLifecycleState.retryScheduled =>
        AppointmentCreateQueueDisplayState.retryScheduled,
      OperationLifecycleState.reconciliationRequired =>
        AppointmentCreateQueueDisplayState.reconciliationRequired,
      OperationLifecycleState.acknowledged =>
        AppointmentCreateQueueDisplayState.acknowledged,
      OperationLifecycleState.rejected =>
        AppointmentCreateQueueDisplayState.rejected,
      OperationLifecycleState.userInterventionRequired =>
        AppointmentCreateQueueDisplayState.userInterventionRequired,
      OperationLifecycleState.cancelled =>
        AppointmentCreateQueueDisplayState.cancelled,
    };
  }
}
