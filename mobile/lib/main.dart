import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:go_router/go_router.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_banner.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_cubit.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
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

  final healthDataSource = BackendHealthRemoteDataSource(apiClient: apiClient);
  final patientDataSource = PatientRemoteDataSource(apiClient: apiClient);
  final authRemoteDataSource = AuthRemoteDataSource(apiClient: apiClient);
  final sessionStore = AuthSessionStore(
    storage: FlutterSecureKeyValueStorage(),
  );
  final sessionCoordinator = AuthSessionCoordinator(
    sessionStore: sessionStore,
    remoteDataSource: authRemoteDataSource,
  );
  final googleSignInProvider = GoogleSignInProvider(
    configuration: GoogleSignInConfiguration.fromEnvironment(),
  );

  runApp(
    ClinicSystemProApp(
      healthDataSource: healthDataSource,
      patientDataSource: patientDataSource,
      loginScreen: GoogleLoginScreen(
        acquireGoogleIdToken: googleSignInProvider.signInForIdToken,
        authRemoteDataSource: authRemoteDataSource,
        sessionCoordinator: sessionCoordinator,
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
  });

  final BackendHealthRemoteDataSource healthDataSource;
  final Widget loginScreen;
  final PatientRemoteDataSource? patientDataSource;
  final DeviceConnectivitySource? deviceConnectivitySource;

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
  }

  @override
  void dispose() {
    _router.dispose();
    super.dispose();
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
