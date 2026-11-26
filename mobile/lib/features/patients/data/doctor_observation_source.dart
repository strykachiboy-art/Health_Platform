import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/features/administration/data/wd09cf_operations_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_allergy.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_vitals.dart';
import 'package:clinic_system_pro/features/patients/data/patient_allergy_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_admission.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';
import 'package:clinic_system_pro/features/emar/data/emar_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/models/medication_reconciliation.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/medication_reconciliation_read_remote_data_source.dart';

import 'package:clinic_system_pro/features/patients/data/models/patient.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_consultation.dart';
import 'package:clinic_system_pro/features/profile/data/profile_remote_data_source.dart';
import 'package:clinic_system_pro/features/administration/data/wd05_administration_read_remote_data_source.dart';

import 'package:clinic_system_pro/features/clinical_orders/data/models/imaging_procedure_order.dart';

part 'doctor_clinical_records.dart';
part 'doctor_directory_profile.dart';

class DoctorObservationUnavailable implements Exception {
  const DoctorObservationUnavailable();
}

class DoctorObservationConflict implements Exception {
  const DoctorObservationConflict();
}

class DoctorObservationUnconfirmed implements Exception {
  const DoctorObservationUnconfirmed();
}

/// Server-reviewed patient scope. This object is not an authorization token.
class DoctorObservationReview {
  const DoctorObservationReview._(this.patientId, this.clinicId, this.revision,
      this.patientLabel, this._actor);
  final int patientId;
  final int clinicId;
  final int revision;
  final String patientLabel;
  final AuthTokenResponse _actor;
}

/// Exact typed patient-observation operations; no generic endpoint, queue,
/// arbitrary scope header, offline retry, or locally fabricated status.
class DoctorObservationSource {
  DoctorObservationSource(this.reader, this.connectivity, this.gate);
  final PatientAllergyRemoteDataSource reader;
  final DeviceConnectivitySource connectivity;
  final Wd09cfOneShotGate gate;
  final Set<String> _inFlight = <String>{};

  static const allergyTypes = <String>{
    'drug', 'food', 'environmental', 'latex', 'biologic', 'other',
  };
  static const severities = <String>{
    'unknown', 'mild', 'moderate', 'severe', 'life_threatening',
  };
  static const vitalFields = <String, String>{
    'temperature': 'Temperature (°C)',
    'blood_pressure_systolic': 'Systolic BP (mmHg)',
    'blood_pressure_diastolic': 'Diastolic BP (mmHg)',
    'heart_rate': 'Heart rate (bpm)',
    'respiratory_rate': 'Respiratory rate (per minute)',
    'oxygen_saturation': 'Oxygen saturation (%)',
    'weight': 'Weight (kg)',
    'height': 'Height (cm)',
  };
  static const _integerLimits = <String, int>{
    'blood_pressure_systolic': 400, 'blood_pressure_diastolic': 300,
    'heart_rate': 400, 'respiratory_rate': 200,
  };

  bool current(int revision) =>
      reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      reader.sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _actor(int revision) async {
    if (!current(revision)) {
      throw const DoctorObservationUnavailable();
    }
    final state = await reader.loadSession();
    final a = state.session;
    if (state.disposition != AuthSessionDisposition.ready || a == null ||
        a.role.trim().toLowerCase() != 'doctor' || a.userId <= 0 ||
        (a.clinicContextId ?? 0) <= 0 || a.accessToken.isEmpty ||
        a.accessToken != a.accessToken.trim() || a.refreshToken.isEmpty ||
        !RegExp(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$')
            .hasMatch(a.tenantId ?? '') || !current(revision)) {
      throw const DoctorObservationUnavailable();
    }
    return a;
  }

  Future<void> _check(AuthTokenResponse first, int revision) async {
    final second = await _actor(revision);
    if (first.userId != second.userId || first.role != second.role ||
        first.clinicContextId != second.clinicContextId ||
        first.tenantId != second.tenantId ||
        first.accessToken != second.accessToken ||
        first.refreshToken != second.refreshToken || !current(revision)) {
      throw const DoctorObservationUnavailable();
    }
  }

  static Map<String, dynamic> _data(Map<String, dynamic> raw) {
    final value = raw['data'];
    if (raw['success'] != true || value is! Map<String, dynamic>) {
      throw const FormatException('Unconfirmed server response.');
    }
    return value;
  }

  Future<bool> canOffer(int clinicId) async {
    final revision = reader.sessionNavigationGuard.revision;
    try {
      final actor = await _actor(revision);
      return actor.clinicContextId == clinicId && current(revision);
    } on Object {
      return false;
    }
  }

  Future<DoctorObservationReview> inspect(int patientId) async {
    if (patientId <= 0) {
      throw const DoctorObservationUnavailable();
    }
    final revision = reader.sessionNavigationGuard.revision;
    final a = await _actor(revision);
    final row = _data(await reader.apiClient.getJson(
      'api/v1/patients/$patientId', bearerToken: a.accessToken,
    ));
    await _check(a, revision);
    final first = row['first_name'];
    final last = row['last_name'];
    if (row['id'] != patientId || row['clinic_id'] != a.clinicContextId ||
        row['is_active'] != true || first is! String || last is! String ||
        first.trim().isEmpty || last.trim().isEmpty) {
      throw const DoctorObservationUnavailable();
    }
    return DoctorObservationReview._(patientId, a.clinicContextId!, revision,
        '$first $last', a);
  }

  Future<PatientAllergyPage> allergies(DoctorObservationReview review,
      {int page = 1}) async {
    await _check(review._actor, review.revision);
    final result = await reader.fetchAllergies(
      patientId: review.patientId, page: page, perPage: 20,
    );
    await _check(review._actor, review.revision);
    return result;
  }

  Future<PatientAllergy> detail(DoctorObservationReview review, int id) async {
    if (id <= 0) {
      throw const FormatException('Invalid allergy ID.');
    }
    await _check(review._actor, review.revision);
    final row = _data(await reader.apiClient.getJson(
      'api/v1/patients/${review.patientId}/allergies/$id',
      bearerToken: review._actor.accessToken,
    ));
    await _check(review._actor, review.revision);
    return _allergy(row, review, expectedId: id);
  }

  static PatientAllergy _allergy(Map<String, dynamic> row,
      DoctorObservationReview review, {int? expectedId}) {
    final result = PatientAllergy.fromJson(row);
    if (result.clinicId != review.clinicId || result.patientId != review.patientId ||
        (expectedId != null && result.id != expectedId)) {
      throw const FormatException('Allergy scope mismatch.');
    }
    return result;
  }

  static Map<String, dynamic> allergyDraft({required String name,
      required String type, required String severity, String code = '',
      String reaction = '', String notes = '', String onset = ''}) {
    final n = name.trim();
    final c = code.trim();
    final r = reaction.trim();
    final s = notes.trim();
    final t = onset.trim();
    if (n.isEmpty || n.length > 255 || !allergyTypes.contains(type) ||
        !severities.contains(severity) || c.length > 64 ||
        (c.isNotEmpty && !RegExp(r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$').hasMatch(c)) ||
        r.length > 500 || s.length > 1000 ||
        (t.isNotEmpty && (DateTime.tryParse(t) == null ||
          !RegExp(r'(Z|[+-]\d{2}:\d{2})$').hasMatch(t)))) {
      throw const FormatException('Invalid allergy documentation.');
    }
    return <String, dynamic>{'allergen_name': n, 'allergen_type': type,
      'severity': severity, if (c.isNotEmpty) 'allergen_code': c,
      if (r.isNotEmpty) 'reaction': r, if (s.isNotEmpty) 'notes': s,
      if (t.isNotEmpty) 'onset_at': t};
  }

  static Map<String, dynamic> vitalDraft(Map<String, String> values) {
    if (values.keys.any((key) => !vitalFields.containsKey(key))) {
      throw const FormatException('Unknown observation field.');
    }
    final body = <String, dynamic>{};
    for (final entry in values.entries) {
      final value = entry.value.trim();
      if (value.isEmpty) {
        continue;
      }
      final limit = _integerLimits[entry.key];
      if (limit != null) {
        final n = int.tryParse(value);
        if (n == null || n < 0 || n > limit || !RegExp(r'^\d+$').hasMatch(value)) {
          throw const FormatException('Invalid integer observation.');
        }
        body[entry.key] = n;
      } else {
        final n = double.tryParse(value);
        if (n == null || !n.isFinite || n < 0 || value.length > 20 ||
            !RegExp(r'^\d+(?:\.\d+)?$').hasMatch(value) ||
            ((entry.key == 'temperature' || entry.key == 'oxygen_saturation') && n > 100)) {
          throw const FormatException('Invalid decimal observation.');
        }
        // Keep measured decimals exact on the wire.
        body[entry.key] = value;
      }
    }
    if (body.isEmpty) {
      throw const FormatException('At least one measurement is required.');
    }
    return body;
  }

  Future<void> _preflight(DoctorObservationReview review) async {
    await _check(review._actor, review.revision);
    if (await connectivity.checkStatus() != DeviceNetworkStatus.connected) {
      throw const DoctorObservationUnavailable();
    }
    final fresh = await inspect(review.patientId);
    await _check(review._actor, review.revision);
    if (fresh.clinicId != review.clinicId || fresh.revision != review.revision ||
        fresh.patientLabel != review.patientLabel) {
      throw const DoctorObservationUnavailable();
    }
  }

  // No patient measurements or allergy descriptions are retained in the
  // router-lifetime attempt key. A single confirmed intent may be submitted.
  Future<T> _write<T>(DoctorObservationReview review, String action,
      String intentId, Map<String, dynamic> body,
      T Function(Map<String, dynamic>) parse, {bool patch = false,
      Future<void> Function()? verifyReviewed}) async {
    if (!RegExp(r'^[a-zA-Z0-9_-]{8,100}$').hasMatch(intentId)) {
      throw const FormatException('Invalid local intent.');
    }
    await _preflight(review);
    if (verifyReviewed != null) {
      await verifyReviewed();
    }
    await _check(review._actor, review.revision);
    final a = review._actor;
    final scope = 'doctor-observation:${a.userId}:${a.tenantId}:${review.clinicId}:${review.patientId}:$action';
    final key = '$scope:$intentId';
    final uncertaintyScope = action.startsWith('allergies/')
        ? scope.replaceFirst(RegExp(r'/status$'), '') : scope;
    final uncertain = '$uncertaintyScope:uncertain';
    if (gate.contains(uncertain) || _inFlight.contains(uncertaintyScope) || !gate.claim(key)) {
      throw const DoctorObservationUnconfirmed();
    }
    _inFlight.add(uncertaintyScope);
    try {
      final path = 'api/v1/patients/${review.patientId}/$action';
      final raw = patch
          ? await reader.apiClient.patchJson(path, body: body, bearerToken: a.accessToken)
          : await reader.apiClient.postJson(path, body: body, bearerToken: a.accessToken);
      await _check(a, review.revision);
      return parse(_data(raw));
    } on ApiException catch (error) {
      if (error.kind == ApiFailureKind.httpStatus && error.statusCode == 409) {
        await _check(a, review.revision);
        throw const DoctorObservationConflict();
      }
      gate.claim(uncertain);
      throw const DoctorObservationUnconfirmed();
    } on Object {
      gate.claim(uncertain);
      throw const DoctorObservationUnconfirmed();
    } finally {
      _inFlight.remove(uncertaintyScope);
    }
  }

  Future<PatientAllergy> createAllergy(DoctorObservationReview review,
      {required String intentId, required String name, required String type,
      required String severity, String code = '', String reaction = '',
      String notes = '', String onset = ''}) {
    final body = allergyDraft(name: name, type: type, severity: severity,
        code: code, reaction: reaction, notes: notes, onset: onset);
    return _write(review, 'allergies', intentId, body, (row) {
      final result = _allergy(row, review);
      if (result.allergenName != body['allergen_name'] ||
          row['allergen_type'] != type || row['severity'] != severity ||
          result.allergenCode != body['allergen_code'] ||
          result.reaction != body['reaction'] || result.notes != body['notes'] ||
          result.onsetAt != (body['onset_at'] == null ? null : DateTime.parse(body['onset_at'] as String).toUtc()) ||
          result.clinicalStatus != PatientAllergyClinicalStatus.active ||
          result.verificationStatus != PatientAllergyVerificationStatus.unconfirmed ||
          result.recordedById == null) {
        throw const FormatException('Allergy creation not confirmed.');
      }
      return result;
    });
  }

  static String _decimalCanonical(String value) {
    if (!RegExp(r'^\d+(?:\.\d+)?$').hasMatch(value)) {
      throw const FormatException('Invalid decimal receipt.');
    }
    final pieces = value.split('.');
    final whole = pieces.first.replaceFirst(RegExp(r'^0+(?=\d)'), '');
    final fraction = pieces.length == 2 ? pieces.last.replaceFirst(RegExp(r'0+$'), '') : '';
    return fraction.isEmpty ? whole : '$whole.$fraction';
  }

  Future<PatientVitals> recordVitals(DoctorObservationReview review,
      {required String intentId, required Map<String, String> measurements}) {
    final body = vitalDraft(measurements);
    return _write(review, 'vitals', intentId, body, (row) {
      final result = PatientVitals.fromJson(row, expectedPatientId: review.patientId);
      if (result.recordedById == null || result.consultationId != null) {
        throw const FormatException('Observation identity not confirmed.');
      }
      for (final entry in body.entries) {
        // The persistence model can apply database precision. Do not display
        // a claimed success if the committed observation differs from input.
        final recorded = row[entry.key];
        if (entry.value is int ? recorded != entry.value :
            (recorded is! String || _decimalCanonical(recorded) != _decimalCanonical(entry.value.toString()))) {
          throw const FormatException('Measurement not confirmed.');
        }
      }
      return result;
    });
  }
  Future<void> _verifyAllergy(DoctorObservationReview review, PatientAllergy prior) async {
    if (prior.clinicId != review.clinicId || prior.patientId != review.patientId) {
      throw const DoctorObservationUnavailable();
    }
    final fresh = await detail(review, prior.id);
    if (fresh.updatedAt != prior.updatedAt) {
      throw const DoctorObservationConflict();
    }
  }

  Future<PatientAllergy> editAllergy(DoctorObservationReview review, PatientAllergy prior,
      {required String intentId, required String name, required String type,
      required String severity, String code = '', String reaction = '',
      String notes = '', String onset = ''}) {
    final body = allergyDraft(name: name, type: type, severity: severity,
        code: code, reaction: reaction, notes: notes, onset: onset);
    for (final field in ['allergen_code', 'reaction', 'notes', 'onset_at']) {
      body.putIfAbsent(field, () => null);
    }
    body['expected_updated_at'] = prior.updatedAt.toUtc().toIso8601String();
    return _write(review, 'allergies/${prior.id}', intentId, body, (row) {
      final result = _allergy(row, review, expectedId: prior.id);
      final expectedOnset = body['onset_at'] == null ? null : DateTime.parse(body['onset_at'] as String).toUtc();
      if (result.allergenName != body['allergen_name'] || row['allergen_type'] != type ||
          row['severity'] != severity || result.allergenCode != body['allergen_code'] ||
          result.reaction != body['reaction'] || result.notes != body['notes'] ||
          result.onsetAt != expectedOnset || result.clinicalStatus != prior.clinicalStatus ||
          result.verificationStatus != prior.verificationStatus ||
          result.updatedAt.isBefore(prior.updatedAt)) {
        throw const FormatException('Allergy edit not confirmed.');
      }
      return result;
    }, patch: true, verifyReviewed: () => _verifyAllergy(review, prior));
  }

  Future<PatientAllergy> transitionAllergy(DoctorObservationReview review, PatientAllergy prior,
      {required String intentId, required String reason,
      String? clinicalStatus, String? verificationStatus}) {
    final note = reason.trim();
    if (note.isEmpty || note.length > 500 ||
        (clinicalStatus == null && verificationStatus == null) ||
        (clinicalStatus != null && !const {'active', 'inactive', 'resolved'}.contains(clinicalStatus)) ||
        (verificationStatus != null && !const {'unconfirmed', 'confirmed', 'refuted'}.contains(verificationStatus))) {
      throw const FormatException('Invalid status review.');
    }
    final body = <String, dynamic>{'expected_updated_at': prior.updatedAt.toUtc().toIso8601String(),
      'reason': note, 'clinical_status': ?clinicalStatus,
      'verification_status': ?verificationStatus};
    return _write(review, 'allergies/${prior.id}/status', intentId, body, (row) {
      final result = _allergy(row, review, expectedId: prior.id);
      final verify = verificationStatus ?? prior.verificationStatus.name;
      final clinical = verify == 'refuted' ? 'inactive' : (clinicalStatus ?? prior.clinicalStatus.name);
      if (result.clinicalStatus.name != clinical || result.verificationStatus.name != verify ||
          !result.updatedAt.isAfter(prior.updatedAt)) {
        throw const FormatException('Allergy transition not confirmed.');
      }
      return result;
    }, patch: true, verifyReviewed: () => _verifyAllergy(review, prior));
  }
}
