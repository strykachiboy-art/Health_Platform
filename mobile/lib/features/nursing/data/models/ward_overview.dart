// P23E.B: strictly parsed, server-owned ward and bed readback.
// No PHI persistence, no client-side bed status decisions.

enum WardKind { general, icu, maternity, pediatric, isolation }

enum WardBedStatus { available, occupied, reserved, maintenance }

int _positive(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! int || value <= 0) {
    throw FormatException('Invalid positive integer: $key');
  }
  return value;
}

int _nonnegative(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! int || value < 0) {
    throw FormatException('Invalid count: $key');
  }
  return value;
}

String _name(Map<String, dynamic> json, String key, int max) {
  final value = json[key];
  if (value is! String ||
      value.trim().isEmpty ||
      value != value.trim() ||
      value.length > max) {
    throw FormatException('Invalid string: $key');
  }
  return value;
}

WardKind _wardKind(Object? value) => switch (value) {
  'general' => WardKind.general,
  'icu' => WardKind.icu,
  'maternity' => WardKind.maternity,
  'pediatric' => WardKind.pediatric,
  'isolation' => WardKind.isolation,
  _ => throw const FormatException('Unknown ward type.'),
};

WardBedStatus _bedStatus(Object? value) => switch (value) {
  'available' => WardBedStatus.available,
  'occupied' => WardBedStatus.occupied,
  'reserved' => WardBedStatus.reserved,
  'maintenance' => WardBedStatus.maintenance,
  _ => throw const FormatException('Unknown bed status.'),
};

class WardRecord {
  const WardRecord({
    required this.id,
    required this.clinicId,
    required this.name,
    required this.kind,
    required this.capacity,
  });

  final int id;
  final int clinicId;
  final String name;
  final WardKind kind;
  final int capacity;

  factory WardRecord.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
  }) {
    final clinicId = _positive(json, 'clinic_id');
    if (clinicId != expectedClinicId) {
      throw const FormatException('Ward clinic scope mismatch.');
    }
    return WardRecord(
      id: _positive(json, 'id'),
      clinicId: clinicId,
      name: _name(json, 'name', 150),
      kind: _wardKind(json['ward_type']),
      capacity: _nonnegative(json, 'capacity'),
    );
  }
}

class WardBedRecord {
  const WardBedRecord({
    required this.id,
    required this.wardId,
    required this.number,
    required this.status,
  });

  final int id;
  final int wardId;
  final String number;
  final WardBedStatus status;

  factory WardBedRecord.fromJson(
    Map<String, dynamic> json, {
    required int expectedWardId,
  }) {
    final wardId = _positive(json, 'ward_id');
    if (wardId != expectedWardId) {
      throw const FormatException('Bed ward scope mismatch.');
    }
    return WardBedRecord(
      id: _positive(json, 'id'),
      wardId: wardId,
      number: _name(json, 'bed_number', 30),
      status: _bedStatus(json['status']),
    );
  }
}

class WardOccupancy {
  const WardOccupancy({
    required this.wardId,
    required this.clinicId,
    required this.wardName,
    required this.capacity,
    required this.totalBeds,
    required this.occupied,
    required this.available,
    required this.reserved,
    required this.maintenance,
  });

  final int wardId;
  final int clinicId;
  final String wardName;
  final int capacity;
  final int totalBeds;
  final int occupied;
  final int available;
  final int reserved;
  final int maintenance;

  factory WardOccupancy.fromJson(
    Map<String, dynamic> json, {
    required int expectedWardId,
    required int expectedClinicId,
  }) {
    final wardId = _positive(json, 'ward_id');
    final clinicId = _positive(json, 'clinic_id');
    if (wardId != expectedWardId || clinicId != expectedClinicId) {
      throw const FormatException('Ward occupancy scope mismatch.');
    }
    final total = _nonnegative(json, 'total_beds');
    final occupied = _nonnegative(json, 'occupied');
    final available = _nonnegative(json, 'available');
    final reserved = _nonnegative(json, 'reserved');
    final maintenance = _nonnegative(json, 'maintenance');
    if (occupied + available + reserved + maintenance != total) {
      throw const FormatException('Inconsistent server occupancy counts.');
    }
    return WardOccupancy(
      wardId: wardId,
      clinicId: clinicId,
      wardName: _name(json, 'ward_name', 150),
      capacity: _nonnegative(json, 'capacity'),
      totalBeds: total,
      occupied: occupied,
      available: available,
      reserved: reserved,
      maintenance: maintenance,
    );
  }
}

class WardPage<T> {
  const WardPage({
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
  bool get hasPrevious => page > 1;

  factory WardPage.fromJson(
    Map<String, dynamic> json, {
    required int requestedPage,
    required int requestedPerPage,
    required T Function(Map<String, dynamic>) parseRow,
  }) {
    final raw = json['items'];
    if (raw is! List) {
      throw const FormatException('Ward page items must be an array.');
    }
    final total = _nonnegative(json, 'total');
    final page = _positive(json, 'page');
    final perPage = _positive(json, 'per_page');
    if (page != requestedPage ||
        perPage != requestedPerPage ||
        perPage > 500 ||
        raw.length > perPage ||
        raw.length > total) {
      throw const FormatException('Ward pagination contract mismatch.');
    }
    final rows = <T>[];
    for (final item in raw) {
      if (item is! Map<String, dynamic>) {
        throw const FormatException('Ward page item must be an object.');
      }
      rows.add(parseRow(item));
    }
    return WardPage<T>(
      items: List<T>.unmodifiable(rows),
      total: total,
      page: page,
      perPage: perPage,
    );
  }
}
