import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';

import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/backend_health/presentation/backend_health_screen.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_detail_screen.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_directory_screen.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_registration_screen.dart';

GoRouter createAppRouter({
  required BackendHealthRemoteDataSource healthDataSource,
  required Widget loginScreen,
  PatientRemoteDataSource? patientDataSource,
  SessionNavigationGuard? sessionNavigationGuard,
}) {
  return GoRouter(
    initialLocation: '/login',
    refreshListenable: sessionNavigationGuard,
    redirect: (context, state) =>
        sessionNavigationGuard?.redirect(state.matchedLocation),
    routes: [
      GoRoute(
        path: '/login',
        name: 'login',
        builder: (context, state) => loginScreen,
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
          return PatientDetailScreen(dataSource: source, patientId: patientId);
        },
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
