import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/sync/operation_lifecycle.dart';

void main() {
  group('OperationIdentity', () {
    test('normalizes and preserves stable operation metadata', () {
      final identity = OperationIdentity(
        operationId: '  operation-001 ',
        operationType: ' appointment.create ',
        idempotencyKey: ' key-001 ',
      );

      expect(identity.operationId, 'operation-001');
      expect(identity.operationType, 'appointment.create');
      expect(identity.idempotencyKey, 'key-001');
    });

    test('rejects blank identifiers and operation names', () {
      expect(
        () => OperationIdentity(
          operationId: ' ',
          operationType: 'appointment.create',
          idempotencyKey: 'key-001',
        ),
        throwsArgumentError,
      );

      expect(
        () => OperationIdentity(
          operationId: 'operation-001',
          operationType: ' ',
          idempotencyKey: 'key-001',
        ),
        throwsArgumentError,
      );

      expect(
        () => OperationIdentity(
          operationId: 'operation-001',
          operationType: 'appointment.create',
          idempotencyKey: '\t',
        ),
        throwsArgumentError,
      );
    });

    test('enforces backend-compatible operation and key lengths', () {
      expect(
        () => OperationIdentity(
          operationId: List<String>.filled(129, 'o').join(),
          operationType: 'appointment.create',
          idempotencyKey: 'key-001',
        ),
        throwsArgumentError,
      );

      expect(
        () => OperationIdentity(
          operationId: 'operation-001',
          operationType: List<String>.filled(121, 't').join(),
          idempotencyKey: 'key-001',
        ),
        throwsArgumentError,
      );

      expect(
        () => OperationIdentity(
          operationId: 'operation-001',
          operationType: 'appointment.create',
          idempotencyKey: List<String>.filled(256, 'k').join(),
        ),
        throwsArgumentError,
      );
    });
  });

  group('OperationRetryPolicy', () {
    test('limits total attempts, including the first attempt', () {
      final policy = OperationRetryPolicy(maxAttempts: 3);

      expect(policy.canRetryAfterFailure(1), isTrue);
      expect(policy.canRetryAfterFailure(2), isTrue);
      expect(policy.canRetryAfterFailure(3), isFalse);
    });

    test('uses exponential backoff and caps the delay', () {
      final policy = OperationRetryPolicy(
        maxAttempts: 5,
        baseDelay: const Duration(seconds: 1),
        maxDelay: const Duration(seconds: 3),
        jitterRatio: 0,
      );

      expect(policy.delayAfterFailure(1), const Duration(seconds: 1));
      expect(policy.delayAfterFailure(2), const Duration(seconds: 2));
      expect(policy.delayAfterFailure(3), const Duration(seconds: 3));
      expect(policy.delayAfterFailure(4), const Duration(seconds: 3));
    });

    test('applies injectable jitter and still respects the cap', () {
      final policy = OperationRetryPolicy(
        maxAttempts: 3,
        baseDelay: const Duration(seconds: 1),
        maxDelay: const Duration(seconds: 4),
        jitterRatio: 0.5,
      );

      expect(
        policy.delayAfterFailure(1, jitterSample: 0),
        const Duration(milliseconds: 500),
      );
      expect(
        policy.delayAfterFailure(1, jitterSample: 1),
        const Duration(milliseconds: 1500),
      );
      expect(
        policy.delayAfterFailure(3, jitterSample: 1),
        const Duration(seconds: 4),
      );
    });

    test('rejects invalid policies and delay inputs', () {
      expect(() => OperationRetryPolicy(maxAttempts: 0), throwsArgumentError);
      expect(() => OperationRetryPolicy(maxAttempts: 11), throwsArgumentError);
      expect(
        () => OperationRetryPolicy(baseDelay: Duration.zero),
        throwsArgumentError,
      );
      expect(
        () => OperationRetryPolicy(
          baseDelay: const Duration(seconds: 2),
          maxDelay: const Duration(seconds: 1),
        ),
        throwsArgumentError,
      );
      expect(
        () => OperationRetryPolicy(jitterRatio: double.nan),
        throwsArgumentError,
      );

      final policy = OperationRetryPolicy();
      expect(() => policy.delayAfterFailure(0), throwsArgumentError);
      expect(
        () => policy.delayAfterFailure(1, jitterSample: 1.1),
        throwsArgumentError,
      );
    });
  });

  group('decideOperationFailure', () {
    final policy = OperationRetryPolicy(maxAttempts: 3);

    test('retries a transient failure only when no effect is confirmed', () {
      final decision = decideOperationFailure(
        failure: OperationAttemptFailure.confirmedTransientNoEffect,
        attemptsCompleted: 1,
        retryPolicy: policy,
      );

      expect(decision.nextState, OperationLifecycleState.retryScheduled);
      expect(decision.mayRetryAutomatically, isTrue);
      expect(decision.requiresReconciliation, isFalse);
    });

    test('stops automatic retries after the bounded attempt budget', () {
      final decision = decideOperationFailure(
        failure: OperationAttemptFailure.confirmedTransientNoEffect,
        attemptsCompleted: 3,
        retryPolicy: policy,
      );

      expect(
        decision.nextState,
        OperationLifecycleState.userInterventionRequired,
      );
      expect(decision.mayRetryAutomatically, isFalse);
      expect(decision.requiresUserIntervention, isTrue);
    });

    test('allows unknown-outcome retry only under stable-key idempotency', () {
      final safeReplay = decideOperationFailure(
        failure: OperationAttemptFailure.outcomeUnknown,
        attemptsCompleted: 1,
        retryPolicy: policy,
        replayContract:
            OperationReplayContract.endpointSupportsStableIdempotencyKey,
      );
      final unverifiedReplay = decideOperationFailure(
        failure: OperationAttemptFailure.outcomeUnknown,
        attemptsCompleted: 1,
        retryPolicy: policy,
      );

      expect(safeReplay.nextState, OperationLifecycleState.retryScheduled);
      expect(
        unverifiedReplay.nextState,
        OperationLifecycleState.reconciliationRequired,
      );
    });

    test('reconciles unknown outcomes after retry budget is exhausted', () {
      final decision = decideOperationFailure(
        failure: OperationAttemptFailure.outcomeUnknown,
        attemptsCompleted: 3,
        retryPolicy: policy,
        replayContract:
            OperationReplayContract.endpointSupportsStableIdempotencyKey,
      );

      expect(
        decision.nextState,
        OperationLifecycleState.reconciliationRequired,
      );
      expect(decision.mayRetryAutomatically, isFalse);
    });

    test('marks definitive no-effect rejection as rejected', () {
      final decision = decideOperationFailure(
        failure: OperationAttemptFailure.definitiveRejectionWithoutEffect,
        attemptsCompleted: 1,
        retryPolicy: policy,
      );

      expect(decision.nextState, OperationLifecycleState.rejected);
    });

    test('requires intervention after session or authorization changes', () {
      for (final failure in [
        OperationAttemptFailure.sessionExpired,
        OperationAttemptFailure.authorizationDenied,
      ]) {
        final decision = decideOperationFailure(
          failure: failure,
          attemptsCompleted: 1,
          retryPolicy: policy,
        );

        expect(
          decision.nextState,
          OperationLifecycleState.userInterventionRequired,
        );
      }
    });

    test('requires reconciliation for server conflicts', () {
      final decision = decideOperationFailure(
        failure: OperationAttemptFailure.serverConflict,
        attemptsCompleted: 1,
        retryPolicy: policy,
      );

      expect(
        decision.nextState,
        OperationLifecycleState.reconciliationRequired,
      );
    });

    test(
      'routes known scheduling and idempotency conflicts to user intervention',
      () {
        const failures = <OperationAttemptFailure>[
          OperationAttemptFailure.appointmentPatientScheduleConflict,
          OperationAttemptFailure.appointmentStaffScheduleConflict,
          OperationAttemptFailure.idempotencyKeyPayloadMismatch,
          OperationAttemptFailure.idempotencyRecordUnresolved,
        ];

        for (final failure in failures) {
          final decision = decideOperationFailure(
            failure: failure,
            attemptsCompleted: 1,
            retryPolicy: policy,
          );

          expect(
            decision.nextState,
            OperationLifecycleState.userInterventionRequired,
            reason: failure.name,
          );
          expect(
            decision.requiresUserIntervention,
            isTrue,
            reason: failure.name,
          );
          expect(decision.mayRetryAutomatically, isFalse);
        }
      },
    );

    test('keeps unresolved idempotency outcomes in reconciliation', () {
      final decision = decideOperationFailure(
        failure: OperationAttemptFailure.idempotencyOutcomeUnresolved,
        attemptsCompleted: 1,
        retryPolicy: policy,
      );

      expect(
        decision.nextState,
        OperationLifecycleState.reconciliationRequired,
      );
      expect(decision.requiresReconciliation, isTrue);
      expect(decision.mayRetryAutomatically, isFalse);
    });

    test('rejects invalid attempt counts', () {
      expect(
        () => decideOperationFailure(
          failure: OperationAttemptFailure.serverConflict,
          attemptsCompleted: 0,
          retryPolicy: policy,
        ),
        throwsArgumentError,
      );
      expect(
        () => decideOperationFailure(
          failure: OperationAttemptFailure.serverConflict,
          attemptsCompleted: 4,
          retryPolicy: policy,
        ),
        throwsArgumentError,
      );
    });
  });

  group('OperationLifecycleState transitions', () {
    test(
      'queued and retry-scheduled states permit only controlled delivery',
      () {
        expect(
          OperationLifecycleState.queued.canTransitionTo(
            OperationLifecycleState.inFlight,
          ),
          isTrue,
        );
        expect(
          OperationLifecycleState.queued.canTransitionTo(
            OperationLifecycleState.cancelled,
          ),
          isTrue,
        );
        expect(
          OperationLifecycleState.queued.canTransitionTo(
            OperationLifecycleState.acknowledged,
          ),
          isFalse,
        );
        expect(
          OperationLifecycleState.retryScheduled.canTransitionTo(
            OperationLifecycleState.inFlight,
          ),
          isTrue,
        );
        expect(
          OperationLifecycleState.retryScheduled.canTransitionTo(
            OperationLifecycleState.acknowledged,
          ),
          isFalse,
        );
      },
    );

    test(
      'reconciliation must return through queued before another attempt',
      () {
        expect(
          OperationLifecycleState.reconciliationRequired.canTransitionTo(
            OperationLifecycleState.queued,
          ),
          isTrue,
        );
        expect(
          OperationLifecycleState.reconciliationRequired.canTransitionTo(
            OperationLifecycleState.inFlight,
          ),
          isFalse,
        );
        expect(
          OperationLifecycleState.reconciliationRequired.canTransitionTo(
            OperationLifecycleState.acknowledged,
          ),
          isTrue,
        );
      },
    );

    test('acknowledged, rejected, and cancelled states are terminal', () {
      final terminalStates = [
        OperationLifecycleState.acknowledged,
        OperationLifecycleState.rejected,
        OperationLifecycleState.cancelled,
      ];

      for (final current in terminalStates) {
        expect(current.canTransitionTo(current), isTrue);
        for (final next in OperationLifecycleState.values) {
          if (next != current) {
            expect(current.canTransitionTo(next), isFalse);
          }
        }
      }
    });
  });
}
