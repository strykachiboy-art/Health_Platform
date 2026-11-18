import 'dart:convert';

import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';

/// An AI response is always a suggestion, never an approved chart entry.
class ClinicalAiSuggestion {
  const ClinicalAiSuggestion({
    required this.title,
    required this.lines,
    this.riskLevel,
  });

  final String title;
  final List<String> lines;
  final String? riskLevel;
}

class ClinicalAiUnavailable implements Exception {
  const ClinicalAiUnavailable();
}

/// Online-only one-shot user-initiated AI calls against the existing Flask API.
/// Never enqueues, retries, caches PHI, signs results or mutates clinical records.
class ClinicalAiRemoteDataSource {
  const ClinicalAiRemoteDataSource({
    required this.dashboard,
    required this.guard,
    required this.connectivity,
  });

  final DashboardRemoteDataSource dashboard;
  final SessionNavigationGuard guard;
  final DeviceConnectivitySource connectivity;

  static const allowedRoles = <String>{
    'admin',
    'doctor',
    'nurse',
    'pharmacist',
    'lab_technician',
  };

  bool _current(int revision) =>
      guard.canAccessProtectedRoutes &&
      guard.revision == revision &&
      identical(dashboard.sessionNavigationGuard, guard);

  Future<AuthTokenResponse> _session(int revision) async {
    if (!_current(revision)) throw const ClinicalAiUnavailable();
    try {
      final state = await dashboard.loadSession();
      final actor = state.session;
      if (!_current(revision) ||
          state.disposition != AuthSessionDisposition.ready ||
          actor == null ||
          actor.userId <= 0 ||
          actor.tenantId == null ||
          actor.clinicContextId == null ||
          actor.clinicContextId! <= 0 ||
          !allowedRoles.contains(actor.role.toLowerCase()) ||
          actor.accessToken.isEmpty ||
          actor.accessToken.trim() != actor.accessToken) {
        throw const ClinicalAiUnavailable();
      }
      return actor;
    } on Object {
      throw const ClinicalAiUnavailable();
    }
  }

  bool _same(AuthTokenResponse first, AuthTokenResponse second) =>
      first.userId == second.userId &&
      first.role == second.role &&
      first.tenantId == second.tenantId &&
      first.clinicContextId == second.clinicContextId &&
      first.accessToken == second.accessToken &&
      first.refreshToken == second.refreshToken;

  Future<ClinicalAiSuggestion> _submit(
    String path,
    Map<String, dynamic> body,
    String title,
  ) async {
    final revision = guard.revision;
    final actor = await _session(revision);
    if (await connectivity.checkStatus() != DeviceNetworkStatus.connected ||
        !_current(revision) ||
        !_same(actor, await _session(revision))) {
      throw const ClinicalAiUnavailable();
    }
    // The single POST is deliberately not retried after timeout or lost reply.
    final response = await dashboard.apiClient.postJson(
      path,
      body: body,
      bearerToken: actor.accessToken,
    );
    if (!_current(revision) || !_same(actor, await _session(revision))) {
      throw const ClinicalAiUnavailable();
    }
    if (response['success'] != true ||
        response['data'] is! Map<String, dynamic>) {
      throw const FormatException('Unverified AI response envelope.');
    }
    final data = response['data'] as Map<String, dynamic>;
    final lines = <String>[];
    final summary = data['summary'];
    if (summary != null) {
      if (summary is! String || summary.length > 5000) {
        throw const FormatException('Invalid AI summary.');
      }
      lines.add(summary);
    }
    for (final key in ['interpretation', 'recommendation']) {
      final value = data[key];
      if (value != null) {
        if (value is! String || value.length > 10000) {
          throw const FormatException('Invalid AI advisory text.');
        }
        lines.add(value);
      }
    }
    for (final key in ['abnormal_findings', 'recommendations']) {
      final values = data[key];
      if (values == null) continue;
      if (values is! List ||
          values.length > 100 ||
          values.any(
            (element) => element is! String || element.length > 2000,
          )) {
        throw const FormatException('Invalid AI advisory list.');
      }
      lines.addAll(values.cast<String>());
    }
    final interactions = data['interactions'];
    if (interactions != null) {
      if (interactions is! List ||
          interactions.length > 100 ||
          interactions.any((element) => element is! Map<String, dynamic>)) {
        throw const FormatException('Invalid AI interactions.');
      }
      for (final item in interactions) {
        final encoded = jsonEncode(item);
        if (encoded.length > 4000) {
          throw const FormatException('Oversized AI interaction.');
        }
        lines.add(encoded);
      }
    }
    final risk = data['risk_score'];
    if (risk != null &&
        (risk is! String ||
            !const {'low', 'medium', 'high', 'critical'}.contains(risk))) {
      throw const FormatException('Invalid AI risk level.');
    }
    // Empty AI results are still visibly advisory and never chart-confirmed.
    return ClinicalAiSuggestion(
      title: title,
      riskLevel: risk as String?,
      lines: List<String>.unmodifiable(lines),
    );
  }

  Future<ClinicalAiSuggestion> drugInteractions({
    required List<String> drugNames,
    int? patientId,
  }) {
    if (drugNames.length < 2 ||
        drugNames.length > 50 ||
        drugNames.any((n) => n.isEmpty || n != n.trim() || n.length > 255) ||
        drugNames.map((n) => n.toLowerCase()).toSet().length !=
            drugNames.length ||
        (patientId != null && patientId <= 0)) {
      throw ArgumentError('Enter two or more distinct valid drug names.');
    }
    return _submit('api/v1/ai/drug-interactions', <String, dynamic>{
      'drug_names': drugNames,
      'patient_id': ?patientId,
    }, 'Drug interaction assistance');
  }

  Future<ClinicalAiSuggestion> triage({
    required int patientId,
    required String symptoms,
  }) {
    if (patientId <= 0 ||
        symptoms.isEmpty ||
        symptoms != symptoms.trim() ||
        symptoms.length > 5000) {
      throw ArgumentError('A patient and symptoms are required.');
    }
    return _submit('api/v1/ai/triage', <String, dynamic>{
      'patient_id': patientId,
      'symptoms': symptoms,
    }, 'Clinical triage assistance');
  }

  Future<ClinicalAiSuggestion> interpretLab({
    required Map<String, dynamic> resultData,
    int? patientId,
    int? labOrderId,
  }) {
    if (resultData.isEmpty ||
        resultData.length > 100 ||
        (patientId != null && patientId <= 0) ||
        (labOrderId != null && labOrderId <= 0)) {
      throw ArgumentError('Provide valid laboratory result data.');
    }
    return _submit('api/v1/ai/lab-results/interpret', <String, dynamic>{
      'result_data': resultData,
      'patient_id': ?patientId,
      'lab_order_id': ?labOrderId,
    }, 'Laboratory interpretation assistance');
  }
}
