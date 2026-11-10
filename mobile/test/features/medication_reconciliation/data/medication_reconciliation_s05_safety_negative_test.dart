// P23G.S05 - negative safety matrix for the existing S04 finalization adapter.
// No production adapter, router, network, or database changes.
import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/medication_reconciliation_finalization_remote_data_source.dart';

import 'medication_reconciliation_finalization_remote_data_source_test.dart';

AuthSessionState _session({
  String role = 'doctor',
  int clinicId = 3,
  String accessToken = 'token',
  String refreshToken = 'refresh',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: accessToken,
    refreshToken: refreshToken,
    userId: 5,
    role: role,
    clinicContextId: clinicId,
  ),
);

class _AdversarialTransport extends S04FakeTransport {
  _AdversarialTransport(super.record);

  bool offline = false;
  bool returnMalformedAmendment = false;
  Completer<void>? holdPost;
  final Completer<void> enteredPost = Completer<void>();
  void Function(int count)? afterGet;

  @override
  Future<Map<String, dynamic>> get(String path, String token) async {
    if (offline) throw StateError('Simulated offline GET');
    afterGet?.call(reads + 1);
    final result = await super.get(path, token);
    return result;
  }

  @override
  Future<Map<String, dynamic>> post(
    String path,
    Map<String, dynamic> body,
    String token, {
    String? idempotencyKey,
  }) async {
    if (!enteredPost.isCompleted) enteredPost.complete();
    final hold = holdPost;
    if (hold != null) await hold.future;
    final response = await super.post(
      path,
      body,
      token,
      idempotencyKey: idempotencyKey,
    );
    if (returnMalformedAmendment && path.endsWith('/amend')) {
      final amendment = Map<String, dynamic>.from(
        response['amendment'] as Map<String, dynamic>,
      )..['new_version'] = 999;
      return <String, dynamic>{...response, 'amendment': amendment};
    }
    return response;
  }
}

void main() {
  group('S05 authorization and clinical scope', () {
    test(
      'patient, nurse, admin and super-admin have no S04 write access',
      () async {
        for (final role in <String>[
          'patient',
          'nurse',
          'admin',
          'super_admin',
        ]) {
          final transport = _AdversarialTransport(s04ExampleRecord());
          final source = s04Source(
            transport,
            session: () => _session(role: role),
          );
          await expectLater(
            s04Inspect(source),
            throwsA(isA<MedRecS04ScopeUnavailable>()),
          );
          expect(
            transport.reads,
            0,
            reason: '$role was allowed to read via write workflow',
          );
          expect(transport.posts, 0);
        }
      },
    );

    test('wrong clinic is denied before server GET', () async {
      final transport = _AdversarialTransport(s04ExampleRecord());
      final source = s04Source(transport, session: () => _session(clinicId: 4));
      await expectLater(
        s04Inspect(source),
        throwsA(isA<MedRecS04ScopeUnavailable>()),
      );
      expect(transport.reads, 0);
      expect(transport.posts, 0);
    });

    test(
      'wrong patient, admission, reconciliation or clinic in GET is rejected',
      () async {
        for (final entry in <String, int>{
          'patient_id': 8,
          'admission_id': 12,
          'id': 20,
          'clinic_id': 4,
        }.entries) {
          final record = s04ExampleRecord()..[entry.key] = entry.value;
          final transport = _AdversarialTransport(record);
          final source = s04Source(transport);
          await expectLater(s04Inspect(source), throwsFormatException);
          expect(transport.posts, 0, reason: entry.key);
        }
      },
    );

    test(
      'invalid record status and naive draft CAS timestamp fail closed',
      () async {
        for (final record in <Map<String, dynamic>>[
          s04ExampleRecord()..['record_status'] = 'signed_by_client',
          s04ExampleRecord()..['updated_at'] = '2026-11-10T10:00:00',
        ]) {
          final transport = _AdversarialTransport(record);
          await expectLater(
            s04Inspect(s04Source(transport)),
            throwsFormatException,
          );
          expect(transport.posts, 0);
        }
      },
    );
  });

  group('S05 stale-evidence rejection before POST', () {
    test(
      'timestamp-only drift and version drift each reject finalization',
      () async {
        for (final change in <void Function(Map<String, dynamic>)>[
          (record) {
            record['updated_at'] = '2026-11-10T10:00:01+00:00';
          },
          (record) {
            record['record_version'] = 2;
          },
        ]) {
          final transport = _AdversarialTransport(s04ExampleRecord());
          final source = s04Source(transport);
          final reviewed = await s04Inspect(source);
          change(transport.record);
          await expectLater(
            source.finalizeOnce(
              reviewed: reviewed,
              idempotencyKey: 's05-freshness',
            ),
            throwsA(isA<MedRecS04EvidenceChanged>()),
          );
          expect(transport.posts, 0);
        }
      },
    );

    test('transfer ID and event identity drift reject finalization', () async {
      for (final change in <void Function(Map<String, dynamic>)>[
        (record) {
          record['ward_transfer_id'] = 92;
        },
        (record) {
          record['event_type'] = 'discharge';
        },
      ]) {
        final record = s04ExampleRecord()
          ..['event_type'] = 'transfer'
          ..['ward_transfer_id'] = 91;
        final transport = _AdversarialTransport(record);
        final source = s04Source(transport);
        final reviewed = await s04Inspect(source);
        change(transport.record);
        await expectLater(
          source.finalizeOnce(
            reviewed: reviewed,
            idempotencyKey: 's05-transfer',
          ),
          throwsA(isA<MedRecS04EvidenceChanged>()),
        );
        expect(transport.posts, 0);
      }
    });

    test('changed medication decision and notes reject finalization', () async {
      for (final change in <void Function(Map<String, dynamic>)>[
        (record) {
          record['notes'] = 'Another clinician changed the draft';
        },
        (record) {
          record['items'] = <Map<String, dynamic>>[
            <String, dynamic>{
              ...((record['items'] as List).first as Map<String, dynamic>),
              'decision': 'hold',
            },
          ];
        },
      ]) {
        final transport = _AdversarialTransport(s04ExampleRecord());
        final source = s04Source(transport);
        final reviewed = await s04Inspect(source);
        change(transport.record);
        await expectLater(
          source.finalizeOnce(
            reviewed: reviewed,
            idempotencyKey: 's05-document',
          ),
          throwsA(isA<MedRecS04EvidenceChanged>()),
        );
        expect(transport.posts, 0);
      }
    });

    test(
      'verify/unknown/replacement without replacement cannot finalize',
      () async {
        for (final decision in <String>['verify', 'unknown', 'replace']) {
          final transport = _AdversarialTransport(
            s04ExampleRecord(decision: decision),
          );
          final source = s04Source(transport);
          final reviewed = await s04Inspect(source);
          expect(reviewed.hasResolvedDecisions, isFalse);
          await expectLater(
            source.finalizeOnce(
              reviewed: reviewed,
              idempotencyKey: 's05-decision',
            ),
            throwsA(isA<MedRecS04EvidenceChanged>()),
          );
          expect(transport.posts, 0);
        }
      },
    );

    test(
      'empty medication list without explicit no-medications cannot finalize',
      () async {
        final transport = _AdversarialTransport(
          s04ExampleRecord()..['items'] = <Map<String, dynamic>>[],
        );
        final source = s04Source(transport);
        final reviewed = await s04Inspect(source);
        expect(reviewed.hasResolvedDecisions, isFalse);
        await expectLater(
          source.finalizeOnce(reviewed: reviewed, idempotencyKey: 's05-empty'),
          throwsA(isA<MedRecS04EvidenceChanged>()),
        );
        expect(transport.posts, 0);
      },
    );

    test('missing or malformed idempotency keys cannot POST', () async {
      for (final key in <String>[
        '',
        ' wrong ',
        List<String>.filled(256, 'x').join(),
      ]) {
        final transport = _AdversarialTransport(s04ExampleRecord());
        final source = s04Source(transport);
        final reviewed = await s04Inspect(source);
        await expectLater(
          source.finalizeOnce(reviewed: reviewed, idempotencyKey: key),
          throwsA(isA<MedRecS04EvidenceChanged>()),
        );
        expect(transport.posts, 0);
      }
    });

    test(
      'signed-out revision between review and submit prevents POST',
      () async {
        final guard = SessionNavigationGuard()..markSessionAvailable();
        final transport = _AdversarialTransport(s04ExampleRecord());
        final source = s04Source(transport, guard: guard);
        final reviewed = await s04Inspect(source);
        guard.invalidate();
        await expectLater(
          source.finalizeOnce(
            reviewed: reviewed,
            idempotencyKey: 's05-revoked',
          ),
          throwsA(isA<MedRecS04ScopeUnavailable>()),
        );
        expect(transport.posts, 0);
      },
    );

    test('token changed during preflight GET rejects clinical POST', () async {
      final transport = _AdversarialTransport(s04ExampleRecord());
      var token = 'token';
      transport.afterGet = (reads) {
        if (reads == 2) token = 'rotated';
      };
      final source = s04Source(
        transport,
        session: () => _session(accessToken: token),
      );
      final reviewed = await s04Inspect(source);
      await expectLater(
        source.finalizeOnce(reviewed: reviewed, idempotencyKey: 's05-session'),
        throwsA(isA<MedRecS04ScopeUnavailable>()),
      );
      expect(transport.posts, 0);
    });
  });

  group('S05 offline, duplicate and uncertainty gates', () {
    test('offline GET cannot reach finalize POST', () async {
      final transport = _AdversarialTransport(s04ExampleRecord())
        ..offline = true;
      final source = s04Source(transport);
      await expectLater(s04Inspect(source), throwsA(isA<StateError>()));
      expect(transport.posts, 0);
    });

    test('network loss during preflight cannot reach finalize POST', () async {
      final transport = _AdversarialTransport(s04ExampleRecord());
      final source = s04Source(transport);
      final reviewed = await s04Inspect(source);
      transport.offline = true;
      await expectLater(
        source.finalizeOnce(reviewed: reviewed, idempotencyKey: 's05-offline'),
        throwsA(isA<StateError>()),
      );
      expect(transport.posts, 0);
    });

    test(
      'simultaneous finalization requests cannot send duplicate POSTs',
      () async {
        final transport = _AdversarialTransport(s04ExampleRecord());
        final release = Completer<void>();
        transport.holdPost = release;
        final source = s04Source(transport);
        final reviewed = await s04Inspect(source);
        final first = source.finalizeOnce(
          reviewed: reviewed,
          idempotencyKey: 's05-once',
        );
        await transport.enteredPost.future;
        await expectLater(
          source.finalizeOnce(reviewed: reviewed, idempotencyKey: 's05-once'),
          throwsA(isA<MedRecS04OutcomeUnconfirmed>()),
        );
        release.complete();
        final result = await first;
        expect(result.status, 'final');
        expect(transport.posts, 1);
        expect(source.writeStarted, isTrue);
      },
    );

    test(
      'HTTP conflict/transport error remains terminal unknown, not replayed',
      () async {
        final transport = _AdversarialTransport(s04ExampleRecord())
          ..failWrite = true;
        final source = s04Source(transport);
        final reviewed = await s04Inspect(source);
        await expectLater(
          source.finalizeOnce(
            reviewed: reviewed,
            idempotencyKey: 's05-conflict',
          ),
          throwsA(isA<MedRecS04OutcomeUnconfirmed>()),
        );
        await expectLater(
          source.finalizeOnce(
            reviewed: reviewed,
            idempotencyKey: 's05-conflict',
          ),
          throwsA(isA<MedRecS04OutcomeUnconfirmed>()),
        );
        expect(transport.posts, 1);
      },
    );

    test(
      'amendment stale version and unchanged notes reject before POST',
      () async {
        final transport = _AdversarialTransport(
          s04ExampleRecord(status: 'final'),
        );
        final source = s04Source(transport);
        final reviewed = await s04Inspect(source);
        await expectLater(
          source.amendNotesOnce(
            reviewed: reviewed,
            reason: 'Valid reason',
            notes: 'Reviewed',
          ),
          throwsA(isA<MedRecS04EvidenceChanged>()),
        );
        transport.record['record_version'] = 2;
        await expectLater(
          source.amendNotesOnce(
            reviewed: reviewed,
            reason: 'Valid reason',
            notes: 'New notes',
          ),
          throwsA(isA<MedRecS04EvidenceChanged>()),
        );
        expect(transport.posts, 0);
      },
    );

    test(
      'amendment empty reason or draft status rejects before POST',
      () async {
        final transport = _AdversarialTransport(
          s04ExampleRecord(status: 'final'),
        );
        final source = s04Source(transport);
        final reviewed = await s04Inspect(source);
        await expectLater(
          source.amendNotesOnce(
            reviewed: reviewed,
            reason: '  ',
            notes: 'New notes',
          ),
          throwsA(isA<MedRecS04EvidenceChanged>()),
        );
        expect(transport.posts, 0);
        final draft = _AdversarialTransport(s04ExampleRecord());
        final draftSource = s04Source(draft);
        await expectLater(
          draftSource.amendNotesOnce(
            reviewed: await s04Inspect(draftSource),
            reason: 'Change',
            notes: 'New notes',
          ),
          throwsA(isA<MedRecS04EvidenceChanged>()),
        );
        expect(draft.posts, 0);
      },
    );

    test(
      'malformed amendment evidence stays unknown and cannot replay',
      () async {
        final transport = _AdversarialTransport(
          s04ExampleRecord(status: 'final'),
        )..returnMalformedAmendment = true;
        final source = s04Source(transport);
        final reviewed = await s04Inspect(source);
        await expectLater(
          source.amendNotesOnce(
            reviewed: reviewed,
            reason: 'Corrected during review',
            notes: 'Replacement note',
          ),
          throwsA(isA<MedRecS04OutcomeUnconfirmed>()),
        );
        await expectLater(
          source.amendNotesOnce(
            reviewed: reviewed,
            reason: 'Corrected during review',
            notes: 'Replacement note',
          ),
          throwsA(isA<MedRecS04OutcomeUnconfirmed>()),
        );
        expect(transport.posts, 1);
      },
    );

    test(
      'amendment readback disagreement is unknown, not silently accepted',
      () async {
        final transport = _AdversarialTransport(
          s04ExampleRecord(status: 'final'),
        );
        final source = s04Source(transport);
        final reviewed = await s04Inspect(source);
        transport.afterGet = (reads) {
          if (reads == 3) transport.record['notes'] = 'Competing change';
        };
        await expectLater(
          source.amendNotesOnce(
            reviewed: reviewed,
            reason: 'Correction',
            notes: 'User requested correction',
          ),
          throwsA(isA<MedRecS04OutcomeUnconfirmed>()),
        );
        expect(transport.posts, 1);
      },
    );
  });
}
