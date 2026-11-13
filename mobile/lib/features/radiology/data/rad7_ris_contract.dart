/// RAD7 client-side RIS document contracts. Neither metadata nor a document
/// draft grants permission for diagnostic interpretation or DICOM retrieval.
class Rad7RisUnavailable implements Exception {
  const Rad7RisUnavailable();
  @override
  String toString() => 'Radiology request unavailable or not authorized.';
}

class Rad7RisUnconfirmed implements Exception {
  const Rad7RisUnconfirmed();
  @override
  String toString() =>
      'Outcome uncertain. Use authenticated readback; do not retry.';
}

int rad7Positive(Object? value, String label) {
  if (value is! int || value <= 0 || value > 2147483647) {
    throw FormatException('Invalid RIS $label.');
  }
  return value;
}

String rad7String(Object? value, String label, {int max = 128}) {
  if (value is! String ||
      value.isEmpty ||
      value.length > max ||
      value != value.trim()) {
    throw FormatException('Invalid RIS $label.');
  }
  return value;
}

Map<String, dynamic> rad7Envelope(Map<String, dynamic> response) {
  if (response['success'] != true ||
      response['ris_foundation_only'] != true ||
      response['mwl_published'] != false ||
      response['archive_verified'] != false ||
      response['diagnostic_report_signed'] != false ||
      response['released_to_chart'] != false ||
      response['critical_result_delivery_authorized'] != false) {
    throw const FormatException('RIS response lacks safe clinical boundaries.');
  }
  final data = response['data'];
  if (data is! Map<String, dynamic>) {
    throw const FormatException('RIS response missing data.');
  }
  for (final name in const <String>[
    'mwl_published',
    'archive_verified',
    'diagnostic_report_signed',
    'released_to_chart',
  ]) {
    if (data.containsKey(name) && data[name] != false) {
      throw const FormatException('Unexpected RIS clinical release status.');
    }
  }
  return data;
}

class Rad7Exam {
  const Rad7Exam({
    required this.examId,
    required this.orderId,
    required this.clinicId,
    required this.version,
    required this.status,
    required this.accession,
    required this.issuer,
  });
  final int examId;
  final int orderId;
  final int clinicId;
  final int version;
  final String status;
  final String accession;
  final String issuer;

  factory Rad7Exam.fromData(
    Map<String, dynamic> data, {
    required int clinicId,
  }) {
    if (data['worklist_published'] != false ||
        data['archive_verified'] != false ||
        data['diagnostic_report_signed'] != false) {
      throw const FormatException('RIS exam is not released.');
    }
    final parsedClinic = rad7Positive(data['clinic_id'], 'clinic');
    if (parsedClinic != clinicId) {
      throw const FormatException('RIS exam clinic mismatch.');
    }
    final status = rad7String(data['status'], 'status', max: 20);
    if (!const <String>{'planned', 'scheduled', 'cancelled'}.contains(status)) {
      throw const FormatException('Invalid RIS exam status.');
    }
    return Rad7Exam(
      examId: rad7Positive(data['exam_id'], 'exam'),
      orderId: rad7Positive(data['imaging_order_id'], 'order'),
      clinicId: parsedClinic,
      version: rad7Positive(data['version'], 'version'),
      status: status,
      accession: rad7String(data['accession_number'], 'accession', max: 48),
      issuer: rad7String(data['issuer_namespace'], 'issuer', max: 64),
    );
  }
}

class Rad7Worklist {
  const Rad7Worklist({
    required this.examId,
    required this.version,
    required this.steps,
  });
  final int examId;
  final int version;
  final List<String> steps;

  factory Rad7Worklist.fromData(
    Map<String, dynamic> data, {
    required int examId,
  }) {
    if (data['mwl_published'] != false ||
        data['archive_verified'] != false ||
        rad7Positive(data['exam_id'], 'exam') != examId) {
      throw const FormatException('Untrusted RIS worklist response.');
    }
    final raw = data['steps'];
    if (raw is! List<dynamic> || raw.length > 25) {
      throw const FormatException('Invalid RIS worklist steps.');
    }
    final result = <String>[];
    for (final row in raw) {
      if (row is! Map<String, dynamic>) {
        throw const FormatException('Invalid RIS worklist step.');
      }
      rad7Positive(row['step_id'], 'step');
      rad7Positive(row['sequence'], 'sequence');
      final status = rad7String(row['status'], 'step status', max: 20);
      if (!const <String>{
        'intent',
        'cancelled',
        'superseded',
      }.contains(status)) {
        throw const FormatException('Untrusted RIS worklist step status.');
      }
      final station = rad7String(row['station_code'], 'station', max: 64);
      final modality = rad7String(row['modality'], 'modality', max: 32);
      final at = rad7String(row['scheduled_at'], 'schedule', max: 50);
      if (!at.endsWith('Z') || DateTime.tryParse(at) == null) {
        throw const FormatException('Invalid RIS schedule timestamp.');
      }
      result.add('$status · $modality · $station · $at');
    }
    return Rad7Worklist(
      examId: examId,
      version: rad7Positive(data['exam_version'], 'version'),
      steps: List<String>.unmodifiable(result),
    );
  }
}

class Rad7Draft {
  const Rad7Draft({
    required this.reportId,
    required this.examId,
    required this.version,
    required this.content,
    required this.digest,
  });
  final int reportId;
  final int examId;
  final int version;
  final String content;
  final String digest;

  factory Rad7Draft.fromData(Map<String, dynamic> data, {required int examId}) {
    if (rad7Positive(data['exam_id'], 'exam') != examId ||
        data['status'] != 'private_draft' ||
        data['diagnostic_report_signed'] != false ||
        data['released_to_chart'] != false) {
      throw const FormatException('RIS report is not a private draft.');
    }
    final raw = data['versions'];
    final version = rad7Positive(data['version'], 'version');
    if (raw is! List<dynamic> ||
        raw.isEmpty ||
        raw.length > 50 ||
        raw.first is! Map<String, dynamic>) {
      throw const FormatException('RIS draft revision history invalid.');
    }
    final head = raw.first as Map<String, dynamic>;
    if (rad7Positive(head['revision'], 'revision') != version) {
      throw const FormatException('RIS draft is missing current revision.');
    }
    final content = rad7String(head['content'], 'draft content', max: 20000);
    final digest = rad7String(head['content_sha256'], 'digest', max: 64);
    if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(digest)) {
      throw const FormatException('RIS draft digest malformed.');
    }
    return Rad7Draft(
      reportId: rad7Positive(data['report_id'], 'report'),
      examId: examId,
      version: version,
      content: content,
      digest: digest,
    );
  }
}
