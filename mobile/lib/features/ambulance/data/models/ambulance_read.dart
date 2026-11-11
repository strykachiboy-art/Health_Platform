// P23I.S05 — server-owned, clinic-scoped ambulance read models only.
// No dispatch, GPS simulation, tracking, or state mutations.

enum AmbulanceReadKind { vehicles, trips }

const ambulanceVehicleStatuses = <String>{
  'available',
  'on_trip',
  'maintenance',
  'out_of_service',
};
const ambulanceTripStatuses = <String>{
  'requested',
  'dispatched',
  'en_route_to_pickup',
  'at_pickup',
  'patient_on_board',
  'en_route_to_destination',
  'completed',
  'cancelled',
};
const ambulanceTripTypes = <String>{
  'emergency_pickup',
  'inter_facility_transfer',
  'discharge_transport',
  'non_emergency',
};
const ambulanceEquipmentLevels = <String>{'bls', 'als', 'cct'};

class AmbulanceVehicleRead {
  const AmbulanceVehicleRead({
    required this.id,
    required this.clinicId,
    required this.plateNumber,
    required this.equipmentLevel,
    required this.capacity,
    required this.status,
    required this.lastServiceDate,
    required this.createdAt,
    required this.updatedAt,
  });

  final int id;
  final int clinicId;
  final String plateNumber;
  final String equipmentLevel;
  final int capacity;
  final String status;
  final String? lastServiceDate;
  final String? createdAt;
  final String? updatedAt;

  factory AmbulanceVehicleRead.fromJson(
    Map<String, dynamic> row, {
    required int expectedClinicId,
  }) {
    final clinic = ambulanceReadId(row, 'clinic_id');
    if (clinic != expectedClinicId) {
      throw const FormatException('Cross-clinic ambulance vehicle rejected.');
    }
    final capacity = ambulanceReadId(row, 'capacity');
    return AmbulanceVehicleRead(
      id: ambulanceReadId(row, 'id'),
      clinicId: clinic,
      plateNumber: ambulanceReadText(row, 'plate_number', limit: 30),
      equipmentLevel: ambulanceReadEnum(
        row,
        'equipment_level',
        ambulanceEquipmentLevels,
      ),
      capacity: capacity,
      status: ambulanceReadEnum(row, 'status', ambulanceVehicleStatuses),
      lastServiceDate: ambulanceReadDate(row, 'last_service_date'),
      createdAt: ambulanceReadTimestamp(row, 'created_at'),
      updatedAt: ambulanceReadTimestamp(row, 'updated_at'),
    );
  }
}

class AmbulanceTripRead {
  const AmbulanceTripRead({
    required this.id,
    required this.clinicId,
    required this.vehicleId,
    required this.patientId,
    required this.driverId,
    required this.paramedicId,
    required this.admissionId,
    required this.tripType,
    required this.status,
    required this.pickupAddress,
    required this.destinationAddress,
    required this.requestedAt,
    required this.dispatchedAt,
    required this.pickupAt,
    required this.completedAt,
    required this.cancelledAt,
    required this.cancellationReason,
    required this.notes,
    required this.invoiceId,
  });

  final int id;
  final int clinicId;
  final int? vehicleId;
  final int? patientId;
  final int? driverId;
  final int? paramedicId;
  final int? admissionId;
  final String tripType;
  final String status;
  final String? pickupAddress;
  final String? destinationAddress;
  final String? requestedAt;
  final String? dispatchedAt;
  final String? pickupAt;
  final String? completedAt;
  final String? cancelledAt;
  final String? cancellationReason;
  final String? notes;
  final int? invoiceId;

  factory AmbulanceTripRead.fromJson(
    Map<String, dynamic> row, {
    required int expectedClinicId,
  }) {
    final clinic = ambulanceReadId(row, 'clinic_id');
    if (clinic != expectedClinicId) {
      throw const FormatException('Cross-clinic ambulance trip rejected.');
    }
    // The backend may include coordinates. We deliberately never display
    // or infer live location from those server-stored historical fields.
    for (final key in <String>[
      'pickup_lat',
      'pickup_lng',
      'destination_lat',
      'destination_lng',
    ]) {
      final value = row[key];
      if (value != null && (value is! num || !value.isFinite)) {
        throw FormatException('Invalid ambulance coordinate: $key');
      }
    }
    return AmbulanceTripRead(
      id: ambulanceReadId(row, 'id'),
      clinicId: clinic,
      vehicleId: ambulanceReadNullableId(row, 'vehicle_id'),
      patientId: ambulanceReadNullableId(row, 'patient_id'),
      driverId: ambulanceReadNullableId(row, 'driver_id'),
      paramedicId: ambulanceReadNullableId(row, 'paramedic_id'),
      admissionId: ambulanceReadNullableId(row, 'admission_id'),
      tripType: ambulanceReadEnum(row, 'trip_type', ambulanceTripTypes),
      status: ambulanceReadEnum(row, 'status', ambulanceTripStatuses),
      pickupAddress: ambulanceReadNullableText(row, 'pickup_address'),
      destinationAddress: ambulanceReadNullableText(row, 'destination_address'),
      requestedAt: ambulanceReadTimestamp(row, 'requested_at'),
      dispatchedAt: ambulanceReadTimestamp(row, 'dispatched_at'),
      pickupAt: ambulanceReadTimestamp(row, 'pickup_at'),
      completedAt: ambulanceReadTimestamp(row, 'completed_at'),
      cancelledAt: ambulanceReadTimestamp(row, 'cancelled_at'),
      cancellationReason: ambulanceReadNullableText(row, 'cancellation_reason'),
      notes: ambulanceReadNullableText(row, 'notes', limit: 2000),
      invoiceId: ambulanceReadNullableId(row, 'invoice_id'),
    );
  }
}

class AmbulanceReadPage<T> {
  const AmbulanceReadPage({
    required this.items,
    required this.total,
    required this.page,
    required this.perPage,
    required this.pages,
    required this.hasNext,
  });
  final List<T> items;
  final int total;
  final int page;
  final int perPage;
  final int pages;
  final bool hasNext;
}

AmbulanceReadPage<T> parseAmbulanceReadPage<T>(
  Map<String, dynamic> envelope, {
  required int requestedPage,
  required int requestedPerPage,
  required T Function(Map<String, dynamic>) decode,
  required int Function(T) identity,
}) {
  if (envelope['success'] != true ||
      envelope['data'] is! Map<String, dynamic>) {
    throw const FormatException('Invalid ambulance list envelope.');
  }
  final data = envelope['data'] as Map<String, dynamic>;
  final raw = data['items'];
  final total = data['total'];
  final page = data['page'];
  final perPage = data['per_page'];
  final pages = data['pages'];
  final hasNext = data['has_next'];
  final hasPrev = data['has_prev'];
  if (raw is! List ||
      total is! int ||
      total < 0 ||
      page != requestedPage ||
      perPage != requestedPerPage ||
      pages is! int ||
      pages != (total + requestedPerPage - 1) ~/ requestedPerPage ||
      hasNext is! bool ||
      hasNext != (page < pages) ||
      hasPrev is! bool ||
      hasPrev != (page > 1) ||
      raw.length > requestedPerPage ||
      (page - 1) * requestedPerPage + raw.length > total ||
      (page == 1 && total > 0 && raw.isEmpty)) {
    throw const FormatException('Inconsistent ambulance pagination.');
  }
  final result = <T>[];
  final ids = <int>{};
  for (final item in raw) {
    if (item is! Map<String, dynamic>) {
      throw const FormatException('Malformed ambulance list row.');
    }
    final record = decode(item);
    if (!ids.add(identity(record))) {
      throw const FormatException('Duplicate ambulance record on page.');
    }
    result.add(record);
  }
  return AmbulanceReadPage<T>(
    items: List<T>.unmodifiable(result),
    total: total,
    page: page,
    perPage: perPage,
    pages: pages,
    hasNext: hasNext,
  );
}

int ambulanceReadId(Map<String, dynamic> row, String key) {
  final value = row[key];
  if (value is! int || value <= 0 || value > 2147483647) {
    throw FormatException('Invalid ambulance identifier: $key');
  }
  return value;
}

int? ambulanceReadNullableId(Map<String, dynamic> row, String key) =>
    row[key] == null ? null : ambulanceReadId(row, key);

String ambulanceReadText(
  Map<String, dynamic> row,
  String key, {
  int limit = 255,
}) {
  final value = row[key];
  if (value is! String ||
      value.isEmpty ||
      value.trim() != value ||
      value.length > limit) {
    throw FormatException('Invalid ambulance text: $key');
  }
  return value;
}

String? ambulanceReadNullableText(
  Map<String, dynamic> row,
  String key, {
  int limit = 255,
}) => row[key] == null ? null : ambulanceReadText(row, key, limit: limit);

String ambulanceReadEnum(
  Map<String, dynamic> row,
  String key,
  Set<String> allowed,
) {
  final value = row[key];
  if (value is! String || !allowed.contains(value)) {
    throw FormatException('Unknown ambulance enum: $key');
  }
  return value;
}

String? ambulanceReadTimestamp(Map<String, dynamic> row, String key) {
  final value = row[key];
  if (value == null) return null;
  if (value is! String ||
      value.length > 64 ||
      DateTime.tryParse(value) == null) {
    throw FormatException('Invalid ambulance timestamp: $key');
  }
  return value;
}

String? ambulanceReadDate(Map<String, dynamic> row, String key) {
  final value = row[key];
  if (value == null) return null;
  if (value is! String ||
      !RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(value) ||
      DateTime.tryParse(value) == null ||
      DateTime.parse(value).toIso8601String().substring(0, 10) != value) {
    throw FormatException('Invalid ambulance date: $key');
  }
  return value;
}
