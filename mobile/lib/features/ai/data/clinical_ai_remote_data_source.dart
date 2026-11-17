import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'clinical_ai_contract.dart';

/// No AI HTTP request was dispatched. May be corrected before a new user intent.
class ClinicalAiUnavailable implements Exception {
  const ClinicalAiUnavailable();
}

/// An AI credit/audit may have been committed. Never retry the POST automatically.
class ClinicalAiOutcomeUnknown implements Exception {
  const ClinicalAiOutcomeUnknown();
}

/// Uses the established JWT, selected clinic, API client and guard.
/// This is deliberately NOT an offline operation or a replayable mutation.
class ClinicalAiRemoteDataSource {
  ClinicalAiRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.guard,
    required this.connectivity,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard guard;
  final DeviceConnectivitySource connectivity;

  // An attempted credit-consuming feature is locked across route revisits.
  // Includes user/clinic identity but never PHI or input text.
  final Set<String> _dispatched = <String>{};

  static const permittedRoles = <String>{
    'admin',
    'doctor',
    'nurse',
    'pharmacist',
    'lab_technician',
  };

  bool _guardCurrent(int revision) =>
      guard.canAccessProtectedRoutes && guard.revision == revision;

  Future<AuthTokenResponse> _session() async {
    try {
      final state = await loadSession();
      final value = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          value == null ||
          value.userId <= 0 ||
          value.clinicContextId == null ||
          value.clinicContextId! <= 0 ||
          !permittedRoles.contains(value.role.trim().toLowerCase()) ||
          value.accessToken.isEmpty ||
          value.accessToken.trim() != value.accessToken) {
        throw const ClinicalAiUnavailable();
      }
      return value;
    } on ClinicalAiUnavailable {
      rethrow;
    } on Object {
      throw const ClinicalAiUnavailable();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  /// Used before revealing the patient-bound AI workspace; no HTTP or credit.
  Future<AuthTokenResponse> requireActiveSession() async {
    final revision = guard.revision;
    if (!_guardCurrent(revision)) throw const ClinicalAiUnavailable();
    final session = await _session();
    if (!_guardCurrent(revision)) throw const ClinicalAiUnavailable();
    return session;
  }

  Future<ClinicalAiAdvisory> checkDrugInteractions({
    required int patientId,
    required List<String> drugNames,
  }) {
    if (patientId <= 0 || drugNames.length < 2 || drugNames.length > 50) {
      throw const ClinicalAiUnavailable();
    }
    final clean = drugNames.map((d) => d.trim()).toList();
    if (clean.any((d) => d.isEmpty || d.length > 255) ||
        clean.map((d) => d.toLowerCase()).toSet().length != clean.length) {
      throw const ClinicalAiUnavailable();
    }
    return _send(
      ClinicalAiFeature.drugInteractions,
      patientId: patientId,
      body: <String, dynamic>{'patient_id': patientId, 'drug_names': clean},
    );
  }

  Future<ClinicalAiAdvisory> assistTriage({
    required int patientId,
    required String symptoms,
  }) {
    final clean = symptoms.trim();
    if (patientId <= 0 || clean.isEmpty || clean.length > 5000) {
      throw const ClinicalAiUnavailable();
    }
    return _send(
      ClinicalAiFeature.triage,
      patientId: patientId,
      body: <String, dynamic>{'patient_id': patientId, 'symptoms': clean},
    );
  }

  Future<ClinicalAiAdvisory> interpretLabResults({
    required int patientId,
    required int labOrderId,
    required Map<String, dynamic> resultData,
  }) {
    if (patientId <= 0 ||
        labOrderId <= 0 ||
        resultData.isEmpty ||
        resultData.length > 100) {
      throw const ClinicalAiUnavailable();
    }
    return _send(
      ClinicalAiFeature.labInterpretation,
      patientId: patientId,
      recordId: labOrderId,
      body: <String, dynamic>{
        'patient_id': patientId,
        'lab_order_id': labOrderId,
        'result_data': resultData,
      },
    );
  }

  Future<ClinicalAiAdvisory> _send(
    ClinicalAiFeature feature, {
    required int patientId,
    int? recordId,
    required Map<String, dynamic> body,
  }) async {
    final revision = guard.revision;
    if (!_guardCurrent(revision)) throw const ClinicalAiUnavailable();
    final before = await _session();
    if (!_guardCurrent(revision)) throw const ClinicalAiUnavailable();
    final key =
        '${before.userId}/${before.clinicContextId}/${feature.name}/$patientId/${recordId ?? 0}';
    if (_dispatched.contains(key)) throw const ClinicalAiOutcomeUnknown();
    try {
      if (await connectivity.checkStatus() != DeviceNetworkStatus.connected) {
        throw const ClinicalAiUnavailable();
      }
    } on ClinicalAiUnavailable {
      rethrow;
    } on Object {
      throw const ClinicalAiUnavailable();
    }
    if (!_guardCurrent(revision)) throw const ClinicalAiUnavailable();
    final verified = await _session();
    if (!_guardCurrent(revision) || !_same(before, verified)) {
      throw const ClinicalAiUnavailable();
    }
    // All preflight checks finished. Lock BEFORE dispatch; even a timeout or
    // malformed response could have consumed a credit and written an audit.
    if (!_dispatched.add(key)) throw const ClinicalAiOutcomeUnknown();
    final Map<String, dynamic> envelope;
    try {
      envelope = await apiClient.postJson(
        feature.path,
        body: body,
        bearerToken: before.accessToken,
      );
    } on Object {
      throw const ClinicalAiOutcomeUnknown();
    }
    if (!_guardCurrent(revision)) throw const ClinicalAiOutcomeUnknown();
    final after = await _session().catchError(
      (Object _) => throw const ClinicalAiOutcomeUnknown(),
    );
    if (!_guardCurrent(revision) || !_same(before, after)) {
      throw const ClinicalAiOutcomeUnknown();
    }
    try {
      return ClinicalAiAdvisory.fromEnvelope(feature, envelope);
    } on Object {
      throw const ClinicalAiOutcomeUnknown();
    }
  }
}
