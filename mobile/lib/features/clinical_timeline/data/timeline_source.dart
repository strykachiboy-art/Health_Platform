import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

const timelineCategories = <String>{
  'appointments',
  'consultations',
  'vitals',
  'problems',
  'laboratory',
  'prescriptions',
  'medication_administration',
  'imaging',
  'admissions',
  'nursing',
  'procedures',
  'surgery',
};
const timelineLimitations = <String>{
  'patient_check_in_timestamp_unavailable',
  'clinical_radiology_final_report_unavailable',
  'surgery_documentation_is_not_operation_completion',
};

// Exact category, event type, phase and record-kind combinations from GAP03-B.
const timelineEvents = <String, (String, String, String)>{
  'appointment_booking_recorded': ('appointments', 'recorded', 'appointment'),
  'appointment_current_slot': ('appointments', 'scheduled', 'appointment'),
  'appointment_cancelled': ('appointments', 'cancelled', 'appointment'),
  'consultation_started': ('consultations', 'recorded', 'consultation'),
  'consultation_completed': ('consultations', 'completed', 'consultation'),
  'consultation_note_finalized': ('consultations', 'recorded', 'consultation'),
  'diagnosis_documented_at_finalization': (
    'consultations',
    'recorded',
    'consultation',
  ),
  'vitals_recorded': ('vitals', 'recorded', 'vitals'),
  'problem_added': ('problems', 'recorded', 'problem'),
  'problem_review_or_change_recorded': ('problems', 'amended', 'problem'),
  'lab_order_created': ('laboratory', 'recorded', 'lab_order'),
  'lab_sample_collected': ('laboratory', 'completed', 'lab_order'),
  'lab_sample_processed': ('laboratory', 'completed', 'lab_order'),
  'lab_result_entry_recorded': ('laboratory', 'recorded', 'lab_order'),
  'lab_results_verified': ('laboratory', 'completed', 'lab_order'),
  'lab_order_completed': ('laboratory', 'completed', 'lab_order'),
  'lab_patient_release_recorded': ('laboratory', 'recorded', 'lab_order'),
  'lab_patient_release_withdrawn': ('laboratory', 'withdrawn', 'lab_order'),
  'lab_corrected_release_recorded': ('laboratory', 'recorded', 'lab_order'),
  'lab_corrected_release_withdrawn': ('laboratory', 'withdrawn', 'lab_order'),
  'prescription_issued': ('prescriptions', 'recorded', 'prescription'),
  'medication_administered': (
    'medication_administration',
    'completed',
    'medication_administration',
  ),
  'medication_held': (
    'medication_administration',
    'held',
    'medication_administration',
  ),
  'medication_refused': (
    'medication_administration',
    'refused',
    'medication_administration',
  ),
  'medication_missed': (
    'medication_administration',
    'missed',
    'medication_administration',
  ),
  'medication_cancelled': (
    'medication_administration',
    'cancelled',
    'medication_administration',
  ),
  'medication_current_slot': (
    'medication_administration',
    'scheduled',
    'medication_administration',
  ),
  'imaging_order_created': ('imaging', 'recorded', 'imaging_order'),
  'imaging_current_slot': ('imaging', 'scheduled', 'imaging_order'),
  'imaging_order_completed': ('imaging', 'completed', 'imaging_order'),
  'patient_admitted': ('admissions', 'completed', 'admission'),
  'patient_discharged': ('admissions', 'completed', 'admission'),
  'bed_transfer_recorded': ('admissions', 'completed', 'admission'),
  'discharge_summary_finalized': (
    'admissions',
    'recorded',
    'discharge_summary',
  ),
  'nursing_task_completed': ('nursing', 'completed', 'nursing_task'),
  'nursing_order_created': ('nursing', 'recorded', 'nursing_order'),
  'nursing_order_execution_recorded': ('nursing', 'completed', 'nursing_order'),
  'nursing_note_signed': ('nursing', 'recorded', 'nursing_note'),
  'procedure_performance_recorded': (
    'procedures',
    'completed',
    'procedure_order',
  ),
  'surgical_case_plan_recorded': ('surgery', 'recorded', 'surgical_case'),
  'surgical_procedure_performance_recorded': (
    'surgery',
    'completed',
    'procedure_order',
  ),
  'operative_document_signature_recorded': (
    'surgery',
    'recorded',
    'surgical_report',
  ),
  'consultation_note_amendment_recorded': (
    'consultations',
    'amended',
    'consultation',
  ),
  'nursing_note_amendment_recorded': ('nursing', 'amended', 'nursing_note'),
  'medication_administration_amendment_recorded': (
    'medication_administration',
    'amended',
    'medication_administration',
  ),
  'discharge_summary_amendment_recorded': (
    'admissions',
    'amended',
    'discharge_summary',
  ),
};
Never _bad() =>
    throw const FormatException('Invalid clinical timeline response.');
Map<String, dynamic> _map(Object? v) => v is Map<String, dynamic> ? v : _bad();
List<dynamic> _list(Object? v) => v is List<dynamic> ? v : _bad();
int _id(Object? v) => v is int && v > 0 && v <= 2147483647 ? v : _bad();
String _text(Object? v, int max) =>
    v is String && v.isNotEmpty && v.length <= max && v.trim().isNotEmpty
    ? v
    : _bad();
String _uuid(Object? v) {
  final s = _text(v, 36);
  if (!RegExp(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$')
      .hasMatch(s)) {
    _bad();
  }
  return s.toLowerCase();
}

DateTime _time(Object? v) {
  final s = _text(v, 40);
  final m = RegExp(
    r'^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$',
  ).firstMatch(s);
  if (m == null) {
    _bad();
  }
  final year = int.parse(m.group(1)!);
  final month = int.parse(m.group(2)!);
  final day = int.parse(m.group(3)!);
  if (!s.endsWith('Z')) {
    final offset = s.substring(s.length - 5);
    if (int.parse(offset.substring(0, 2)) > 23 ||
        int.parse(offset.substring(3)) > 59) {
      _bad();
    }
  }
  final d = DateTime.tryParse(s);
  final check = DateTime.utc(year, month, day);
  if (d == null ||
      year < 1 ||
      check.year != year ||
      check.month != month ||
      check.day != day ||
      int.parse(m.group(4)!) > 23 ||
      int.parse(m.group(5)!) > 59 ||
      int.parse(m.group(6)!) > 59) {
    _bad();
  }
  return d.toUtc();
}

Set<String> _choices(Object? v, Set<String> allowed) {
  final list = _list(v);
  final result = <String>{};
  for (final x in list) {
    if (x is! String || !allowed.contains(x) || !result.add(x)) {
      _bad();
    }
  }
  return Set.unmodifiable(result);
}

class TimelineUnavailable implements Exception {
  const TimelineUnavailable();
}

class TimelineFilters {
  TimelineFilters({this.category, this.start, this.end, this.limit = 25}) {
    if ((category != null && !timelineCategories.contains(category)) ||
        limit < 1 ||
        limit > 100 ||
        (start != null && end != null && !start!.isBefore(end!))) {
      _bad();
    }
  }
  final String? category;
  final DateTime? start, end;
  final int limit;
  Map<String, String> parameters(String? cursor) {
    final values = <String, String>{'limit': '$limit'};
    final cat = category;
    final first = start;
    final last = end;
    if (cat != null) {
      values['categories'] = cat;
    }
    if (first != null) {
      values['start'] = first.toUtc().toIso8601String();
    }
    if (last != null) {
      values['end'] = last.toUtc().toIso8601String();
    }
    if (cursor != null) {
      values['cursor'] = cursor;
    }
    return values;
  }

  String get key => Uri(queryParameters: parameters(null)).query;
}

class TimelineEvent {
  TimelineEvent._(Map<String, dynamic> json)
    : eventId = _text(json['event_id'], 110),
      category = _text(json['category'], 40),
      type = _text(json['event_type'], 80),
      phase = _text(json['phase'], 20),
      occurredAt = _time(json['occurred_at']),
      sourceKind = _text(_map(json['source'])['kind'], 40),
      sourceId = _id(_map(json['source'])['id']),
      version = json['record_version'] == null
          ? null
          : _id(json['record_version']),
      staff = json['related_staff'] == null
          ? null
          : TimelineStaff._(_map(json['related_staff'])) {
    if (timelineEvents[type] != (category, phase, sourceKind) ||
        !eventId.startsWith('$type:')) {
      _bad();
    }
    final row = eventId.substring(type.length + 1);
    rowId = _id(int.tryParse(row));
    if (row != '$rowId') {
      _bad();
    }
    if (phase == 'amended' && (version ?? 0) < 2) {
      _bad();
    }
  }
  final String eventId, category, type, phase, sourceKind;
  final DateTime occurredAt;
  final int sourceId;
  late final int rowId;
  final int? version;
  final TimelineStaff? staff;
  int compareTo(TimelineEvent other) {
    final time = other.occurredAt.compareTo(occurredAt);
    if (time != 0) {
      return time;
    }
    final kind = type.compareTo(other.type);
    return kind != 0 ? kind : other.rowId.compareTo(rowId);
  }

  String? route(int patientId) => switch (sourceKind) {
    'consultation' => '/consultations/patient/$patientId/$sourceId',
    'prescription' => '/patients/$patientId/prescriptions/$sourceId',
    'surgical_case' => '/surgery/cases/$sourceId',
    'appointment' => '/appointments/patient/$patientId',
    'lab_order' => '/patients/$patientId/lab-orders',
    'imaging_order' => '/clinical-orders/patient/$patientId/imaging',
    'procedure_order' => '/clinical-orders/patient/$patientId/procedures',
    'nursing_order' => '/clinical-orders/patient/$patientId/nursing-orders',
    _ => null,
  };
  bool get exactRoute => const {
    'consultation',
    'prescription',
    'surgical_case',
  }.contains(sourceKind);
}

class TimelineStaff {
  TimelineStaff._(Map<String, dynamic> j)
    : id = _id(j['id']),
      name = _text(j['name'], 512),
      department = j['current_department'] == null
          ? null
          : _text(j['current_department'], 512);
  final int id;
  final String name;
  final String? department;
}

class TimelinePage {
  const TimelinePage._(
    this.items,
    this.available,
    this.limitations,
    this.cursor,
    this.labReleaseEnabled,
    this._session,
    this._filterKey,
  );
  final TimelineSession _session;
  final String _filterKey;
  final List<TimelineEvent> items;
  final Set<String> available, limitations;
  final String? cursor;
  final bool labReleaseEnabled;
}

class TimelineSource {
  const TimelineSource({
    required this.apiClient,
    required this.loadSession,
    required this.guard,
  });
  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard guard;
  Future<AuthTokenResponse> _ready(int revision, int clinic) async {
    if (!guard.canAccessProtectedRoutes || guard.revision != revision) {
      throw const TimelineUnavailable();
    }
    final state = await loadSession();
    final a = state.session;
    if (!guard.canAccessProtectedRoutes ||
        guard.revision != revision ||
        state.disposition != AuthSessionDisposition.ready ||
        a == null ||
        a.userId <= 0 ||
        a.clinicContextId != clinic ||
        clinic <= 0 ||
        a.tenantId == null ||
        a.accessToken.isEmpty ||
        a.accessToken.trim() != a.accessToken ||
        a.refreshToken.isEmpty ||
        !const {'doctor', 'nurse'}.contains(a.role.trim().toLowerCase())) {
      throw const TimelineUnavailable();
    }
    _uuid(a.tenantId);
    return a;
  }

  Future<TimelineSession> open(int patient, int clinic) async {
    _id(patient);
    _id(clinic);
    final rev = guard.revision;
    final auth = await _ready(rev, clinic);
    return TimelineSession._(this, auth, rev, patient, clinic);
  }
}

class TimelineSession {
  const TimelineSession._(
    this.source,
    this._auth,
    this._revision,
    this.patientId,
    this.clinicId,
  );
  final TimelineSource source;
  final AuthTokenResponse _auth;
  final int _revision, patientId, clinicId;
  bool get current =>
      source.guard.canAccessProtectedRoutes &&
      source.guard.revision == _revision;
  String get role => _auth.role.trim().toLowerCase();
  Future<void> check() async {
    final a = await source._ready(_revision, clinicId);
    if (a.userId != _auth.userId ||
        a.tenantId != _auth.tenantId ||
        a.role != _auth.role ||
        a.accessToken != _auth.accessToken ||
        a.refreshToken != _auth.refreshToken) {
      throw const TimelineUnavailable();
    }
  }

  Future<TimelinePage> read(
    TimelineFilters filters, {
    TimelinePage? previous,
  }) async {
    await check();
    final cursor = previous?.cursor;
    if (previous != null &&
        (cursor == null ||
            !identical(previous._session, this) ||
            previous._filterKey != filters.key)) {
      _bad();
    }
    final path = Uri(
      path: 'api/v1/patients/$patientId/clinical-timeline',
      queryParameters: filters.parameters(cursor),
    ).toString();
    final raw = await source.apiClient.getJson(
      path,
      bearerToken: _auth.accessToken,
    );
    await check();
    if (raw['success'] != true) {
      _bad();
    }
    final j = _map(raw['data']);
    if (_id(j['patient_id']) != patientId ||
        _id(j['clinic_id']) != clinicId ||
        _uuid(j['organization_id']) != _uuid(_auth.tenantId)) {
      _bad();
    }
    final available = _choices(j['available_categories'], timelineCategories);
    final limitations = _choices(j['source_limitations'], timelineLimitations);
    final lab = j['lab_release_events_enabled'];
    if (lab is! bool ||
        (role == 'nurse' && (available.contains('prescriptions') || lab))) {
      _bad();
    }
    if (previous != null &&
        (previous.available.length != available.length ||
            !available.containsAll(previous.available) ||
            previous.labReleaseEnabled != lab)) {
      _bad();
    }
    final rows = _list(j['items']);
    if (rows.length > filters.limit) {
      _bad();
    }
    final next = j['next_cursor'] == null
        ? null
        : _text(j['next_cursor'], 2048);
    if (next != null && (next == cursor || rows.length != filters.limit)) {
      _bad();
    }
    final events = <TimelineEvent>[];
    TimelineEvent? last = previous == null || previous.items.isEmpty
        ? null
        : previous.items.last;
    final ids = <String>{};
    for (final raw in rows) {
      final event = TimelineEvent._(_map(raw));
      if (!available.contains(event.category) ||
          (filters.category != null && event.category != filters.category) ||
          (filters.start != null &&
              event.occurredAt.isBefore(filters.start!)) ||
          (filters.end != null && !event.occurredAt.isBefore(filters.end!)) ||
          (!lab &&
              event.type.startsWith('lab_') &&
              event.type.contains('release')) ||
          !ids.add(event.eventId) ||
          (last != null && last.compareTo(event) >= 0)) {
        _bad();
      }
      events.add(event);
      last = event;
    }
    return TimelinePage._(
      List.unmodifiable(events),
      available,
      limitations,
      next,
      lab,
      this,
      filters.key,
    );
  }
}
