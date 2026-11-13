import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/surgery/data/models/surgery_read_models.dart';

Map<String, dynamic> widgetJson(String view) => {
  'view': view,
  'scope': switch (view) {
    'super_admin' => 'system_aggregate',
    'management' => 'clinic',
    'doctor' || 'nurse' => 'assigned_cases',
    'operations' => 'operational_aggregate',
    'finance' => 'billing_aggregate',
    'patient' => 'own_patient',
    _ => 'restricted',
  },
  'visibility': switch (view) {
    'super_admin' || 'operations' || 'finance' => 'aggregate_only',
    'management' => 'clinic_team',
    'doctor' || 'nurse' => 'assigned_only',
    'patient' => 'release_required',
    _ => 'restricted',
  },
  'total_cases': const {'operations', 'finance', 'restricted'}.contains(view)
      ? 0
      : 1,
  'planned':
      const {'operations', 'finance', 'restricted', 'patient'}.contains(view)
      ? 0
      : 1,
  'on_hold': 0,
  'cancelled': 0,
  'scheduled_bookings': view == 'operations' ? 1 : 0,
  'sealed_documentation': 0,
  'reviewed_pacu_preparations': 0,
  'dual_signed_reports': 0,
  'pending_signatures': 0,
  'linked_billing_invoices': view == 'finance' ? 1 : 0,
  'cases': <dynamic>[],
  'release_required': view == 'patient',
  'period_applies': false,
  'documentation_only': true,
  'operative_completion_authorized': false,
  'nursing_transfer_authorized': false,
  'medication_administration_authorized': false,
  'billing_clearance_authorized': false,
};

void main() {
  test('seven role-specific projections preserve their exact scopes', () {
    final roles = <String, String>{
      'super_admin': 'super_admin',
      'admin': 'management',
      'doctor': 'doctor',
      'nurse': 'nurse',
      'receptionist': 'operations',
      'accountant': 'finance',
      'patient': 'patient',
    };
    for (final entry in roles.entries) {
      final parsed = SurgeryDashboardWidget.fromJson(
        widgetJson(entry.value),
        role: entry.key,
      );
      expect(parsed.view, entry.value);
      expect(
        parsed.totalCases,
        const {'operations', 'finance'}.contains(entry.value) ? 0 : 1,
      );
      expect(parsed.cases, isEmpty);
    }
  });

  test('system and operational aggregates cannot leak case previews', () {
    for (final view in const [
      'super_admin',
      'operations',
      'finance',
      'patient',
    ]) {
      final payload = widgetJson(view);
      payload['cases'] = [
        {
          'case_id': 3,
          'case_status': 'planned',
          'documentation_status': 'draft',
          'pacu_preparation_status': 'not_started',
        },
      ];
      expect(
        () => SurgeryDashboardWidget.fromJson(
          payload,
          role: switch (view) {
            'super_admin' => 'super_admin',
            'operations' => 'receptionist',
            'finance' => 'accountant',
            _ => 'patient',
          },
        ),
        throwsFormatException,
      );
    }
  });

  test('doctor and nurse see only bounded CE1 case previews', () {
    final payload = widgetJson('doctor');
    payload['cases'] = [
      {
        'case_id': 17,
        'case_status': 'planned',
        'documentation_status': 'surgeon_signed',
        'pacu_preparation_status': 'prepared',
      },
    ];
    final parsed = SurgeryDashboardWidget.fromJson(payload, role: 'doctor');
    expect(parsed.cases.single.caseId, 17);
    expect(parsed.cases.single.documentationStatus, 'surgeon_signed');
    expect(
      () => SurgeryDashboardWidget.fromJson(payload, role: 'nurse'),
      throwsFormatException,
    );
  });

  test('positive clinical authority flag is rejected, never ignored', () {
    for (final field in const [
      'operative_completion_authorized',
      'medication_administration_authorized',
      'nursing_transfer_authorized',
      'billing_clearance_authorized',
    ]) {
      final payload = widgetJson('doctor')..[field] = true;
      expect(
        () => SurgeryDashboardWidget.fromJson(payload, role: 'doctor'),
        throwsFormatException,
      );
    }
  });

  test(
    'patient release stays required; restricted roles get no case views',
    () {
      final payload = widgetJson('patient')..['release_required'] = false;
      expect(
        () => SurgeryDashboardWidget.fromJson(payload, role: 'patient'),
        throwsFormatException,
      );
      final restricted = SurgeryDashboardWidget.fromJson(
        widgetJson('restricted'),
        role: 'pharmacist',
      );
      expect(restricted.visibility, 'restricted');
      expect(surgeryCanReadCases('pharmacist'), false);
      expect(surgeryCanReadCases('doctor'), true);
    },
  );

  test('read models reject fabricated operation clearance', () {
    final payload = {
      'case_id': 12,
      'case_status': 'planned',
      'journal_status': 'draft',
      'pacu_preparation_status': 'prepared',
      'operative_report_status': 'draft',
      'signature_roles': <String>[],
      'material_trace_count': 0,
      'clinical_reference_count': 0,
      'documentary_only': true,
      'operative_completion_authorized': false,
      'pacu_arrival_or_transfer_authorized': false,
      'sign_out_authorized': false,
      'medication_administration_authorized': false,
      'billing_clearance_authorized': false,
    };
    expect(SurgeryCaseRead.fromJson(payload).caseId, 12);
    payload['operative_completion_authorized'] = true;
    expect(() => SurgeryCaseRead.fromJson(payload), throwsFormatException);
  });
}
