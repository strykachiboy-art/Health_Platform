import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:go_router/go_router.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_banner.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_cubit.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_invalidation_coordinator.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';
import 'package:clinic_system_pro/core/sync/operation_queue_store.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_worker.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_scheduler.dart';
import 'package:clinic_system_pro/core/sync/appointment_create_queue_producer.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_create_queue_state_read_model.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_create_sync_status_panel.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/clinic_context_switch_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/authenticated_sign_out_coordinator.dart';
import 'package:clinic_system_pro/features/profile/data/profile_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/coordinator_authenticated_session_access.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_store.dart';
import 'package:clinic_system_pro/core/networking/local_debug_tls_client.dart';
import 'package:clinic_system_pro/features/auth/data/google_sign_in_provider.dart';
import 'package:clinic_system_pro/features/auth/presentation/google_login_screen.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/status/data/scoped_sync_status_source.dart';
import 'package:clinic_system_pro/features/status/presentation/authenticated_status_strip.dart';
import 'package:clinic_system_pro/features/status/presentation/authenticated_clinical_emergency_indicators.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_session_scope.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_context.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_remote_data_source.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_allergy_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_vitals_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_consultation_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_clinical_alert_remote_data_source.dart';

void main() {
  final backendConfig = BackendConfig.fromEnvironment();
  final apiClient = ApiClient(
    config: backendConfig,
    client: createLocalDebugTlsClient(backendConfig),
  );
  final connectivitySource = ConnectivityPlusDeviceConnectivitySource();

  final healthDataSource = BackendHealthRemoteDataSource(apiClient: apiClient);
  final authRemoteDataSource = AuthRemoteDataSource(apiClient: apiClient);
  final secureStorage = FlutterSecureKeyValueStorage();
  final sessionStore = AuthSessionStore(storage: secureStorage);
  final sessionCoordinator = AuthSessionCoordinator(
    sessionStore: sessionStore,
    remoteDataSource: authRemoteDataSource,
  );

  final authenticatedSessionAccess = CoordinatorAuthenticatedSessionAccess(
    sessionCoordinator: sessionCoordinator,
  );

  final patientDataSource = PatientRemoteDataSource(
    apiClient: apiClient,
    loadAccessToken: authenticatedSessionAccess.requireAccessToken,
  );

  final emergencyAccessDataSource = EmergencyAccessRemoteDataSource(
    apiClient: apiClient,
    loadAccessToken: authenticatedSessionAccess.requireAccessToken,
  );

  final sessionNavigationGuard = SessionNavigationGuard(
    protectedRoutePrefixes: const <String>[
      '/patients',
      '/appointments',
      '/consultations',
      '/clinical-orders',
      '/home',
      '/account',
      '/emergency-access',
      '/clinical-safety',
    ],
  );
  final appointmentDataSource = AppointmentRemoteDataSource(
    apiClient: apiClient,
    loadSession: sessionCoordinator.restoreSession,
    sessionNavigationGuard: sessionNavigationGuard,
  );
  final consultationDataSource = ConsultationRemoteDataSource(
    apiClient: apiClient,
    loadSession: sessionCoordinator.restoreSession,
    sessionNavigationGuard: sessionNavigationGuard,
  );
  final clinicalOrdersDataSource = ClinicalOrdersRemoteDataSource(
    apiClient: apiClient,
    loadSession: sessionCoordinator.restoreSession,
    sessionNavigationGuard: sessionNavigationGuard,
  );
  final patientAllergyDataSource = PatientAllergyRemoteDataSource(
    apiClient: apiClient,
    loadSession: sessionCoordinator.restoreSession,
    sessionNavigationGuard: sessionNavigationGuard,
  );

  final patientVitalsDataSource = PatientVitalsRemoteDataSource(
    apiClient: apiClient,
    loadSession: sessionCoordinator.restoreSession,
    sessionNavigationGuard: sessionNavigationGuard,
  );
  final patientAdmissionDataSource = PatientAdmissionRemoteDataSource(
    apiClient: apiClient,
    loadSession: sessionCoordinator.restoreSession,
    sessionNavigationGuard: sessionNavigationGuard,
  );
  final patientConsultationDataSource = PatientConsultationRemoteDataSource(
    apiClient: apiClient,
    loadSession: sessionCoordinator.restoreSession,
    sessionNavigationGuard: sessionNavigationGuard,
  );
  final patientPrescriptionDataSource = PatientPrescriptionRemoteDataSource(
    apiClient: apiClient,
    loadSession: sessionCoordinator.restoreSession,
    sessionNavigationGuard: sessionNavigationGuard,
  );
  final patientClinicalAlertDataSource = PatientClinicalAlertRemoteDataSource(
    apiClient: apiClient,
    loadSession: sessionCoordinator.restoreSession,
    sessionNavigationGuard: sessionNavigationGuard,
  );
  final profileDataSource = ProfileRemoteDataSource(
    apiClient: apiClient,
    loadSession: sessionCoordinator.restoreSession,
    sessionNavigationGuard: sessionNavigationGuard,
  );
  final dashboardDataSource = DashboardRemoteDataSource(
    apiClient: apiClient,
    loadSession: sessionCoordinator.restoreSession,
    sessionNavigationGuard: sessionNavigationGuard,
  );
  final clinicalSafetySessionScope = ClinicalSafetySessionScope(
    restoreSession: sessionCoordinator.restoreSession,
    navigationGuard: sessionNavigationGuard,
  );
  final clinicalSafetyDataSource = ClinicalSafetyRemoteDataSource(
    apiClient: apiClient,
    loadAccessToken: authenticatedSessionAccess.requireAccessToken,
    loadSessionStamp: clinicalSafetySessionScope.snapshot,
    deviceConnectivitySource: connectivitySource,
  );
  final emergencyAccessContext = EmergencyAccessContext(
    sessionNavigationGuard: sessionNavigationGuard,
  );
  final googleSignInProvider = GoogleSignInProvider(
    configuration: GoogleSignInConfiguration.fromEnvironment(),
  );

  final localDatabase = AppDatabase();
  final syncStatusSource = ScopedSyncStatusSource(database: localDatabase);
  final queueStore = OperationQueueStore(
    localDatabase,
    payloadCrypto: OperationPayloadCrypto(secureStorage: secureStorage),
  );

  final replayWorker = OperationReplayWorker.withApiClient(
    queueStore: queueStore,
    apiClient: apiClient,
    loadSession: () async {
      final state = await sessionCoordinator.restoreSession();
      if (state.disposition != AuthSessionDisposition.ready) {
        return null;
      }
      return state.session;
    },
  );

  final replayScheduler = OperationReplayScheduler(
    connectivitySource: connectivitySource,
    runReplay: replayWorker.runOnce,
    runMaintenance: () async {
      await queueStore.purgeTerminalOperationsPastRetention(
        now: DateTime.now().toUtc(),
      );
    },
  );
  final sessionInvalidationCoordinator = SessionInvalidationCoordinator(
    sessionCoordinator: sessionCoordinator,
    sessionNavigationGuard: sessionNavigationGuard,
    replayScheduler: replayScheduler,
  );
  apiClient.onAuthenticationRejected = () {
    unawaited(
      sessionInvalidationCoordinator.handleDefinitiveAuthenticationRejection(),
    );
  };

  final appointmentCreateQueueProducer = AppointmentCreateQueueProducer(
    queueStore: queueStore,
    replayScheduler: replayScheduler,
    loadContext: () async {
      try {
        final state = await sessionCoordinator.restoreSession();
        if (state.disposition != AuthSessionDisposition.ready) return null;

        final session = state.session;
        if (session == null || session.userId <= 0) return null;

        final clinicContextId = session.clinicContextId;
        if (clinicContextId == null || clinicContextId <= 0) return null;

        return OperationPayloadContext(
          userId: session.userId.toString(),
          clinicContextId: clinicContextId.toString(),
        );
      } catch (_) {
        return null;
      }
    },
  );

  // Reuse the existing encrypted create queue, never a parallel queue.
  // Every read revalidates the active authenticated clinic and guard revision.
  final appointmentCreateSyncReadModel = AppointmentCreateQueueStateReadModel(
    queueStore: queueStore,
    loadSession: () async {
      final revision = sessionNavigationGuard.revision;
      if (!sessionNavigationGuard.canAccessProtectedRoutes) return null;
      try {
        final state = await sessionCoordinator.restoreSession();
        if (state.disposition != AuthSessionDisposition.ready) return null;
        final session = state.session;
        if (session == null ||
            session.userId <= 0 ||
            session.clinicContextId == null ||
            session.clinicContextId! <= 0 ||
            session.accessToken.trim().isEmpty) {
          return null;
        }
        const allowedRoles = <String>{
          'admin',
          'doctor',
          'nurse',
          'receptionist',
        };
        if (!allowedRoles.contains(session.role.trim().toLowerCase()) ||
            !sessionNavigationGuard.canAccessProtectedRoutes ||
            sessionNavigationGuard.revision != revision) {
          return null;
        }
        return AppointmentCreateQueueReadSession(
          context: OperationPayloadContext(
            userId: session.userId.toString(),
            clinicContextId: session.clinicContextId!.toString(),
          ),
          revision: revision,
        );
      } catch (_) {
        // Auth/storage errors cannot expose cross-session queue metadata.
        return null;
      }
    },
  );
  final clinicContextSwitcher = ClinicContextSwitchCoordinator(
    sessionCoordinator: sessionCoordinator,
    sessionNavigationGuard: sessionNavigationGuard,
    replayScheduler: replayScheduler,
  );

  final signOutCoordinator = AuthenticatedSignOutCoordinator(
    sessionCoordinator: sessionCoordinator,
    sessionNavigationGuard: sessionNavigationGuard,
    replayScheduler: replayScheduler,
  );

  final loginScreen = GoogleLoginScreen(
    acquireGoogleIdToken: googleSignInProvider.signInForIdToken,
    authRemoteDataSource: authRemoteDataSource,
    sessionCoordinator: sessionCoordinator,
    sessionNavigationGuard: sessionNavigationGuard,
    onSessionEnding: replayScheduler.pauseAndWait,
    onSessionChanged: replayScheduler.notifySessionChanged,
  );

  runApp(
    AppointmentCreateQueueScope(
      producer: appointmentCreateQueueProducer,
      reconcileOperation: replayWorker.reconcileAppointmentCreate,
      child: ClinicSystemProApp(
        healthDataSource: healthDataSource,
        appointmentDataSource: appointmentDataSource,
        consultationDataSource: consultationDataSource,
        clinicalOrdersDataSource: clinicalOrdersDataSource,
        patientDataSource: patientDataSource,
        patientAllergyDataSource: patientAllergyDataSource,
        patientVitalsDataSource: patientVitalsDataSource,
        patientAdmissionDataSource: patientAdmissionDataSource,
        patientConsultationDataSource: patientConsultationDataSource,
        patientPrescriptionDataSource: patientPrescriptionDataSource,
        patientClinicalAlertDataSource: patientClinicalAlertDataSource,
        emergencyAccessDataSource: emergencyAccessDataSource,
        clinicalSafetyDataSource: clinicalSafetyDataSource,
        emergencyAccessContext: emergencyAccessContext,
        loginScreen: loginScreen,
        loadHomeSession: sessionCoordinator.restoreSession,
        dashboardDataSource: dashboardDataSource,
        profileDataSource: profileDataSource,
        authRemoteDataSource: authRemoteDataSource,
        clinicContextSwitcher: clinicContextSwitcher,
        signOutCoordinator: signOutCoordinator,
        statusStripBuilder: () => Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            AuthenticatedStatusStrip(
              loadSession: sessionCoordinator.restoreSession,
              loadSyncCounts: syncStatusSource.load,
              sessionNavigationGuard: sessionNavigationGuard,
            ),
            AuthenticatedClinicalEmergencyIndicators(
              loadSession: sessionCoordinator.restoreSession,
              loadOpenAlertCount: clinicalSafetyDataSource.fetchOpenAlertCount,
              loadEmergencyRequest: emergencyAccessDataSource.fetchRequest,
              emergencyAccessContext: emergencyAccessContext,
              sessionNavigationGuard: sessionNavigationGuard,
            ),
          ],
        ),
        sessionNavigationGuard: sessionNavigationGuard,
        deviceConnectivitySource: connectivitySource,
        operationReplayScheduler: replayScheduler,
        appointmentCreateSyncPageLoader:
            appointmentCreateSyncReadModel.loadPage,
        localDatabase: localDatabase,
      ),
    ),
  );
}

class ClinicSystemProApp extends StatefulWidget {
  const ClinicSystemProApp({
    super.key,
    required this.healthDataSource,
    required this.loginScreen,
    this.loadHomeSession,
    this.dashboardDataSource,
    this.profileDataSource,
    this.authRemoteDataSource,
    this.clinicContextSwitcher,
    this.signOutCoordinator,
    this.statusStripBuilder,
    this.appointmentDataSource,
    this.consultationDataSource,
    this.clinicalOrdersDataSource,
    this.patientDataSource,
    this.patientAllergyDataSource,
    this.patientVitalsDataSource,
    this.patientAdmissionDataSource,
    this.patientConsultationDataSource,
    this.patientPrescriptionDataSource,
    this.patientClinicalAlertDataSource,
    this.emergencyAccessDataSource,
    this.clinicalSafetyDataSource,
    this.emergencyAccessContext,
    this.sessionNavigationGuard,
    this.deviceConnectivitySource,
    this.operationReplayScheduler,
    this.appointmentCreateSyncPageLoader,
    this.localDatabase,
  });

  final BackendHealthRemoteDataSource healthDataSource;
  final Widget loginScreen;
  final Future<AuthSessionState> Function()? loadHomeSession;
  final DashboardRemoteDataSource? dashboardDataSource;
  final ProfileRemoteDataSource? profileDataSource;
  final AuthRemoteDataSource? authRemoteDataSource;
  final ClinicContextSwitchCoordinator? clinicContextSwitcher;
  final AuthenticatedSignOutCoordinator? signOutCoordinator;
  final Widget Function()? statusStripBuilder;
  final AppointmentRemoteDataSource? appointmentDataSource;
  final ConsultationRemoteDataSource? consultationDataSource;
  final ClinicalOrdersRemoteDataSource? clinicalOrdersDataSource;
  final PatientRemoteDataSource? patientDataSource;
  final PatientAllergyRemoteDataSource? patientAllergyDataSource;
  final PatientVitalsRemoteDataSource? patientVitalsDataSource;
  final PatientAdmissionRemoteDataSource? patientAdmissionDataSource;
  final PatientConsultationRemoteDataSource? patientConsultationDataSource;
  final PatientPrescriptionRemoteDataSource? patientPrescriptionDataSource;
  final PatientClinicalAlertRemoteDataSource? patientClinicalAlertDataSource;
  final EmergencyAccessRemoteDataSource? emergencyAccessDataSource;
  final ClinicalSafetyRemoteDataSource? clinicalSafetyDataSource;
  final EmergencyAccessContext? emergencyAccessContext;
  final SessionNavigationGuard? sessionNavigationGuard;
  final DeviceConnectivitySource? deviceConnectivitySource;
  final OperationReplayScheduler? operationReplayScheduler;
  final AppointmentCreateSyncPageLoader? appointmentCreateSyncPageLoader;
  final AppDatabase? localDatabase;

  @override
  State<ClinicSystemProApp> createState() => _ClinicSystemProAppState();
}

class _ClinicSystemProAppState extends State<ClinicSystemProApp> {
  late final GoRouter _router;

  @override
  void initState() {
    super.initState();
    _router = createAppRouter(
      healthDataSource: widget.healthDataSource,
      loginScreen: widget.loginScreen,
      loadHomeSession: widget.loadHomeSession,
      dashboardDataSource: widget.dashboardDataSource,
      profileDataSource: widget.profileDataSource,
      authRemoteDataSource: widget.authRemoteDataSource,
      clinicContextSwitcher: widget.clinicContextSwitcher,
      signOutCoordinator: widget.signOutCoordinator,
      statusStripBuilder: widget.statusStripBuilder,
      appointmentDataSource: widget.appointmentDataSource,
      consultationDataSource: widget.consultationDataSource,
      clinicalOrdersDataSource: widget.clinicalOrdersDataSource,
      patientDataSource: widget.patientDataSource,
      patientAllergyDataSource: widget.patientAllergyDataSource,
      patientVitalsDataSource: widget.patientVitalsDataSource,
      patientAdmissionDataSource: widget.patientAdmissionDataSource,
      patientConsultationDataSource: widget.patientConsultationDataSource,
      patientPrescriptionDataSource: widget.patientPrescriptionDataSource,
      patientClinicalAlertDataSource: widget.patientClinicalAlertDataSource,
      emergencyAccessDataSource: widget.emergencyAccessDataSource,
      clinicalSafetyDataSource: widget.clinicalSafetyDataSource,
      emergencyAccessContext: widget.emergencyAccessContext,
      sessionNavigationGuard: widget.sessionNavigationGuard,
      appointmentCreateSyncPageLoader: widget.appointmentCreateSyncPageLoader,
    );

    unawaited(widget.operationReplayScheduler?.start());
  }

  @override
  void dispose() {
    _router.dispose();
    unawaited(_disposeReplayResources());
    super.dispose();
  }

  Future<void> _disposeReplayResources() async {
    final scheduler = widget.operationReplayScheduler;
    if (scheduler != null) {
      await scheduler.dispose();
    }

    final database = widget.localDatabase;
    if (database != null) {
      await database.close();
    }
  }

  @override
  Widget build(BuildContext context) {
    return BlocProvider<DeviceConnectivityCubit>(
      create: (_) => DeviceConnectivityCubit(
        source:
            widget.deviceConnectivitySource ??
            ConnectivityPlusDeviceConnectivitySource(),
      )..start(),
      child: MaterialApp.router(
        title: 'Clinic System Pro',
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: Colors.teal),
        ),
        routerConfig: _router,
        builder: (context, child) => DeviceConnectivityBanner(
          apiClient: widget.healthDataSource.apiClient,
          child: child ?? const SizedBox.shrink(),
        ),
      ),
    );
  }
}
