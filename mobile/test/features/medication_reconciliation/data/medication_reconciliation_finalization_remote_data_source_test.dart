import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/medication_reconciliation_finalization_remote_data_source.dart';

const _timestamp = '2026-11-10T10:00:00+00:00';

Map<String, dynamic> s04ExampleRecord({
  String status = 'draft',
  String decision = 'continue',
}) => <String, dynamic>{
  'id': 19,
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 11,
  'created_by_staff_id': 24,
  'ward_transfer_id': null,
  'event_type': 'admission',
  'record_status': status,
  'record_version': 1,
  'updated_at': _timestamp,
  'finalized_at': status == 'draft' ? null : _timestamp,
  'latest_amendment_id': null,
  'no_medications_reported': false,
  'notes': 'Reviewed',
  'items': <Map<String, dynamic>>[
    <String, dynamic>{
      'source_prescription_item_id': null,
      'medication_name': 'Test medication',
      'decision': decision,
      'replacement_medication_name': null,
      'dose_text': null,
      'route_text': null,
      'frequency_text': null,
      'instructions': null,
      'rationale': null,
    },
  ],
};

class S04FakeTransport implements MedRecS04Transport {
  S04FakeTransport(this.record);
  final Map<String, dynamic> record;
  int reads = 0;
  int posts = 0;
  String? lastPath;
  String? lastKey;
  Map<String, dynamic>? lastBody;
  bool failWrite = false;
  bool refuseReadback = false;

  @override
  Future<Map<String, dynamic>> get(String path, String token) async {
    reads++;
    if (token != 'token' || path != 'api/v1/medication-reconciliations/19') {
      throw StateError('Wrong authenticated endpoint');
    }
    return <String, dynamic>{
      'reconciliation': Map<String, dynamic>.from(record),
    };
  }

  @override
  Future<Map<String, dynamic>> post(
    String path,
    Map<String, dynamic> body,
    String token, {
    String? idempotencyKey,
  }) async {
    posts++;
    lastPath = path;
    lastBody = body;
    lastKey = idempotencyKey;
    if (failWrite) throw StateError('Transport timeout');
    if (path.endsWith('/finalize')) {
      record['record_status'] = 'final';
      record['finalized_at'] = _timestamp;
    } else if (path.endsWith('/amend')) {
      record['record_status'] = 'amended';
      record['record_version'] = 2;
      record['latest_amendment_id'] = 90;
      record['notes'] = (body['document'] as Map<String, dynamic>)['notes'];
    } else {
      throw StateError('Unexpected clinical operation');
    }
    final snapshot = Map<String, dynamic>.from(record);
    if (refuseReadback) record['record_status'] = 'draft';
    return <String, dynamic>{
      'reconciliation': snapshot,
      if (path.endsWith('/amend'))
        'amendment': <String, dynamic>{
          'id': 90,
          'source_record_id': 19,
          'previous_version': 1,
          'new_version': 2,
        },
    };
  }
}

AuthSessionState s04DoctorSession() => const AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'token',
    refreshToken: 'refresh',
    userId: 5,
    role: 'doctor',
    clinicContextId: 3,
  ),
);

MedRecS04FinalizationRemoteDataSource s04Source(
  S04FakeTransport transport, {
  AuthSessionState Function()? session,
  SessionNavigationGuard? guard,
}) {
  final activeGuard = guard ?? SessionNavigationGuard();
  activeGuard.markSessionAvailable();
  return MedRecS04FinalizationRemoteDataSource(
    transport: transport,
    loadSession: () async => (session ?? s04DoctorSession)(),
    sessionNavigationGuard: activeGuard,
  );
}

Future<MedRecS04Record> s04Inspect(
  MedRecS04FinalizationRemoteDataSource source,
) => source.inspect(
  reconciliationId: 19,
  clinicId: 3,
  patientId: 7,
  admissionId: 11,
);

void main() {
  test(
    'finalization uses version + aware draft timestamp + idempotency, then GET',
    () async {
      final transport = S04FakeTransport(s04ExampleRecord());
      final source = s04Source(transport);
      final reviewed = await s04Inspect(source);
      final result = await source.finalizeOnce(
        reviewed: reviewed,
        idempotencyKey: 's04-final-19',
      );
      expect(result.status, 'final');
      expect(result.version, 1);
      expect(result.finalizedAt, isNotNull);
      expect(transport.posts, 1);
      expect(transport.reads, 3);
      expect(
        transport.lastPath,
        'api/v1/medication-reconciliations/19/finalize',
      );
      expect(transport.lastBody, <String, dynamic>{
        'expected_version': 1,
        'expected_updated_at': _timestamp,
      });
      expect(transport.lastKey, 's04-final-19');
      await expectLater(
        source.finalizeOnce(reviewed: reviewed, idempotencyKey: 'new-key'),
        throwsA(isA<MedRecS04OutcomeUnconfirmed>()),
      );
      expect(transport.posts, 1);
    },
  );

  test('different draft content blocks finalize before POST', () async {
    final transport = S04FakeTransport(s04ExampleRecord());
    final source = s04Source(transport);
    final reviewed = await s04Inspect(source);
    transport.record['notes'] = 'Revised in another session';
    await expectLater(
      source.finalizeOnce(reviewed: reviewed, idempotencyKey: 'one'),
      throwsA(isA<MedRecS04EvidenceChanged>()),
    );
    expect(transport.posts, 0);
  });

  test('unresolved verify decision cannot finalize', () async {
    final transport = S04FakeTransport(s04ExampleRecord(decision: 'verify'));
    final source = s04Source(transport);
    final reviewed = await s04Inspect(source);
    expect(reviewed.hasResolvedDecisions, isFalse);
    await expectLater(
      source.finalizeOnce(reviewed: reviewed, idempotencyKey: 'one'),
      throwsA(isA<MedRecS04EvidenceChanged>()),
    );
    expect(transport.posts, 0);
  });

  test('timeout locks instance permanently with no write replay', () async {
    final transport = S04FakeTransport(s04ExampleRecord())..failWrite = true;
    final source = s04Source(transport);
    final reviewed = await s04Inspect(source);
    await expectLater(
      source.finalizeOnce(reviewed: reviewed, idempotencyKey: 'one'),
      throwsA(isA<MedRecS04OutcomeUnconfirmed>()),
    );
    await expectLater(
      source.finalizeOnce(reviewed: reviewed, idempotencyKey: 'one'),
      throwsA(isA<MedRecS04OutcomeUnconfirmed>()),
    );
    expect(transport.posts, 1);
  });

  test('response without authoritative readback remains uncertain', () async {
    final transport = S04FakeTransport(s04ExampleRecord())
      ..refuseReadback = true;
    final source = s04Source(transport);
    final reviewed = await s04Inspect(source);
    await expectLater(
      source.finalizeOnce(reviewed: reviewed, idempotencyKey: 'one'),
      throwsA(isA<MedRecS04OutcomeUnconfirmed>()),
    );
    expect(transport.posts, 1);
  });

  test(
    'amendment sends complete document, no draft timestamp and no idempotency',
    () async {
      final transport = S04FakeTransport(s04ExampleRecord(status: 'final'));
      final source = s04Source(transport);
      final reviewed = await s04Inspect(source);
      final result = await source.amendNotesOnce(
        reviewed: reviewed,
        reason: 'Correction following review',
        notes: 'Corrected clinical note',
      );
      expect(result.status, 'amended');
      expect(result.version, 2);
      expect(transport.lastKey, isNull);
      expect(transport.lastPath, 'api/v1/medication-reconciliations/19/amend');
      expect(transport.lastBody?['reason'], 'Correction following review');
      expect(transport.lastBody?['expected_version'], 1);
      final doc = transport.lastBody?['document'] as Map<String, dynamic>;
      expect(doc.keys.toSet(), <String>{
        'no_medications_reported',
        'notes',
        'items',
      });
      expect(doc['notes'], 'Corrected clinical note');
      expect(transport.posts, 1);
    },
  );

  test('nurse cannot submit or read through S04 write workflow', () async {
    final transport = S04FakeTransport(s04ExampleRecord());
    final source = s04Source(
      transport,
      session: () => const AuthSessionState.ready(
        AuthTokenResponse(
          accessToken: 'token',
          refreshToken: 'refresh',
          userId: 5,
          role: 'nurse',
          clinicContextId: 3,
        ),
      ),
    );
    await expectLater(
      s04Inspect(source),
      throwsA(isA<MedRecS04ScopeUnavailable>()),
    );
    expect(transport.reads, 0);
    expect(transport.posts, 0);
  });

  test('cross-patient record is rejected on GET', () async {
    final transport = S04FakeTransport(s04ExampleRecord())
      ..record['patient_id'] = 99;
    final source = s04Source(transport);
    await expectLater(s04Inspect(source), throwsFormatException);
    expect(transport.posts, 0);
  });
}
