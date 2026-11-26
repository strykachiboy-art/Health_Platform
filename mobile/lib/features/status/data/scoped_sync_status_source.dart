import 'package:drift/drift.dart';

import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';

/// Scoped queue metadata only. No operation body, token, or patient record is
/// retrieved and a count never implies that an operation reached the server.
class ScopedSyncCounts {
  const ScopedSyncCounts({required this.pending, required this.needsReview});

  /// Queued, in-flight and retry-scheduled operations, including deliveries
  /// whose outcome may still be unknown.
  final int pending;

  /// Reconciliation or explicit user intervention is needed. Not replayable
  /// automatically, even when connectivity returns.
  final int needsReview;
}

typedef ScopedSyncCountsLoader = Future<ScopedSyncCounts> Function({
  required int userId,
  required int clinicContextId,
  required String? tenantId,
});

/// Reads the existing local Drift table without creating a new queue or
/// changing its lifecycle. All queries enforce both owner and clinic scope.
class ScopedSyncStatusSource {
  const ScopedSyncStatusSource({required this.database});

  final AppDatabase database;

  Future<ScopedSyncCounts> load({
    required int userId,
    required int clinicContextId,
  }) async {
    if (userId <= 0 || clinicContextId <= 0) {
      throw ArgumentError('A positive user and clinic context are required.');
    }

    return loadForContext(OperationPayloadContext(
      userId: userId.toString(),
      clinicContextId: clinicContextId.toString(),
    ));
  }

  /// Production composition passes the tenant-bound local scope.
  Future<ScopedSyncCounts> loadForContext(
    OperationPayloadContext context, {
    Set<String>? operationTypes,
  }) async {
    final table = database.operationQueueEntry;
    final tally = table.operationId.count();
    final rows =
        await (database.selectOnly(table)
              ..addColumns([table.lifecycleState, tally])
              ..where(
                table.ownerUserId.equals(context.userId) &
                    table.clinicContextId.equals(context.clinicContextId) &
                    (operationTypes == null ? const Constant(true) : table.operationType.isIn(operationTypes)),
              )
              ..groupBy([table.lifecycleState]))
            .get();

    var pending = 0;
    var review = 0;
    for (final row in rows) {
      final state = row.read(table.lifecycleState);
      final count = row.read(tally);
      if (state == null || count == null || count < 0) {
        throw const FormatException('Invalid local sync metadata.');
      }
      switch (state) {
        case 'queued':
        case 'inFlight':
        case 'retryScheduled':
          pending += count;
          break;
        case 'reconciliationRequired':
        case 'userInterventionRequired':
          review += count;
          break;
        case 'acknowledged':
        case 'rejected':
        case 'cancelled':
          break;
        default:
          // Future/unknown states must never be reported as zero pending.
          throw const FormatException('Unknown local sync state.');
      }
    }
    return ScopedSyncCounts(pending: pending, needsReview: review);
  }
}
