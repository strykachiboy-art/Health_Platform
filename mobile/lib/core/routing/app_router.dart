import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/clinic_context_switch_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/authenticated_sign_out_coordinator.dart';
import 'package:clinic_system_pro/features/profile/data/profile_remote_data_source.dart';
import 'package:clinic_system_pro/features/profile/presentation/account_clinic_screen.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_feature_frame.dart';

import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/backend_health/presentation/backend_health_screen.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/presentation/clinical_alert_inbox_screen.dart';
import 'package:clinic_system_pro/features/clinical_safety/presentation/clinical_alert_detail_screen.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_context.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_remote_data_source.dart';
import 'package:clinic_system_pro/features/emergency_access/presentation/emergency_access_request_screen.dart';
import 'package:clinic_system_pro/features/emergency_access/presentation/emergency_access_review_screen.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_remote_data_source.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_remote_data_source.dart';
import 'package:clinic_system_pro/features/consultations/presentation/consultation_start_screen.dart';
import 'package:clinic_system_pro/features/consultations/presentation/consultation_detail_screen.dart';
import 'package:clinic_system_pro/features/consultations/presentation/consultation_clinical_note_screen.dart';
import 'package:clinic_system_pro/core/sync/appointment_create_queue_producer.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_create_screen.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_entry_screen.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_list_screen.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_mutation_screen.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_create_sync_status_panel.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_create_sync_status_route.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/data/models/appointment_record.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_allergy_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_vitals_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_consultation_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_clinical_alert_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_detail_screen.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_directory_screen.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_registration_screen.dart';

GoRouter createAppRouter({
  required BackendHealthRemoteDataSource healthDataSource,
  required Widget loginScreen,
  Future<AuthSessionState> Function()? loadHomeSession,
  DashboardRemoteDataSource? dashboardDataSource,
  ProfileRemoteDataSource? profileDataSource,
  AuthRemoteDataSource? authRemoteDataSource,
  ClinicContextSwitchCoordinator? clinicContextSwitcher,
  AuthenticatedSignOutCoordinator? signOutCoordinator,
  Widget Function()? statusStripBuilder,
  AppointmentRemoteDataSource? appointmentDataSource,
  ConsultationRemoteDataSource? consultationDataSource,
  AppointmentCreateSyncPageLoader? appointmentCreateSyncPageLoader,
  PatientRemoteDataSource? patientDataSource,
  PatientAllergyRemoteDataSource? patientAllergyDataSource,
  PatientVitalsRemoteDataSource? patientVitalsDataSource,
  PatientAdmissionRemoteDataSource? patientAdmissionDataSource,
  PatientConsultationRemoteDataSource? patientConsultationDataSource,
  PatientPrescriptionRemoteDataSource? patientPrescriptionDataSource,
  PatientClinicalAlertRemoteDataSource? patientClinicalAlertDataSource,
  EmergencyAccessRemoteDataSource? emergencyAccessDataSource,
  ClinicalSafetyRemoteDataSource? clinicalSafetyDataSource,
  EmergencyAccessContext? emergencyAccessContext,
  SessionNavigationGuard? sessionNavigationGuard,
}) {
  // Router lifetime, not route lifetime. An uncertain mutation stays locked
  // when the user leaves and revisits the screen during this session.
  final mutationSource =
      appointmentDataSource != null &&
          sessionNavigationGuard != null &&
          loadHomeSession != null &&
          identical(
            appointmentDataSource.sessionNavigationGuard,
            sessionNavigationGuard,
          )
      ? AppointmentMutationRemoteDataSource(
          apiClient: appointmentDataSource.apiClient,
          loadSession: loadHomeSession,
          sessionNavigationGuard: sessionNavigationGuard,
        )
      : null;
  return GoRouter(
    initialLocation: '/login',
    refreshListenable: sessionNavigationGuard,
    redirect: (context, state) {
      final authRedirect = sessionNavigationGuard?.redirect(
        state.matchedLocation,
      );
      if (authRedirect != null) return authRedirect;
      // Login and saved-session restoration use the existing guard. Once
      // ready, land on home without requiring a second manual button tap.
      // The explicit session route remains reachable for logout/recovery.
      if (loadHomeSession != null &&
          sessionNavigationGuard?.canAccessProtectedRoutes == true &&
          state.matchedLocation == '/login' &&
          state.uri.queryParameters['session'] != '1') {
        return '/home';
      }
      return null;
    },
    routes: [
      GoRoute(
        path: '/login',
        name: 'login',
        builder: (context, state) => loginScreen,
      ),
      GoRoute(
        path: '/home',
        name: 'home',
        builder: (context, state) {
          final loader = loadHomeSession;
          if (loader == null) return const _HomeRouteUnavailableScreen();
          return AuthenticatedHomeScreen(
            loadSession: loader,
            dashboardDataSource: dashboardDataSource,
            statusStripBuilder: statusStripBuilder,
            sessionNavigationGuard: sessionNavigationGuard,
          );
        },
      ),
      ShellRoute(
        builder: (context, state, child) {
          final loader = loadHomeSession;
          // Keep the old router test seams unchanged when no home loader
          // is configured; production always injects the session loader.
          if (loader == null) return child;
          return AuthenticatedFeatureFrame(
            location: state.uri.path,
            loadSession: loader,
            sessionNavigationGuard: sessionNavigationGuard,
            statusStripBuilder: statusStripBuilder,
            child: child,
          );
        },
        routes: [
          GoRoute(
            path: '/account',
            name: 'account-clinic',
            builder: (context, state) {
              final profile = profileDataSource;
              final auth = authRemoteDataSource;
              final switcher = clinicContextSwitcher;
              final loader = loadHomeSession;
              if (profile == null ||
                  auth == null ||
                  switcher == null ||
                  loader == null) {
                return const Scaffold(
                  body: Center(child: Text('Account is not configured.')),
                );
              }
              return AccountClinicScreen(
                loadSession: loader,
                profileDataSource: profile,
                authRemoteDataSource: auth,
                clinicContextSwitcher: switcher,
                signOutCoordinator: signOutCoordinator,
                sessionNavigationGuard: sessionNavigationGuard,
              );
            },
          ),
          GoRoute(
            path: '/consultations',
            name: 'consultations-start',
            builder: (context, state) {
              final source = consultationDataSource;
              final guard = sessionNavigationGuard;
              if (source == null ||
                  guard == null ||
                  !identical(source.sessionNavigationGuard, guard)) {
                return const Scaffold(
                  body: Center(child: Text('Consultations unavailable.')),
                );
              }
              return ConsultationStartScreen(
                dataSource: source,
                onCreated: (patientId, consultationId) => context.go(
                  '/consultations/patient/$patientId/$consultationId',
                ),
              );
            },
          ),

          GoRoute(
            path: '/consultations/patient/:patientId/:consultationId/note',
            name: 'consultation-clinical-note-lifecycle',
            builder: (context, state) {
              final source = consultationDataSource;
              final guard = sessionNavigationGuard;
              final patientId = int.tryParse(
                state.pathParameters['patientId'] ?? '',
              );
              final consultationId = int.tryParse(
                state.pathParameters['consultationId'] ?? '',
              );
              if (source == null ||
                  guard == null ||
                  !identical(source.sessionNavigationGuard, guard) ||
                  patientId == null ||
                  patientId <= 0 ||
                  consultationId == null ||
                  consultationId <= 0) {
                return const Scaffold(
                  body: Center(child: Text('Clinical note unavailable.')),
                );
              }
              return ConsultationClinicalNoteScreen(
                key: ValueKey('p23c-note-route-$patientId-$consultationId'),
                consultationDataSource: source,
                patientId: patientId,
                consultationId: consultationId,
              );
            },
          ),
          GoRoute(
            path: '/consultations/patient/:patientId/:consultationId',
            name: 'consultation-detail',
            builder: (context, state) {
              final source = consultationDataSource;
              final guard = sessionNavigationGuard;
              final patientId = int.tryParse(
                state.pathParameters['patientId'] ?? '',
              );
              final consultationId = int.tryParse(
                state.pathParameters['consultationId'] ?? '',
              );
              if (source == null ||
                  guard == null ||
                  !identical(source.sessionNavigationGuard, guard) ||
                  patientId == null ||
                  patientId <= 0 ||
                  consultationId == null ||
                  consultationId <= 0) {
                return const Scaffold(
                  body: Center(child: Text('Consultation record unavailable.')),
                );
              }
              return ConsultationDetailScreen(
                key: ValueKey('p23c-consultation-$patientId-$consultationId'),
                dataSource: source,
                patientId: patientId,
                consultationId: consultationId,
                onClinicalActionsRequested: () => context.push<void>(
                  '/consultations/patient/$patientId/$consultationId/note',
                ),
              );
            },
          ), // Only the existing encrypted queue can submit an appointment-create
          // intent. Route availability is not proof of backend authorization.
          GoRoute(
            path: '/appointments/create',
            name: 'appointments-create',
            builder: (context, state) {
              final source = appointmentDataSource;
              final guard = sessionNavigationGuard;
              final loader = loadHomeSession;
              final queueScope = context
                  .dependOnInheritedWidgetOfExactType<
                    AppointmentCreateQueueScope
                  >();
              if (source == null ||
                  guard == null ||
                  loader == null ||
                  queueScope == null ||
                  !identical(source.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes) {
                return const _AppointmentRouteUnavailableScreen();
              }
              return AppointmentCreateScreen(
                key: const ValueKey('appointment-create-route'),
                loadSession: loader,
                sessionNavigationGuard: guard,
                enqueue: queueScope.producer.enqueue,
              );
            },
          ),
          // A bookmarked URL alone cannot supply a verified appointment row.
          // This route does not GET by ID (the backend has no such endpoint).
          GoRoute(
            path: '/appointments/change',
            name: 'appointments-change',
            builder: (context, state) {
              final source = appointmentDataSource;
              final guard = sessionNavigationGuard;
              final mutations = mutationSource;
              final grant = state.extra;
              if (source == null ||
                  guard == null ||
                  mutations == null ||
                  grant is! _AppointmentChangeGrant ||
                  grant.guardRevision != guard.revision ||
                  !identical(source.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes ||
                  grant.record.id <= 0 ||
                  grant.record.clinicId <= 0 ||
                  grant.record.patientId <= 0 ||
                  grant.record.staffId <= 0 ||
                  (grant.record.status != AppointmentRecordStatus.scheduled &&
                      grant.record.status !=
                          AppointmentRecordStatus.confirmed)) {
                return const _AppointmentRouteUnavailableScreen();
              }
              return AppointmentMutationScreen(
                key: ValueKey('appointment-change-route-${grant.record.id}'),
                appointment: grant.record,
                mutationDataSource: mutations,
                readDataSource: source,
              );
            },
          ),
          // A direct URL does not itself authorize reading local queue metadata.
          // Production must inject a reader bound to the active user, clinic,
          // and navigation revision. No reader means no route availability.
          GoRoute(
            path: '/appointments/sync',
            name: 'appointments-create-sync-status',
            builder: (context, state) {
              final source = appointmentDataSource;
              final guard = sessionNavigationGuard;
              final loadSession = loadHomeSession;
              final readPage = appointmentCreateSyncPageLoader;
              if (source == null ||
                  guard == null ||
                  loadSession == null ||
                  readPage == null ||
                  !identical(source.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes) {
                return const _AppointmentRouteUnavailableScreen();
              }
              return AppointmentCreateSyncStatusRoute(
                key: const ValueKey('appointment-sync-status-route'),
                sessionNavigationGuard: guard,
                loadSession: loadSession,
                loadPage: readPage,
              );
            },
          ),
          // Discoverable entry does not assume a user ID is a staff ID.
          GoRoute(
            path: '/appointments',
            name: 'appointments-entry',
            builder: (context, state) {
              final source = appointmentDataSource;
              final guard = sessionNavigationGuard;
              final loader = loadHomeSession;
              if (source == null ||
                  guard == null ||
                  loader == null ||
                  !identical(source.sessionNavigationGuard, guard)) {
                return const _AppointmentRouteUnavailableScreen();
              }
              return AppointmentEntryScreen(
                loadSession: loader,
                sessionNavigationGuard: guard,
                onPatientSelected: (patientId) =>
                    context.push<void>('/appointments/patient/$patientId'),
                onSyncStatusRequested: appointmentCreateSyncPageLoader == null
                    ? null
                    : () => context.push<void>('/appointments/sync'),
                onCreateRequested:
                    context
                            .dependOnInheritedWidgetOfExactType<
                              AppointmentCreateQueueScope
                            >() ==
                        null
                    ? null
                    : () => context.push<void>('/appointments/create'),
                onStaffSelected: (staffId) =>
                    context.push<void>('/appointments/staff/$staffId'),
              );
            },
          ), // Read-only appointment views require numeric scope IDs and a
          // data source bound to this exact navigation guard.
          GoRoute(
            path: '/appointments/patient/:patientId',
            name: 'appointments-patient-history',
            builder: (context, state) {
              final source = appointmentDataSource;
              final guard = sessionNavigationGuard;
              final patientId = int.tryParse(
                state.pathParameters['patientId'] ?? '',
              );
              if (source == null ||
                  guard == null ||
                  !identical(source.sessionNavigationGuard, guard) ||
                  patientId == null ||
                  patientId <= 0) {
                return const _AppointmentRouteUnavailableScreen();
              }
              return AppointmentListScreen(
                key: ValueKey('appointment-patient-route-$patientId'),
                dataSource: source,
                onChangeRequested: mutationSource == null
                    ? null
                    : (record) => context.push<void>(
                        '/appointments/change',
                        extra: _AppointmentChangeGrant(record, guard.revision),
                      ),
                patientId: patientId,
              );
            },
          ),
          GoRoute(
            path: '/appointments/staff/:staffId',
            name: 'appointments-staff-schedule',
            builder: (context, state) {
              final source = appointmentDataSource;
              final guard = sessionNavigationGuard;
              final staffId = int.tryParse(
                state.pathParameters['staffId'] ?? '',
              );
              if (source == null ||
                  guard == null ||
                  !identical(source.sessionNavigationGuard, guard) ||
                  staffId == null ||
                  staffId <= 0) {
                return const _AppointmentRouteUnavailableScreen();
              }
              return AppointmentListScreen(
                key: ValueKey('appointment-staff-route-$staffId'),
                dataSource: source,
                onChangeRequested: mutationSource == null
                    ? null
                    : (record) => context.push<void>(
                        '/appointments/change',
                        extra: _AppointmentChangeGrant(record, guard.revision),
                      ),
                staffId: staffId,
              );
            },
          ),
          GoRoute(
            path: '/patients',
            name: 'patients',
            builder: (context, state) {
              final source = patientDataSource;
              if (source == null) return const _PatientRouteUnavailableScreen();
              return PatientDirectoryScreen(dataSource: source);
            },
          ),
          GoRoute(
            path: '/patients/register',
            name: 'patient-registration',
            builder: (context, state) {
              final source = patientDataSource;
              if (source == null) return const _PatientRouteUnavailableScreen();
              return PatientRegistrationScreen(dataSource: source);
            },
          ),
          GoRoute(
            path: '/patients/:patientId',
            name: 'patient-detail',
            builder: (context, state) {
              final source = patientDataSource;
              if (source == null) return const _PatientRouteUnavailableScreen();
              final patientId =
                  int.tryParse(state.pathParameters['patientId'] ?? '') ?? 0;
              return PatientDetailScreen(
                key: ValueKey('patient-detail-route-$patientId'),
                dataSource: source,
                patientId: patientId,
                allergyDataSource: patientAllergyDataSource,
                vitalsDataSource: patientVitalsDataSource,
                admissionDataSource: patientAdmissionDataSource,
                consultationDataSource: patientConsultationDataSource,
                prescriptionDataSource: patientPrescriptionDataSource,
                clinicalAlertDataSource: patientClinicalAlertDataSource,
                emergencyAccessContext: emergencyAccessContext,
              );
            },
          ),
          GoRoute(
            path: '/emergency-access/request',
            name: 'emergency-access-request',
            builder: (context, state) {
              final source = emergencyAccessDataSource;
              if (source == null) {
                return const _EmergencyAccessRouteUnavailableScreen();
              }
              return EmergencyAccessRequestScreen(dataSource: source);
            },
          ),
          GoRoute(
            path: '/emergency-access/requests/:emergencyAccessId',
            name: 'emergency-access-review',
            builder: (context, state) {
              final source = emergencyAccessDataSource;
              if (source == null) {
                return const _EmergencyAccessRouteUnavailableScreen();
              }

              final emergencyAccessId = int.tryParse(
                state.pathParameters['emergencyAccessId'] ?? '',
              );

              if (emergencyAccessId == null || emergencyAccessId <= 0) {
                return const _EmergencyAccessRouteUnavailableScreen();
              }

              return EmergencyAccessReviewScreen(
                dataSource: source,
                emergencyAccessId: emergencyAccessId,
                emergencyAccessContext: emergencyAccessContext,
              );
            },
          ),
          GoRoute(
            path: '/clinical-safety',
            name: 'clinical-alert-inbox',
            builder: (context, state) {
              final source = clinicalSafetyDataSource;
              if (source == null) {
                return const _ClinicalSafetyRouteUnavailableScreen();
              }
              return ClinicalAlertInboxScreen(
                dataSource: source,
                sessionNavigationGuard: sessionNavigationGuard,
                onAlertSelected: (alertId) =>
                    context.push<void>('/clinical-safety/alerts/$alertId'),
              );
            },
          ),
          GoRoute(
            path: '/clinical-safety/alerts/:alertId',
            name: 'clinical-alert-detail',
            builder: (context, state) {
              final source = clinicalSafetyDataSource;
              final alertId = int.tryParse(
                state.pathParameters['alertId'] ?? '',
              );
              if (source == null || alertId == null || alertId <= 0) {
                return const _ClinicalSafetyRouteUnavailableScreen();
              }
              return ClinicalAlertDetailScreen(
                dataSource: source,
                alertId: alertId,
                sessionNavigationGuard: sessionNavigationGuard,
              );
            },
          ),
        ],
      ),
      GoRoute(
        path: '/health',
        name: 'health',
        builder: (context, state) {
          return BackendHealthScreen(dataSource: healthDataSource);
        },
      ),
    ],
  );
}

class _HomeRouteUnavailableScreen extends StatelessWidget {
  const _HomeRouteUnavailableScreen();

  @override
  Widget build(BuildContext context) =>
      const Scaffold(body: Center(child: Text('Home is not configured.')));
}

class _PatientRouteUnavailableScreen extends StatelessWidget {
  const _PatientRouteUnavailableScreen();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Patients')),
      body: const Center(child: Text('Patient directory is not configured.')),
    );
  }
}

class _EmergencyAccessRouteUnavailableScreen extends StatelessWidget {
  const _EmergencyAccessRouteUnavailableScreen();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Emergency access')),
      body: const Center(child: Text('Emergency access is not configured.')),
    );
  }
}

class _ClinicalSafetyRouteUnavailableScreen extends StatelessWidget {
  const _ClinicalSafetyRouteUnavailableScreen();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Clinical safety')),
      body: const Center(child: Text('Clinical safety route is unavailable.')),
    );
  }
}

class _AppointmentRouteUnavailableScreen extends StatelessWidget {
  const _AppointmentRouteUnavailableScreen();

  @override
  Widget build(BuildContext context) => const Scaffold(
    body: Center(child: Text('Appointment route is unavailable.')),
  );
}

// Only this router can issue a navigation grant from a loaded appointment row.
// Direct URLs or arbitrary extras cannot forge this private capability.
class _AppointmentChangeGrant {
  const _AppointmentChangeGrant(this.record, this.guardRevision);
  final AppointmentRecord record;
  final int guardRevision;
}
