import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/features/dashboard/data/models/dashboard_extended_sections.dart';

Map<String, dynamic> _exampleAi() => <String, dynamic>{
  'overview': <String, dynamic>{
    'total_ai_requests': 8,
    'total_credits_used': 19,
    'total_input_tokens': 12000,
    'total_output_tokens': 3400,
    'total_tokens': 15400,
    'estimated_cost': '9007199254740993.12345678',
    'pending_reviews': 2,
    'approved_reviews': 3,
    'rejected_reviews': 1,
    'low_risk_results': 1,
    'medium_risk_results': 2,
    'high_risk_results': 3,
    'critical_risk_results': 0,
  },
  'feature_usage': <dynamic>[
    <String, dynamic>{
      'feature': 'clinical_summary',
      'request_count': 8,
      'credits_used': 19,
      'estimated_cost': '0.00000001',
    },
  ],
  'risk_summary': <dynamic>[
    <String, dynamic>{'risk_level': 'high', 'count': 3},
  ],
  'approval_summary': <dynamic>[
    <String, dynamic>{'approval_status': 'pending', 'count': 2},
  ],
};

void main() {
  test('S11 accountant keeps actual invoice amounts and AI costs exactly', () {
    final details = DashboardExtendedSections.fromPayload(<String, dynamic>{
      'overview': <String, dynamic>{
        'outstanding_invoice_count': 2,
        'outstanding_invoice_amount': '9007199254740993.12345678',
        'overdue_invoice_count': 1,
        'overdue_invoice_amount': '123.45000001',
        'successful_payments_today': 4,
        'successful_payments_today_amount': '0.00000001',
        'pending_payment_count': 0,
      },
      'ai': <String, dynamic>{
        'total_ai_requests': 7,
        'total_credits_used': 40,
        'estimated_cost': '0.00000001',
      },
    }, 'accountant');
    expect(details.sections.length, 2);
    expect(details.sections.first.title, 'Billing and payment totals');
    expect(
      details.sections.first.metrics[1].value,
      '9007199254740993.12345678',
    );
    expect(details.sections.last.title, 'AI usage and safety');
    expect(details.sections.last.metrics.last.value, '0.00000001');
  });

  test('S11 admin displays real hospital, department and full AI reports', () {
    final details = DashboardExtendedSections.fromPayload(<String, dynamic>{
      'hospital': <String, dynamic>{
        'admissions': <String, dynamic>{
          'active_admissions': 2,
          'total_beds': 9,
        },
        'nursing': <String, dynamic>{'pending_tasks': 4},
        'emar': <String, dynamic>{'due': 5},
        'medication_reconciliation': <String, dynamic>{'draft': 6},
      },
      'departments': <String, dynamic>{'total_departments': 3},
      'ai': _exampleAi(),
    }, 'admin');
    expect(
      details.sections.map((e) => e.title),
      containsAll(<String>[
        'Hospital bed capacity',
        'Nursing workload',
        'Medication administrations',
        'Medication reconciliation',
        'Clinic departments',
        'AI usage and safety',
      ]),
    );
    final ai = details.sections.last;
    expect(
      ai.metrics.any((e) => e.value == '9007199254740993.12345678'),
      isTrue,
    );
    expect(ai.breakdowns.length, 3);
    expect(ai.breakdowns.first.entries.single, contains('credits used: 19'));
  });

  test('S11 super admin receives system access and AI summaries', () {
    final details = DashboardExtendedSections.fromPayload(<String, dynamic>{
      'access_control': <String, dynamic>{
        'total_users': 17,
        'users_by_role': <String, dynamic>{'doctor': 4, 'nurse': 3},
      },
      'departments': <String, dynamic>{'total_departments': 3},
      'ai': _exampleAi(),
    }, 'super_admin');
    expect(details.sections.length, 3);
    expect(details.sections.first.breakdowns.first.entries.length, 2);
    expect(
      details.sections.last.metrics.any((e) => e.label == 'Input tokens'),
      isTrue,
    );
  });

  test('S11 clinical staff AI is user-scoped presentation only', () {
    final details = DashboardExtendedSections.fromPayload(<String, dynamic>{
      'ai': <String, dynamic>{
        'overview': <String, dynamic>{
          'ai_requests': 4,
          'pending_reviews': 2,
          'high_risk_results': 1,
          'critical_risk_results': 0,
          'approved_results': 1,
          'rejected_results': 0,
        },
        'feature_usage': <dynamic>[
          <String, dynamic>{'feature': 'other', 'request_count': 4},
        ],
        'risk_summary': <dynamic>[],
        'review_summary': <dynamic>[],
      },
      'my_patients': <String, dynamic>{'total': 7},
    }, 'doctor');
    expect(details.sections.first.title, 'My patient assignment summary');
    expect(details.sections.last.metrics.first.value, '4');
  });

  test('S11 ambulance and patient roles never inherit manager analytics', () {
    final data = <String, dynamic>{
      'ai': _exampleAi(),
      'access_control': <String, dynamic>{'total_users': 99},
    };
    for (final role in <String>[
      'paramedic',
      'emt',
      'driver',
      'ambulance_dispatcher',
      'ambulance_coordinator',
      'patient',
      'other',
    ]) {
      expect(
        DashboardExtendedSections.fromPayload(data, role).sections,
        isEmpty,
        reason: role,
      );
    }
  });

  test('S11 rejects malformed, negative, and lossy numeric fields', () {
    for (final bad in <Object?>[-1, '-4.5', true, double.nan, '1,500']) {
      expect(
        () => DashboardExtendedSections.fromPayload(<String, dynamic>{
          'ai': <String, dynamic>{'total_ai_requests': bad},
        }, 'accountant'),
        throwsFormatException,
      );
    }
    expect(
      () => DashboardExtendedSections.fromPayload(<String, dynamic>{
        'ai': <String, dynamic>{
          'overview': <String, dynamic>{},
          'feature_usage': <String, dynamic>{},
        },
      }, 'admin'),
      throwsFormatException,
    );
  });

  test('S11 does not invent counts for missing optional sections', () {
    final details = DashboardExtendedSections.fromPayload(
      <String, dynamic>{},
      'accountant',
    );
    expect(details.sections, isEmpty);
    final partial = DashboardExtendedSections.fromPayload(<String, dynamic>{
      'ai': <String, dynamic>{'total_ai_requests': 2},
    }, 'accountant');
    expect(partial.sections.single.metrics.single.value, '2');
  });

  test('S11 receptionist sees clinic capacity only when returned', () {
    final details = DashboardExtendedSections.fromPayload(<String, dynamic>{
      'hospital_capacity': <String, dynamic>{'available_beds': 7},
    }, 'receptionist');
    expect(details.sections.single.metrics.single.value, '7');
    expect(
      DashboardExtendedSections.fromPayload(<String, dynamic>{
        'hospital_capacity': <String, dynamic>{'available_beds': 7},
      }, 'driver').sections,
      isEmpty,
    );
  });
}
