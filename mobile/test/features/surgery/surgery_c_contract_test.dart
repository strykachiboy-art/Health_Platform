import 'package:clinic_system_pro/features/surgery/data/surgery_action_contract.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('theatre form builds exact case-bound and timezone-qualified body', () {
    final input = SurgeryActionInput(
      caseId: 23,
      kind: SurgeryActionKind.book,
      raw: {
        'theatre_id': '8',
        'starts_at': '2030-01-11T09:00:00Z',
        'ends_at': '2030-01-11T11:00:00+00:00',
      },
    );
    expect(input.encode(), {
      'theatre_id': 8,
      'surgical_case_id': 23,
      'starts_at': '2030-01-11T09:00:00Z',
      'ends_at': '2030-01-11T11:00:00+00:00',
    });
    expect(input.kind.path(input.caseId, null), 'api/v1/surgery/bookings');
  });

  test(
    'forged actor, clinic and unsafe authority fields are never accepted',
    () {
      for (final forbidden in const [
        'actor_staff_id',
        'clinic_id',
        'operative_completion_authorized',
        'idempotency_key',
      ]) {
        expect(
          () => SurgeryActionInput(
            caseId: 5,
            kind: SurgeryActionKind.book,
            raw: {
              'theatre_id': '9',
              'starts_at': '2030-01-11T09:00:00Z',
              'ends_at': '2030-01-11T10:00:00Z',
              forbidden: '1',
            },
          ).encode(),
          throwsFormatException,
        );
      }
    },
  );

  test(
    'booleans, missing checklist confirmations and Sign-Out fail closed',
    () {
      expect(
        SurgeryActionKind.values.map((x) => x.name),
        isNot(contains('signOut')),
      );
      final fields = SurgeryActionKind.signIn.fields;
      expect(fields.length, 7);
      final raw = <String, String>{
        'surgeon_staff_id': '3',
        'anesthetist_staff_id': '4',
        for (final field in fields.where(
          (f) => f.type == SurgeryFieldType.affirmative,
        ))
          field.name: 'true',
      };
      expect(
        SurgeryActionInput(
          caseId: 8,
          kind: SurgeryActionKind.signIn,
          raw: raw,
        ).encode()['answers'],
        hasLength(5),
      );
      raw.remove('patient_identity_confirmed');
      expect(
        () => SurgeryActionInput(
          caseId: 8,
          kind: SurgeryActionKind.signIn,
          raw: raw,
        ).encode(),
        throwsFormatException,
      );
      raw['patient_identity_confirmed'] = 'false';
      expect(
        () => SurgeryActionInput(
          caseId: 8,
          kind: SurgeryActionKind.signIn,
          raw: raw,
        ).encode(),
        throwsFormatException,
      );
    },
  );

  test('rejects unknown role paths, malformed IDs and naive timestamps', () {
    expect(SurgeryActionKind.signIn.role, 'nurse');
    expect(SurgeryActionKind.timeOut.role, 'doctor');
    expect(
      SurgeryActionKind.reportSign.path(4, 9),
      'api/v1/surgery/reports/9/sign',
    );
    expect(SurgeryActionKind.reportRevise.usePatch, isTrue);
    expect(
      () => SurgeryActionInput(
        caseId: 2,
        kind: SurgeryActionKind.reschedule,
        raw: {
          'booking_id': '001',
          'expected_version': '2',
          'starts_at': '2030-01-11T09:00:00',
          'ends_at': '2030-01-11T10:00:00Z',
        },
      ).encode(),
      throwsFormatException,
    );
  });

  test('consent uploading and credential privileges have no mobile action', () {
    final actions = SurgeryActionKind.values.map((a) => a.name).toSet();
    expect(actions.contains('verifyConsent'), isFalse);
    expect(actions.contains('recordConsent'), isFalse);
    expect(actions.contains('grantPrivilege'), isFalse);
    expect(actions.contains('reportDraft'), isFalse);
    expect(actions.contains('anesthesiaInduction'), isFalse);
    expect(actions.contains('discharge'), isFalse);
  });
}
