import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';

import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/backend_health/presentation/backend_health_screen.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/presentation/clinical_alert_inbox_screen.dart';
import 'package:clinic_system_pro/features/clinical_safety/presentation/clinical_alert_detail_screen.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_context.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_remote_data_source.dart';
import 'package:clinic_system_pro/features/emergency_access/presentation/emergency_access_request_screen.dart';
import 'package:clinic_system_pro/features/emergency_access/presentation/emergency_access_review_screen.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_detail_screen.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_directory_screen.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_registration_screen.dart';

GoRouter createAppRouter({
  required BackendHealthRemoteDataSource healthDataSource,
  required Widget loginScreen,
  PatientRemoteDataSource? patientDataSource,
  EmergencyAccessRemoteDataSource? emergencyAccessDataSource,
  ClinicalSafetyRemoteDataSource? clinicalSafetyDataSource,
  EmergencyAccessContext? emergencyAccessContext,
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
          return PatientDetailScreen(
            dataSource: source,
            patientId: patientId,
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
          final alertId = int.tryParse(state.pathParameters['alertId'] ?? '');
          if (source == null || alertId == null || alertId <= 0) {
            return const _ClinicalSafetyRouteUnavailableScreen();
          }
          return ClinicalAlertDetailScreen(
            dataSource: source,
            alertId: alertId,
          );
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
