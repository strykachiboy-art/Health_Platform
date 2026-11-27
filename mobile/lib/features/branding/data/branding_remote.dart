import 'dart:convert';
import 'dart:typed_data';
import 'dart:math' as math;

import 'package:cryptography/cryptography.dart';
import 'package:flutter/foundation.dart' show kDebugMode, kIsWeb;
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

const brandingStaged =
    kDebugMode && !kIsWeb && bool.fromEnvironment('CSP_STAGED_BRANDING');

enum BrandingScope { organization, clinic, entitlement }

class BrandingUnavailable implements Exception {
  const BrandingUnavailable();
}

final _uuid = RegExp(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$');
final _hex = RegExp(r'^#[0-9A-Fa-f]{6}$');
final _digest = RegExp(r'^[0-9a-f]{64}$');

bool validBrandingUuid(String value) =>
    value.length == 36 && _uuid.hasMatch(value);

bool validBrandingColor(String value) {
  if (value.length != 7 || !_hex.hasMatch(value)) {
    return false;
  }
  // WCAG sRGB luminance; same threshold as the pinned Flask contract.
  final rgb = [
    for (final i in [1, 3, 5])
      int.parse(value.substring(i, i + 2), radix: 16) / 255.0,
  ];
  final linear = rgb
      .map(
        (x) => x <= .04045
            ? x / 12.92
            : math.pow((x + .055) / 1.055, 2.4).toDouble(),
      )
      .toList();
  return 1.05 /
          (.2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2] + .05) >=
      4.5;
}

typedef BrandingActive = bool Function();

class BrandingState {
  BrandingState._(
    this.scope,
    this.organizationId,
    this.clinicId,
    this.name,
    this.version,
    this.overrideAllowed,
    this.primary,
    this.secondary,
    this.paletteSource,
    this.logoSource,
    this.logoSha,
    this.localPrimary,
    this.localSecondary,
    this.organizationVersion,
  );

  final BrandingScope scope;
  final String organizationId;
  final int? clinicId;
  final String name;
  final int version;
  final bool overrideAllowed;
  final String? primary, secondary, logoSha, localPrimary, localSecondary;
  final String? paletteSource, logoSource;
  final int organizationVersion;

  static int _version(Object? value) {
    if (value is! int || value < 0 || value > 2147483647) {
      throw const FormatException('Invalid branding version.');
    }
    return value;
  }

  static String _name(Object? value) {
    if (value is! String || value.trim().isEmpty || value.length > 255) {
      throw const FormatException('Invalid branding identity.');
    }
    return value;
  }

  static String? _color(Object? value) {
    if (value == null) {
      return null;
    }
    if (value is! String ||
        value != value.toUpperCase() ||
        !validBrandingColor(value)) {
      throw const FormatException('Invalid branding palette.');
    }
    return value;
  }

  static String? _sha(Object? value) {
    if (value == null) {
      return null;
    }
    if (value is! String || !_digest.hasMatch(value)) {
      throw const FormatException('Invalid branding logo digest.');
    }
    return value;
  }

  static void _pair(String? a, String? b) {
    if ((a == null) != (b == null)) {
      throw const FormatException('Incomplete palette.');
    }
  }

  factory BrandingState.parse(
    BrandingScope scope,
    Map<String, dynamic> data, {
    required String organizationId,
    int? clinicId,
  }) {
    if (!validBrandingUuid(organizationId)) {
      throw const BrandingUnavailable();
    }
    final allowed = data['clinic_override_allowed'];
    if (allowed is! bool) {
      throw const FormatException('Invalid branding entitlement.');
    }
    if (scope == BrandingScope.entitlement) {
      if (data.keys.toSet().difference({
            'version',
            'clinic_override_allowed',
          }).isNotEmpty ||
          data.length != 2) {
        throw const FormatException('Unexpected platform branding data.');
      }
      final version = _version(data['version']);
      return BrandingState._(
        scope,
        organizationId,
        null,
        'Organisation $organizationId',
        version,
        allowed,
        null,
        null,
        null,
        null,
        null,
        null,
        null,
        version,
      );
    }
    if (data['organization_id'] != organizationId) {
      throw const BrandingUnavailable();
    }
    final org = _name(data['organization_name']);
    final clinic = scope == BrandingScope.clinic;
    if (clinic &&
        (clinicId == null || clinicId <= 0 || data['clinic_id'] != clinicId)) {
      throw const BrandingUnavailable();
    }
    final version = _version(data[clinic ? 'clinic_version' : 'version']);
    final organizationVersion = clinic
        ? _version(data['organization_version'])
        : version;
    final primary = _color(
      data[clinic ? 'primary_color' : 'effective_primary_color'],
    );
    final secondary = _color(
      data[clinic ? 'secondary_color' : 'effective_secondary_color'],
    );
    _pair(primary, secondary);
    if (primary == null) {
      throw const FormatException('Missing effective palette.');
    }
    final localPrimary = _color(
      data[clinic ? 'clinic_primary_color' : 'primary_color'],
    );
    final localSecondary = _color(
      data[clinic ? 'clinic_secondary_color' : 'secondary_color'],
    );
    _pair(localPrimary, localSecondary);
    final palette = data['palette_source'];
    if (palette is! String ||
        !{'default', 'organization', if (clinic) 'clinic'}.contains(palette) ||
        (clinic && !allowed && palette == 'clinic')) {
      throw const FormatException('Invalid palette inheritance.');
    }
    final sha = _sha(data['logo_sha256']);
    final logo = clinic
        ? data['logo_source']
        : (sha == null ? 'none' : 'organization');
    if (logo is! String ||
        !{'none', 'organization', if (clinic) 'clinic'}.contains(logo) ||
        (logo == 'none') != (sha == null) ||
        (clinic && !allowed && logo == 'clinic') ||
        (!clinic && data['has_logo'] != (sha != null))) {
      throw const FormatException('Invalid logo inheritance.');
    }
    if (clinic && _sha(data['branding_sha256']) == null) {
      throw const FormatException('Missing branding digest.');
    }
    return BrandingState._(
      scope,
      organizationId,
      clinic ? clinicId : null,
      clinic ? '$org · ${_name(data['clinic_name'])}' : org,
      version,
      allowed,
      primary,
      secondary,
      palette,
      logo,
      sha,
      localPrimary,
      localSecondary,
      organizationVersion,
    );
  }
}

class BrandingView {
  const BrandingView(this.state, this.logo);
  final BrandingState state;
  final Uint8List? logo;
}

class BrandingCapabilities {
  const BrandingCapabilities({
    required this.organizationId,
    required this.name,
    required this.membershipRole,
    required this.clinicId,
    required this.organization,
    required this.clinic,
    required this.customize,
    required this.reset,
  });
  final String organizationId, name, membershipRole;
  final int? clinicId;
  final bool organization, clinic, customize, reset;
  factory BrandingCapabilities.parse(
    Map<String, dynamic> data,
    AuthTokenResponse actor,
  ) {
    final id = data['organization_id'];
    final role = data['membership_role'];
    final cid = data['clinic_id'];
    final org = data['can_manage_organization_branding'];
    final local = data['can_manage_clinic_branding'];
    final custom = data['can_customize_clinic_branding'];
    final reset = data['can_reset_clinic_branding'];
    if (data.length != 8 ||
        !data.containsKey('clinic_id') ||
        id is! String ||
        id != actor.tenantId ||
        !validBrandingUuid(id) ||
        cid != actor.clinicContextId ||
        role is! String ||
        !const {
          'member',
          'organization_admin',
          'billing_admin',
        }.contains(role) ||
        org is! bool ||
        local is! bool ||
        custom is! bool ||
        reset is! bool ||
        (org && role != 'organization_admin') ||
        (local && (cid is! int || cid <= 0 || actor.role != 'admin')) ||
        (custom && !local) ||
        reset != local) {
      throw const FormatException('Invalid branding capabilities.');
    }
    return BrandingCapabilities(
      organizationId: id,
      name: BrandingState._name(data['organization_name']),
      membershipRole: role,
      clinicId: cid as int?,
      organization: org,
      clinic: local,
      customize: custom,
      reset: reset,
    );
  }
}

class BrandingOrganization {
  const BrandingOrganization(this.id, this.name, this.version, this.allowed);
  final String id, name;
  final int version;
  final bool allowed;
  factory BrandingOrganization.parse(Object? value) {
    if (value is! Map<String, dynamic> || value.length != 4) {
      throw const FormatException('Invalid organisation record.');
    }
    final id = value['organization_id'];
    final allowed = value['clinic_override_allowed'];
    if (id is! String ||
        !validBrandingUuid(id) ||
        id != id.toLowerCase() ||
        allowed is! bool) {
      throw const FormatException('Invalid organisation record.');
    }
    return BrandingOrganization(
      id,
      BrandingState._name(value['organization_name']),
      BrandingState._version(value['version']),
      allowed,
    );
  }
}

class BrandingDirectoryPage {
  const BrandingDirectoryPage(this.items, this.next);
  final List<BrandingOrganization> items;
  final String? next;
  factory BrandingDirectoryPage.parse(
    Map<String, dynamic> data,
    String? after,
  ) {
    final raw = data['items'];
    final next = data['next_cursor'];
    if (data.length != 2 ||
        !data.containsKey('next_cursor') ||
        raw is! List ||
        raw.length > 50 ||
        (next != null && next is! String)) {
      throw const FormatException('Invalid organisation directory.');
    }
    final items = raw.map(BrandingOrganization.parse).toList(growable: false);
    var previous = after;
    for (final item in items) {
      if (previous != null && item.id.compareTo(previous) <= 0) {
        throw const FormatException('Invalid organisation ordering.');
      }
      previous = item.id;
    }
    if (next != null && (items.length != 50 || next != items.last.id)) {
      throw const FormatException('Invalid directory cursor.');
    }
    return BrandingDirectoryPage(List.unmodifiable(items), next as String?);
  }
}

class BrandingRemote {
  BrandingRemote({
    required this.api,
    required this.loadSession,
    required this.guard,
    this.staging = brandingStaged,
  });
  final ApiClient api;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard guard;
  final bool staging;
  static final _leases = Expando<_BrandingLease>('branding-session');
  static const maxLogo = 2 * 1024 * 1024;
  static final _busy = Expando<bool>('branding-mutation');

  Future<AuthTokenResponse> _actor(
    BrandingScope scope,
    int revision,
    BrandingActive active,
  ) async {
    if (!staging ||
        !active() ||
        !guard.canAccessProtectedRoutes ||
        guard.revision != revision) {
      throw const BrandingUnavailable();
    }
    final session = await loadSession();
    final actor = session.session;
    if (!active() ||
        !guard.canAccessProtectedRoutes ||
        guard.revision != revision ||
        session.disposition != AuthSessionDisposition.ready ||
        actor == null ||
        actor.userId <= 0 ||
        actor.accessToken.isEmpty ||
        actor.accessToken.trim() != actor.accessToken ||
        actor.refreshToken.isEmpty) {
      throw const BrandingUnavailable();
    }
    final role = actor.role.toLowerCase();
    if (scope == BrandingScope.entitlement) {
      if (role != 'super_admin' ||
          actor.clinicContextId != null ||
          actor.tenantId != null) {
        throw const BrandingUnavailable();
      }
    } else {
      if (role != 'admin' ||
          actor.tenantId == null ||
          !validBrandingUuid(actor.tenantId!) ||
          (scope == BrandingScope.clinic &&
              (actor.clinicContextId == null || actor.clinicContextId! <= 0))) {
        throw const BrandingUnavailable();
      }
    }
    return actor;
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.role == b.role &&
      a.tenantId == b.tenantId &&
      a.clinicContextId == b.clinicContextId &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  String _path(BrandingScope scope, String org) => switch (scope) {
    BrandingScope.organization => 'api/v1/clinics/branding/organization',
    BrandingScope.clinic => 'api/v1/clinics/branding/clinic',
    BrandingScope.entitlement =>
      'api/v1/clinics/organizations/$org/branding-entitlement',
  };

  String _org(
    BrandingScope scope,
    AuthTokenResponse actor,
    String? organization,
  ) {
    final value = scope == BrandingScope.entitlement
        ? organization
        : actor.tenantId;
    if (value == null ||
        !validBrandingUuid(value) ||
        (scope != BrandingScope.entitlement && organization != null)) {
      throw const BrandingUnavailable();
    }
    return value.toLowerCase();
  }

  Future<Uint8List> _send(
    String method,
    String path,
    AuthTokenResponse actor,
    BrandingActive active, {
    Map<String, dynamic>? body,
    Uint8List? logo,
    String? mime,
    bool image = false,
  }) {
    final http.BaseRequest request;
    if (logo != null) {
      final upload = http.MultipartRequest(method, api.config.endpoint(path));
      upload.fields['expected_version'] = '${body!['expected_version']}';
      upload.files.add(
        http.MultipartFile.fromBytes(
          'logo',
          logo,
          filename: mime == 'image/png' ? 'logo.png' : 'logo.jpg',
          contentType: MediaType.parse(mime!),
        ),
      );
      request = upload;
    } else {
      final json = http.Request(method, api.config.endpoint(path));
      if (body != null) {
        json.headers['Content-Type'] = 'application/json';
        json.body = jsonEncode(body);
      }
      request = json;
    }
    request.followRedirects = false;
    request.headers['Authorization'] = 'Bearer ${actor.accessToken}';
    request.headers['Accept'] = image ? 'image/png' : 'application/json';
    request.headers['Cache-Control'] = 'no-store';
    return api.sendPrivateBranding(request, image: image, active: active);
  }

  Map<String, dynamic> _data(Uint8List bytes) {
    final envelope = jsonDecode(utf8.decode(bytes));
    if (envelope is! Map<String, dynamic> ||
        envelope.length != 1 ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid branding response.');
    }
    return envelope['data'] as Map<String, dynamic>;
  }

  Future<BrandingView> _read(
    BrandingScope scope,
    String org,
    AuthTokenResponse actor,
    int revision,
    BrandingActive active,
  ) async {
    Future<void> check() async {
      if (!_same(actor, await _actor(scope, revision, active))) {
        throw const BrandingUnavailable();
      }
    }

    final path = _path(scope, org);
    await check();
    final state = BrandingState.parse(
      scope,
      _data(await _send('GET', path, actor, active)),
      organizationId: org,
      clinicId: actor.clinicContextId,
    );
    await check();
    Uint8List? bytes;
    if (state.logoSha != null) {
      bytes = await _send('GET', '$path/logo', actor, active, image: true);
      try {
        final dims = ByteData.sublistView(bytes);
        if (bytes.length < 33 ||
            !_png(bytes) ||
            dims.getUint32(8) != 13 ||
            dims.getUint32(12) != 0x49484452 ||
            dims.getUint32(16) < 1 ||
            dims.getUint32(16) > 512 ||
            dims.getUint32(20) < 1 ||
            dims.getUint32(20) > 512) {
          throw const FormatException('Invalid normalized logo.');
        }
        final hash = (await Sha256().hash(bytes)).bytes
            .map((n) => n.toRadixString(16).padLeft(2, '0'))
            .join();
        if (hash != state.logoSha) {
          throw const FormatException('Logo changed. Refresh branding.');
        }
        await check();
      } on Object {
        bytes.fillRange(0, bytes.length, 0);
        rethrow;
      }
    }
    _leases[state] = _BrandingLease(actor, revision);
    return BrandingView(state, bytes);
  }

  Future<BrandingView> read(
    BrandingScope scope, {
    String? organization,
    required BrandingActive active,
  }) async {
    final revision = guard.revision;
    final actor = await _actor(scope, revision, active);
    return _read(
      scope,
      _org(scope, actor, organization),
      actor,
      revision,
      active,
    );
  }

  Future<BrandingCapabilities> capabilities({
    required BrandingActive active,
  }) async {
    final revision = guard.revision;
    final actor = await _actor(BrandingScope.organization, revision, active);
    final data = _data(
      await _send('GET', 'api/v1/clinics/branding/capabilities', actor, active),
    );
    if (!_same(
      actor,
      await _actor(BrandingScope.organization, revision, active),
    )) {
      throw const BrandingUnavailable();
    }
    return BrandingCapabilities.parse(data, actor);
  }

  Future<BrandingDirectoryPage> directory({
    String? after,
    required BrandingActive active,
  }) async {
    if (after != null &&
        (!validBrandingUuid(after) || after != after.toLowerCase())) {
      throw const BrandingUnavailable();
    }
    final revision = guard.revision;
    final actor = await _actor(BrandingScope.entitlement, revision, active);
    final data = _data(
      await _send(
        'GET',
        'api/v1/clinics/branding/organizations${after == null ? '' : '?after=$after'}',
        actor,
        active,
      ),
    );
    if (!_same(
      actor,
      await _actor(BrandingScope.entitlement, revision, active),
    )) {
      throw const BrandingUnavailable();
    }
    return BrandingDirectoryPage.parse(data, after);
  }

  Future<BrandingOrganization> organizationDetail(
    String id, {
    required BrandingActive active,
  }) async {
    if (!validBrandingUuid(id) || id != id.toLowerCase()) {
      throw const BrandingUnavailable();
    }
    final revision = guard.revision;
    final actor = await _actor(BrandingScope.entitlement, revision, active);
    final data = _data(
      await _send(
        'GET',
        'api/v1/clinics/branding/organizations/$id',
        actor,
        active,
      ),
    );
    if (!_same(
      actor,
      await _actor(BrandingScope.entitlement, revision, active),
    )) {
      throw const BrandingUnavailable();
    }
    final result = BrandingOrganization.parse(data);
    if (result.id != id) {
      throw const BrandingUnavailable();
    }
    return result;
  }

  static bool _png(List<int> bytes) =>
      bytes.length >= 8 &&
      const [
        137,
        80,
        78,
        71,
        13,
        10,
        26,
        10,
      ].asMap().entries.every((e) => bytes[e.key] == e.value);
  static String logoMime(Uint8List bytes) {
    if (bytes.isEmpty || bytes.length > maxLogo) {
      throw const FormatException('Logo must be at most 2 MiB.');
    }
    if (_png(bytes)) {
      return 'image/png';
    }
    if (bytes.length >= 3 &&
        bytes[0] == 255 &&
        bytes[1] == 216 &&
        bytes[2] == 255) {
      return 'image/jpeg';
    }
    throw const FormatException('Choose a PNG or JPEG logo.');
  }

  Future<BrandingView> _change(
    BrandingState prior,
    String method,
    String suffix,
    Map<String, dynamic> body,
    BrandingActive active, {
    Uint8List? logo,
    String? mime,
  }) async {
    if (_busy[guard] == true || prior.version > 2147483646) {
      throw const BrandingUnavailable();
    }
    _busy[guard] = true;
    try {
      final revision = guard.revision;
      final actor = await _actor(prior.scope, revision, active);
      final lease = _leases[prior];
      if (lease == null ||
          lease.revision != revision ||
          !_same(lease.actor, actor)) {
        throw const BrandingUnavailable();
      }
      final org = _org(
        prior.scope,
        actor,
        prior.scope == BrandingScope.entitlement ? prior.organizationId : null,
      );
      if (org != prior.organizationId ||
          (prior.scope == BrandingScope.clinic &&
              actor.clinicContextId != prior.clinicId)) {
        throw const BrandingUnavailable();
      }
      final receipt = _data(
        await _send(
          method,
          '${_path(prior.scope, org)}$suffix',
          actor,
          active,
          body: {'expected_version': prior.version, ...body},
          logo: logo,
          mime: mime,
        ),
      );
      if (!_same(actor, await _actor(prior.scope, revision, active))) {
        throw const BrandingUnavailable();
      }
      final v = BrandingState._version(receipt['version']);
      if (v < prior.version || v > prior.version + 1) {
        throw const FormatException('Unexpected branding version.');
      }
      // A separate authoritative read is necessary after every mutation. Never retry a write.
      final verified = await _read(prior.scope, org, actor, revision, active);
      final state = verified.state;
      final matches = prior.scope == BrandingScope.entitlement
          ? receipt['clinic_override_allowed'] ==
                    body['clinic_override_allowed'] &&
                state.overrideAllowed == body['clinic_override_allowed']
          : suffix == '/colors'
          ? receipt['primary_color'] == body['primary_color'] &&
                receipt['secondary_color'] == body['secondary_color'] &&
                state.localPrimary == body['primary_color'] &&
                state.localSecondary == body['secondary_color']
          : method == 'DELETE'
          ? receipt['has_logo'] == false &&
                receipt['logo_sha256'] == null &&
                state.logoSource !=
                    (prior.scope == BrandingScope.clinic
                        ? 'clinic'
                        : 'organization')
          : receipt['has_logo'] == true &&
                receipt['logo_sha256'] != null &&
                receipt['logo_sha256'] == state.logoSha &&
                state.logoSource ==
                    (prior.scope == BrandingScope.clinic
                        ? 'clinic'
                        : 'organization');
      if (state.version != v || !matches) {
        verified.logo?.fillRange(0, verified.logo!.length, 0);
        throw const FormatException(
          'Branding changed again. Refresh before editing.',
        );
      }
      return verified;
    } finally {
      _busy[guard] = false;
    }
  }

  Future<BrandingView> setColors(
    BrandingState prior,
    String? primary,
    String? secondary,
    BrandingActive active,
  ) {
    if (prior.scope == BrandingScope.entitlement ||
        (primary == null) != (secondary == null) ||
        (primary != null &&
            (!validBrandingColor(primary) ||
                !validBrandingColor(secondary!) ||
                (prior.scope == BrandingScope.clinic &&
                    !prior.overrideAllowed)))) {
      throw const BrandingUnavailable();
    }
    return _change(prior, 'PUT', '/colors', {
      'primary_color': primary?.toUpperCase(),
      'secondary_color': secondary?.toUpperCase(),
    }, active);
  }

  Future<BrandingView> replaceLogo(
    BrandingState prior,
    Uint8List bytes,
    BrandingActive active,
  ) {
    if (prior.scope == BrandingScope.entitlement ||
        (prior.scope == BrandingScope.clinic && !prior.overrideAllowed)) {
      throw const BrandingUnavailable();
    }
    return _change(
      prior,
      'PUT',
      '',
      {},
      active,
      logo: bytes,
      mime: logoMime(bytes),
    );
  }

  Future<BrandingView> clearLogo(BrandingState prior, BrandingActive active) {
    if (prior.scope == BrandingScope.entitlement) {
      throw const BrandingUnavailable();
    }
    return _change(prior, 'DELETE', '', {}, active);
  }

  Future<BrandingView> setEntitlement(
    BrandingState prior,
    bool allowed,
    BrandingActive active,
  ) {
    if (prior.scope != BrandingScope.entitlement) {
      throw const BrandingUnavailable();
    }
    return _change(prior, 'PUT', '', {
      'clinic_override_allowed': allowed,
    }, active);
  }
}

class _BrandingLease {
  const _BrandingLease(this.actor, this.revision);
  final AuthTokenResponse actor;
  final int revision;
}
