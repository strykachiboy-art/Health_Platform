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
import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';
import 'package:clinic_system_pro/core/sync/operation_queue_store.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_worker.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_scheduler.dart';
import 'package:clinic_system_pro/core/sync/appointment_create_queue_producer.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_store.dart';
import 'package:clinic_system_pro/features/auth/data/google_sign_in_provider.dart';
import 'package:clinic_system_pro/features/auth/presentation/google_login_screen.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';

void main() {
  final apiClient = ApiClient(config: BackendConfig.fromEnvironment());
  final connectivitySource = ConnectivityPlusDeviceConnectivitySource();

  final healthDataSource = BackendHealthRemoteDataSource(apiClient: apiClient);
  final patientDataSource = PatientRemoteDataSource(apiClient: apiClient);
  final authRemoteDataSource = AuthRemoteDataSource(apiClient: apiClient);
  final secureStorage = FlutterSecureKeyValueStorage();
  final sessionStore = AuthSessionStore(storage: secureStorage);
  final sessionCoordinator = AuthSessionCoordinator(
    sessionStore: sessionStore,
    remoteDataSource: authRemoteDataSource,
  );
  final googleSignInProvider = GoogleSignInProvider(
    configuration: GoogleSignInConfiguration.fromEnvironment(),
  );

  final localDatabase = AppDatabase();
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
  );
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

  final loginScreen = GoogleLoginScreen(
    acquireGoogleIdToken: googleSignInProvider.signInForIdToken,
    authRemoteDataSource: authRemoteDataSource,
    sessionCoordinator: sessionCoordinator,
    onSessionEnding: replayScheduler.pauseAndWait,
    onSessionChanged: replayScheduler.notifySessionChanged,
  );

  runApp(
    AppointmentCreateQueueScope(
      producer: appointmentCreateQueueProducer,
      reconcileOperation: replayWorker.reconcileAppointmentCreate,
      child: ClinicSystemProApp(
        healthDataSource: healthDataSource,
        patientDataSource: patientDataSource,
        loginScreen: loginScreen,
        deviceConnectivitySource: connectivitySource,
        operationReplayScheduler: replayScheduler,
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
    this.patientDataSource,
    this.deviceConnectivitySource,
    this.operationReplayScheduler,
    this.localDatabase,
  });

  final BackendHealthRemoteDataSource healthDataSource;
  final Widget loginScreen;
  final PatientRemoteDataSource? patientDataSource;
  final DeviceConnectivitySource? deviceConnectivitySource;
  final OperationReplayScheduler? operationReplayScheduler;
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
      patientDataSource: widget.patientDataSource,
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
