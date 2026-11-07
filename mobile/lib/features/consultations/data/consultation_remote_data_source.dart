import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_consultation.dart';

class ConsultationSessionMismatchException implements Exception {
  const ConsultationSessionMismatchException();

  @override
  String toString() => 'Consultation session is unavailable or changed.';
}

/// One online start intent. No PHI is persisted in the local queue.
class ConsultationStartCommand {
  const ConsultationStartCommand({
    required this.patientId,
    required this.staffId,
    required this.idempotencyKey,
    this.appointmentId,
    this.consultationType = 'general',
    this.chiefComplaint,
    this.symptoms,
  });

  final int patientId;
  final int staffId;
  final int? appointmentId;
  final String consultationType;
  final String? chiefComplaint;
  final String? symptoms;
  final String idempotencyKey;

  void validate() {
    if (patientId <= 0 ||
        staffId <= 0 ||
        (appointmentId != null && appointmentId! <= 0)) {
      throw ArgumentError(
        'Patient, staff, and appointment IDs must be positive.',
      );
    }
    if (!const <String>{
      'general',
      'follow_up',
      'specialist',
      'emergency',
    }.contains(consultationType)) {
      throw ArgumentError.value(consultationType, 'consultationType');
    }
    if (idempotencyKey.isEmpty ||
        idempotencyKey.length > 255 ||
        idempotencyKey.trim() != idempotencyKey) {
      throw ArgumentError.value(idempotencyKey, 'idempotencyKey');
    }
    if ((chiefComplaint != null &&
            (chiefComplaint!.trim().isEmpty ||
                chiefComplaint!.length > 2000)) ||
        (symptoms != null &&
            (symptoms!.trim().isEmpty || symptoms!.length > 5000))) {
      throw ArgumentError('Consultation text violates the backend schema.');
    }
  }

  Map<String, dynamic> toBody() => <String, dynamic>{
    'patient_id': patientId,
    'staff_id': staffId,
    'consultation_type': consultationType,
    if (appointmentId != null) 'appointment_id': appointmentId,
    if (chiefComplaint != null) 'chief_complaint': chiefComplaint,
    if (symptoms != null) 'symptoms': symptoms,
  };
}

/// Reuses P23A's strict consultation record model and the shared API client.
/// The GET and POST both revalidate the active user, clinic, token and guard
/// revision after transport; no automatic write retry is attempted here.
class ConsultationRemoteDataSource {
  const ConsultationRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const _roles = <String>{'admin', 'doctor', 'nurse'};

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _session() async {
    try {
      final result = await loadSession();
      final session = result.session;
      if (result.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.isEmpty ||
          session.accessToken != session.accessToken.trim() ||
          !_roles.contains(session.role.trim().toLowerCase())) {
        throw const ConsultationSessionMismatchException();
      }
      return session;
    } on ConsultationSessionMismatchException {
      rethrow;
    } on Object {
      throw const ConsultationSessionMismatchException();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<PatientConsultation> _readEnvelope(
    Future<Map<String, dynamic>> Function(String token) request, {
    required int expectedPatientId,
    int? expectedStaffId,
    int? expectedAppointmentId,
    int? expectedConsultationId,
  }) async {
    final revision = sessionNavigationGuard.revision;
    if (!_current(revision)) {
      throw const ConsultationSessionMismatchException();
    }
    final before = await _session();
    if (!_current(revision)) {
      throw const ConsultationSessionMismatchException();
    }
    final result = await request(before.accessToken);
    if (!_current(revision)) {
      throw const ConsultationSessionMismatchException();
    }
    final after = await _session();
    if (!_current(revision) || !_same(before, after)) {
      throw const ConsultationSessionMismatchException();
    }
    if (result['success'] != true || result['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid consultation response envelope.');
    }
    final record = PatientConsultation.fromJson(
      result['data'] as Map<String, dynamic>,
      expectedPatientId: expectedPatientId,
      expectedClinicId: before.clinicContextId!,
    );
    if ((expectedStaffId != null && record.staffId != expectedStaffId) ||
        (expectedAppointmentId != null &&
            record.appointmentId != expectedAppointmentId) ||
        (expectedConsultationId != null &&
            record.id != expectedConsultationId)) {
      throw const FormatException('Consultation response identity mismatch.');
    }
    return record;
  }

  Future<PatientConsultation> start(ConsultationStartCommand command) async {
    command.validate();
    final record = await _readEnvelope(
      (token) => apiClient.postJson(
        'api/v1/consultations/',
        body: command.toBody(),
        bearerToken: token,
        idempotencyKey: command.idempotencyKey,
      ),
      expectedPatientId: command.patientId,
      expectedStaffId: command.staffId,
      expectedAppointmentId: command.appointmentId,
    );
    if (record.status != PatientConsultationStatus.inProgress) {
      throw const FormatException('Unexpected consultation start status.');
    }
    return record;
  }

  Future<PatientConsultation> getForPatient({
    required int patientId,
    required int consultationId,
  }) {
    if (patientId <= 0 || consultationId <= 0) {
      throw ArgumentError('Patient and consultation IDs must be positive.');
    }
    return _readEnvelope(
      (token) => apiClient.getJson(
        'api/v1/consultations/$consultationId',
        bearerToken: token,
      ),
      expectedPatientId: patientId,
      expectedConsultationId: consultationId,
    );
  }
}
