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
import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/presentation/clinical_orders_workspace_screen.dart';
import 'package:clinic_system_pro/features/clinical_orders/presentation/imaging_procedure_orders_screen.dart';
import 'package:clinic_system_pro/features/clinical_orders/presentation/diet_nursing_orders_screen.dart';
import 'package:clinic_system_pro/features/clinical_orders/presentation/blood_product_orders_screen.dart';
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
import 'package:clinic_system_pro/features/emar/presentation/emar_patient_route_screen.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/presentation/medication_reconciliation_patient_route_screen.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/presentation/clinic_chat_inbox_screen.dart';
import 'package:clinic_system_pro/features/notifications/data/notification_remote_data_source.dart';
import 'package:clinic_system_pro/features/notifications/data/notification_read_actions.dart';
import 'package:clinic_system_pro/features/notifications/presentation/notification_inbox_screen.dart';
import 'package:clinic_system_pro/features/notifications/data/notification_deep_link_contract.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_coordinator.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_remote_data_source.dart';
import 'package:clinic_system_pro/features/devices/data/device_token_provider.dart';
import 'package:clinic_system_pro/features/devices/presentation/device_registration_screen.dart';
import 'package:clinic_system_pro/features/nursing/data/ward_dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/nursing/presentation/ward_dashboard_screen.dart';
import 'package:clinic_system_pro/features/patients/data/patient_consultation_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_prescription_detail_screen.dart';
import 'package:clinic_system_pro/features/pharmacy/data/pharmacy_inventory_remote_data_source.dart';
import 'package:clinic_system_pro/features/pharmacy/presentation/pharmacy_stock_screen.dart';
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/presentation/lab_patient_orders_screen.dart';
import 'package:clinic_system_pro/features/p23h_navigation/p23h_clinical_action_hub.dart';
import 'package:clinic_system_pro/features/billing/data/billing_outstanding_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/data/billing_invoice_create_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/data/billing_payment_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_dispatch_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_lifecycle_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_invoice_link_remote_data_source.dart';
import 'package:clinic_system_pro/features/p23i_navigation/p23i_workspaces.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_prescription_mutation_screen.dart';
import 'package:clinic_system_pro/features/pharmacy/data/pharmacy_dispense_remote_data_source.dart';
import 'package:clinic_system_pro/features/pharmacy/presentation/pharmacy_dispense_screen.dart';
import 'package:clinic_system_pro/features/lab/data/lab_order_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/presentation/lab_order_mutation_screen.dart';
import 'package:clinic_system_pro/features/lab/data/lab_technician_lifecycle_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/presentation/lab_technician_lifecycle_screen.dart';
import 'package:clinic_system_pro/features/lab/data/lab_result_finalization_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/presentation/lab_result_finalization_screen.dart';
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
  ClinicalOrdersRemoteDataSource? clinicalOrdersDataSource,
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
  DeviceTokenProvider? deviceTokenProvider,
  DeviceConnectivitySource? deviceRegistrationConnectivitySource,
}) {
  // P23H.S06A reuses the already-injected authenticated client/session.
  // GET-only readers still recheck role, clinic, patient and session authority.
  final p23hReader = patientPrescriptionDataSource;
  final p23hGuard = sessionNavigationGuard;
  final p23hSourceReady =
      p23hReader != null &&
      p23hGuard != null &&
      identical(p23hReader.sessionNavigationGuard, p23hGuard);
  final PharmacyInventoryRemoteDataSource? pharmacyInventorySource;
  final LabOrderReadRemoteDataSource? labReadSource;
  if (p23hReader != null &&
      p23hGuard != null &&
      identical(p23hReader.sessionNavigationGuard, p23hGuard)) {
    pharmacyInventorySource = PharmacyInventoryRemoteDataSource(
      apiClient: p23hReader.apiClient,
      loadSession: p23hReader.loadSession,
      sessionNavigationGuard: p23hGuard,
    );
    labReadSource = LabOrderReadRemoteDataSource(
      apiClient: p23hReader.apiClient,
      loadSession: p23hReader.loadSession,
      sessionNavigationGuard: p23hGuard,
    );
  } else {
    pharmacyInventorySource = null;
    labReadSource = null;
  }
  // Retain one-shot clinical adapters for the router lifetime, keyed by exact
  // patient/record/action. Route revisits cannot manufacture another POST.
  final p23hActionAdapters = <String, Object>{};
  // P23I.S09 shares the authenticated P23H HTTP client and session guard.
  // No additional transport, token store, or backend grant is created here.
  final BillingOutstandingRemoteDataSource? p23iBillingReader;
  final AmbulanceReadRemoteDataSource? p23iAmbulanceReader;
  if (p23hSourceReady) {
    p23iBillingReader = BillingOutstandingRemoteDataSource(
      apiClient: p23hReader.apiClient,
      loadSession: p23hReader.loadSession,
      sessionNavigationGuard: p23hGuard,
    );
    p23iAmbulanceReader = AmbulanceReadRemoteDataSource(
      apiClient: p23hReader.apiClient,
      loadSession: p23hReader.loadSession,
      sessionNavigationGuard: p23hGuard,
    );
  } else {
    p23iBillingReader = null;
    p23iAmbulanceReader = null;
  }
  // Retain each P23I one-shot writer across route revisits. Closing a screen
  // cannot manufacture a fresh attempt after an uncertain financial/dispatch POST.
  final p23iActionAdapters = <String, Object>{};
  Widget p23iWorkspace(P23IWorkspaceMode mode) {
    final guard = sessionNavigationGuard;
    final billing = p23iBillingReader;
    final ambulance = p23iAmbulanceReader;
    if (guard == null ||
        !guard.canAccessProtectedRoutes ||
        billing == null ||
        ambulance == null) {
      return Scaffold(
        body: Center(
          child: Text(
            mode == P23IWorkspaceMode.billing
                ? 'Billing navigation unavailable.'
                : 'Ambulance navigation unavailable.',
          ),
        ),
      );
    }
    return P23IWorkspaceScreen(
      key: ValueKey('p23i-workspace-route-${mode.name}'),
      mode: mode,
      guard: guard,
      loadSession: billing.loadSession,
      billingReader: billing,
      ambulanceReader: ambulance,
      invoiceCreationSource: (patientId) => p23iActionAdapters.putIfAbsent(
        'invoice-create/${guard.revision}/$patientId',
        () => BillingInvoiceCreateRemoteDataSource(reader: billing),
      ) as BillingInvoiceCreateRemoteDataSource,
      paymentSource: (invoiceId) => p23iActionAdapters.putIfAbsent(
        'payment/${guard.revision}/$invoiceId',
        () => BillingPaymentRemoteDataSource(reader: billing),
      ) as BillingPaymentRemoteDataSource,
      dispatchSource: () => p23iActionAdapters.putIfAbsent(
        'dispatch/${guard.revision}',
        () => AmbulanceDispatchRemoteDataSource(reader: ambulance),
      ) as AmbulanceDispatchRemoteDataSource,
      lifecycleSource: () => p23iActionAdapters.putIfAbsent(
        'lifecycle/${guard.revision}',
        () => AmbulanceLifecycleRemoteDataSource(reader: ambulance),
      ) as AmbulanceLifecycleRemoteDataSource,
      invoiceLinkSource: () => p23iActionAdapters.putIfAbsent(
        'invoice-link/${guard.revision}',
        () => AmbulanceInvoiceLinkRemoteDataSource(
          ambulanceReader: ambulance,
          billingReader: billing,
        ),
      ) as AmbulanceInvoiceLinkRemoteDataSource,
    );
  }

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
  // P24A reuses the already-injected authenticated admission transport.
  // The reader and one-shot action gate live for the router lifetime.
  final admissionsForNotifications = patientAdmissionDataSource;
  final notificationGuard = sessionNavigationGuard;
  final NotificationRemoteDataSource? notificationSource =
      admissionsForNotifications != null &&
          notificationGuard != null &&
          identical(
            admissionsForNotifications.sessionNavigationGuard,
            notificationGuard,
          )
      ? NotificationRemoteDataSource(
          apiClient: admissionsForNotifications.apiClient,
          loadSession: admissionsForNotifications.loadSession,
          sessionNavigationGuard: notificationGuard,
        )
      : null;
  final NotificationReadActions? notificationActions =
      notificationSource == null
      ? null
      : NotificationReadActions(notificationSource);
  // P24B: no push SDK is installed. P24D supplies the verified provider.
  // Keep registration one-shot state alive across route revisits.
  final DeviceRegistrationCoordinator? registrationCoordinator =
      deviceTokenProvider != null &&
          admissionsForNotifications != null &&
          notificationGuard != null &&
          identical(
            admissionsForNotifications.sessionNavigationGuard,
            notificationGuard,
          )
      ? DeviceRegistrationCoordinator(
          source: DeviceRegistrationRemoteDataSource(
            apiClient: admissionsForNotifications.apiClient,
            loadSession: admissionsForNotifications.loadSession,
            sessionNavigationGuard: notificationGuard,
            connectivity:
                deviceRegistrationConnectivitySource ??
                ConnectivityPlusDeviceConnectivitySource(),
          ),
          provider: deviceTokenProvider,
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
      // This project's default guard protects /patients only. Do not let
      // /pharmacy/stock bypass session redirection on direct/deep links.
      if (state.matchedLocation == '/pharmacy/stock' &&
          sessionNavigationGuard?.canAccessProtectedRoutes != true) {
        return '/login';
      }
      // P23I destinations lie outside the default /patients route guard.
      if ((state.matchedLocation == '/billing' ||
              state.matchedLocation == '/ambulance') &&
          sessionNavigationGuard?.canAccessProtectedRoutes != true) {
        return '/login';
      }
      // P24C: both inbox and ID-only deep links are session protected.
      // The target itself still checks role, selected clinic, and owner.
      if ((state.uri.path == '/notifications' ||
              state.uri.path.startsWith('/notifications/')) &&
          sessionNavigationGuard?.canAccessProtectedRoutes != true) {
        return '/login';
      }
      // P24B route is guarded separately from the default /patients prefix.
      if (state.matchedLocation == '/devices/register' &&
          sessionNavigationGuard?.canAccessProtectedRoutes != true) {
        return '/login';
      }
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
            path: '/billing',
            name: 'p23i-billing-workspace',
            builder: (context, state) =>
                p23iWorkspace(P23IWorkspaceMode.billing),
          ),
          GoRoute(
            path: '/ambulance',
            name: 'p23i-ambulance-workspace',
            builder: (context, state) =>
                p23iWorkspace(P23IWorkspaceMode.ambulance),
          ),
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
            path: '/notifications',
            name: 'p24a-notification-inbox',
            builder: (context, state) {
              final source = notificationSource;
              final actions = notificationActions;
              if (source == null ||
                  actions == null ||
                  !source.sessionNavigationGuard.canAccessProtectedRoutes) {
                return const Scaffold(
                  body: Center(child: Text('Notification inbox unavailable.')),
                );
              }
              return NotificationInboxScreen(
                source: source,
                actions: actions,
                onOpenLink: (id) async {
                  await context.push<void>(NotificationDeepLink.pathForId(id));
                },
              );
            },
          ),
          GoRoute(
            path: '/devices/register',
            name: 'p24b-device-registration',
            builder: (context, state) {
              final coordinator = registrationCoordinator;
              if (coordinator == null ||
                  !coordinator
                      .source
                      .sessionNavigationGuard
                      .canAccessProtectedRoutes) {
                return const Scaffold(
                  body: Center(
                    child: Text(
                      'Device registration unavailable: provider not configured.',
                    ),
                  ),
                );
              }
              return DeviceRegistrationScreen(coordinator: coordinator);
            },
          ),
          GoRoute(
            path: '/notifications/:notificationId',
            name: 'p24c-notification-deep-link',
            builder: (context, state) {
              // No ID, clinic, token, content or reference in a query.
              final link = NotificationDeepLink.parse(state.uri.toString());
              if (link == null ||
                  state.pathParameters['notificationId'] !=
                      link.notificationId.toString()) {
                return const Scaffold(
                  body: Center(
                    child: Text('Invalid or unsupported notification link.'),
                  ),
                );
              }
              final source = notificationSource;
              final actions = notificationActions;
              if (source == null ||
                  actions == null ||
                  !source.sessionNavigationGuard.canAccessProtectedRoutes) {
                return const Scaffold(
                  body: Center(child: Text('Notification link unavailable.')),
                );
              }
              // Detail always re-fetches and verifies role, user and clinic.
              // Its reference fields never authorize a clinical route.
              return NotificationDetailScreen(
                id: link.notificationId,
                source: source,
                actions: actions,
              );
            },
          ),
          GoRoute(
            path: '/chat',
            name: 'clinic-chat-inbox',
            builder: (context, state) {
              final admissions = patientAdmissionDataSource;
              final guard = sessionNavigationGuard;
              if (admissions == null ||
                  guard == null ||
                  !identical(admissions.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes) {
                return const Scaffold(
                  body: Center(child: Text('Clinic messages unavailable.')),
                );
              }
              return ClinicChatInboxScreen(
                dataSource: ClinicChatReadRemoteDataSource(
                  apiClient: admissions.apiClient,
                  loadSession: admissions.loadSession,
                  sessionNavigationGuard: guard,
                ),
              );
            },
          ),
          GoRoute(
            path: '/wards',
            name: 'p23e-b-wards',
            builder: (context, state) {
              final admissions = patientAdmissionDataSource;
              final guard = sessionNavigationGuard;
              if (admissions == null ||
                  guard == null ||
                  !identical(admissions.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes) {
                return const Scaffold(
                  body: Center(child: Text('Ward dashboard unavailable.')),
                );
              }
              return WardDashboardScreen(
                dataSource: WardDashboardRemoteDataSource(
                  apiClient: admissions.apiClient,
                  loadSession: admissions.loadSession,
                  sessionNavigationGuard: guard,
                ),
              );
            },
          ),
          GoRoute(
            path: '/clinical-orders',
            name: 'clinical-orders-workspace',
            builder: (context, state) {
              final source = clinicalOrdersDataSource;
              final guard = sessionNavigationGuard;
              if (source == null ||
                  guard == null ||
                  !identical(source.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes) {
                return const Scaffold(
                  body: Center(child: Text('Clinical orders unavailable.')),
                );
              }
              return ClinicalOrdersWorkspaceScreen(
                dataSource: source,
                onPatientSelected: (id) =>
                    context.push<void>('/clinical-orders/patient/$id'),
              );
            },
          ),
          GoRoute(
            path: '/clinical-orders/patient/:patientId/imaging',
            name: 'p23d-imaging-orders',
            builder: (context, state) {
              final source = clinicalOrdersDataSource;
              final admissions = patientAdmissionDataSource;
              final guard = sessionNavigationGuard;
              final patientId = int.tryParse(
                state.pathParameters['patientId'] ?? '',
              );
              if (source == null ||
                  admissions == null ||
                  guard == null ||
                  !identical(source.sessionNavigationGuard, guard) ||
                  !identical(admissions.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes ||
                  patientId == null ||
                  patientId <= 0 ||
                  patientId > 2147483647) {
                return const Scaffold(
                  body: Center(child: Text('Imaging order route unavailable.')),
                );
              }
              return ImagingProcedureOrdersScreen(
                key: ValueKey('p23d-b-imaging-$patientId'),
                unifiedSource: source,
                admissionDataSource: admissions,
                patientId: patientId,
                kind: 'imaging',
              );
            },
          ),
          GoRoute(
            path: '/clinical-orders/patient/:patientId/procedures',
            name: 'p23d-procedure-orders',
            builder: (context, state) {
              final source = clinicalOrdersDataSource;
              final admissions = patientAdmissionDataSource;
              final guard = sessionNavigationGuard;
              final patientId = int.tryParse(
                state.pathParameters['patientId'] ?? '',
              );
              if (source == null ||
                  admissions == null ||
                  guard == null ||
                  !identical(source.sessionNavigationGuard, guard) ||
                  !identical(admissions.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes ||
                  patientId == null ||
                  patientId <= 0 ||
                  patientId > 2147483647) {
                return const Scaffold(
                  body: Center(
                    child: Text('Procedure order route unavailable.'),
                  ),
                );
              }
              return ImagingProcedureOrdersScreen(
                key: ValueKey('p23d-b-procedures-$patientId'),
                unifiedSource: source,
                admissionDataSource: admissions,
                patientId: patientId,
                kind: 'procedures',
              );
            },
          ),
          GoRoute(
            path: '/clinical-orders/patient/:patientId/diets',
            name: 'p23d-diet-orders',
            builder: (context, state) {
              final source = clinicalOrdersDataSource;
              final admissions = patientAdmissionDataSource;
              final guard = sessionNavigationGuard;
              final patientId = int.tryParse(
                state.pathParameters['patientId'] ?? '',
              );
              if (source == null ||
                  admissions == null ||
                  guard == null ||
                  !identical(source.sessionNavigationGuard, guard) ||
                  !identical(admissions.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes ||
                  patientId == null ||
                  patientId <= 0 ||
                  patientId > 2147483647) {
                return const Scaffold(
                  body: Center(child: Text('Diet order route unavailable.')),
                );
              }
              return DietNursingOrdersScreen(
                key: ValueKey('p23d-c-diets-$patientId'),
                unifiedSource: source,
                admissionDataSource: admissions,
                patientId: patientId,
                kind: 'diets',
              );
            },
          ),
          GoRoute(
            path: '/clinical-orders/patient/:patientId/nursing-orders',
            name: 'p23d-nursing-orders',
            builder: (context, state) {
              final source = clinicalOrdersDataSource;
              final admissions = patientAdmissionDataSource;
              final guard = sessionNavigationGuard;
              final patientId = int.tryParse(
                state.pathParameters['patientId'] ?? '',
              );
              if (source == null ||
                  admissions == null ||
                  guard == null ||
                  !identical(source.sessionNavigationGuard, guard) ||
                  !identical(admissions.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes ||
                  patientId == null ||
                  patientId <= 0 ||
                  patientId > 2147483647) {
                return const Scaffold(
                  body: Center(child: Text('Nursing order route unavailable.')),
                );
              }
              return DietNursingOrdersScreen(
                key: ValueKey('p23d-c-nursing-$patientId'),
                unifiedSource: source,
                admissionDataSource: admissions,
                patientId: patientId,
                kind: 'nursing-orders',
              );
            },
          ),
          GoRoute(
            path: '/clinical-orders/blood-products',
            name: 'p23d-d-blood-lookup',
            builder: (context, state) {
              final source = clinicalOrdersDataSource;
              final guard = sessionNavigationGuard;
              if (source == null ||
                  guard == null ||
                  !identical(source.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes) {
                return const Scaffold(
                  body: Center(
                    child: Text('Blood-product orders unavailable.'),
                  ),
                );
              }
              return BloodProductOrdersScreen(
                key: const ValueKey('p23d-d-blood-lookup'),
                unifiedSource: source,
                admissionDataSource: patientAdmissionDataSource,
              );
            },
          ),
          GoRoute(
            path: '/clinical-orders/patient/:patientId/blood-products',
            name: 'p23d-d-blood-patient',
            builder: (context, state) {
              final source = clinicalOrdersDataSource;
              final admissions = patientAdmissionDataSource;
              final guard = sessionNavigationGuard;
              final patientId = int.tryParse(
                state.pathParameters['patientId'] ?? '',
              );
              if (source == null ||
                  admissions == null ||
                  guard == null ||
                  !identical(source.sessionNavigationGuard, guard) ||
                  !identical(admissions.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes ||
                  patientId == null ||
                  patientId <= 0 ||
                  patientId > 2147483647) {
                return const Scaffold(
                  body: Center(
                    child: Text('Blood-product orders unavailable.'),
                  ),
                );
              }
              return BloodProductOrdersScreen(
                key: ValueKey('p23d-d-blood-patient-$patientId'),
                unifiedSource: source,
                admissionDataSource: admissions,
                patientId: patientId,
              );
            },
          ),
          GoRoute(
            path: '/clinical-orders/patient/:patientId',
            name: 'clinical-orders-patient',
            builder: (context, state) {
              final source = clinicalOrdersDataSource;
              final guard = sessionNavigationGuard;
              final patientId = int.tryParse(
                state.pathParameters['patientId'] ?? '',
              );
              if (source == null ||
                  guard == null ||
                  !identical(source.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes ||
                  patientId == null ||
                  patientId <= 0 ||
                  patientId > 2147483647) {
                return const Scaffold(
                  body: Center(child: Text('Clinical orders unavailable.')),
                );
              }
              return ClinicalOrdersWorkspaceScreen(
                key: ValueKey('p23d-orders-patient-$patientId'),
                dataSource: source,
                patientId: patientId,
                onPatientSelected: (id) =>
                    context.push<void>('/clinical-orders/patient/$id'),
                onConsultationSelected: (patient, consultation) =>
                    context.push<void>(
                      '/consultations/patient/$patient/$consultation',
                    ),
                onImagingRequested: (id) =>
                    context.push<void>('/clinical-orders/patient/$id/imaging'),
                onProcedureRequested: (id) => context.push<void>(
                  '/clinical-orders/patient/$id/procedures',
                ),
                onDietRequested: (id) =>
                    context.push<void>('/clinical-orders/patient/$id/diets'),
                onNursingRequested: (id) => context.push<void>(
                  '/clinical-orders/patient/$id/nursing-orders',
                ),
                onBloodRequested: (id) => context.push<void>(
                  '/clinical-orders/patient/$id/blood-products',
                ),
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
          // Server-scoped eMAR entry; a URL does not grant medication access.
          GoRoute(
            path: '/emar/patient/:patientId',
            name: 'p23f-s03c-emar-patient',
            builder: (context, state) {
              final admissions = patientAdmissionDataSource;
              final guard = sessionNavigationGuard;
              final patientId = int.tryParse(
                state.pathParameters['patientId'] ?? '',
              );
              if (admissions == null ||
                  guard == null ||
                  !identical(admissions.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes ||
                  patientId == null ||
                  patientId <= 0 ||
                  patientId > 2147483647) {
                return const Scaffold(
                  body: Center(child: Text('eMAR navigation unavailable.')),
                );
              }
              return EmarPatientRouteScreen(
                key: ValueKey('p23f-s03c-emar-$patientId'),
                admissions: admissions,
                patientId: patientId,
              );
            },
          ),
          // P23G: authenticated patient scope plus server-confirmed current
          // admission. A URL alone never authorizes a medication read/write.
          GoRoute(
            path: '/patients/:patientId/medication-reconciliation',
            name: 'p23g-medrec-patient',
            builder: (context, state) {
              final admissions = patientAdmissionDataSource;
              final guard = sessionNavigationGuard;
              final patientId = int.tryParse(
                state.pathParameters['patientId'] ?? '',
              );
              if (admissions == null ||
                  guard == null ||
                  !identical(admissions.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes ||
                  patientId == null ||
                  patientId <= 0 ||
                  patientId > 2147483647) {
                return const Scaffold(
                  body: Center(
                    child: Text(
                      'Medication reconciliation navigation unavailable.',
                    ),
                  ),
                );
              }
              return MedicationReconciliationPatientRouteScreen(
                key: ValueKey('p23g-route-patient-$patientId'),
                admissions: admissions,
                patientId: patientId,
              );
            },
          ),
          // P23H.S06A: no clinical writes or privileges are granted by paths.
          // Source readers reauthenticate and enforce backend role/clinic scope.
          GoRoute(
            path: '/patients/:patientId/prescriptions/:prescriptionId',
            name: 'p23h-prescription-detail-read',
            builder: (context, state) {
              final guard = sessionNavigationGuard;
              final reader = patientPrescriptionDataSource;
              final patientId = int.tryParse(
                state.pathParameters['patientId'] ?? '',
              );
              final prescriptionId = int.tryParse(
                state.pathParameters['prescriptionId'] ?? '',
              );
              if (guard == null ||
                  reader == null ||
                  !identical(reader.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes ||
                  patientId == null ||
                  patientId <= 0 ||
                  patientId > 2147483647 ||
                  prescriptionId == null ||
                  prescriptionId <= 0 ||
                  prescriptionId > 2147483647) {
                return const Scaffold(
                  body: Center(
                    child: Text('Prescription detail navigation unavailable.'),
                  ),
                );
              }
              return PatientPrescriptionDetailScreen(
                key: ValueKey(
                  'p23h-route-prescription-$patientId-$prescriptionId',
                ),
                dataSource: reader,
                patientId: patientId,
                prescriptionId: prescriptionId,
              );
            },
          ),
          GoRoute(
            path: '/patients/:patientId/lab-orders',
            name: 'p23h-patient-lab-orders-read',
            builder: (context, state) {
              final guard = sessionNavigationGuard;
              final patientId = int.tryParse(
                state.pathParameters['patientId'] ?? '',
              );
              if (guard == null ||
                  labReadSource == null ||
                  !guard.canAccessProtectedRoutes ||
                  patientId == null ||
                  patientId <= 0 ||
                  patientId > 2147483647) {
                return const Scaffold(
                  body: Center(
                    child: Text('Laboratory navigation unavailable.'),
                  ),
                );
              }
              return LabPatientOrdersScreen(
                key: ValueKey('p23h-route-lab-orders-$patientId'),
                patientId: patientId,
                dataSource: labReadSource,
              );
            },
          ),
          GoRoute(
            path: '/pharmacy/stock',
            name: 'p23h-pharmacy-stock-read',
            builder: (context, state) {
              final guard = sessionNavigationGuard;
              if (guard == null ||
                  pharmacyInventorySource == null ||
                  !guard.canAccessProtectedRoutes) {
                return const Scaffold(
                  body: Center(
                    child: Text('Pharmacy inventory navigation unavailable.'),
                  ),
                );
              }
              return PharmacyStockScreen(
                key: const ValueKey('p23h-route-pharmacy-stock'),
                dataSource: pharmacyInventorySource,
              );
            },
          ),
          // S06B: Clinical action entry exists only inside a verified patient
          // chart. This hub performs GETs only and never grants RBAC by URL.
          GoRoute(
            path: '/patients/:patientId/clinical-actions',
            name: 'p23h-patient-clinical-action-hub',
            builder: (context, state) {
              final guard = sessionNavigationGuard;
              final reader = patientPrescriptionDataSource;
              final patientId = int.tryParse(
                state.pathParameters['patientId'] ?? '',
              );
              if (guard == null ||
                  reader == null ||
                  labReadSource == null ||
                  !identical(reader.sessionNavigationGuard, guard) ||
                  !guard.canAccessProtectedRoutes ||
                  patientId == null ||
                  patientId <= 0 ||
                  patientId > 2147483647) {
                return const Scaffold(
                  body: Center(
                    child: Text('Clinical action navigation unavailable.'),
                  ),
                );
              }
              return P23HClinicalActionHub(
                key: ValueKey('p23h-clinical-hub-$patientId'),
                patientId: patientId,
                prescriptionReader: reader,
                labReader: labReadSource,
                onActionRequested: (intent) {
                  if (!guard.canAccessProtectedRoutes ||
                      !intent.isValid ||
                      intent.patientId != patientId) {
                    return;
                  }
                  context.push<void>(
                    '/patients/$patientId/clinical-action',
                    extra: _P23HMutationNavigationGrant(intent, guard.revision),
                  );
                },
              );
            },
          ),
          // No URL can create a clinical write grant. The user must select an
          // existing server-scoped action from the in-app hub. The destination
          // screen then rechecks backend scope and obtains explicit consent.
          GoRoute(
            path: '/patients/:patientId/clinical-action',
            name: 'p23h-granted-clinical-action',
            builder: (context, state) {
              const unavailable = Scaffold(
                body: Center(child: Text('Clinical action grant unavailable.')),
              );
              final guard = sessionNavigationGuard;
              final grant = state.extra;
              final patientId = int.tryParse(
                state.pathParameters['patientId'] ?? '',
              );
              if (guard == null ||
                  !guard.canAccessProtectedRoutes ||
                  grant is! _P23HMutationNavigationGrant ||
                  grant.revision != guard.revision ||
                  !grant.intent.isValid ||
                  patientId == null ||
                  patientId <= 0 ||
                  patientId > 2147483647 ||
                  grant.intent.patientId != patientId ||
                  patientPrescriptionDataSource == null ||
                  labReadSource == null ||
                  pharmacyInventorySource == null) {
                return unavailable;
              }
              final intent = grant.intent;
              final key =
                  '${intent.kind.name}/$patientId/'
                  '${intent.prescriptionId ?? 0}/${intent.orderId ?? 0}/'
                  '${intent.orderItemId ?? 0}';
              if (intent.kind == P23HClinicalActionKind.prescribe ||
                  intent.kind == P23HClinicalActionKind.cancelPrescription ||
                  intent.kind == P23HClinicalActionKind.completePrescription) {
                final action = switch (intent.kind) {
                  P23HClinicalActionKind.prescribe =>
                    PrescriptionMutationAction.create,
                  P23HClinicalActionKind.cancelPrescription =>
                    PrescriptionMutationAction.cancel,
                  _ => PrescriptionMutationAction.complete,
                };
                final source = p23hActionAdapters.putIfAbsent(
                  key,
                  () => PatientPrescriptionMutationRemoteDataSource(
                    reader: patientPrescriptionDataSource,
                  ),
                ) as PatientPrescriptionMutationRemoteDataSource;
                return PatientPrescriptionMutationScreen(
                  key: ValueKey('p23h-clinical-$key'),
                  source: source,
                  patientId: patientId,
                  action: action,
                  prescriptionId: intent.prescriptionId,
                );
              }
              if (intent.kind == P23HClinicalActionKind.dispense) {
                final source = p23hActionAdapters.putIfAbsent(
                  key,
                  () => PharmacyDispenseRemoteDataSource(
                    inventory: pharmacyInventorySource!,
                  ),
                ) as PharmacyDispenseRemoteDataSource;
                return PharmacyDispenseScreen(
                  key: ValueKey('p23h-clinical-$key'),
                  source: source,
                  patientId: patientId,
                  prescriptionId: intent.prescriptionId!,
                );
              }
              if (intent.kind == P23HClinicalActionKind.labCreateOrCancel) {
                final source = p23hActionAdapters.putIfAbsent(
                  key,
                  () =>
                      LabOrderMutationRemoteDataSource(reader: labReadSource!),
                ) as LabOrderMutationRemoteDataSource;
                return LabOrderMutationScreen(
                  key: ValueKey('p23h-clinical-$key'),
                  patientId: patientId,
                  dataSource: source,
                );
              }
              if (intent.kind == P23HClinicalActionKind.collectSample ||
                  intent.kind == P23HClinicalActionKind.linkEquipment ||
                  intent.kind == P23HClinicalActionKind.processSample) {
                final action = switch (intent.kind) {
                  P23HClinicalActionKind.collectSample =>
                    LabTechnicianAction.collect,
                  P23HClinicalActionKind.linkEquipment =>
                    LabTechnicianAction.linkEquipment,
                  _ => LabTechnicianAction.process,
                };
                final source = p23hActionAdapters.putIfAbsent(
                  key,
                  () => LabTechnicianLifecycleRemoteDataSource(
                    reader: labReadSource!,
                  ),
                ) as LabTechnicianLifecycleRemoteDataSource;
                return LabTechnicianLifecycleScreen(
                  key: ValueKey('p23h-clinical-$key'),
                  dataSource: source,
                  patientId: patientId,
                  orderId: intent.orderId!,
                  action: action,
                );
              }
              final action = switch (intent.kind) {
                P23HClinicalActionKind.enterResult =>
                  LabResultAction.enterResult,
                P23HClinicalActionKind.verifyResults => LabResultAction.verify,
                P23HClinicalActionKind.completeLabOrder =>
                  LabResultAction.complete,
                _ => null,
              };
              if (action == null) {
                return unavailable;
              }
              final source = p23hActionAdapters.putIfAbsent(
                key,
                () => LabResultFinalizationRemoteDataSource(
                  reader: labReadSource!,
                ),
              ) as LabResultFinalizationRemoteDataSource;
              return LabResultFinalizationScreen(
                key: ValueKey('p23h-clinical-$key'),
                dataSource: source,
                patientId: patientId,
                orderId: intent.orderId!,
                orderItemId: intent.orderItemId,
                action: action,
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
                onEmarRequested:
                    patientAdmissionDataSource == null ||
                        sessionNavigationGuard == null
                    ? null
                    : () => context.push<void>('/emar/patient/$patientId'),
                onMedicationReconciliationRequested:
                    patientAdmissionDataSource == null ||
                        sessionNavigationGuard == null
                    ? null
                    : () => context.push<void>(
                        '/patients/$patientId/medication-reconciliation',
                      ),
                onPrescriptionDetailRequested:
                    p23hSourceReady && patientPrescriptionDataSource != null
                    ? (id) => context.push<void>(
                        '/patients/$patientId/prescriptions/$id',
                      )
                    : null,
                onLabOrdersRequested: labReadSource == null
                    ? null
                    : () =>
                          context.push<void>('/patients/$patientId/lab-orders'),
                onPharmacyStockRequested: pharmacyInventorySource == null
                    ? null
                    : () => context.push<void>('/pharmacy/stock'),
                onClinicalActionsRequested:
                    p23hSourceReady && labReadSource != null
                    ? () => context.push<void>(
                        '/patients/$patientId/clinical-actions',
                      )
                    : null,
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

// Private, revision-bound navigation token. Deep links do not carry this
// object and cannot open clinical mutation screens directly.
class _P23HMutationNavigationGrant {
  const _P23HMutationNavigationGrant(this.intent, this.revision);
  final P23HClinicalActionIntent intent;
  final int revision;
}
