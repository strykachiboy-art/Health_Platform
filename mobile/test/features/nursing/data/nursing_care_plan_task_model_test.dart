import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/features/nursing/data/models/nursing_care_plan_task.dart';

Map<String, dynamic> plan({String status = 'active', int clinic = 3}) => {
  'id': 20,
  'clinic_id': clinic,
  'patient_id': 7,
  'admission_id': 11,
  'created_by_staff_id': 8,
  'title': 'Pain management',
  'problem': 'Acute discomfort',
  'goal': 'Pain score reduced',
  'status': status,
  'started_at': '2026-11-08T09:00:00+00:00',
  'target_at': null,
  'completed_at': null,
  'version': 1,
};

Map<String, dynamic> task({String status = 'pending', int planId = 20}) => {
  'id': 30,
  'care_plan_id': planId,
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 11,
  'created_by_staff_id': 8,
  'assigned_to_staff_id': null,
  'description': 'Assess pain',
  'instructions': 'Use validated scale',
  'priority': 'high',
  'status': status,
  'due_at': null,
  'version': 1,
};

NursingCarePlan parsePlan(Map<String, dynamic> value) =>
    NursingCarePlan.fromJson(
      value,
      expectedClinicId: 3,
      expectedPatientId: 7,
      expectedAdmissionId: 11,
    );

NursingCareTask parseTask(Map<String, dynamic> value) =>
    NursingCareTask.fromJson(
      value,
      expectedClinicId: 3,
      expectedPatientId: 7,
      expectedAdmissionId: 11,
    );

void main() {
  test('care plan is scoped and status actions follow readback', () {
    final p = parsePlan(plan());
    expect(p.title, 'Pain management');
    expect(p.startedAt.isUtc, isTrue);
    expect(p.allows(NursingPlanAction.hold), isTrue);
    expect(p.allows(NursingPlanAction.resume), isFalse);
    expect(p.allows(NursingPlanAction.complete), isTrue);
    final onHold = parsePlan(plan(status: 'on_hold'));
    expect(onHold.allows(NursingPlanAction.resume), isTrue);
    expect(onHold.allows(NursingPlanAction.complete), isFalse);
  });

  test('completed and cancelled plans do not expose transitions', () {
    for (final value in ['completed', 'cancelled']) {
      final p = parsePlan(plan(status: value));
      for (final action in NursingPlanAction.values) {
        expect(p.allows(action), isFalse);
      }
    }
  });

  test('care task exposes status, priority and safe transitions', () {
    final pending = parseTask(task());
    expect(pending.priority, NursingTaskPriority.high);
    expect(pending.allows(NursingTaskAction.start), isTrue);
    expect(pending.allows(NursingTaskAction.complete), isFalse);
    final inProgress = parseTask(task(status: 'in_progress'));
    expect(inProgress.allows(NursingTaskAction.complete), isTrue);
    expect(inProgress.allows(NursingTaskAction.cancel), isTrue);
    final done = parseTask(task(status: 'completed'));
    expect(done.allows(NursingTaskAction.start), isFalse);
  });

  test('clinic, patient and admission mismatches fail closed', () {
    expect(() => parsePlan(plan(clinic: 4)), throwsFormatException);
    expect(
      () => parseTask({...task(), 'admission_id': 12}),
      throwsFormatException,
    );
    expect(
      () => parseTask({...task(), 'patient_id': 9}),
      throwsFormatException,
    );
  });

  test('unknown status, priority and malformed timestamps fail', () {
    expect(() => parsePlan(plan(status: 'finished')), throwsFormatException);
    expect(
      () => parseTask({...task(), 'priority': 'stat'}),
      throwsFormatException,
    );
    expect(
      () => parsePlan({...plan(), 'started_at': 'yesterday'}),
      throwsFormatException,
    );
  });

  test('raw nursing list rejects duplicate identifiers and wrong count', () {
    final parsed = NursingCareList<NursingCarePlan>.parse({
      'items': [plan()],
      'total': 1,
    }, parsePlan);
    expect(parsed.items.single.id, 20);
    expect(
      () => NursingCareList<NursingCarePlan>.parse({
        'items': [plan(), plan()],
        'total': 2,
      }, parsePlan),
      throwsFormatException,
    );
    expect(
      () => NursingCareList<NursingCarePlan>.parse({
        'items': [plan()],
        'total': 2,
      }, parsePlan),
      throwsFormatException,
    );
  });
}
