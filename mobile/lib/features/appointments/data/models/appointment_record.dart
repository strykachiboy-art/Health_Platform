/// Appointment list record returned by the backend.
/// No timezone is assumed for naive Python ISO-8601 timestamps.
enum AppointmentRecordStatus {
  scheduled,
  confirmed,
  cancelled,
  completed,
  noShow,
}

enum AppointmentRecordType { inPerson, telemedicine, followUp, emergency }

class AppointmentRecord {
  const AppointmentRecord({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.staffId,
    required this.scheduledStart,
    required this.scheduledEnd,
    required this.status,
    required this.appointmentType,
    required this.reason,
    required this.notes,
    required this.googleCalendarEventId,
    required this.reminderSent,
    required this.createdAt,
    required this.updatedAt,
    required this.cancelledAt,
    required this.cancellationReason,
  });

  final int id;
  final int clinicId;
  final int patientId;
  final int staffId;
  final String scheduledStart;
  final String scheduledEnd;
  final AppointmentRecordStatus status;
  final AppointmentRecordType appointmentType;
  final String? reason;
  final String? notes;
  final String? googleCalendarEventId;
  final bool reminderSent;
  final String? createdAt;
  final String? updatedAt;
  final String? cancelledAt;
  final String? cancellationReason;

  factory AppointmentRecord.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    int? expectedPatientId,
    int? expectedStaffId,
  }) {
    if (expectedClinicId <= 0 ||
        ((expectedPatientId == null) == (expectedStaffId == null))) {
      throw ArgumentError('Specify exactly one appointment list scope.');
    }
    final clinicId = _integer(json, 'clinic_id', 1);
    final patientId = _integer(json, 'patient_id', 1);
    final staffId = _integer(json, 'staff_id', 1);
    if (clinicId != expectedClinicId ||
        (expectedPatientId != null && patientId != expectedPatientId) ||
        (expectedStaffId != null && staffId != expectedStaffId)) {
      throw const FormatException(
        'Appointment patient/staff/clinic scope mismatch.',
      );
    }
    final reminder = json['reminder_sent'];
    if (reminder is! bool) {
      throw const FormatException('Invalid appointment reminder state.');
    }
    return AppointmentRecord(
      id: _integer(json, 'id', 1),
      clinicId: clinicId,
      patientId: patientId,
      staffId: staffId,
      scheduledStart: _timestamp(json, 'scheduled_start'),
      scheduledEnd: _timestamp(json, 'scheduled_end'),
      status: switch (json['status']) {
        'scheduled' => AppointmentRecordStatus.scheduled,
        'confirmed' => AppointmentRecordStatus.confirmed,
        'cancelled' => AppointmentRecordStatus.cancelled,
        'completed' => AppointmentRecordStatus.completed,
        'no_show' => AppointmentRecordStatus.noShow,
        _ => throw const FormatException('Unknown appointment status.'),
      },
      appointmentType: switch (json['appointment_type']) {
        'in_person' => AppointmentRecordType.inPerson,
        'telemedicine' => AppointmentRecordType.telemedicine,
        'follow_up' => AppointmentRecordType.followUp,
        'emergency' => AppointmentRecordType.emergency,
        _ => throw const FormatException('Unknown appointment type.'),
      },
      reason: _text(json, 'reason'),
      notes: _text(json, 'notes'),
      googleCalendarEventId: _text(json, 'google_calendar_event_id'),
      reminderSent: reminder,
      createdAt: _optionalTimestamp(json, 'created_at'),
      updatedAt: _optionalTimestamp(json, 'updated_at'),
      cancelledAt: _optionalTimestamp(json, 'cancelled_at'),
      cancellationReason: _text(json, 'cancellation_reason'),
    );
  }
}

class AppointmentPage {
  const AppointmentPage({
    required this.items,
    required this.total,
    required this.page,
    required this.perPage,
    required this.pages,
    required this.hasNext,
    required this.hasPrev,
  });

  final List<AppointmentRecord> items;
  final int total;
  final int page;
  final int perPage;
  final int pages;
  final bool hasNext;
  final bool hasPrev;

  factory AppointmentPage.fromEnvelope(
    Map<String, dynamic> envelope, {
    required int expectedClinicId,
    required int requestedPage,
    required int requestedPerPage,
    int? expectedPatientId,
    int? expectedStaffId,
  }) {
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid appointment response envelope.');
    }
    final data = envelope['data'] as Map<String, dynamic>;
    final rawRows = data['items'];
    if (rawRows is! List) {
      throw const FormatException('Appointment rows must be an array.');
    }
    final total = _integer(data, 'total', 0);
    final page = _integer(data, 'page', 1);
    final perPage = _integer(data, 'per_page', 1);
    final pages = _integer(data, 'pages', 0);
    final hasNext = data['has_next'];
    final hasPrev = data['has_prev'];
    final expectedPages = (total + perPage - 1) ~/ perPage;
    if (page != requestedPage ||
        perPage != requestedPerPage ||
        perPage > 500 ||
        pages != expectedPages ||
        hasNext is! bool ||
        hasPrev is! bool ||
        hasNext != (page < pages) ||
        hasPrev != (page > 1) ||
        (total > 0 && page > pages) ||
        rawRows.length > perPage ||
        rawRows.length > total) {
      throw const FormatException('Inconsistent appointment pagination.');
    }
    final ids = <int>{};
    final records = <AppointmentRecord>[];
    for (final raw in rawRows) {
      if (raw is! Map<String, dynamic>) {
        throw const FormatException('Malformed appointment row.');
      }
      final record = AppointmentRecord.fromJson(
        raw,
        expectedClinicId: expectedClinicId,
        expectedPatientId: expectedPatientId,
        expectedStaffId: expectedStaffId,
      );
      if (!ids.add(record.id)) {
        throw const FormatException('Duplicate appointment ID.');
      }
      records.add(record);
    }
    return AppointmentPage(
      items: List<AppointmentRecord>.unmodifiable(records),
      total: total,
      page: page,
      perPage: perPage,
      pages: pages,
      hasNext: hasNext,
      hasPrev: hasPrev,
    );
  }
}

int _integer(Map<String, dynamic> json, String field, int minimum) {
  final raw = json[field];
  if (raw is! int || raw < minimum) {
    throw FormatException('Invalid appointment integer field: $field');
  }
  return raw;
}

String? _text(Map<String, dynamic> json, String field) {
  final raw = json[field];
  if (raw == null) return null;
  if (raw is! String) {
    throw FormatException('Invalid appointment text field: $field');
  }
  return raw;
}

String _timestamp(Map<String, dynamic> json, String field) {
  final raw = json[field];
  if (raw is! String ||
      !RegExp(
        r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$',
      ).hasMatch(raw) ||
      DateTime.tryParse(raw) == null) {
    throw FormatException('Invalid appointment timestamp: $field');
  }
  return raw;
}

String? _optionalTimestamp(Map<String, dynamic> json, String field) =>
    json[field] == null ? null : _timestamp(json, field);
