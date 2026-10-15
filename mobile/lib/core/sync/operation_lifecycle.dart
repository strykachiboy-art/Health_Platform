/// Metadata and decision rules for a future durable offline-operation queue.
///
/// This file deliberately contains no queue, transport, persistence, or
/// sensitive request payload. A caller must create the identity once for a
/// logical operation and preserve its values across repeated delivery attempts.
/// Only use the identity with a backend operation whose idempotency contract
/// explicitly supports the supplied key.
enum OperationLifecycleState {
  queued,
  inFlight,
  retryScheduled,
  reconciliationRequired,
  acknowledged,
  rejected,
  userInterventionRequired,
  cancelled;

  /// Returns whether a transition from this state to [next] is allowed.
  ///
  /// Reconciliation must explicitly return an operation to `queued` before
  /// it can be sent again. A completed, rejected, or cancelled operation is
  /// terminal; create a new logical operation rather than reviving it.
  bool canTransitionTo(OperationLifecycleState next) {
    if (this == next) return true;

    return switch (this) {
      OperationLifecycleState.queued =>
        next == OperationLifecycleState.inFlight ||
            next == OperationLifecycleState.cancelled,
      OperationLifecycleState.inFlight =>
        next == OperationLifecycleState.retryScheduled ||
            next == OperationLifecycleState.reconciliationRequired ||
            next == OperationLifecycleState.acknowledged ||
            next == OperationLifecycleState.rejected ||
            next == OperationLifecycleState.userInterventionRequired,
      OperationLifecycleState.retryScheduled =>
        next == OperationLifecycleState.inFlight ||
            next == OperationLifecycleState.cancelled,
      OperationLifecycleState.reconciliationRequired =>
        next == OperationLifecycleState.queued ||
            next == OperationLifecycleState.acknowledged ||
            next == OperationLifecycleState.rejected ||
            next == OperationLifecycleState.userInterventionRequired,
      OperationLifecycleState.userInterventionRequired =>
        next == OperationLifecycleState.queued ||
            next == OperationLifecycleState.cancelled,
      OperationLifecycleState.acknowledged ||
      OperationLifecycleState.rejected ||
      OperationLifecycleState.cancelled =>
        false,
    };
  }
}

/// Identifies one logical operation without holding its request payload.
///
/// The queue implementation must persist this metadata before first delivery.
/// This descriptor does not itself provide persistence, uniqueness enforcement,
/// payload fingerprinting, or permission to use a key on an unsupported route.
class OperationIdentity {
  OperationIdentity({
    required String operationId,
    required String operationType,
    required String idempotencyKey,
  }) : operationId = _normalizeRequired(
         operationId,
         'operationId',
         128,
       ),
       operationType = _normalizeRequired(
         operationType,
         'operationType',
         120,
       ),
       idempotencyKey = _normalizeRequired(
         idempotencyKey,
         'idempotencyKey',
         255,
       );

  /// Stable client-side identifier for this logical operation.
  final String operationId;

  /// Stable operation name, independent of a raw URL or HTTP request.
  final String operationType;

  /// Stable backend idempotency key for repeated attempts of the same payload.
  final String idempotencyKey;

  static String _normalizeRequired(
    String value,
    String argumentName,
    int maximumLength,
  ) {
    final normalized = value.trim();

    if (normalized.isEmpty) {
      throw ArgumentError.value(
        value,
        argumentName,
        'Must be non-empty.',
      );
    }
    if (normalized.length > maximumLength) {
      throw ArgumentError.value(
        value,
        argumentName,
        'Must not exceed $maximumLength characters.',
      );
    }

    return normalized;
  }
}

/// A failure category supplied by an operation-specific adapter.
///
/// These values are semantic classifications, not HTTP-status shortcuts.
/// For example, use `confirmedTransientNoEffect` only when the adapter knows
/// that the attempted mutation did not take effect.
enum OperationAttemptFailure {
  confirmedTransientNoEffect,
  outcomeUnknown,
  definitiveRejectionWithoutEffect,
  sessionExpired,
  authorizationDenied,
  serverConflict,
}

/// The endpoint-specific retry guarantee available for an operation.
enum OperationReplayContract {
  /// There is no verified guarantee that replaying an uncertain mutation is safe.
  notVerified,

  /// The endpoint supports idempotency and the exact same key and payload
  /// fingerprint will be reused for every attempt.
  endpointSupportsStableIdempotencyKey,
}

/// Bounded exponential retry policy.
///
/// Randomness is deliberately injected through [jitterSample] when computing
/// a delay. Production code can supply a random value; tests can supply a fixed
/// value without sleeping or relying on nondeterministic assertions.
class OperationRetryPolicy {
  OperationRetryPolicy({
    this.maxAttempts = 5,
    this.baseDelay = const Duration(seconds: 1),
    this.maxDelay = const Duration(minutes: 1),
    this.jitterRatio = 0.2,
  }) {
    if (maxAttempts < 1 || maxAttempts > 10) {
      throw ArgumentError.value(
        maxAttempts,
        'maxAttempts',
        'Must be between 1 and 10.',
      );
    }
    if (baseDelay <= Duration.zero) {
      throw ArgumentError.value(
        baseDelay,
        'baseDelay',
        'Must be positive.',
      );
    }
    if (maxDelay < baseDelay) {
      throw ArgumentError.value(
        maxDelay,
        'maxDelay',
        'Must be greater than or equal to baseDelay.',
      );
    }
    if (!jitterRatio.isFinite || jitterRatio < 0 || jitterRatio > 1) {
      throw ArgumentError.value(
        jitterRatio,
        'jitterRatio',
        'Must be a finite value between 0 and 1.',
      );
    }
  }

  /// Maximum total attempts, including the original delivery attempt.
  final int maxAttempts;
  final Duration baseDelay;
  final Duration maxDelay;
  final double jitterRatio;

  /// Whether another automatic attempt fits within the configured budget.
  ///
  /// [attemptsCompleted] includes the initial attempt and must be positive.
  bool canRetryAfterFailure(int attemptsCompleted) {
    _validateAttemptCount(attemptsCompleted);
    return attemptsCompleted < maxAttempts;
  }

  /// Computes a bounded delay after [attemptsCompleted] failed attempts.
  ///
  /// The exponential ceiling is `baseDelay * 2^(attemptsCompleted - 1)`,
  /// capped at [maxDelay]. Jitter scales that ceiling within the configured
  /// ratio and the final delay is capped again at [maxDelay].
  Duration delayAfterFailure(
    int attemptsCompleted, {
    double jitterSample = 0.5,
  }) {
    _validateAttemptCount(attemptsCompleted);

    if (!jitterSample.isFinite ||
        jitterSample < 0 ||
        jitterSample > 1) {
      throw ArgumentError.value(
        jitterSample,
        'jitterSample',
        'Must be a finite value between 0 and 1.',
      );
    }

    final maximumMicros = maxDelay.inMicroseconds;
    var ceilingMicros = baseDelay.inMicroseconds;

    for (
      var attempt = 1;
      attempt < attemptsCompleted && ceilingMicros < maximumMicros;
      attempt++
    ) {
      ceilingMicros = ceilingMicros > maximumMicros ~/ 2
          ? maximumMicros
          : ceilingMicros * 2;
      if (ceilingMicros > maximumMicros) {
        ceilingMicros = maximumMicros;
      }
    }

    final factor =
        1 - jitterRatio + (2 * jitterRatio * jitterSample);
    final jitteredMicros = (ceilingMicros * factor).round();
    final boundedMicros = jitteredMicros.clamp(1, maximumMicros).toInt();

    return Duration(microseconds: boundedMicros);
  }

  void _validateAttemptCount(int attemptsCompleted) {
    if (attemptsCompleted < 1 || attemptsCompleted > maxAttempts) {
      throw ArgumentError.value(
        attemptsCompleted,
        'attemptsCompleted',
        'Must be between 1 and maxAttempts ($maxAttempts).',
      );
    }
  }
}

/// Conservative decision for one failed delivery attempt.
class OperationFailureDecision {
  const OperationFailureDecision(this.nextState);

  final OperationLifecycleState nextState;

  bool get mayRetryAutomatically =>
      nextState == OperationLifecycleState.retryScheduled;

  bool get requiresReconciliation =>
      nextState == OperationLifecycleState.reconciliationRequired;

  bool get requiresUserIntervention =>
      nextState == OperationLifecycleState.userInterventionRequired;
}

/// Classifies a failure into an explicit next state.
///
/// This function does not perform retries. A worker must separately enforce
/// state transitions, persist state changes, and wait for the policy delay.
///
/// Unknown outcomes may only be retried when the endpoint-specific contract
/// guarantees replay safety for the same stable idempotency key. Otherwise,
/// the operation must be reconciled before another attempt.
OperationFailureDecision decideOperationFailure({
  required OperationAttemptFailure failure,
  required int attemptsCompleted,
  required OperationRetryPolicy retryPolicy,
  OperationReplayContract replayContract =
      OperationReplayContract.notVerified,
}) {
  // Validates the count even for non-retryable failure classes.
  final hasRetryBudget = retryPolicy.canRetryAfterFailure(attemptsCompleted);

  switch (failure) {
    case OperationAttemptFailure.confirmedTransientNoEffect:
      return OperationFailureDecision(
        hasRetryBudget
            ? OperationLifecycleState.retryScheduled
            : OperationLifecycleState.userInterventionRequired,
      );

    case OperationAttemptFailure.outcomeUnknown:
      if (hasRetryBudget &&
          replayContract ==
              OperationReplayContract.endpointSupportsStableIdempotencyKey) {
        return const OperationFailureDecision(
          OperationLifecycleState.retryScheduled,
        );
      }
      return const OperationFailureDecision(
        OperationLifecycleState.reconciliationRequired,
      );

    case OperationAttemptFailure.definitiveRejectionWithoutEffect:
      return const OperationFailureDecision(
        OperationLifecycleState.rejected,
      );

    case OperationAttemptFailure.sessionExpired:
    case OperationAttemptFailure.authorizationDenied:
      return const OperationFailureDecision(
        OperationLifecycleState.userInterventionRequired,
      );

    case OperationAttemptFailure.serverConflict:
      return const OperationFailureDecision(
        OperationLifecycleState.reconciliationRequired,
      );
  }
}
