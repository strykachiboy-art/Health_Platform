import 'dart:async';
import 'dart:convert';

import 'package:drift/drift.dart';

import '../storage/app_database.dart';
import 'operation_lifecycle.dart';
import 'operation_payload_crypto.dart';

/// Result of attempting to enqueue a logical operation.
enum OperationQueueEnqueueResult {
  inserted,
  alreadyPresent,
  operationIdConflict,
  idempotencyKeyConflict,
  payloadAttached,
  payloadConflict,
  contextConflict,
}

enum OperationQueueKeyCleanupStatus {
  deleted,
  retainedForPayloadDependencies,
  failed,
}

enum OperationQueueDiscardCleanupStatus {
  removedAndKeyDeleted,
  removedKeyRetainedForDependencies,
  removedKeyCleanupPending,
  alreadyAbsentAndKeyDeleted,
  alreadyAbsentKeyRetainedForDependencies,
  alreadyAbsentKeyCleanupPending,
  contextMismatch,
  acknowledgedOperation,
}

class _DiscardTransactionOutcome {
  const _DiscardTransactionOutcome({
    required this.removed,
    required this.blockedStatus,
    required this.hasDependencies,
  });

  final bool removed;
  final OperationQueueDiscardCleanupStatus? blockedStatus;
  final bool hasDependencies;
}

/// Durable metadata store for the offline-operation lifecycle.
///
/// This store deliberately does not persist request or response payloads.
/// An operation handler or synchronization worker must separately establish
/// what operation can safely be delivered and reconstruct its request.
///
/// The backend remains authoritative for authorization and application state.
class OperationQueueStore {
  static const Duration terminalRetentionPeriod = Duration(days: 30);
  static const Duration unresolvedReviewAge = Duration(days: 30);

  // Serializes encrypted payload persistence with context-key deletion. The
  // lock is process-wide so separate store instances in the same app isolate
  // cannot delete a key while another store is attaching a payload under it.
  static final Map<String, Future<void>> _contextMutationTails =
      <String, Future<void>>{};

  static Future<T> _withContextMutationLock<T>(
    OperationPayloadContext context,
    Future<T> Function() action,
  ) async {
    final lockKey = jsonEncode(<String>[
      context.userId,
      context.clinicContextId,
    ]);
    final previous = _contextMutationTails[lockKey];
    final release = Completer<void>();
    _contextMutationTails[lockKey] = release.future;

    if (previous != null) await previous;

    try {
      return await action();
    } finally {
      release.complete();
      if (identical(_contextMutationTails[lockKey], release.future)) {
        _contextMutationTails.remove(lockKey);
      }
    }
  }

  OperationQueueStore(
    this._database, {
    OperationRetryPolicy? retryPolicy,
    this._payloadCrypto,
  }) : _retryPolicy = retryPolicy ?? OperationRetryPolicy();

  final AppDatabase _database;
  final OperationRetryPolicy _retryPolicy;
  final OperationPayloadCrypto? _payloadCrypto;

  /// Inserts an operation without replacing an existing operation or key.
  ///
  /// Re-enqueuing exactly the same identity is an idempotent no-op.
  /// Reusing an operation ID or idempotency key for different identity
  /// metadata is reported as a conflict and never overwrites the existing row.
  Future<OperationQueueEnqueueResult> enqueue(
    OperationIdentity identity, {
    DateTime? now,
  }) async {
    final timestamp = (now ?? DateTime.now()).toUtc();

    return _database.transaction(() async {
      final table = _database.operationQueueEntry;

      final existingById =
          await (_database.select(table)
                ..where((row) => row.operationId.equals(identity.operationId)))
              .getSingleOrNull();

      if (existingById != null) {
        final sameIdentity =
            existingById.operationType == identity.operationType &&
            existingById.idempotencyKey == identity.idempotencyKey;

        return sameIdentity
            ? OperationQueueEnqueueResult.alreadyPresent
            : OperationQueueEnqueueResult.operationIdConflict;
      }

      final existingByKey =
          await (_database.select(table)..where(
                (row) => row.idempotencyKey.equals(identity.idempotencyKey),
              ))
              .getSingleOrNull();

      if (existingByKey != null) {
        return OperationQueueEnqueueResult.idempotencyKeyConflict;
      }

      await _database
          .into(table)
          .insert(
            OperationQueueEntryCompanion.insert(
              operationId: identity.operationId,
              operationType: identity.operationType,
              idempotencyKey: identity.idempotencyKey,
              createdAt: timestamp,
              updatedAt: timestamp,
            ),
          );

      return OperationQueueEnqueueResult.inserted;
    });
  }

  /// Atomically persists queue metadata and its authenticated encrypted body.
  ///
  /// An identical re-enqueue is a no-op. Reuse of an operation ID with
  /// different payload or scope is rejected without replacing existing data.
  /// Atomically persists queue metadata and its authenticated encrypted body.
  ///
  /// The per-context mutation lock spans encryption and database persistence,
  /// so safe key cleanup cannot race between ciphertext creation and insertion.
  Future<OperationQueueEnqueueResult> enqueueEncrypted(
    OperationIdentity identity, {
    required OperationPayloadContext context,
    required Map<String, dynamic> payload,
    DateTime? now,
  }) async {
    final crypto = _payloadCrypto;
    if (crypto == null) {
      throw StateError('Encrypted payload support is not configured.');
    }

    return _withContextMutationLock(context, () async {
      // Encrypt before touching the queue. A crypto or secure-storage failure
      // must not leave a queue record that appears ready for delivery.
      final encrypted = await crypto.encrypt(
        identity: identity,
        context: context,
        payload: payload,
      );
      final timestamp = (now ?? DateTime.now()).toUtc();

      return _database.transaction(() async {
        final table = _database.operationQueueEntry;
        final payloadTable = _database.operationQueuePayload;

        final existingById =
            await (_database.select(
                  table,
                )..where((row) => row.operationId.equals(identity.operationId)))
                .getSingleOrNull();

        if (existingById != null) {
          final sameIdentity =
              existingById.operationType == identity.operationType &&
              existingById.idempotencyKey == identity.idempotencyKey;

          if (!sameIdentity) {
            return OperationQueueEnqueueResult.operationIdConflict;
          }

          if ((existingById.ownerUserId != null &&
                  existingById.ownerUserId != context.userId) ||
              (existingById.clinicContextId != null &&
                  existingById.clinicContextId != context.clinicContextId) ||
              ((existingById.ownerUserId == null) !=
                  (existingById.clinicContextId == null))) {
            return OperationQueueEnqueueResult.contextConflict;
          }

          final existingPayload =
              await (_database.select(payloadTable)..where(
                    (row) => row.operationId.equals(identity.operationId),
                  ))
                  .getSingleOrNull();

          if (existingPayload != null) {
            if (existingById.ownerUserId != context.userId ||
                existingById.clinicContextId != context.clinicContextId) {
              return OperationQueueEnqueueResult.contextConflict;
            }

            final originalPayload = await _decryptPayloadForEntry(
              existingById,
              context,
            );

            return _canonicalPayload(originalPayload) ==
                    _canonicalPayload(payload)
                ? OperationQueueEnqueueResult.alreadyPresent
                : OperationQueueEnqueueResult.payloadConflict;
          }

          // Only an untouched queued metadata row can be upgraded to carry a
          // payload. Never attach a new body after a delivery attempt/outcome.
          if (existingById.lifecycleState !=
                  OperationLifecycleState.queued.name ||
              existingById.attemptsCompleted != 0) {
            return OperationQueueEnqueueResult.payloadConflict;
          }

          await (_database.update(table)
                ..where((row) => row.operationId.equals(identity.operationId)))
              .write(
                OperationQueueEntryCompanion(
                  ownerUserId: Value<String?>(context.userId),
                  clinicContextId: Value<String?>(context.clinicContextId),
                  updatedAt: Value<DateTime>(timestamp),
                ),
              );

          await _insertEncryptedPayload(identity.operationId, encrypted);
          return OperationQueueEnqueueResult.payloadAttached;
        }

        final existingByKey =
            await (_database.select(table)..where(
                  (row) => row.idempotencyKey.equals(identity.idempotencyKey),
                ))
                .getSingleOrNull();

        if (existingByKey != null) {
          return OperationQueueEnqueueResult.idempotencyKeyConflict;
        }

        await _database
            .into(table)
            .insert(
              OperationQueueEntryCompanion.insert(
                operationId: identity.operationId,
                operationType: identity.operationType,
                idempotencyKey: identity.idempotencyKey,
                createdAt: timestamp,
                updatedAt: timestamp,
                ownerUserId: Value<String?>(context.userId),
                clinicContextId: Value<String?>(context.clinicContextId),
              ),
            );

        await _insertEncryptedPayload(identity.operationId, encrypted);
        return OperationQueueEnqueueResult.inserted;
      });
    });
  }

  Future<void> _insertEncryptedPayload(
    String operationId,
    EncryptedOperationPayload encrypted,
  ) async {
    await _database
        .into(_database.operationQueuePayload)
        .insert(
          OperationQueuePayloadCompanion.insert(
            operationId: operationId,
            ciphertext: Uint8List.fromList(encrypted.ciphertext),
            nonce: Uint8List.fromList(encrypted.nonce),
            mac: Uint8List.fromList(encrypted.mac),
            encryptionScheme: encrypted.scheme,
            encryptionKeyVersion: encrypted.keyVersion,
          ),
        );
  }

  /// Returns one operation by its stable ID, or null when it is absent.
  Future<OperationQueueEntryData?> findByOperationId(String operationId) {
    return (_database.select(
      _database.operationQueueEntry,
    )..where((row) => row.operationId.equals(operationId))).getSingleOrNull();
  }

  /// Lists only due operations with a payload, for the active owner and clinic.
  ///
  /// This is candidate selection, not authorization or a delivery claim. The
  /// caller must load the payload and transition to inFlight before sending.
  /// Omitting the active context deliberately yields no candidates.
  Future<List<OperationQueueEntryData>> listReady({
    OperationPayloadContext? context,
    DateTime? now,
    int limit = 25,
    Set<String>? operationTypes,
  }) async {
    if (limit < 1 || limit > 100) {
      throw ArgumentError.value(limit, 'limit', 'Must be between 1 and 100.');
    }
    if (operationTypes != null && operationTypes.isEmpty) {
      return const <OperationQueueEntryData>[];
    }
    if (context == null) return const <OperationQueueEntryData>[];

    final timestamp = (now ?? DateTime.now()).toUtc();
    final reviewCutoff = timestamp.subtract(unresolvedReviewAge);
    final table = _database.operationQueueEntry;
    final payloadTable = _database.operationQueuePayload;

    final query =
        _database.select(table).join([
            innerJoin(
              payloadTable,
              payloadTable.operationId.equalsExp(table.operationId),
            ),
          ])
          ..where(
            table.lifecycleState.isIn([
                  OperationLifecycleState.queued.name,
                  OperationLifecycleState.retryScheduled.name,
                ]) &
                table.createdAt.isBiggerThanValue(reviewCutoff) &
                table.attemptsCompleted.isSmallerThanValue(
                  _retryPolicy.maxAttempts,
                ) &
                (table.nextAttemptAt.isNull() |
                    table.nextAttemptAt.isSmallerOrEqualValue(timestamp)) &
                table.ownerUserId.equals(context.userId) &
                table.clinicContextId.equals(context.clinicContextId) &
                (operationTypes == null
                    ? const Constant(true)
                    : table.operationType.isIn(operationTypes)),
          )
          ..orderBy([
            OrderingTerm.asc(table.createdAt),
            OrderingTerm.asc(table.operationId),
          ])
          ..limit(limit);

    final rows = await query.get();
    return rows.map((row) => row.readTable(table)).toList(growable: false);
  }

  /// Lists unresolved entries at or beyond the review age for this context.
  ///
  /// The persisted createdAt timestamp makes the review condition durable.
  /// A review listing does not itself authorize replay or deletion.
  Future<List<OperationQueueEntryData>> listReviewRequired({
    required OperationPayloadContext context,
    DateTime? now,
    int limit = 100,
  }) async {
    if (limit < 1 || limit > 100) {
      throw ArgumentError.value(limit, 'limit', 'Must be between 1 and 100.');
    }

    final cutoff = (now ?? DateTime.now()).toUtc().subtract(
      unresolvedReviewAge,
    );
    final table = _database.operationQueueEntry;
    final unresolvedStates = <String>{
      OperationLifecycleState.queued.name,
      OperationLifecycleState.inFlight.name,
      OperationLifecycleState.retryScheduled.name,
      OperationLifecycleState.reconciliationRequired.name,
      OperationLifecycleState.userInterventionRequired.name,
    };

    final query = _database.select(table)
      ..where(
        (entry) =>
            entry.lifecycleState.isIn(unresolvedStates) &
            entry.createdAt.isSmallerOrEqualValue(cutoff) &
            entry.ownerUserId.equals(context.userId) &
            entry.clinicContextId.equals(context.clinicContextId),
      )
      ..orderBy([
        (entry) => OrderingTerm.asc(entry.createdAt),
        (entry) => OrderingTerm.asc(entry.operationId),
      ])
      ..limit(limit);

    final rows = await query.get();
    return rows.toList(growable: false);
  }

  /// Returns the earliest scheduled retry for this owner and clinic.
  ///
  /// This is scheduling metadata only. It does not decrypt payloads or expose
  /// another user's queued work.
  Future<DateTime?> nextScheduledAttempt({
    required OperationPayloadContext context,
    DateTime? now,
  }) async {
    final cutoff = (now ?? DateTime.now()).toUtc().subtract(
      unresolvedReviewAge,
    );
    final table = _database.operationQueueEntry;

    final query = _database.select(table)
      ..where(
        (entry) =>
            entry.lifecycleState.equals(
              OperationLifecycleState.retryScheduled.name,
            ) &
            entry.nextAttemptAt.isNotNull() &
            entry.createdAt.isBiggerThanValue(cutoff) &
            entry.ownerUserId.equals(context.userId) &
            entry.clinicContextId.equals(context.clinicContextId),
      )
      ..orderBy([(entry) => OrderingTerm.asc(entry.nextAttemptAt)])
      ..limit(1);

    final row = await query.getSingleOrNull();
    return row?.nextAttemptAt;
  }

  /// Lists persisted in-flight operations for the active owner and clinic.
  ///
  /// An in-flight operation may have reached the server before the app died.
  /// Recovery must therefore treat it as an unknown outcome.
  Future<List<OperationQueueEntryData>> listInFlight({
    required OperationPayloadContext context,
    int limit = 100,
  }) async {
    if (limit < 1 || limit > 100) {
      throw ArgumentError.value(limit, 'limit', 'Must be between 1 and 100.');
    }

    final table = _database.operationQueueEntry;
    final payloadTable = _database.operationQueuePayload;

    final query =
        _database.select(table).join([
            innerJoin(
              payloadTable,
              payloadTable.operationId.equalsExp(table.operationId),
            ),
          ])
          ..where(
            table.lifecycleState.equals(OperationLifecycleState.inFlight.name) &
                table.ownerUserId.equals(context.userId) &
                table.clinicContextId.equals(context.clinicContextId),
          )
          ..orderBy([
            OrderingTerm.asc(table.updatedAt),
            OrderingTerm.asc(table.operationId),
          ])
          ..limit(limit);

    final rows = await query.get();
    return rows.map((row) => row.readTable(table)).toList(growable: false);
  }

  /// Transitions an existing operation after checking its persisted state.
  ///
  /// Entering `inFlight` increments the attempt count. A retry must provide
  /// both a classified failure and a due time. Raw error messages are not
  /// accepted or stored.
  ///
  /// Returning from reconciliation requires `reconciliationConfirmed`.
  /// Returning after user intervention requires `sessionRevalidated`; callers
  /// must actually revalidate the session and current clinic context first.
  Future<OperationQueueEntryData> transition({
    required String operationId,
    required OperationLifecycleState expectedState,
    required OperationLifecycleState nextState,
    required DateTime now,
    OperationPayloadContext? context,
    OperationAttemptFailure? failure,
    DateTime? nextAttemptAt,
    bool reconciliationConfirmed = false,
    bool sessionRevalidated = false,
    OperationReplayContract replayContract =
        OperationReplayContract.notVerified,
    double jitterSample = 0.5,
  }) async {
    if (expectedState == nextState) {
      throw ArgumentError('A transition must change the lifecycle state.');
    }
    if (nextState == OperationLifecycleState.inFlight && context == null) {
      throw ArgumentError(
        'An active user/clinic context is required for delivery.',
      );
    }

    const failureStates = <OperationLifecycleState>{
      OperationLifecycleState.retryScheduled,
      OperationLifecycleState.reconciliationRequired,
      OperationLifecycleState.rejected,
      OperationLifecycleState.userInterventionRequired,
    };

    if (failureStates.contains(nextState) && failure == null) {
      throw ArgumentError('A classified failure is required for $nextState.');
    }

    if (failure != null && !failureStates.contains(nextState)) {
      throw ArgumentError('A classified failure is not valid for $nextState.');
    }

    if (nextState == OperationLifecycleState.retryScheduled) {
      if (nextAttemptAt == null) {
        throw ArgumentError('A retry must have a scheduled next-attempt time.');
      }
    } else if (nextAttemptAt != null) {
      throw ArgumentError(
        'A next-attempt time is only valid for retryScheduled.',
      );
    }

    final timestamp = now.toUtc();
    final table = _database.operationQueueEntry;

    return _database.transaction(() async {
      final current = await (_database.select(
        table,
      )..where((row) => row.operationId.equals(operationId))).getSingleOrNull();

      if (current == null) {
        throw StateError('Operation does not exist.');
      }

      final currentState = _parseState(current.lifecycleState);

      if (currentState != expectedState) {
        throw StateError(
          'Operation state changed: expected ${expectedState.name}, '
          'found ${currentState.name}.',
        );
      }

      if (!currentState.canTransitionTo(nextState)) {
        throw StateError(
          'Illegal operation transition: '
          '${currentState.name} -> ${nextState.name}.',
        );
      }
      if (nextState == OperationLifecycleState.inFlight) {
        // Verify scope, key availability, ciphertext integrity and AAD before
        // permitting any delivery-attempt transition.
        await _decryptPayloadForEntry(current, context!);
      }

      if (failure != null) {
        final decision = decideOperationFailure(
          failure: failure,
          attemptsCompleted: current.attemptsCompleted,
          retryPolicy: _retryPolicy,
          replayContract: replayContract,
        );

        if (decision.nextState != nextState) {
          throw StateError(
            'Failure classification requires '
            '${decision.nextState.name}.',
          );
        }

        if (nextState == OperationLifecycleState.retryScheduled) {
          final minimumNextAttemptAt = timestamp.add(
            _retryPolicy.delayAfterFailure(
              current.attemptsCompleted,
              jitterSample: jitterSample,
            ),
          );

          if (nextAttemptAt!.toUtc().isBefore(minimumNextAttemptAt)) {
            throw ArgumentError(
              'Next attempt time violates the configured backoff.',
            );
          }
        }
      }

      if (currentState == OperationLifecycleState.reconciliationRequired &&
          nextState == OperationLifecycleState.queued &&
          !reconciliationConfirmed) {
        throw StateError(
          'Reconciliation must be confirmed before re-queueing.',
        );
      }

      if (currentState == OperationLifecycleState.userInterventionRequired &&
          nextState == OperationLifecycleState.queued &&
          !sessionRevalidated) {
        throw StateError(
          'Session and authorization context must be revalidated first.',
        );
      }

      if (nextState == OperationLifecycleState.queued &&
          current.attemptsCompleted >= _retryPolicy.maxAttempts) {
        throw StateError(
          'Retry budget is exhausted; create a newly reviewed operation.',
        );
      }

      if (nextState == OperationLifecycleState.retryScheduled &&
          current.attemptsCompleted >= _retryPolicy.maxAttempts) {
        throw StateError(
          'Retry budget is exhausted; automatic retry is not permitted.',
        );
      }

      var attempts = current.attemptsCompleted;

      if (nextState == OperationLifecycleState.inFlight) {
        if (attempts >= _retryPolicy.maxAttempts) {
          throw StateError(
            'Retry budget is exhausted; automatic delivery is not permitted.',
          );
        }
        attempts++;
      }

      final updatedRows =
          await (_database.update(table)..where(
                (row) =>
                    row.operationId.equals(operationId) &
                    row.lifecycleState.equals(expectedState.name),
              ))
              .write(
                OperationQueueEntryCompanion(
                  lifecycleState: Value(nextState.name),
                  attemptsCompleted: Value(attempts),
                  updatedAt: Value(timestamp),
                  nextAttemptAt: Value<DateTime?>(
                    nextState == OperationLifecycleState.retryScheduled
                        ? nextAttemptAt!.toUtc()
                        : null,
                  ),
                  lastFailureCategory: Value<String?>(failure?.name),
                ),
              );

      if (updatedRows != 1) {
        throw StateError(
          'Operation state changed during the transition; reload and reconcile.',
        );
      }

      final updated = await (_database.select(
        table,
      )..where((row) => row.operationId.equals(operationId))).getSingleOrNull();

      if (updated == null) {
        throw StateError('Operation disappeared after transition.');
      }

      return updated;
    });
  }

  /// Returns the clear request only after checking the caller's active context.
  ///
  /// The returned map must still be dispatched only through a verified,
  /// operation-specific adapter. Stored context is never authorization.
  Future<Map<String, dynamic>> loadPayload({
    required String operationId,
    required OperationPayloadContext context,
  }) async {
    final current = await findByOperationId(operationId);
    if (current == null) {
      throw StateError('Operation does not exist.');
    }
    return _decryptPayloadForEntry(current, context);
  }

  Future<Map<String, dynamic>> _decryptPayloadForEntry(
    OperationQueueEntryData entry,
    OperationPayloadContext context,
  ) async {
    final crypto = _payloadCrypto;
    if (crypto == null) {
      throw StateError('Encrypted payload support is not configured.');
    }

    if (entry.ownerUserId != context.userId ||
        entry.clinicContextId != context.clinicContextId) {
      throw StateError('Operation owner does not match the active context.');
    }

    final stored =
        await (_database.select(_database.operationQueuePayload)
              ..where((row) => row.operationId.equals(entry.operationId)))
            .getSingleOrNull();

    if (stored == null) {
      throw StateError('Operation has no persisted encrypted payload.');
    }

    return crypto.decrypt(
      identity: OperationIdentity(
        operationId: entry.operationId,
        operationType: entry.operationType,
        idempotencyKey: entry.idempotencyKey,
      ),
      context: context,
      encrypted: EncryptedOperationPayload(
        scheme: stored.encryptionScheme,
        keyVersion: stored.encryptionKeyVersion,
        nonce: stored.nonce,
        ciphertext: stored.ciphertext,
        mac: stored.mac,
      ),
    );
  }

  String _canonicalPayload(Map<String, dynamic> payload) {
    Object? normalize(Object? value) {
      if (value is Map) {
        final keys = value.keys.cast<String>().toList()..sort();
        return <String, Object?>{
          for (final key in keys) key: normalize(value[key]),
        };
      }
      if (value is List) {
        return value.map(normalize).toList(growable: false);
      }
      return value;
    }

    return jsonEncode(normalize(payload));
  }

  /// Applies the approved 30-day retention window to terminal entries.
  ///
  /// This is safe to repeat and never removes unresolved work or encryption keys.
  Future<int> purgeTerminalOperationsPastRetention({required DateTime now}) {
    final cutoff = now.toUtc().subtract(terminalRetentionPeriod);
    return purgeExpiredTerminalOperations(olderThan: cutoff);
  }

  /// Deletes terminal operations older than an explicit retention cutoff.
  ///
  /// Retention policy belongs to the caller. Active or unresolved operations
  /// are never removed by this method. Payload and entry deletion are atomic.
  Future<int> purgeExpiredTerminalOperations({
    required DateTime olderThan,
  }) async {
    final cutoff = olderThan.toUtc();
    final terminalStates = <String>{
      OperationLifecycleState.acknowledged.name,
      OperationLifecycleState.rejected.name,
      OperationLifecycleState.cancelled.name,
    };
    final entryTable = _database.operationQueueEntry;
    final payloadTable = _database.operationQueuePayload;

    return _database.transaction(() async {
      final expired =
          await (_database.select(entryTable)..where(
                (row) =>
                    row.lifecycleState.isIn(terminalStates) &
                    row.updatedAt.isSmallerThanValue(cutoff),
              ))
              .get();

      if (expired.isEmpty) return 0;

      final operationIds = expired
          .map((row) => row.operationId)
          .toList(growable: false);

      await (_database.delete(
        payloadTable,
      )..where((row) => row.operationId.isIn(operationIds))).go();

      return (_database.delete(
        entryTable,
      )..where((row) => row.operationId.isIn(operationIds))).go();
    });
  }

  /// Removes one local queue record only after a server-issued no-effect
  /// disposition receipt has been validated by the caller.
  ///
  /// The record and encrypted body are deleted in one Drift transaction. The
  /// context mutation lock then prevents a new payload from appearing between
  /// the dependency check and deletion of its context key. A secure-storage
  /// failure is reported as pending cleanup because the DB deletion has already
  /// committed; retrying cleanup is safe and does not recreate queue data.
  Future<OperationQueueDiscardCleanupStatus> discardAfterConfirmedDisposition({
    required String operationId,
    required OperationPayloadContext context,
    required int serverDispositionId,
  }) async {
    if (serverDispositionId <= 0) {
      throw ArgumentError.value(
        serverDispositionId,
        'serverDispositionId',
        'Must be a positive server disposition record ID.',
      );
    }
    final crypto = _payloadCrypto;
    if (crypto == null) {
      throw StateError('Encrypted payload support is not configured.');
    }

    return _withContextMutationLock(context, () async {
      final outcome = await _database.transaction(() async {
        final entryTable = _database.operationQueueEntry;
        final payloadTable = _database.operationQueuePayload;
        final current =
            await (_database.select(entryTable)
                  ..where((row) => row.operationId.equals(operationId)))
                .getSingleOrNull();

        if (current != null &&
            (current.ownerUserId != context.userId ||
                current.clinicContextId != context.clinicContextId)) {
          return _DiscardTransactionOutcome(
            removed: false,
            blockedStatus: OperationQueueDiscardCleanupStatus.contextMismatch,
            hasDependencies: true,
          );
        }

        // A locally acknowledged operation represents a known server effect;
        // never erase it through the no-effect discard path.
        if (current?.lifecycleState ==
            OperationLifecycleState.acknowledged.name) {
          return _DiscardTransactionOutcome(
            removed: false,
            blockedStatus:
                OperationQueueDiscardCleanupStatus.acknowledgedOperation,
            hasDependencies: true,
          );
        }

        var removed = false;
        if (current != null) {
          await (_database.delete(
            payloadTable,
          )..where((row) => row.operationId.equals(operationId))).go();
          final deleted =
              await (_database.delete(entryTable)..where(
                    (row) =>
                        row.operationId.equals(operationId) &
                        row.ownerUserId.equals(context.userId) &
                        row.clinicContextId.equals(context.clinicContextId),
                  ))
                  .go();
          if (deleted != 1) {
            throw StateError(
              'Queue entry changed during confirmed-disposition cleanup.',
            );
          }
          removed = true;
        }

        final hasDependencies = await _hasPayloadDependenciesForContext(
          context,
        );
        return _DiscardTransactionOutcome(
          removed: removed,
          blockedStatus: null,
          hasDependencies: hasDependencies,
        );
      });

      if (outcome.blockedStatus != null) return outcome.blockedStatus!;
      if (outcome.hasDependencies) {
        return outcome.removed
            ? OperationQueueDiscardCleanupStatus
                  .removedKeyRetainedForDependencies
            : OperationQueueDiscardCleanupStatus
                  .alreadyAbsentKeyRetainedForDependencies;
      }

      try {
        await crypto.deleteContextKeyForQueueCleanup(context);
        return outcome.removed
            ? OperationQueueDiscardCleanupStatus.removedAndKeyDeleted
            : OperationQueueDiscardCleanupStatus.alreadyAbsentAndKeyDeleted;
      } on Object {
        return outcome.removed
            ? OperationQueueDiscardCleanupStatus.removedKeyCleanupPending
            : OperationQueueDiscardCleanupStatus.alreadyAbsentKeyCleanupPending;
      }
    });
  }

  /// Retryable key cleanup for a context after a partial discard cleanup or
  /// other maintenance. It deletes no queue rows and never removes a key while
  /// a retained payload may still depend on it.
  Future<OperationQueueKeyCleanupStatus> cleanupContextKeyIfUnused({
    required OperationPayloadContext context,
  }) async {
    final crypto = _payloadCrypto;
    if (crypto == null) {
      throw StateError('Encrypted payload support is not configured.');
    }

    return _withContextMutationLock(context, () async {
      final hasDependencies = await _database.transaction(
        () => _hasPayloadDependenciesForContext(context),
      );
      if (hasDependencies) {
        return OperationQueueKeyCleanupStatus.retainedForPayloadDependencies;
      }
      try {
        await crypto.deleteContextKeyForQueueCleanup(context);
        return OperationQueueKeyCleanupStatus.deleted;
      } on Object {
        return OperationQueueKeyCleanupStatus.failed;
      }
    });
  }

  Future<bool> _hasPayloadDependenciesForContext(
    OperationPayloadContext context,
  ) async {
    final entryTable = _database.operationQueueEntry;
    final payloadTable = _database.operationQueuePayload;
    final matchingContext =
        entryTable.ownerUserId.equals(context.userId) &
        entryTable.clinicContextId.equals(context.clinicContextId);
    // Unknown/incomplete ownership is treated conservatively: it prevents
    // deleting a context key until every payload's dependency is understood.
    final unknownContext =
        entryTable.ownerUserId.isNull() | entryTable.clinicContextId.isNull();
    final query =
        _database.select(payloadTable).join([
          innerJoin(
            entryTable,
            entryTable.operationId.equalsExp(payloadTable.operationId),
          ),
        ])..where(
          payloadTable.encryptionKeyVersion.equals(
                OperationPayloadCrypto.encryptionKeyVersion,
              ) &
              (matchingContext | unknownContext),
        );
    return (await query.get()).isNotEmpty;
  }

  /// Applies the failure policy and persists its resulting state and backoff.
  ///
  /// This method does not perform a network request or a retry. A delivery
  /// adapter must classify the observed failure honestly and supply a verified
  /// replay contract before an unknown outcome can be retried automatically.
  Future<OperationQueueEntryData> recordFailure({
    required String operationId,
    required OperationAttemptFailure failure,
    required DateTime now,
    OperationReplayContract replayContract =
        OperationReplayContract.notVerified,
    double jitterSample = 0.5,
  }) async {
    final current = await findByOperationId(operationId);

    if (current == null) {
      throw StateError('Operation does not exist.');
    }

    if (_parseState(current.lifecycleState) !=
        OperationLifecycleState.inFlight) {
      throw StateError('Only an in-flight operation can record a failure.');
    }

    final decision = decideOperationFailure(
      failure: failure,
      attemptsCompleted: current.attemptsCompleted,
      retryPolicy: _retryPolicy,
      replayContract: replayContract,
    );

    final timestamp = now.toUtc();
    final nextState = decision.nextState;
    final nextAttemptAt = nextState == OperationLifecycleState.retryScheduled
        ? timestamp.add(
            _retryPolicy.delayAfterFailure(
              current.attemptsCompleted,
              jitterSample: jitterSample,
            ),
          )
        : null;

    return transition(
      operationId: operationId,
      expectedState: OperationLifecycleState.inFlight,
      nextState: nextState,
      now: timestamp,
      failure: failure,
      nextAttemptAt: nextAttemptAt,
      replayContract: replayContract,
      jitterSample: jitterSample,
    );
  }

  OperationLifecycleState _parseState(String storedState) {
    for (final state in OperationLifecycleState.values) {
      if (state.name == storedState) return state;
    }

    // Fail closed if a future or invalid state value is encountered.
    throw StateError('Unknown persisted operation lifecycle state.');
  }
}
