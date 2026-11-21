import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/features/dashboard/data/models/dashboard_snapshot.dart';
import 'package:clinic_system_pro/features/dashboard/presentation/dashboard_role_presentation.dart';

DashboardMetric metric(String key) => DashboardMetric.fromJson(
  <String, dynamic>{'key': key, 'label': key, 'value': 3, 'unit': 'items'},
);

void main() {
  const allRoles = <String>{
    'super_admin',
    'admin',
    'doctor',
    'nurse',
    'patient',
    'pharmacist',
    'lab_technician',
    'receptionist',
    'accountant',
    'paramedic',
    'emt',
    'driver',
    'ambulance_dispatcher',
    'ambulance_coordinator',
    'other',
  };

  test('S10B role coverage includes all fifteen backend roles', () {
    expect(allRoles.length, 15);
    for (final role in allRoles) {
      expect(
        visibleDashboardMetricsForRole(role, <DashboardMetric>[
          metric('ai_requests'),
        ]),
        isA<List<DashboardMetric>>(),
      );
    }
  });

  test('S10B paramedic and EMT do not show doctor, AI or chat KPI cards', () {
    final cards = <DashboardMetric>[
      metric('appointments'),
      metric('my_patients'),
      metric('pending_consultations'),
      metric('pending_labs'),
      metric('ai_requests'),
      metric('unread_chat'),
    ];
    for (final role in <String>['paramedic', 'emt']) {
      expect(visibleDashboardMetricsForRole(role, cards), isEmpty);
      expect(
        dashboardMetricContextForRole(role),
        contains('Ambulance & trips'),
      );
    }
  });

  test('S10B doctor and nurse keep their existing clinical cards', () {
    final cards = <DashboardMetric>[
      metric('appointments'),
      metric('my_patients'),
      metric('unread_chat'),
      metric('emar_due'),
      metric('pending_nursing_tasks'),
      metric('draft_discharge_reconciliations'),
    ];
    expect(
      visibleDashboardMetricsForRole('doctor', cards).map((e) => e.key),
      <String>[
        'appointments',
        'my_patients',
        'unread_chat',
        'draft_discharge_reconciliations',
      ],
    );
    expect(
      visibleDashboardMetricsForRole('nurse', cards).map((e) => e.key),
      <String>[
        'appointments',
        'my_patients',
        'unread_chat',
        'emar_due',
        'pending_nursing_tasks',
      ],
    );
  });

  test('S10B pharmacy and laboratory hide unrelated clinic-wide metrics', () {
    final cards = <DashboardMetric>[
      metric('my_patients'),
      metric('pending_labs'),
      metric('draft_medication_reconciliations'),
      metric('ai_requests'),
      metric('unread_chat'),
    ];
    expect(
      visibleDashboardMetricsForRole('PHARMACIST', cards).map((e) => e.key),
      <String>['draft_medication_reconciliations', 'ai_requests'],
    );
    expect(
      visibleDashboardMetricsForRole('lab_technician', cards).map((e) => e.key),
      <String>['pending_labs', 'ai_requests'],
    );
  });

  test(
    'S10B ambulance operations are clinic-wide; no invented assignments',
    () {
      final cards = <DashboardMetric>[
        metric('appointments'),
        metric('active_ambulance_trips'),
        metric('available_ambulances'),
        metric('unread_chat_messages'),
      ];
      for (final role in <String>[
        'driver',
        'ambulance_dispatcher',
        'ambulance_coordinator',
      ]) {
        expect(
          visibleDashboardMetricsForRole(role, cards).map((e) => e.key),
          <String>['active_ambulance_trips', 'available_ambulances'],
        );
        expect(dashboardMetricContextForRole(role), contains('clinic-wide'));
      }
    },
  );

  test('S10B receptionist sees only front desk metrics and other none', () {
    final cards = <DashboardMetric>[
      metric('appointments'),
      metric('missed_appointments'),
      metric('active_ambulance_trips'),
      metric('hospital_available_beds'),
      metric('unread_chat_messages'),
    ];
    expect(
      visibleDashboardMetricsForRole('receptionist', cards).map((e) => e.key),
      <String>[
        'appointments',
        'missed_appointments',
        'hospital_available_beds',
      ],
    );
    expect(visibleDashboardMetricsForRole('other', cards), isEmpty);
  });

  test(
    'S10B dedicated backend dashboard families retain every server card',
    () {
      final cards = <DashboardMetric>[metric('money'), metric('patients')];
      for (final role in <String>[
        'super_admin',
        'admin',
        'patient',
        'accountant',
      ]) {
        expect(
          visibleDashboardMetricsForRole(role, cards),
          orderedEquals(cards),
        );
        expect(dashboardMetricContextForRole(role), isNull);
      }
    },
  );

  test(
    'S10B filters only presentation, never alters server snapshot values',
    () {
      final card = metric('pending_labs');
      final server = <DashboardMetric>[card, metric('my_patients')];
      final shown = visibleDashboardMetricsForRole('lab_technician', server);
      expect(shown.single, same(card));
      expect(shown.single.value.text, '3');
      expect(server.length, 2);
      expect(() => shown.add(card), throwsUnsupportedError);
    },
  );
}
