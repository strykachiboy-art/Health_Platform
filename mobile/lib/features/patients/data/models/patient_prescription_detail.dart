// P23H.S01 — strict, read-only backend-owned prescription detail fields.
// Legacy patient history remains governed by PatientPrescription.fromJson.
import 'patient_prescription.dart';

class PatientPrescriptionDetail {
  const PatientPrescriptionDetail({
    required this.prescription,
    required this.structuredItems,
  });

  final PatientPrescription prescription;
  final List<PatientPrescriptionStructuredItem> structuredItems;

  factory PatientPrescriptionDetail.fromEnvelope(
    Map<String, dynamic> envelope, {
    required int expectedClinicId,
    required int expectedPatientId,
    required int expectedPrescriptionId,
  }) {
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid prescription detail envelope.');
    }
    final raw = envelope['data'] as Map<String, dynamic>;
    final base = PatientPrescription.fromJson(
      raw,
      expectedClinicId: expectedClinicId,
      expectedPatientId: expectedPatientId,
    );
    if (base.id != expectedPrescriptionId) {
      throw const FormatException('Unexpected prescription identity.');
    }
    final rawItems = raw['items'] as List<dynamic>;
    final structured = <PatientPrescriptionStructuredItem>[];
    for (var i = 0; i < rawItems.length; i++) {
      final item = rawItems[i];
      if (item is! Map<String, dynamic>) {
        throw const FormatException('Invalid structured prescription item.');
      }
      structured.add(
        PatientPrescriptionStructuredItem.fromJson(
          item,
          expectedItem: base.items[i],
        ),
      );
    }
    return PatientPrescriptionDetail(
      prescription: base,
      structuredItems: List<PatientPrescriptionStructuredItem>.unmodifiable(
        structured,
      ),
    );
  }
}

class PatientPrescriptionStructuredItem {
  const PatientPrescriptionStructuredItem({
    required this.id,
    required this.drugId,
    required this.doseValue,
    required this.doseUnit,
    required this.routeCode,
    required this.routeDisplay,
    required this.frequencyCode,
    required this.frequencyInterval,
    required this.frequencyIntervalUnit,
    required this.scheduledTimes,
    required this.startAt,
    required this.endAt,
    required this.isPrn,
    required this.prnReason,
    required this.maxSingleDose,
    required this.maxDailyDose,
    required this.administrationInstructions,
    required this.priority,
    required this.orderType,
    required this.orderedAt,
  });

  final int id;
  final int drugId;
  final double? doseValue;
  final String? doseUnit;
  final String? routeCode;
  final String? routeDisplay;
  final String? frequencyCode;
  final int? frequencyInterval;
  final String? frequencyIntervalUnit;
  final List<String> scheduledTimes;
  final DateTime? startAt;
  final DateTime? endAt;
  final bool isPrn;
  final String? prnReason;
  final double? maxSingleDose;
  final double? maxDailyDose;
  final String? administrationInstructions;
  final String priority;
  final String orderType;
  final DateTime? orderedAt;

  static double? _positiveNumber(Map<String, dynamic> json, String key) {
    final raw = json[key];
    if (raw == null) return null;
    if (raw is! num || !raw.isFinite || raw <= 0) {
      throw FormatException('Invalid prescription numeric field: $key');
    }
    return raw.toDouble();
  }

  static String? _text(Map<String, dynamic> json, String key, int maxLength) {
    final raw = json[key];
    if (raw == null) return null;
    if (raw is! String ||
        raw.isEmpty ||
        raw != raw.trim() ||
        raw.length > maxLength) {
      throw FormatException('Invalid prescription text field: $key');
    }
    return raw;
  }

  static int? _interval(Map<String, dynamic> json) {
    final raw = json['frequency_interval'];
    if (raw == null) return null;
    if (raw is! int || raw <= 0) {
      throw const FormatException('Invalid prescription frequency interval.');
    }
    return raw;
  }

  static DateTime? _awareTime(Map<String, dynamic> json, String key) {
    final raw = json[key];
    if (raw == null) return null;
    if (raw is! String ||
        !RegExp(r'(Z|[+-][0-9]{2}:[0-9]{2})$').hasMatch(raw)) {
      throw FormatException('Untrusted prescription timestamp: $key');
    }
    final parsed = DateTime.tryParse(raw);
    if (parsed == null) {
      throw FormatException('Invalid prescription timestamp: $key');
    }
    return parsed.toUtc();
  }

  factory PatientPrescriptionStructuredItem.fromJson(
    Map<String, dynamic> json, {
    required PatientPrescriptionItem expectedItem,
  }) {
    if (json['id'] != expectedItem.id ||
        json['drug_id'] != expectedItem.drugId ||
        json['prescription_id'] != expectedItem.prescriptionId) {
      throw const FormatException(
        'Structured prescription item identity changed.',
      );
    }
    final priority = json['order_priority'];
    final orderType = json['order_type'];
    if (priority is! String ||
        !const <String>{'routine', 'urgent', 'stat'}.contains(priority) ||
        orderType is! String ||
        !const <String>{
          'inpatient',
          'outpatient',
          'discharge',
        }.contains(orderType)) {
      throw const FormatException('Unknown medication priority or order type.');
    }
    final isPrn = json['is_prn'];
    if (isPrn is! bool) {
      throw const FormatException('Missing or invalid PRN status.');
    }
    final unit = _text(json, 'frequency_interval_unit', 16);
    if (unit != null &&
        !const <String>{'minute', 'hour', 'day', 'week'}.contains(unit)) {
      throw const FormatException('Invalid frequency interval unit.');
    }
    final interval = _interval(json);
    if ((interval == null) != (unit == null)) {
      throw const FormatException('Incomplete medication frequency interval.');
    }
    final times = json['scheduled_times'];
    if (times is! List || times.length > 24) {
      throw const FormatException('Invalid scheduled medication times.');
    }
    final schedule = <String>[];
    for (final time in times) {
      if (time is! String ||
          !RegExp(r'^([01][0-9]|2[0-3]):[0-5][0-9]$').hasMatch(time) ||
          schedule.contains(time)) {
        throw const FormatException('Invalid scheduled medication clock time.');
      }
      schedule.add(time);
    }
    final start = _awareTime(json, 'start_at');
    final end = _awareTime(json, 'end_at');
    if (start != null && end != null && !end.isAfter(start)) {
      throw const FormatException('Medication start must precede end.');
    }
    final prnReason = _text(json, 'prn_reason', 500);
    if (isPrn && prnReason == null) {
      throw const FormatException('PRN medication requires indication.');
    }
    if (!isPrn && prnReason != null) {
      throw const FormatException('Non-PRN medication has PRN indication.');
    }
    final single = _positiveNumber(json, 'max_single_dose');
    final daily = _positiveNumber(json, 'max_daily_dose');
    return PatientPrescriptionStructuredItem(
      id: expectedItem.id,
      drugId: expectedItem.drugId,
      doseValue: _positiveNumber(json, 'dose_value'),
      doseUnit: _text(json, 'dose_unit', 32),
      routeCode: _text(json, 'route_code', 64),
      routeDisplay: _text(json, 'route_display', 128),
      frequencyCode: _text(json, 'frequency_code', 64),
      frequencyInterval: interval,
      frequencyIntervalUnit: unit,
      scheduledTimes: List<String>.unmodifiable(schedule),
      startAt: start,
      endAt: end,
      isPrn: isPrn,
      prnReason: prnReason,
      maxSingleDose: single,
      maxDailyDose: daily,
      administrationInstructions: _text(
        json,
        'administration_instructions',
        1000,
      ),
      priority: priority,
      orderType: orderType,
      orderedAt: _awareTime(json, 'ordered_at'),
    );
  }
}
