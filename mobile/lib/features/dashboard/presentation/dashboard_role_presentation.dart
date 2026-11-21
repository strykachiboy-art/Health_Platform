import 'package:clinic_system_pro/features/dashboard/data/models/dashboard_snapshot.dart';

/// WD07-B S10-B: presentation-only selection of server-supplied KPI cards.
///
/// A metric remains a read-only snapshot, not a role/clinic authorization
/// grant. No value is invented, renamed or recomputed by the client. The
/// backend remains responsible for *which data can be returned*.
///
/// The existing API groups several different clinical and operations roles
/// in one dashboard family. Some generic cards are not meaningful for all
/// members of those families (for example, 'My patients' for a paramedic).
/// Until a dedicated role-specific backend KPI is approved, omit the card.
List<DashboardMetric> visibleDashboardMetricsForRole(
  String role,
  List<DashboardMetric> serverMetrics,
) {
  final normalizedRole = role.trim().toLowerCase();
  const clinical = <String, Set<String>>{
    'doctor': <String>{
      'appointments',
      'appointments_today',
      'my_patients',
      'pending_consultations',
      'pending_labs',
      'ai_requests',
      'unread_chat',
      'active_admissions',
      'draft_discharge_reconciliations',
    },
    'nurse': <String>{
      'appointments',
      'appointments_today',
      'my_patients',
      'pending_consultations',
      'pending_labs',
      'ai_requests',
      'unread_chat',
      'pending_nursing_tasks',
      'emar_due',
    },
    'pharmacist': <String>{'draft_medication_reconciliations', 'ai_requests'},
    'lab_technician': <String>{'pending_labs', 'ai_requests'},
    'paramedic': <String>{},
    'emt': <String>{},
  };
  const operations = <String, Set<String>>{
    'receptionist': <String>{
      'appointments',
      'missed_appointments',
      'hospital_active_admissions',
      'hospital_available_beds',
    },
    'driver': <String>{
      'active_ambulance_trips',
      'available_ambulances',
      'maintenance_ambulances',
    },
    'ambulance_dispatcher': <String>{
      'active_ambulance_trips',
      'available_ambulances',
      'maintenance_ambulances',
    },
    'ambulance_coordinator': <String>{
      'active_ambulance_trips',
      'available_ambulances',
      'maintenance_ambulances',
    },
    'other': <String>{},
  };
  final allowed = clinical[normalizedRole] ?? operations[normalizedRole];
  // Admin/system/finance/patient dashboard families already have dedicated
  // backend models and keep their existing real, server-supplied cards.
  if (allowed == null) return List<DashboardMetric>.unmodifiable(serverMetrics);
  return List<DashboardMetric>.unmodifiable(
    serverMetrics.where((metric) => allowed.contains(metric.key)),
  );
}

/// Explanatory copy only. It must not imply that an ambulance assignment,
/// communication privilege or specialist KPI exists unless verified on server.
String? dashboardMetricContextForRole(String role) {
  switch (role.trim().toLowerCase()) {
    case 'paramedic':
    case 'emt':
      return 'Prehospital-specific counts are not supplied by this dashboard. '
          'Open Ambulance & trips to see server-authorized trip status.';
    case 'driver':
    case 'ambulance_dispatcher':
    case 'ambulance_coordinator':
      return 'Ambulance counts below are clinic-wide, not personal assignments. '
          'Open Ambulance & trips for server-authorized operational status.';
    case 'pharmacist':
      return 'Pharmacy and medication-related counts only. '
          'Use Pharmacy stock for authorized inventory information.';
    case 'lab_technician':
      return 'Laboratory-related counts only. '
          'A pending-order total does not grant access to every order.';
    case 'receptionist':
      return 'Front-desk and admission-related clinic counts only.';
    case 'other':
      return 'No specialized KPI cards are currently configured for this role.';
    default:
      return null;
  }
}
