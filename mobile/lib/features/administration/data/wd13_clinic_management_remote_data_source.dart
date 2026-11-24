import 'package:clinic_system_pro/core/models/clinic_hours_status.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

/// Root-clinic creation is a system-level Super Admin operation. A selected
/// clinic is not required. Backend role and quota decisions remain authoritative.
class Wd13ClinicAccessUnavailable implements Exception {
  const Wd13ClinicAccessUnavailable();
}

class Wd13ClinicMutationAlreadyAttempted implements Exception {
  const Wd13ClinicMutationAlreadyAttempted();
}

class Wd13Clinic {
  const Wd13Clinic({
    required this.id,
    required this.name,
    required this.type,
    required this.status,
    required this.headquarters,
    required this.parentId,
    required this.timezone,
    this.address,
    this.city,
    this.country,
    this.phone,
    this.email,
    this.openingTime,
    this.closingTime,
    this.operatingMode,
    this.hoursStatus,
  });

  final int id;
  final String name;
  final String type;
  final String status;
  final bool headquarters;
  final int? parentId;
  final String timezone;
  final String? address;
  final String? city;
  final String? country;
  final String? phone;
  final String? email;
  final String? openingTime;
  final String? closingTime;

  /// Absent on older backends or while extended hours are disabled.
  final String? operatingMode;
  final ClinicHoursStatus? hoursStatus;

  static const operatingModes = <String>{
    'unspecified',
    'always_open',
    'scheduled',
  };

  static const types = <String>{
    'general',
    'specialist',
    'dental',
    'diagnostic_center',
    'pharmacy_only',
  };
  static const statuses = <String>{'active', 'inactive', 'suspended'};

  static String _requiredString(Map<String, dynamic> data, String field) {
    final value = data[field];
    if (value is! String || value.trim().isEmpty || value != value.trim()) {
      throw FormatException('Invalid clinic $field.');
    }
    return value;
  }

  static String? _optionalString(Map<String, dynamic> data, String field) {
    final value = data[field];
    if (value == null) return null;
    if (value is! String) throw FormatException('Invalid clinic $field.');
    return value;
  }

  factory Wd13Clinic.parse(Object? raw) {
    if (raw is! Map<String, dynamic>) {
      throw const FormatException('Clinic data must be an object.');
    }
    final id = raw['id'];
    final type = _requiredString(raw, 'clinic_type');
    final status = _requiredString(raw, 'status');
    final parent = raw['parent_clinic_id'];
    final hq = raw['is_headquarters'];
    final rawMode = raw['operating_mode'];
    if (rawMode != null &&
        (rawMode is! String || !operatingModes.contains(rawMode))) {
      throw const FormatException('Invalid clinic operating mode.');
    }
    if (id is! int ||
        id <= 0 ||
        id > 2147483647 ||
        !types.contains(type) ||
        !statuses.contains(status) ||
        hq is! bool ||
        (parent != null &&
            (parent is! int || parent <= 0 || parent > 2147483647)) ||
        (hq && parent != null)) {
      throw const FormatException('Clinic record contains invalid fields.');
    }
    return Wd13Clinic(
      id: id,
      name: _requiredString(raw, 'name'),
      type: type,
      status: status,
      parentId: parent as int?,
      headquarters: hq,
      timezone: _requiredString(raw, 'timezone'),
      address: _optionalString(raw, 'address'),
      city: _optionalString(raw, 'city'),
      country: _optionalString(raw, 'country'),
      phone: _optionalString(raw, 'phone'),
      email: _optionalString(raw, 'email'),
      openingTime: _optionalString(raw, 'opening_time'),
      closingTime: _optionalString(raw, 'closing_time'),
      operatingMode: rawMode as String?,
      hoursStatus: ClinicHoursStatus.fromJson(raw),
    );
  }
}

class Wd13RootClinicDraft {
  const Wd13RootClinicDraft({
    required this.name,
    required this.type,
    required this.headquarters,
    required this.timezone,
    this.address = '',
    this.city = '',
    this.country = '',
    this.phone = '',
    this.email = '',
    this.openingTime = '',
    this.closingTime = '',
    this.operatingMode,
  });

  final String name;
  final String type;
  final bool headquarters;
  final String timezone;
  final String address;
  final String city;
  final String country;
  final String phone;
  final String email;
  final String openingTime;
  final String closingTime;

  /// Null preserves the legacy create contract until backend rollout approval.
  final String? operatingMode;

  static String _trimmed(
    String value,
    String label,
    int limit, {
    bool required = false,
  }) {
    final normalized = value.trim();
    if ((required && normalized.isEmpty) || normalized.length > limit) {
      throw FormatException('Invalid $label.');
    }
    return normalized;
  }

  static String? _time(String input) {
    final text = input.trim();
    if (text.isEmpty) return null;
    final match = RegExp(r'^(\d{2}):(\d{2})(?::(\d{2}))?$').firstMatch(text);
    if (match == null) {
      throw const FormatException('Time must be HH:mm or HH:mm:ss.');
    }
    final hours = int.parse(match.group(1)!);
    final minutes = int.parse(match.group(2)!);
    final seconds = int.parse(match.group(3) ?? '00');
    if (hours > 23 || minutes > 59 || seconds > 59) {
      throw const FormatException('Invalid operating time.');
    }
    return '${match.group(1)}:${match.group(2)}:${match.group(3) ?? '00'}';
  }

  Map<String, dynamic> toBackendJson() {
    final normalizedName = _trimmed(name, 'clinic name', 150, required: true);
    final normalizedTimezone = _trimmed(
      timezone,
      'timezone',
      50,
      required: true,
    );
    if (!Wd13Clinic.types.contains(type)) {
      throw const FormatException('Invalid clinic type.');
    }
    final opening = _time(openingTime);
    final closing = _time(closingTime);
    final mode = operatingMode;
    if (mode == null) {
      // Old API behavior remains exactly as before while rollout is disabled.
      if (opening != null &&
          closing != null &&
          opening.compareTo(closing) >= 0) {
        throw const FormatException(
          'Opening time must be earlier than closing time.',
        );
      }
    } else if (!Wd13Clinic.operatingModes.contains(mode)) {
      throw const FormatException('Invalid clinic operating mode.');
    } else if (mode == 'scheduled') {
      if (opening == null || closing == null || opening == closing) {
        throw const FormatException(
          'Scheduled hours require distinct opening and closing times.',
        );
      }
    } else if (opening != null || closing != null) {
      throw const FormatException(
        '24/7 or unspecified mode cannot include hours.',
      );
    }
    final data = <String, dynamic>{
      'name': normalizedName,
      'clinic_type': type,
      'parent_clinic_id': null, // Never create a branch through this workspace.
      'is_headquarters': headquarters,
      'timezone': normalizedTimezone,
    };
    for (final item in <(String, String, int)>[
      ('address', address, 255),
      ('city', city, 100),
      ('country', country, 100),
      ('phone', phone, 30),
      ('email', email, 120),
    ]) {
      final normalized = _trimmed(item.$2, item.$1, item.$3);
      if (normalized.isNotEmpty) data[item.$1] = normalized;
    }
    if (opening != null) data['opening_time'] = opening;
    if (closing != null) data['closing_time'] = closing;
    if (mode != null) data['operating_mode'] = mode;
    return data;
  }
}

/// In-memory one-shot claim, owned by the router rather than by a single page.
/// The backend has no idempotency contract for POST /clinics; ambiguous writes
/// remain claimed. Reopening a page cannot replay the same named creation.
class Wd13RootClinicAttemptGate {
  final Set<String> _claimed = <String>{};

  bool claim({required int actorId, required String name}) =>
      _claimed.add('$actorId|${name.trim().toLowerCase()}');
}

class Wd13ClinicManagementRemoteDataSource {
  const Wd13ClinicManagementRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
    required this.createGate,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  final Wd13RootClinicAttemptGate createGate;

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.role.toLowerCase() == b.role.toLowerCase() &&
      a.tenantId == b.tenantId &&
      a.clinicContextId == b.clinicContextId &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AuthTokenResponse> _authorized(int revision) async {
    if (!_current(revision)) throw const Wd13ClinicAccessUnavailable();
    try {
      final state = await loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.role.trim().toLowerCase() != 'super_admin' ||
          session.accessToken.isEmpty ||
          session.accessToken != session.accessToken.trim() ||
          !_current(revision)) {
        throw const Wd13ClinicAccessUnavailable();
      }
      return session;
    } on Wd13ClinicAccessUnavailable {
      rethrow;
    } on Object {
      throw const Wd13ClinicAccessUnavailable();
    }
  }

  Future<List<Wd13Clinic>> list({String? status}) async {
    if (status != null && !Wd13Clinic.statuses.contains(status)) {
      throw ArgumentError.value(status, 'status');
    }
    final revision = sessionNavigationGuard.revision;
    final before = await _authorized(revision);
    final response = await apiClient.getJson(
      'api/v1/clinics${status == null ? '' : '?status=$status'}',
      bearerToken: before.accessToken,
    );
    final after = await _authorized(revision);
    if (!_current(revision) || !_same(before, after)) {
      throw const Wd13ClinicAccessUnavailable();
    }
    final raw = response['data'];
    if (raw is! List || raw.length > 5000) {
      throw const FormatException('Invalid clinic list response.');
    }
    final ids = <int>{};
    final clinics = <Wd13Clinic>[];
    for (final item in raw) {
      final record = Wd13Clinic.parse(item);
      if (!ids.add(record.id) || (status != null && record.status != status)) {
        throw const FormatException('Invalid clinic list or filter response.');
      }
      clinics.add(record);
    }
    return List<Wd13Clinic>.unmodifiable(clinics);
  }

  Future<Wd13Clinic> read(int id) async {
    if (id <= 0 || id > 2147483647) throw ArgumentError.value(id, 'id');
    final revision = sessionNavigationGuard.revision;
    final before = await _authorized(revision);
    final response = await apiClient.getJson(
      'api/v1/clinics/$id',
      bearerToken: before.accessToken,
    );
    final after = await _authorized(revision);
    if (!_current(revision) || !_same(before, after)) {
      throw const Wd13ClinicAccessUnavailable();
    }
    final record = Wd13Clinic.parse(response['data']);
    if (record.id != id) throw const FormatException('Clinic ID mismatch.');
    return record;
  }

  /// Exactly one POST attempt per actor/clinic name for this app-router lifetime.
  /// Does NOT retry on timeout, server error or ambiguous connection failure.
  Future<Wd13Clinic> createRoot(Wd13RootClinicDraft draft) async {
    final body = draft.toBackendJson(); // Validate before claiming.
    final revision = sessionNavigationGuard.revision;
    final before = await _authorized(revision);
    final name = body['name'] as String;
    if (!createGate.claim(actorId: before.userId, name: name)) {
      throw const Wd13ClinicMutationAlreadyAttempted();
    }
    if (!_current(revision)) throw const Wd13ClinicAccessUnavailable();
    final response = await apiClient.postJson(
      'api/v1/clinics',
      bearerToken: before.accessToken,
      body: body, // No idempotency key: backend does not support one here.
    );
    final after = await _authorized(revision);
    if (!_current(revision) || !_same(before, after)) {
      throw const Wd13ClinicAccessUnavailable();
    }
    if (response['message'] != 'Clinic created successfully') {
      throw const FormatException('Clinic creation receipt is invalid.');
    }
    final clinic = Wd13Clinic.parse(response['data']);
    if (clinic.name != name ||
        clinic.type != draft.type ||
        clinic.parentId != null ||
        clinic.headquarters != draft.headquarters ||
        (draft.operatingMode != null &&
            clinic.operatingMode != draft.operatingMode)) {
      throw const FormatException('Clinic creation receipt mismatch.');
    }
    return clinic;
  }
}
