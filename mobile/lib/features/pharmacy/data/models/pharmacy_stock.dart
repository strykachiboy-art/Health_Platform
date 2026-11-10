/// P23H.S03A — server-owned pharmacy inventory, strictly read-only.
/// A drug may be clinic-local or global (null clinic_id). Batch and stock
/// records always belong to the authenticated clinic context.
class PharmacyDrug {
  const PharmacyDrug({
    required this.id,
    required this.clinicId,
    required this.name,
    required this.category,
    required this.strength,
    required this.isControlled,
    required this.isActive,
  });

  final int id;
  final int? clinicId;
  final String name;
  final String category;
  final String? strength;
  final bool isControlled;
  final bool isActive;

  factory PharmacyDrug.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    int? expectedId,
  }) {
    final id = _positive(json, 'id');
    final clinicId = _nullablePositive(json, 'clinic_id');
    if ((clinicId != null && clinicId != expectedClinicId) ||
        (expectedId != null && id != expectedId)) {
      throw const FormatException('Drug clinic or ID scope mismatch.');
    }
    final category = _requiredText(json, 'category');
    if (!const <String>{
      'antibiotic',
      'analgesic',
      'antiviral',
      'antihistamine',
      'vitamin',
      'controlled_substance',
      'other',
    }.contains(category)) {
      throw const FormatException('Unknown drug category.');
    }
    return PharmacyDrug(
      id: id,
      clinicId: clinicId,
      name: _requiredText(json, 'name'),
      category: category,
      strength: _optionalText(json, 'strength'),
      isControlled: _boolean(json, 'is_controlled'),
      isActive: _boolean(json, 'is_active'),
    );
  }
}

class PharmacyBatch {
  const PharmacyBatch({
    required this.id,
    required this.clinicId,
    required this.drugId,
    required this.batchNumber,
    required this.quantityOnHand,
    required this.reorderLevel,
    required this.expiryDate,
  });

  final int id;
  final int clinicId;
  final int drugId;
  final String batchNumber;
  final int quantityOnHand;
  final int reorderLevel;
  final String expiryDate;

  factory PharmacyBatch.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    required int expectedDrugId,
  }) {
    final clinicId = _positive(json, 'clinic_id');
    final drugId = _positive(json, 'drug_id');
    if (clinicId != expectedClinicId || drugId != expectedDrugId) {
      throw const FormatException('Drug batch scope mismatch.');
    }
    final expiry = _requiredText(json, 'expiry_date');
    if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(expiry) ||
        DateTime.tryParse(expiry)?.toIso8601String().substring(0, 10) !=
            expiry) {
      throw const FormatException('Invalid batch expiry date.');
    }
    return PharmacyBatch(
      id: _positive(json, 'id'),
      clinicId: clinicId,
      drugId: drugId,
      batchNumber: _requiredText(json, 'batch_number'),
      quantityOnHand: _nonnegative(json, 'quantity_on_hand'),
      reorderLevel: _nonnegative(json, 'reorder_level'),
      expiryDate: expiry,
    );
  }
}

class PharmacyStockSummary {
  const PharmacyStockSummary({
    required this.clinicId,
    required this.drugId,
    required this.drugName,
    required this.quantityOnHand,
    required this.batchCount,
  });

  final int clinicId;
  final int drugId;
  final String drugName;
  final int quantityOnHand;
  final int batchCount;

  factory PharmacyStockSummary.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    required int expectedDrugId,
  }) {
    final clinicId = _positive(json, 'clinic_id');
    final drugId = _positive(json, 'drug_id');
    if (clinicId != expectedClinicId || drugId != expectedDrugId) {
      throw const FormatException('Stock summary scope mismatch.');
    }
    return PharmacyStockSummary(
      clinicId: clinicId,
      drugId: drugId,
      drugName: _requiredText(json, 'drug_name'),
      quantityOnHand: _nonnegative(json, 'quantity_on_hand'),
      batchCount: _nonnegative(json, 'batch_count'),
    );
  }
}

class PharmacyPage<T> {
  const PharmacyPage({
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

  factory PharmacyPage.fromEnvelope(
    Map<String, dynamic> envelope, {
    required int requestedPage,
    required int requestedPerPage,
    required T Function(Map<String, dynamic>) parse,
    required int Function(T) idOf,
  }) {
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid pharmacy response envelope.');
    }
    final data = envelope['data'] as Map<String, dynamic>;
    final rawRows = data['items'];
    final total = data['total'];
    final page = data['page'];
    final perPage = data['per_page'];
    if (rawRows is! List ||
        total is! int ||
        total < 0 ||
        page != requestedPage ||
        perPage != requestedPerPage ||
        rawRows.length > perPage ||
        (requestedPage - 1) * requestedPerPage + rawRows.length > total) {
      throw const FormatException('Invalid pharmacy pagination.');
    }
    final seen = <int>{};
    final parsed = <T>[];
    for (final row in rawRows) {
      if (row is! Map<String, dynamic>) {
        throw const FormatException('Invalid pharmacy list row.');
      }
      final item = parse(row);
      if (!seen.add(idOf(item))) {
        throw const FormatException('Duplicate pharmacy identifier.');
      }
      parsed.add(item);
    }
    if (parsed.isEmpty && (requestedPage - 1) * requestedPerPage < total) {
      throw const FormatException('Incomplete pharmacy page.');
    }
    return PharmacyPage<T>(
      items: List<T>.unmodifiable(parsed),
      total: total,
      page: requestedPage,
      perPage: requestedPerPage,
    );
  }
}

class PharmacyStockSnapshot {
  const PharmacyStockSnapshot({
    required this.drug,
    required this.summary,
    required this.batches,
  });

  final PharmacyDrug drug;
  final PharmacyStockSummary summary;
  final PharmacyPage<PharmacyBatch> batches;
}

int _positive(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! int || value <= 0 || value > 2147483647) {
    throw FormatException('Invalid pharmacy identifier: $key');
  }
  return value;
}

int? _nullablePositive(Map<String, dynamic> json, String key) =>
    json[key] == null ? null : _positive(json, key);

int _nonnegative(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! int || value < 0) {
    throw FormatException('Invalid pharmacy quantity: $key');
  }
  return value;
}

bool _boolean(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! bool) {
    throw FormatException('Invalid pharmacy boolean: $key');
  }
  return value;
}

String _requiredText(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! String ||
      value.trim().isEmpty ||
      value != value.trim() ||
      value.length > 255) {
    throw FormatException('Invalid pharmacy text: $key');
  }
  return value;
}

String? _optionalText(Map<String, dynamic> json, String key) =>
    json[key] == null ? null : _requiredText(json, key);
