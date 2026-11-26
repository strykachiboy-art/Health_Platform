part of 'doctor_observation_source.dart';

/// These operations reuse the patient review and session fence. They do not
/// infer discharge clearance or modify a clinical record outside its own API.
class DoctorDischargeReview {
  const DoctorDischargeReview._(this.admission, this.reconciliation, this.summary);
  final PatientAdmission admission;
  final MedicationReconciliationRecord? reconciliation;
  final Map<String, dynamic>? summary;
}

extension DoctorClinicalRecords on DoctorObservationSource {
  PatientAdmissionRemoteDataSource get _admissions => PatientAdmissionRemoteDataSource(
    apiClient: reader.apiClient, loadSession: reader.loadSession,
    sessionNavigationGuard: reader.sessionNavigationGuard);
  MedicationReconciliationReadRemoteDataSource get _reconciliations => MedicationReconciliationReadRemoteDataSource(
    apiClient: reader.apiClient, loadSession: reader.loadSession,
    sessionNavigationGuard: reader.sessionNavigationGuard);
  EmarReadRemoteDataSource get _emar => EmarReadRemoteDataSource(
    apiClient: reader.apiClient, loadSession: reader.loadSession,
    sessionNavigationGuard: reader.sessionNavigationGuard);

  Future<Map<String, dynamic>> _clinicalGet(DoctorObservationReview review, String path) async {
    await _check(review._actor, review.revision);
    final raw = await reader.apiClient.getJson(path, bearerToken: review._actor.accessToken);
    await _check(review._actor, review.revision);
    return raw;
  }

  Future<PatientAdmissionPage> admissions(DoctorObservationReview review, {int page = 1}) async {
    await _check(review._actor, review.revision);
    final result = await _admissions.fetchAdmissionHistory(patientId: review.patientId, page: page, perPage: 20);
    await _check(review._actor, review.revision);
    return result;
  }

  Future<PatientAdmission> _admission(DoctorObservationReview review, int id) async {
    if (id <= 0) {
      throw const FormatException('Invalid admission.');
    }
    final row = PatientAdmission.fromJson(await _clinicalGet(review, 'api/v1/wards/admissions/$id'), expectedPatientId: review.patientId);
    if (row.id != id) {
      throw const FormatException('Admission identity mismatch.');
    }
    return row;
  }

  static Map<String, dynamic> _object(Object? raw) {
    if (raw is! Map<String, dynamic>) {
      throw const FormatException('Expected clinical object.');
    }
    return raw;
  }

  static int _positive(Object? value) {
    if (value is! int || value <= 0) {
      throw const FormatException('Invalid record identifier.');
    }
    return value;
  }

  static const dischargeFields = <String, int>{'primary_diagnosis': 2000,
    'condition_at_discharge': 5000, 'instructions': 20000, 'follow_up_plan': 20000,
    'destination': 255};

  static Map<String, dynamic> dischargeDocument(Map<String, String> input, String secondary) {
    if (input.keys.toSet().difference(dischargeFields.keys.toSet()).isNotEmpty) {
      throw const FormatException('Unknown discharge field.');
    }
    final result = <String, dynamic>{};
    for (final field in dischargeFields.entries) {
      final value = input[field.key]?.trim() ?? '';
      if (value.isEmpty || value.length > field.value) {
        throw const FormatException('Complete the required discharge documentation.');
      }
      result[field.key] = value;
    }
    final diagnoses = secondary.split('\n').map((x) => x.trim()).where((x) => x.isNotEmpty).toList();
    if (diagnoses.length > 100 || diagnoses.any((x) => x.length > 2000)) {
      throw const FormatException('Invalid secondary diagnosis list.');
    }
    result['secondary_diagnoses'] = diagnoses;
    return result;
  }

  static Map<String, dynamic> _summary(DoctorObservationReview review, int admission, Object? raw) {
    final row = _object(raw);
    _positive(row['id']);
    _positive(row['medication_reconciliation_id']);
    _positive(row['discharged_by_staff_id']);
    final version = _positive(row['record_version']);
    if (row['clinic_id'] != review.clinicId || row['patient_id'] != review.patientId ||
        row['admission_id'] != admission || !const {'final', 'amended'}.contains(row['record_status']) ||
        (row['record_status'] == 'final' && row['latest_amendment_id'] != null) ||
        (row['record_status'] == 'amended' && (row['latest_amendment_id'] is! int || version < 2)) ||
        row['final_medication_list'] is! List || (row['final_medication_list'] as List).any((x) => x is! Map<String, dynamic>) ||
        row['secondary_diagnoses'] is! List || (row['secondary_diagnoses'] as List).any((x) => x is! String)) {
      throw const FormatException('Discharge summary identity or status mismatch.');
    }
    for (final key in dischargeFields.keys) {
      if (row[key] is! String || (row[key] as String).trim().isEmpty) {
        throw const FormatException('Missing discharge documentation.');
      }
    }
    return Map.unmodifiable(row);
  }

  Future<DoctorDischargeReview> inspectDischarge(DoctorObservationReview review, int id) async {
    final admission = await _admission(review, id);
    if (admission.status == PatientAdmissionStatus.admitted) {
      MedicationReconciliationRecord? rec;
      try {
        rec = await _reconciliations.getForEvent(admissionId: id,
          event: MedicationReconciliationEvent.discharge, expectedPatientId: review.patientId);
      } on ApiException catch (error) {
        if (error.kind != ApiFailureKind.httpStatus || error.statusCode != 404) {
          rethrow;
        }
      }
      await _check(review._actor, review.revision);
      return DoctorDischargeReview._(admission, rec, null);
    }
    if (admission.status != PatientAdmissionStatus.discharged) {
      return DoctorDischargeReview._(admission, null, null);
    }
    final data = await _clinicalGet(review, 'api/v1/wards/admissions/$id/discharge-summary');
    return DoctorDischargeReview._(admission, null, _summary(review, id, data['discharge_summary']));
  }

  bool canSubmitDischarge(DoctorDischargeReview review) => review.admission.isActive &&
      review.reconciliation != null && review.reconciliation!.status != MedicationReconciliationRecordStatus.draft;

  Future<T> _clinicalMutation<T>(DoctorObservationReview review, String scope, String intent,
      String path, Map<String, dynamic> body, T Function(Map<String, dynamic>) parse,
      {bool idempotent = false}) async {
    if (!RegExp(r'^[a-zA-Z0-9_-]{8,100}$').hasMatch(intent)) {
      throw const FormatException('Invalid clinical intent.');
    }
    await _check(review._actor, review.revision);
    if (await connectivity.checkStatus() != DeviceNetworkStatus.connected) {
      throw const DoctorObservationUnavailable();
    }
    await _check(review._actor, review.revision);
    final actor = review._actor;
    final key = 'doctor-clinical:${actor.userId}:${actor.tenantId}:${review.clinicId}:${review.patientId}:$scope';
    if (gate.contains('$key:uncertain') || _inFlight.contains(key) || !gate.claim('$key:$intent')) {
      throw const DoctorObservationUnconfirmed();
    }
    _inFlight.add(key);
    try {
      final raw = await reader.apiClient.postJson(path, body: body,
        bearerToken: actor.accessToken, idempotencyKey: idempotent ? intent : null);
      await _check(actor, review.revision);
      return parse(raw);
    } on ApiException catch (error) {
      if (error.kind == ApiFailureKind.httpStatus && error.statusCode == 409) {
        await _check(actor, review.revision);
        throw const DoctorObservationConflict();
      }
      gate.claim('$key:uncertain');
      throw const DoctorObservationUnconfirmed();
    } on Object {
      gate.claim('$key:uncertain');
      throw const DoctorObservationUnconfirmed();
    } finally {
      _inFlight.remove(key);
    }
  }

  Future<Map<String, dynamic>> discharge(DoctorObservationReview patient, DoctorDischargeReview prior,
      {required String intent, required Map<String, String> fields, required String secondary}) async {
    final body = dischargeDocument(fields, secondary);
    final id = prior.admission.id;
    await _preflight(patient);
    final fresh = await inspectDischarge(patient, id);
    final current = await _admissions.fetchCurrentAdmission(patientId: patient.patientId);
    if (!canSubmitDischarge(prior) || !canSubmitDischarge(fresh) || current?.id != id ||
        prior.admission.updatedAt != fresh.admission.updatedAt || prior.admission.bedId != fresh.admission.bedId ||
        prior.reconciliation!.id != fresh.reconciliation!.id || prior.reconciliation!.version != fresh.reconciliation!.version) {
      throw const DoctorObservationConflict();
    }
    return _clinicalMutation(patient, 'discharge:$id', intent, 'api/v1/wards/admissions/$id/discharge', body, (raw) {
      final admission = PatientAdmission.fromJson(_object(raw['admission']), expectedPatientId: patient.patientId);
      final summary = _summary(patient, id, raw['discharge_summary']);
      if (admission.id != id || admission.status != PatientAdmissionStatus.discharged ||
          admission.dischargedAt == null || summary['medication_reconciliation_id'] != fresh.reconciliation!.id ||
          !_documentMatches(summary, body)) {
        throw const FormatException('Discharge not confirmed.');
      }
      return summary;
    }, idempotent: true);
  }

  static bool _documentMatches(Map<String, dynamic> summary, Map<String, dynamic> body) =>
      body.entries.every((e) => e.key == 'secondary_diagnoses'
        ? jsonEncode(summary[e.key]) == jsonEncode(e.value) : summary[e.key] == e.value);

  Future<Map<String, dynamic>> amendDischarge(DoctorObservationReview patient, DoctorDischargeReview prior,
      {required String intent, required Map<String, String> fields, required String secondary, required String reason}) async {
    final document = dischargeDocument(fields, secondary);
    final note = reason.trim();
    if (note.isEmpty || note.length > 10000 || prior.summary == null) {
      throw const FormatException('A reviewed summary and amendment reason are required.');
    }
    await _preflight(patient);
    final id = prior.admission.id;
    final fresh = await inspectDischarge(patient, id);
    final old = prior.summary!;
    final version = _positive(old['record_version']);
    if (fresh.summary?['id'] != old['id'] || fresh.summary?['record_version'] != version) {
      throw const DoctorObservationConflict();
    }
    return _clinicalMutation(patient, 'discharge:$id', intent,
      'api/v1/wards/admissions/$id/discharge-summary/amend',
      {'expected_version': version, 'reason': note, 'document': document}, (raw) {
      final summary = _summary(patient, id, raw['discharge_summary']);
      final amendment = _object(raw['amendment']);
      _validateAmendment(amendment, patient, 'discharge_summary', _positive(old['id']));
      if (summary['id'] != old['id'] || summary['record_version'] != version + 1 ||
          summary['record_status'] != 'amended' || summary['latest_amendment_id'] != amendment['id'] ||
          amendment['previous_version'] != version || amendment['new_version'] != version + 1 ||
          !_documentMatches(summary, document)) {
        throw const FormatException('Discharge amendment not confirmed.');
      }
      return summary;
    });
  }

  static void _validateAmendment(Map<String, dynamic> row, DoctorObservationReview patient, String type, int id) {
    _positive(row['id']);
    _positive(row['amended_by_staff_id']);
    final prev = _positive(row['previous_version']);
    final next = _positive(row['new_version']);
    if (row['clinic_id'] != patient.clinicId || row['patient_id'] != patient.patientId ||
        row['source_record_id'] != id || row['source_record_type'] != type || next != prev + 1 ||
        row['reason'] is! String || (row['reason'] as String).trim().isEmpty ||
        row['new_content'] is! Map<String, dynamic> || row['new_content_sha256'] is! String ||
        !RegExp(r'^[a-fA-F0-9]{64}$').hasMatch(row['new_content_sha256'] as String) ||
        row['amended_at'] is! String || DateTime.tryParse(row['amended_at'] as String) == null) {
      throw const FormatException('Clinical amendment lineage mismatch.');
    }
  }

  Future<List<Map<String, dynamic>>> _history(DoctorObservationReview patient, String path, String type,
      int id, int version, {int? latest}) async {
    final data = await _clinicalGet(patient, path);
    final raw = data['items'];
    if (raw is! List || data['total'] != raw.length) {
      throw const FormatException('Unconfirmed history envelope.');
    }
    final rows = <Map<String, dynamic>>[];
    final ids = <int>{};
    for (final value in raw) {
      final row = _object(value);
      _validateAmendment(row, patient, type, id);
      if (!ids.add(row['id'] as int)) {
        throw const FormatException('Duplicate amendment.');
      }
      rows.add(Map.unmodifiable(row));
    }
    rows.sort((a, b) => (a['new_version'] as int).compareTo(b['new_version'] as int));
    for (var i = 1; i < rows.length; i++) {
      if (rows[i]['previous_version'] != rows[i - 1]['new_version'] || rows[i]['previous_amendment_id'] != rows[i - 1]['id']) {
        throw const FormatException('Broken amendment chain.');
      }
    }
    if ((latest != null && (rows.isEmpty || rows.last['id'] != latest)) ||
        (version > 1 && rows.isEmpty) ||
        (rows.isNotEmpty && rows.last['new_version'] != version)) {
      throw const FormatException('History changed; reload the current record.');
    }
    return List.unmodifiable(rows);
  }

  Future<List<Map<String, dynamic>>> dischargeHistory(DoctorObservationReview patient, DoctorDischargeReview prior) async {
    final fresh = await inspectDischarge(patient, prior.admission.id);
    final row = fresh.summary;
    if (row == null || row['id'] != prior.summary?['id'] || row['record_version'] != prior.summary?['record_version']) {
      throw const DoctorObservationConflict();
    }
    return _history(patient, 'api/v1/wards/admissions/${prior.admission.id}/discharge-summary/amendments',
      'discharge_summary', _positive(row['id']), _positive(row['record_version']), latest: row['latest_amendment_id'] as int?);
  }

  Future<EmarAdministrationPage> emarRecords(DoctorObservationReview patient, PatientAdmission selected, {int page = 1}) async {
    await _admission(patient, selected.id);
    final result = await _emar.listForAdmission(admissionId: selected.id, expectedPatientId: patient.patientId, page: page, perPage: 20);
    await _check(patient._actor, patient.revision);
    return result;
  }

  Future<MedicationReconciliationPage> reconciliationRecords(DoctorObservationReview patient, PatientAdmission selected, {int page = 1}) async {
    await _admission(patient, selected.id);
    final result = await _reconciliations.listForAdmission(admissionId: selected.id, expectedPatientId: patient.patientId, page: page, perPage: 20);
    await _check(patient._actor, patient.revision);
    return result;
  }

  Future<List<Map<String, dynamic>>> emarHistory(DoctorObservationReview patient, PatientAdmission admission, EmarAdministration selected) async {
    await _admission(patient, admission.id);
    final row = await _emar.getAdministration(administrationId: selected.id, expectedPatientId: patient.patientId, expectedAdmissionId: admission.id);
    if (row.recordVersion != selected.recordVersion) {
      throw const DoctorObservationConflict();
    }
    return _history(patient, 'api/v1/emar/${row.id}/amendments', 'medication_administration', row.id, row.recordVersion);
  }

  Future<List<Map<String, dynamic>>> reconciliationHistory(DoctorObservationReview patient, PatientAdmission admission, MedicationReconciliationRecord selected) async {
    await _admission(patient, admission.id);
    final row = await _reconciliations.getById(reconciliationId: selected.id, expectedPatientId: patient.patientId, expectedAdmissionId: admission.id);
    if (row.version != selected.version) {
      throw const DoctorObservationConflict();
    }
    return _history(patient, 'api/v1/medication-reconciliations/${row.id}/amendments', 'medication_reconciliation', row.id, row.version, latest: row.latestAmendmentId);
  }

  Future<List<ImagingProcedureOrder>> surgicalOrders(DoctorObservationReview patient, PatientAdmission admission) async {
    final current = await _admission(patient, admission.id);
    if (!current.isActive) {
      throw const DoctorObservationConflict();
    }
    final list = ImagingProcedureOrderList.fromJson(await _clinicalGet(patient,
      'api/v1/clinical-orders/procedures?admission_id=${admission.id}'), kind: 'procedures',
      expectedClinicId: patient.clinicId, expectedPatientId: patient.patientId, expectedAdmissionId: admission.id);
    return List.unmodifiable(list.items.where((order) => order.category == 'surgical' && order.status == 'active' && order.consentRequired));
  }

  Future<int> planSurgicalCase(DoctorObservationReview patient, PatientAdmission admission,
      ImagingProcedureOrder prior, {required String intent}) async {
    await _preflight(patient);
    final current = await _admission(patient, admission.id);
    if (!current.isActive || prior.patientId != patient.patientId || prior.clinicId != patient.clinicId || prior.admissionId != admission.id) {
      throw const DoctorObservationConflict();
    }
    final fresh = ImagingProcedureOrder.fromJson(await _clinicalGet(patient, 'api/v1/clinical-orders/procedures/${prior.id}'),
      kind: 'procedures', expectedClinicId: patient.clinicId, expectedPatientId: patient.patientId,
      expectedAdmissionId: admission.id, expectedId: prior.id);
    if (fresh.version != prior.version || fresh.category != 'surgical' || fresh.status != 'active' || !fresh.consentRequired) {
      throw const DoctorObservationConflict();
    }
    return _clinicalMutation(patient, 'surgery-plan:${prior.id}', intent, 'api/v1/surgery/cases',
      {'procedure_order_id': prior.id}, (raw) {
        final row = DoctorObservationSource._data(raw);
        if (raw['documentation_only'] != true ||
            ['operative_completion_authorized', 'anesthesia_induction_authorized', 'medication_administration_authorized',
              'nursing_transfer_authorized', 'billing_clearance_authorized'].any((field) => raw[field] != false) ||
            row['clinic_id'] != patient.clinicId || row['procedure_order_id'] != prior.id || row['status'] != 'planned' || row['version'] != 1) {
          throw const FormatException('Inert case planning receipt not confirmed.');
        }
        return _positive(row['id']);
      });
  }
}
