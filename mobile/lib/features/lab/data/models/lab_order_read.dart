// P23H.S04A: server-owned laboratory catalog, patient orders and result flags.
// No local result interpretation or critical-result acknowledgement.

enum LabOrderStatus {
  ordered,
  sampleCollected,
  inProgress,
  completed,
  cancelled,
}

enum LabResultFlag { normal, abnormal, critical }

class LabCatalogTest {
  const LabCatalogTest({
    required this.id,
    required this.clinicId,
    required this.name,
    required this.sampleType,
    required this.referenceRange,
    required this.unit,
    required this.criticalLow,
    required this.criticalHigh,
    required this.isActive,
  });

  final int id;
  final int clinicId;
  final String name;
  final String sampleType;
  final String? referenceRange;
  final String? unit;
  final String? criticalLow;
  final String? criticalHigh;
  final bool isActive;

  factory LabCatalogTest.fromJson(
    Map<String, dynamic> value, {
    required int expectedClinicId,
    int? expectedId,
  }) {
    final id = _positive(value, 'id');
    final clinicId = _positive(value, 'clinic_id');
    if (clinicId != expectedClinicId ||
        (expectedId != null && id != expectedId)) {
      throw const FormatException('Lab test clinic or ID mismatch.');
    }
    final sample = _requiredText(value, 'sample_type');
    if (!const <String>{
      'blood',
      'urine',
      'stool',
      'saliva',
      'tissue',
      'swab',
      'other',
    }.contains(sample)) {
      throw const FormatException('Unrecognized lab sample type.');
    }
    final active = value['is_active'];
    if (active is! bool) {
      throw const FormatException('Invalid lab test active status.');
    }
    return LabCatalogTest(
      id: id,
      clinicId: clinicId,
      name: _requiredText(value, 'name'),
      sampleType: sample,
      referenceRange: _optionalText(value, 'reference_range'),
      unit: _optionalText(value, 'unit'),
      criticalLow: _optionalText(value, 'critical_low'),
      criticalHigh: _optionalText(value, 'critical_high'),
      isActive: active,
    );
  }
}

class LabOrderResultItem {
  const LabOrderResultItem({
    required this.id,
    required this.orderId,
    required this.testId,
    required this.test,
    required this.resultValue,
    required this.flag,
    required this.resultNotes,
  });

  final int id;
  final int orderId;
  final int testId;
  final LabCatalogTest? test;
  final String? resultValue;
  final LabResultFlag? flag;
  final String? resultNotes;

  factory LabOrderResultItem.fromJson(
    Map<String, dynamic> value, {
    required int expectedOrderId,
    required int expectedClinicId,
  }) {
    final orderId = _positive(value, 'order_id');
    if (orderId != expectedOrderId) {
      throw const FormatException('Lab item belongs to another order.');
    }
    final testId = _positive(value, 'test_id');
    final rawTest = value['test'];
    if (rawTest != null && rawTest is! Map<String, dynamic>) {
      throw const FormatException('Invalid lab order test object.');
    }
    final test = rawTest == null
        ? null
        : LabCatalogTest.fromJson(
            rawTest,
            expectedClinicId: expectedClinicId,
            expectedId: testId,
          );
    final flag = switch (value['flag']) {
      null => null,
      'normal' => LabResultFlag.normal,
      'abnormal' => LabResultFlag.abnormal,
      'critical' => LabResultFlag.critical,
      _ => throw const FormatException('Unknown laboratory result flag.'),
    };
    final resultValue = _optionalText(value, 'result_value');
    if (flag != null && resultValue == null) {
      throw const FormatException('Flagged laboratory item lacks result.');
    }
    return LabOrderResultItem(
      id: _positive(value, 'id'),
      orderId: orderId,
      testId: testId,
      test: test,
      resultValue: resultValue,
      flag: flag,
      resultNotes: _optionalText(value, 'result_notes'),
    );
  }
}

class LabPatientOrder {
  const LabPatientOrder({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.consultationId,
    required this.orderedById,
    required this.status,
    required this.createdAt,
    required this.items,
  });

  final int id;
  final int clinicId;
  final int patientId;
  final int? consultationId;
  final int orderedById;
  final LabOrderStatus status;
  final String createdAt;
  final List<LabOrderResultItem> items;

  factory LabPatientOrder.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    required int expectedPatientId,
    int? expectedId,
  }) {
    final id = _positive(json, 'id');
    final clinicId = _positive(json, 'clinic_id');
    final patientId = _positive(json, 'patient_id');
    if (clinicId != expectedClinicId ||
        patientId != expectedPatientId ||
        (expectedId != null && id != expectedId)) {
      throw const FormatException('Lab order patient, clinic, or ID mismatch.');
    }
    final status = switch (json['status']) {
      'ordered' => LabOrderStatus.ordered,
      'sample_collected' => LabOrderStatus.sampleCollected,
      'in_progress' => LabOrderStatus.inProgress,
      'completed' => LabOrderStatus.completed,
      'cancelled' => LabOrderStatus.cancelled,
      _ => throw const FormatException('Unknown laboratory order status.'),
    };
    final rawItems = json['items'];
    if (rawItems is! List || rawItems.isEmpty) {
      throw const FormatException('Invalid laboratory order items.');
    }
    final seen = <int>{};
    final items = <LabOrderResultItem>[];
    for (final raw in rawItems) {
      if (raw is! Map<String, dynamic>) {
        throw const FormatException('Invalid laboratory order item.');
      }
      final item = LabOrderResultItem.fromJson(
        raw,
        expectedOrderId: id,
        expectedClinicId: expectedClinicId,
      );
      if (!seen.add(item.id)) {
        throw const FormatException('Duplicate laboratory result item.');
      }
      items.add(item);
    }
    return LabPatientOrder(
      id: id,
      clinicId: clinicId,
      patientId: patientId,
      consultationId: _nullablePositive(json, 'consultation_id'),
      orderedById: _positive(json, 'ordered_by_id'),
      status: status,
      createdAt: _timestamp(json, 'created_at'),
      items: List<LabOrderResultItem>.unmodifiable(items),
    );
  }
}

class LabReadPage<T> {
  const LabReadPage({
    required this.items,
    required this.total,
    required this.page,
    required this.perPage,
  });

  final List<T> items;
  final int total;
  final int page;
  final int perPage;

  bool get hasNext => page * perPage < total;

  factory LabReadPage.fromEnvelope(
    Map<String, dynamic> envelope, {
    required int requestedPage,
    required int requestedPerPage,
    required T Function(Map<String, dynamic>) parse,
    required int Function(T) idOf,
  }) {
    final raw = envelope['data'];
    if (envelope['success'] != true || raw is! Map<String, dynamic>) {
      throw const FormatException('Invalid laboratory page envelope.');
    }
    final items = raw['items'];
    final total = raw['total'];
    if (items is! List ||
        total is! int ||
        total < 0 ||
        raw['page'] != requestedPage ||
        raw['per_page'] != requestedPerPage ||
        items.length > requestedPerPage ||
        (requestedPage - 1) * requestedPerPage + items.length > total ||
        (items.isEmpty && (requestedPage - 1) * requestedPerPage < total)) {
      throw const FormatException('Invalid laboratory pagination.');
    }
    final parsed = <T>[];
    final ids = <int>{};
    for (final row in items) {
      if (row is! Map<String, dynamic>) {
        throw const FormatException('Invalid laboratory page item.');
      }
      final result = parse(row);
      if (!ids.add(idOf(result))) {
        throw const FormatException('Duplicate laboratory page ID.');
      }
      parsed.add(result);
    }
    return LabReadPage<T>(
      items: List<T>.unmodifiable(parsed),
      total: total,
      page: requestedPage,
      perPage: requestedPerPage,
    );
  }
}

int _positive(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! int || value <= 0 || value > 2147483647) {
    throw FormatException('Invalid laboratory ID: $key');
  }
  return value;
}

int? _nullablePositive(Map<String, dynamic> json, String key) =>
    json[key] == null ? null : _positive(json, key);

String _requiredText(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! String ||
      value.trim().isEmpty ||
      value != value.trim() ||
      value.length > 2000) {
    throw FormatException('Invalid laboratory text: $key');
  }
  return value;
}

String? _optionalText(Map<String, dynamic> json, String key) =>
    json[key] == null ? null : _requiredText(json, key);

String _timestamp(Map<String, dynamic> json, String key) {
  final value = _requiredText(json, key);
  if (DateTime.tryParse(value) == null ||
      !RegExp(
        r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$',
      ).hasMatch(value)) {
    throw FormatException('Invalid laboratory timestamp: $key');
  }
  return value;
}
