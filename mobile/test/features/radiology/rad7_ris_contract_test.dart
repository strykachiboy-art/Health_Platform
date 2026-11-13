import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/radiology/data/rad7_ris_contract.dart';

Map<String, dynamic> safe(Map<String, dynamic> data) => <String, dynamic>{
  'success': true,
  'ris_foundation_only': true,
  'mwl_published': false,
  'archive_verified': false,
  'diagnostic_report_signed': false,
  'released_to_chart': false,
  'critical_result_delivery_authorized': false,
  'data': data,
};

Map<String, dynamic> exam({int clinic = 2}) => <String, dynamic>{
  'exam_id': 12,
  'imaging_order_id': 8,
  'clinic_id': clinic,
  'status': 'planned',
  'version': 1,
  'accession_number': 'RAD-ABC123',
  'issuer_namespace': 'CSP-CLINIC-$clinic',
  'worklist_published': false,
  'archive_verified': false,
  'diagnostic_report_signed': false,
};

void main() {
  test('RAD7 accepts clinic-pinned unreleased RIS exam', () {
    final parsed = Rad7Exam.fromData(rad7Envelope(safe(exam())), clinicId: 2);
    expect(parsed.examId, 12);
    expect(parsed.orderId, 8);
    expect(parsed.status, 'planned');
  });
  test('RAD7 refuses cross-clinic exam', () {
    expect(
      () => Rad7Exam.fromData(exam(clinic: 3), clinicId: 2),
      throwsFormatException,
    );
  });
  test('RAD7 never trusts missing clinical safety flags', () {
    final envelope = safe(exam())..remove('archive_verified');
    expect(() => rad7Envelope(envelope), throwsFormatException);
  });
  test('RAD7 refuses release or sign assertion', () {
    final envelope = safe(exam())..['released_to_chart'] = true;
    expect(() => rad7Envelope(envelope), throwsFormatException);
    final unsigned = exam()..['diagnostic_report_signed'] = true;
    expect(
      () => Rad7Exam.fromData(unsigned, clinicId: 2),
      throwsFormatException,
    );
  });
  test('RAD7 refuses noncanonical IDs and bools', () {
    expect(() => rad7Positive(true, 'id'), throwsFormatException);
    expect(() => rad7Positive(-1, 'id'), throwsFormatException);
    expect(rad7Positive(1, 'id'), 1);
  });
  test('RAD7 worklist is an unpublished intent', () {
    final data = <String, dynamic>{
      'exam_id': 12,
      'exam_version': 2,
      'mwl_published': false,
      'archive_verified': false,
      'steps': <Map<String, dynamic>>[
        {
          'step_id': 1,
          'sequence': 1,
          'status': 'intent',
          'station_code': 'ST-1',
          'modality': 'CT',
          'scheduled_at': '2026-12-01T09:00:00Z',
        },
      ],
    };
    expect(Rad7Worklist.fromData(data, examId: 12).steps, hasLength(1));
    expect(
      () => Rad7Worklist.fromData({...data, 'mwl_published': true}, examId: 12),
      throwsFormatException,
    );
  });
  test('RAD7 history accepts only private unsigned head revision', () {
    final data = <String, dynamic>{
      'exam_id': 12,
      'report_id': 14,
      'status': 'private_draft',
      'version': 2,
      'diagnostic_report_signed': false,
      'released_to_chart': false,
      'versions': <Map<String, dynamic>>[
        {
          'revision': 2,
          'content': 'Private note',
          'content_sha256': List<String>.filled(64, 'a').join(),
        },
      ],
    };
    expect(Rad7Draft.fromData(data, examId: 12).version, 2);
    expect(
      () =>
          Rad7Draft.fromData({...data, 'released_to_chart': true}, examId: 12),
      throwsFormatException,
    );
    expect(
      () => Rad7Draft.fromData({...data, 'version': 3}, examId: 12),
      throwsFormatException,
    );
  });
}
