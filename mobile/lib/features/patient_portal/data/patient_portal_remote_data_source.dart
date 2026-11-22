import 'dart:async';

import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';

/// S12: no patient ID is accepted by this interface. The Flask server resolves
/// the enrolled patient and clinic from its JWT-authenticated user.
abstract class PatientPortalDataSource {
  Future<PatientPortalView> load();
  Future<void> book({
    required int staffId,
    required DateTime startUtc,
    required String appointmentType,
    required String idempotencyKey,
  });
  Future<void> cancel(int appointmentId);
}

class PatientPortalView {
  const PatientPortalView({
    required this.name,
    required this.clinicians,
    required this.appointments,
    required this.prescriptions,
    required this.invoices,
  });

  final String name;
  final List<Map<String, dynamic>> clinicians;
  final List<Map<String, dynamic>> appointments;
  final List<Map<String, dynamic>> prescriptions;
  final List<Map<String, dynamic>> invoices;
}

class BackendPatientPortalDataSource implements PatientPortalDataSource {
  const BackendPatientPortalDataSource(this.dashboard);

  final DashboardRemoteDataSource dashboard;

  Future<AuthTokenResponse> _session() async {
    final state = await dashboard.loadSession();
    final session = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        session == null ||
        session.role.toLowerCase() != 'patient' ||
        session.userId <= 0 ||
        (session.clinicContextId != null && session.clinicContextId! <= 0) ||
        session.accessToken.isEmpty ||
        dashboard.sessionNavigationGuard?.canAccessProtectedRoutes == false) {
      throw const DashboardSessionMismatchException();
    }
    return session;
  }

  Future<Map<String, dynamic>> _request(
    String endpoint, {
    Map<String, dynamic>? body,
    String? key,
  }) async {
    final revision = dashboard.sessionNavigationGuard?.revision;
    final before = await _session();
    final response = body == null
        ? await dashboard.apiClient.getJson(
            'api/v1/patient-portal/$endpoint',
            bearerToken: before.accessToken,
          )
        : await dashboard.apiClient.postJson(
            'api/v1/patient-portal/$endpoint',
            bearerToken: before.accessToken,
            body: body,
            idempotencyKey: key,
          );
    final after = await _session();
    if (revision != dashboard.sessionNavigationGuard?.revision ||
        before.userId != after.userId ||
        before.role != after.role ||
        before.accessToken != after.accessToken ||
        before.clinicContextId != after.clinicContextId ||
        before.tenantId != after.tenantId) {
      throw const DashboardSessionMismatchException();
    }
    if (response['success'] != true ||
        response['data'] is! Map<String, dynamic>) {
      throw const FormatException('Patient response is invalid.');
    }
    return response['data'] as Map<String, dynamic>;
  }

  List<Map<String, dynamic>> _items(Map<String, dynamic> data) {
    final raw = data['items'];
    if (raw is! List || raw.length > 100) {
      throw const FormatException('Patient result list is invalid.');
    }
    final items = <Map<String, dynamic>>[];
    for (final row in raw) {
      if (row is! Map<String, dynamic>) {
        throw const FormatException('Patient result row is invalid.');
      }
      items.add(Map<String, dynamic>.unmodifiable(row));
    }
    return List<Map<String, dynamic>>.unmodifiable(items);
  }

  @override
  Future<PatientPortalView> load() async {
    final revision = dashboard.sessionNavigationGuard?.revision;
    final before = await _session();
    final me = await _request('me');
    final clinicians = await _request('clinicians');
    final appointments = await _request('appointments');
    final prescriptions = await _request('prescriptions');
    final invoices = await _request('invoices');
    final after = await _session();
    if (revision != dashboard.sessionNavigationGuard?.revision ||
        before.userId != after.userId ||
        before.accessToken != after.accessToken ||
        before.clinicContextId != after.clinicContextId) {
      throw const DashboardSessionMismatchException();
    }
    final first = me['first_name'];
    final last = me['last_name'];
    if (first is! String ||
        last is! String ||
        first.length > 80 ||
        last.length > 80) {
      throw const FormatException('Patient profile is invalid.');
    }
    return PatientPortalView(
      name: '$first $last'.trim(),
      clinicians: _items(clinicians),
      appointments: _items(appointments),
      prescriptions: _items(prescriptions),
      invoices: _items(invoices),
    );
  }

  @override
  Future<void> book({
    required int staffId,
    required DateTime startUtc,
    required String appointmentType,
    required String idempotencyKey,
  }) async {
    if (staffId <= 0 ||
        !startUtc.isUtc ||
        !const {
          'in_person',
          'telemedicine',
          'follow_up',
        }.contains(appointmentType)) {
      throw const FormatException('Appointment selection is invalid.');
    }
    final result = await _request(
      'appointments',
      body: {
        'staff_id': staffId,
        'scheduled_start': startUtc.toIso8601String(),
        'scheduled_end': startUtc
            .add(const Duration(minutes: 30))
            .toIso8601String(),
        'appointment_type': appointmentType,
      },
      key: idempotencyKey,
    );
    if (result['id'] is! int || result['status'] != 'scheduled') {
      throw const FormatException(
        'Booking result was not confirmed by the backend.',
      );
    }
  }

  @override
  Future<void> cancel(int appointmentId) async {
    if (appointmentId <= 0) {
      throw const FormatException('Appointment selection is invalid.');
    }
    final result = await _request(
      'appointments/$appointmentId/cancel',
      body: {'cancellation_reason': 'Cancelled by patient through portal'},
    );
    if (result['id'] != appointmentId || result['status'] != 'cancelled') {
      throw const FormatException('Cancellation outcome was not confirmed.');
    }
  }
}
