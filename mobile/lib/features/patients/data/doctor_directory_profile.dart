part of 'doctor_observation_source.dart';

class DoctorDirectorySession {
  const DoctorDirectorySession._(this._actor, this.revision);
  final AuthTokenResponse _actor;
  final int revision;
  int get clinicId => _actor.clinicContextId!;
}

class DoctorDirectoryPage<T> {
  const DoctorDirectoryPage(this.items, this.page, this.total);
  final List<T> items;
  final int page;
  final int total;
}

class DoctorOwnProfile {
  const DoctorOwnProfile._(this._session, this.staffId, this.firstName,
    this.lastName, this.phone, this.specialty, this._imageKey);
  final DoctorDirectorySession _session;
  final int staffId;
  final String firstName;
  final String lastName;
  final String? phone;
  final String? specialty;
  final String? _imageKey;
  bool get hasImage => _imageKey != null;
}

extension DoctorDirectoryProfile on DoctorObservationSource {
  Future<DoctorDirectorySession> directorySession() async {
    final revision = reader.sessionNavigationGuard.revision;
    final actor = await _actor(revision);
    return DoctorDirectorySession._(actor, revision);
  }

  Future<Map<String, dynamic>> _directoryGet(DoctorDirectorySession session, String path) async {
    await _check(session._actor, session.revision);
    final raw = await reader.apiClient.getJson(path, bearerToken: session._actor.accessToken);
    await _check(session._actor, session.revision);
    return raw;
  }

  static Map<String, dynamic> _map(Object? raw) {
    if (raw is! Map<String, dynamic>) {
      throw const FormatException('Invalid server object.');
    }
    return raw;
  }

  static int _id(Object? raw) {
    if (raw is! int || raw <= 0) {
      throw const FormatException('Invalid server identity.');
    }
    return raw;
  }

  static String? _optional(Object? raw, int max) {
    if (raw == null) {
      return null;
    }
    if (raw is! String || raw.trim().isEmpty || raw.length > max) {
      throw const FormatException('Invalid profile field.');
    }
    return raw;
  }

  Future<DoctorOwnProfile> ownProfile(DoctorDirectorySession session) async {
    final envelope = await _directoryGet(session, 'api/v1/profile/me');
    AuthenticatedProfile.fromEnvelope(envelope, expectedUserId: session._actor.userId, expectedClinicId: session.clinicId);
    final data = DoctorObservationSource._data(envelope);
    final user = _map(data['user']);
    final staff = _map(data['staff']);
    if (staff['user_id'] != session._actor.userId || staff['clinic_id'] != session.clinicId) {
      throw const FormatException('Profile staff identity mismatch.');
    }
    return DoctorOwnProfile._(session, _id(staff['id']),
      _optional(staff['first_name'], 150)!, _optional(staff['last_name'], 150)!,
      _optional(staff['phone'], 50), _optional(staff['specialty'], 255),
      _optional(user['profile_image_storage_key'], 500));
  }

  Future<DoctorDirectoryPage<Patient>> myPatients(DoctorDirectorySession session, {int page = 1}) async {
    final data = await _paged(session, 'api/v1/consultations/my-patients', page);
    final items = <Patient>[];
    final seen = <int>{};
    for (final raw in data.items) {
      final id = _id(raw['id']);
      if (!seen.add(id)) {
        throw const FormatException('Duplicate patient.');
      }
      // The summary endpoint omits clinic_id; never display its PHI until a
      // second server patient read confirms the exact clinic and identity.
      final patient = Patient.fromJson(DoctorObservationSource._data(await _directoryGet(session, 'api/v1/patients/$id')));
      if (patient.id != id || patient.clinicId != session.clinicId) {
        throw const FormatException('Patient scope mismatch.');
      }
      items.add(patient);
    }
    return DoctorDirectoryPage(List.unmodifiable(items), page, data.total);
  }

  Future<DoctorDirectoryPage<Map<String, dynamic>>> _paged(DoctorDirectorySession session, String path, int page) async {
    if (page <= 0) {
      throw const FormatException('Invalid page.');
    }
    final raw = await _directoryGet(session, '$path?page=$page&per_page=20');
    final rows = raw['data'];
    final pagination = _map(raw['pagination']);
    final total = pagination['total'];
    if (raw['success'] != true || rows is! List || rows.length > 20 ||
        total is! int || total < 0 || rows.length > total || pagination['page'] != page ||
        pagination['per_page'] != 20 || pagination['pages'] != (total + 19) ~/ 20 ||
        pagination['has_next'] != (page * 20 < total) || pagination['has_prev'] != (page > 1)) {
      throw const FormatException('Invalid directory pagination.');
    }
    return DoctorDirectoryPage(rows.map(_map).toList(), page, total);
  }

  Future<Wd05AdministrationReadPage> staffDirectory(DoctorDirectorySession session, {int page = 1}) async {
    await _check(session._actor, session.revision);
    final source = Wd05AdministrationReadRemoteDataSource(apiClient: reader.apiClient,
      loadSession: reader.loadSession, sessionNavigationGuard: reader.sessionNavigationGuard);
    final rows = await source.load(section: Wd05AdministrationSection.staff, page: page, perPage: 20);
    await _check(session._actor, session.revision);
    return rows;
  }

  Future<DoctorDirectoryPage<PatientConsultation>> staffHistory(DoctorDirectorySession session, int staffId, {int page = 1}) async {
    if (staffId <= 0) {
      throw const FormatException('Invalid staff ID.');
    }
    final detail = _map((await _directoryGet(session, 'api/v1/staff/$staffId'))['data']);
    if (detail['id'] != staffId || detail['clinic_id'] != session.clinicId) {
      throw const FormatException('Staff scope mismatch.');
    }
    final data = await _paged(session, 'api/v1/consultations/staff/$staffId', page);
    final rows = <PatientConsultation>[];
    final seen = <int>{};
    for (final raw in data.items) {
      final row = PatientConsultation.fromJson(raw, expectedPatientId: _id(raw['patient_id']), expectedClinicId: session.clinicId);
      if (row.staffId != staffId || !seen.add(row.id)) {
        throw const FormatException('Consultation scope mismatch.');
      }
      rows.add(row);
    }
    return DoctorDirectoryPage(List.unmodifiable(rows), page, data.total);
  }

  Future<T> _profileWrite<T>(DoctorOwnProfile prior, Future<T> Function() perform) async {
    final session = prior._session;
    await _check(session._actor, session.revision);
    if (await connectivity.checkStatus() != DeviceNetworkStatus.connected) {
      throw const DoctorObservationUnavailable();
    }
    final fresh = await ownProfile(session);
    if (fresh.staffId != prior.staffId || fresh.firstName != prior.firstName || fresh.lastName != prior.lastName ||
        fresh.phone != prior.phone || fresh.specialty != prior.specialty || fresh._imageKey != prior._imageKey) {
      throw const DoctorObservationConflict();
    }
    final key = 'doctor-profile:${session._actor.userId}';
    await _check(session._actor, session.revision);
    if (gate.contains('$key:uncertain') || _inFlight.contains(key)) {
      throw const DoctorObservationUnconfirmed();
    }
    _inFlight.add(key);
    try {
      final result = await perform();
      await _check(session._actor, session.revision);
      return result;
    } on Object {
      gate.claim('$key:uncertain');
      throw const DoctorObservationUnconfirmed();
    } finally {
      _inFlight.remove(key);
    }
  }

  Future<DoctorOwnProfile> updateOwnProfile(DoctorOwnProfile prior, {required String firstName,
      required String lastName, required String phone, required String specialty}) async {
    final body = <String, dynamic>{'first_name': firstName.trim(), 'last_name': lastName.trim(),
      'phone': phone.trim().isEmpty ? null : phone.trim(), 'specialty': specialty.trim().isEmpty ? null : specialty.trim()};
    if (firstName.trim().isEmpty || firstName.trim().length > 150 || lastName.trim().isEmpty ||
        lastName.trim().length > 150 || phone.trim().length > 50 || specialty.trim().length > 255) {
      throw const FormatException('Invalid profile fields.');
    }
    return _profileWrite(prior, () async {
      final session = prior._session;
      final response = await reader.apiClient.patchJson('api/v1/profile/me', body: body, bearerToken: session._actor.accessToken);
      AuthenticatedProfile.fromEnvelope(response, expectedUserId: session._actor.userId, expectedClinicId: session.clinicId);
      final fresh = await ownProfile(session);
      if (fresh.staffId != prior.staffId || fresh.firstName != body['first_name'] || fresh.lastName != body['last_name'] ||
          fresh.phone != body['phone'] || fresh.specialty != body['specialty']) {
        throw const FormatException('Profile update unconfirmed.');
      }
      return fresh;
    });
  }

  static String imageMime(Uint8List bytes) {
    if (bytes.isEmpty || bytes.length > 5 * 1024 * 1024) {
      throw const FormatException('Profile image exceeds size limit.');
    }
    if (bytes.length >= 8 && const [137,80,78,71,13,10,26,10].asMap().entries.every((e) => bytes[e.key] == e.value)) {
      return 'image/png';
    }
    if (bytes.length >= 3 && bytes[0] == 255 && bytes[1] == 216 && bytes[2] == 255) {
      return 'image/jpeg';
    }
    throw const FormatException('Only PNG and JPEG profile images are accepted.');
  }

  Future<DoctorOwnProfile> uploadOwnImage(DoctorOwnProfile prior, Uint8List bytes) async {
    final mime = imageMime(bytes);
    // No arbitrary filename or destination, no redirect forwarding of bearer.
    return _profileWrite(prior, () async {
      final session = prior._session;
      final uri = reader.apiClient.config.endpoint('api/v1/profile/me/image');
      final request = http.MultipartRequest('POST', uri)
        ..followRedirects = false
        ..headers['Authorization'] = 'Bearer ${session._actor.accessToken}'
        ..headers['Accept'] = 'application/json';
      request.files.add(http.MultipartFile.fromBytes('image', bytes,
        filename: mime == 'image/png' ? 'profile.png' : 'profile.jpg', contentType: MediaType.parse(mime)));
      final client = http.Client();
      try {
        final streamed = await client.send(request).timeout(const Duration(seconds: 40));
        final response = await http.Response.fromStream(streamed).timeout(const Duration(seconds: 40));
        if (response.statusCode == 401) {
          reader.apiClient.onAuthenticationRejected?.call();
        }
        if (response.statusCode != 200) {
          throw const DoctorObservationUnconfirmed();
        }
        final raw = DoctorObservationSource._data(_map(jsonDecode(utf8.decode(response.bodyBytes))));
        final key = _optional(raw['profile_image_storage_key'], 500);
        if (key == null) {
          throw const FormatException('Image upload not confirmed.');
        }
        final fresh = await ownProfile(session);
        if (fresh._imageKey != key || fresh.staffId != prior.staffId) {
          throw const FormatException('Image readback mismatch.');
        }
        return fresh;
      } finally {
        client.close();
      }
    });
  }

  Future<DoctorOwnProfile> removeOwnImage(DoctorOwnProfile prior) async {
    if (!prior.hasImage) {
      throw const FormatException('No reviewed image to remove.');
    }
    return _profileWrite(prior, () async {
      final session = prior._session;
      final raw = DoctorObservationSource._data(await reader.apiClient.deleteJson('api/v1/profile/me/image', bearerToken: session._actor.accessToken));
      final fresh = await ownProfile(session);
      if (!raw.containsKey('profile_image_storage_key') || raw['profile_image_storage_key'] != null || fresh.hasImage || fresh.staffId != prior.staffId) {
        throw const FormatException('Image removal not confirmed.');
      }
      return fresh;
    });
  }
}
