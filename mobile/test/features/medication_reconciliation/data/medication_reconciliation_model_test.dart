import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/features/medication_reconciliation/data/models/medication_reconciliation.dart';

Map<String, dynamic> medication({
  String decision = 'continue',
  int? sourceId = 19,
}) => {
  'source_prescription_item_id': sourceId,
  'medication_name': 'Metformin',
  'decision': decision,
  'replacement_medication_name': decision == 'replace' ? 'Alternative' : null,
  'dose_text': '500 mg',
  'route_text': 'oral',
  'frequency_text': 'twice daily',
  'instructions': 'Take with food',
  'rationale': 'Clinician reviewed',
};

Map<String, dynamic> record({
  int id = 31,
  int clinic = 3,
  int patient = 5,
  int admission = 7,
  String event = 'admission',
  int? transfer,
  String status = 'draft',
  int version = 1,
  bool noMedications = false,
  List<Map<String, dynamic>>? items,
}) => {
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'admission_id': admission,
  'ward_transfer_id': transfer,
  'event_type': event,
  'event_identity': event == 'transfer'
      ? 'transfer:$transfer'
      : '$event:$admission',
  'created_by_staff_id': 9,
  'no_medications_reported': noMedications,
  'notes': 'Medication history reviewed',
  'items': items ?? (noMedications ? <Map<String, dynamic>>[] : [medication()]),
  'record_status': status,
  'record_version': version,
  'finalized_by_staff_id': status == 'draft' ? null : 9,
  'finalized_at': status == 'draft' ? null : '2026-11-10T10:00:00+01:00',
  'latest_amendment_id': status == 'amended' ? 37 : null,
  'created_at': '2026-11-09T10:00:00Z',
  'updated_at': '2026-11-10T10:00:00Z',
};

MedicationReconciliationRecord parse(Map<String, dynamic> raw) =>
    MedicationReconciliationRecord.fromJson(
      raw,
      expectedClinicId: 3,
      expectedPatientId: 5,
      expectedAdmissionId: 7,
    );

void main() {
  test('draft preserves unresolved decisions, not clinically completed', () {
    final parsed = parse(
      record(
        items: [
          medication(decision: 'verify'),
          medication(decision: 'unknown', sourceId: null),
        ],
      ),
    );
    expect(parsed.status, MedicationReconciliationRecordStatus.draft);
    expect(parsed.hasUnresolvedDecisions, isTrue);
    expect(parsed.isClinicallyFinal, isFalse);
    expect(parsed.items, hasLength(2));
    expect(parsed.finalizedAt, isNull);
    expect(() => parsed.items.clear(), throwsUnsupportedError);
  });

  test('final and amended are typed and finalization evidence is retained', () {
    final finalised = parse(record(status: 'final'));
    expect(finalised.status, MedicationReconciliationRecordStatus.finalised);
    expect(finalised.isClinicallyFinal, isTrue);
    expect(finalised.finalizedAt, DateTime.utc(2026, 11, 10, 9));
    expect(parse(record(status: 'amended', version: 2)).latestAmendmentId, 37);
    expect(parse(record(noMedications: true, status: 'final')).items, isEmpty);
  });

  test(
    'exact admission, transfer, discharge event identities are mandatory',
    () {
      expect(
        parse(record(event: 'discharge')).event,
        MedicationReconciliationEvent.discharge,
      );
      expect(
        parse(record(event: 'transfer', transfer: 41)).eventIdentity,
        'transfer:41',
      );
      expect(
        () => parse({
          ...record(event: 'transfer', transfer: 41),
          'event_identity': 'transfer:99',
        }),
        throwsFormatException,
      );
      expect(() => parse(record(event: 'transfer')), throwsFormatException);
      expect(
        () => parse(record(event: 'admission', transfer: 41)),
        throwsFormatException,
      );
    },
  );

  test('all seven backend decisions have exact values', () {
    const values = [
      'continue',
      'hold',
      'stop',
      'replace',
      'new',
      'verify',
      'unknown',
    ];
    for (final value in values) {
      final parsed = MedicationReconciliationItem.fromJson(
        medication(decision: value),
      );
      expect(parsed.medicationName, 'Metformin');
    }
    expect(
      () =>
          MedicationReconciliationItem.fromJson(medication(decision: 'change')),
      throwsFormatException,
    );
    expect(
      () => MedicationReconciliationItem.fromJson(medication(decision: 'late')),
      throwsFormatException,
    );
    expect(
      () => MedicationReconciliationItem.fromJson({
        ...medication(),
        'replacement_medication_name': 'Unapproved substitution',
      }),
      throwsFormatException,
    );
    expect(
      () => MedicationReconciliationItem.fromJson({
        ...medication(decision: 'replace'),
        'replacement_medication_name': null,
      }),
      throwsFormatException,
    );
  });

  test('rejects cross-clinic, wrong patient/admission, wrong id or event', () {
    expect(() => parse(record(clinic: 8)), throwsFormatException);
    expect(() => parse(record(patient: 8)), throwsFormatException);
    expect(() => parse(record(admission: 8)), throwsFormatException);
    expect(
      () => parse({...record(), 'patient_id': '5'}),
      throwsFormatException,
    );
    expect(
      () => MedicationReconciliationRecord.fromJson(
        record(),
        expectedClinicId: 3,
        expectedId: 44,
      ),
      throwsFormatException,
    );
    expect(
      () => MedicationReconciliationRecord.fromJson(
        record(),
        expectedClinicId: 3,
        expectedEvent: MedicationReconciliationEvent.discharge,
      ),
      throwsFormatException,
    );
  });

  test('rejects malformed documents and inconsistent finalization', () {
    expect(
      () => parse({...record(), 'no_medications_reported': 'false'}),
      throwsFormatException,
    );
    expect(
      () => parse(record(noMedications: true, items: [medication()])),
      throwsFormatException,
    );
    expect(
      () => parse(record(items: [medication(), medication()])),
      throwsFormatException,
    );
    expect(
      () => parse(record(status: 'final', items: [])),
      throwsFormatException,
    );
    expect(
      () => parse(
        record(
          status: 'final',
          items: [medication(decision: 'unknown')],
        ),
      ),
      throwsFormatException,
    );
    expect(
      () => parse(record(status: 'final', version: 2)),
      throwsFormatException,
    );
    expect(
      () => parse(record(status: 'amended', version: 1)),
      throwsFormatException,
    );
    expect(
      () => parse({...record(), 'finalized_at': '2026-11-10T09:00:00Z'}),
      throwsFormatException,
    );
    expect(
      () => parse({
        ...record(status: 'final'),
        'finalized_at': '2026-11-10T09:00:00',
      }),
      throwsFormatException,
    );
    expect(
      () => parse({...record(), 'event_type': 'home'}),
      throwsFormatException,
    );
    expect(
      () => parse({...record(), 'items': 'not-an-array'}),
      throwsFormatException,
    );
    expect(
      () => parse({...record(), 'record_version': true}),
      throwsFormatException,
    );
    expect(
      () => parse({...record(), 'record_version': 0}),
      throwsFormatException,
    );
  });

  test('list requires real raw-page envelope and exact scope', () {
    final page = MedicationReconciliationPage.fromJson(
      {
        'items': [record()],
        'total': 2,
        'page': 1,
        'per_page': 1,
      },
      expectedClinicId: 3,
      expectedAdmissionId: 7,
      expectedPatientId: 5,
      requestedPage: 1,
      requestedPerPage: 1,
    );
    expect(page.items.single.id, 31);
    expect(page.hasNext, isTrue);
    expect(() => page.items.clear(), throwsUnsupportedError);
    final empty = MedicationReconciliationPage.fromJson(
      {'items': [], 'total': 0, 'page': 1, 'per_page': 50},
      expectedClinicId: 3,
      expectedAdmissionId: 7,
      requestedPage: 1,
      requestedPerPage: 50,
    );
    expect(empty.items, isEmpty);
    expect(empty.total, 0);
  });

  test(
    'rejects page ambiguity, duplicates, wrong event and forged records',
    () {
      final base = {
        'items': [record()],
        'total': 1,
        'page': 1,
        'per_page': 50,
      };
      MedicationReconciliationPage load(
        Map<String, dynamic> json, {
        MedicationReconciliationEvent? event,
      }) => MedicationReconciliationPage.fromJson(
        json,
        expectedClinicId: 3,
        expectedAdmissionId: 7,
        expectedPatientId: 5,
        requestedPage: 1,
        requestedPerPage: 50,
        requestedEvent: event,
      );
      expect(() => load({...base, 'page': 2}), throwsFormatException);
      expect(() => load({...base, 'total': -1}), throwsFormatException);
      expect(
        () => load({
          ...base,
          'items': [record(), record()],
        }),
        throwsFormatException,
      );
      expect(
        () => load({
          ...base,
          'items': [record(clinic: 8)],
        }),
        throwsFormatException,
      );
      expect(
        () => load(base, event: MedicationReconciliationEvent.discharge),
        throwsFormatException,
      );
    },
  );
}
