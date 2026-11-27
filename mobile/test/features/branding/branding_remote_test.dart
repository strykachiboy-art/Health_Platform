import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:cryptography/cryptography.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/branding/data/branding_remote.dart';

const org = '12345678-1234-1234-1234-123456789abc';
const foreignOrg = '12345678-1234-1234-1234-123456789abd';
const headers = {
  'content-type': 'application/json',
  'cache-control': 'private, no-store, max-age=0',
  'x-content-type-options': 'nosniff',
};
AuthTokenResponse actor(
  String role, {
  String? tenant = org,
  int? clinic = 2,
  int user = 3,
}) => AuthTokenResponse(
  accessToken: 'token',
  refreshToken: 'refresh',
  userId: user,
  role: role,
  clinicContextId: clinic,
  tenantId: tenant,
);
Map<String, dynamic> orgState({
  int version = 2,
  String? primary,
  String? secondary,
  String organization = org,
}) => {
  'version': version,
  'organization_id': organization,
  'organization_name': 'Clinic Organisation',
  'clinic_override_allowed': true,
  'has_logo': false,
  'logo_sha256': null,
  'primary_color': primary,
  'secondary_color': secondary,
  'effective_primary_color': primary ?? '#176B65',
  'effective_secondary_color': secondary ?? '#243D54',
  'palette_source': primary == null ? 'default' : 'organization',
};
Map<String, dynamic> clinicState({bool allowed = false}) => {
  'clinic_version': 3,
  'organization_version': 2,
  'organization_id': org,
  'organization_name': 'Clinic Organisation',
  'clinic_id': 2,
  'clinic_name': 'Branch',
  'clinic_override_allowed': allowed,
  'logo_source': 'none',
  'logo_sha256': null,
  'primary_color': '#176B65',
  'secondary_color': '#243D54',
  'palette_source': 'organization',
  'clinic_primary_color': null,
  'clinic_secondary_color': null,
  'branding_sha256':
      '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
};
http.Response response(
  Map<String, dynamic> data, {
  Map<String, String> hdrs = headers,
}) => http.Response(jsonEncode({'data': data}), 200, headers: hdrs);

void main() {
  late SessionNavigationGuard guard;
  late AuthTokenResponse session;
  late ApiClient api;
  late BrandingRemote remote;
  late Future<http.Response> Function(http.Request) reply;
  late List<http.Request> calls;
  setUp(() {
    guard = SessionNavigationGuard()..markSessionAvailable();
    session = actor('admin');
    calls = [];
    reply = (_) async => response(orgState());
    api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: MockClient((req) async {
        calls.add(req);
        expect(req.url.hasQuery, isFalse);
        expect(req.followRedirects, isFalse);
        expect(req.headers['Authorization'], 'Bearer token');
        expect(
          req.headers.keys.any((k) => k.toLowerCase() == 'idempotency-key'),
          isFalse,
        );
        return reply(req);
      }),
    );
    remote = BrandingRemote(
      api: api,
      guard: guard,
      loadSession: () async => AuthSessionState.ready(session),
      staging: true,
    );
  });
  tearDown(() {
    api.close();
    guard.dispose();
  });
  Future<BrandingView> read([
    BrandingScope scope = BrandingScope.organization,
  ]) => remote.read(scope, active: () => true);
  test('palette validation matches backend contrast and format', () {
    for (final color in ['#000000', '#176B65', '#243D54', '#767676']) {
      expect(validBrandingColor(color), isTrue);
    }
    for (final color in [
      '#FFFFFF',
      '#777777',
      '#00FF00',
      'red',
      '#123',
      ' #176B65',
    ]) {
      expect(validBrandingColor(color), isFalse);
    }
  });
  test('staging closed and inappropriate role deny before HTTP', () async {
    final closed = BrandingRemote(
      api: api,
      guard: guard,
      loadSession: () async => AuthSessionState.ready(session),
      staging: false,
    );
    await expectLater(
      closed.read(BrandingScope.organization, active: () => true),
      throwsA(isA<BrandingUnavailable>()),
    );
    session = actor('doctor');
    await expectLater(read(), throwsA(isA<BrandingUnavailable>()));
    session = actor('super_admin', tenant: null, clinic: null);
    await expectLater(read(), throwsA(isA<BrandingUnavailable>()));
    expect(calls, isEmpty);
  });
  test('organization GET uses current session scope only', () async {
    final result = await read();
    expect(result.state.organizationId, org);
    expect(result.state.version, 2);
    expect(result.logo, isNull);
    expect(calls.single.url.path, '/api/v1/clinics/branding/organization');
    await expectLater(
      remote.read(
        BrandingScope.organization,
        organization: foreignOrg,
        active: () => true,
      ),
      throwsA(isA<BrandingUnavailable>()),
    );
    expect(calls.length, 1);
  });
  test('wrong tenant in server response denied', () async {
    reply = (_) async => response(orgState(organization: foreignOrg));
    await expectLater(read(), throwsA(isA<BrandingUnavailable>()));
  });
  test(
    'organization membership refusal does not expose editable snapshot',
    () async {
      reply = (_) async => http.Response('denied', 403);
      await expectLater(read(), throwsA(anything));
      expect(calls.length, 1);
    },
  );
  for (final missing in [
    'cache-control',
    'x-content-type-options',
    'content-type',
  ]) {
    test('missing $missing denied', () async {
      reply = (_) async =>
          response(orgState(), hdrs: Map.of(headers)..remove(missing));
      await expectLater(read(), throwsA(anything));
    });
  }
  test('stale session response and mutation lease rejected', () async {
    final prior = (await read()).state;
    session = actor('admin', user: 9);
    await expectLater(
      remote.setColors(prior, '#176B65', '#243D54', () => true),
      throwsA(isA<BrandingUnavailable>()),
    );
    expect(calls.length, 1);
    reply = (_) async {
      session = actor('admin', clinic: 5);
      return response(orgState());
    };
    await expectLater(read(), throwsA(isA<BrandingUnavailable>()));
  });
  test(
    'color PUT uses exactly expected version then authoritative GET',
    () async {
      final prior = (await read()).state;
      reply = (req) async {
        if (req.method == 'PUT') {
          expect(req.url.path, '/api/v1/clinics/branding/organization/colors');
          expect(jsonDecode(req.body), {
            'expected_version': 2,
            'primary_color': '#176B65',
            'secondary_color': '#243D54',
          });
          return response({
            'version': 3,
            'has_logo': false,
            'logo_sha256': null,
            'primary_color': '#176B65',
            'secondary_color': '#243D54',
          });
        }
        return response(
          orgState(version: 3, primary: '#176B65', secondary: '#243D54'),
        );
      };
      final updated = await remote.setColors(
        prior,
        '#176b65',
        '#243D54',
        () => true,
      );
      expect(updated.state.version, 3);
      expect(calls.map((c) => c.method), ['GET', 'PUT', 'GET']);
    },
  );
  test('conflict sends no retry', () async {
    final prior = (await read()).state;
    reply = (_) async => http.Response('conflict', 409);
    await expectLater(
      remote.setColors(prior, '#176B65', '#243D54', () => true),
      throwsA(anything),
    );
    expect(calls.map((c) => c.method), ['GET', 'PUT']);
  });
  test(
    'revoked clinic can reset but cannot set a custom palette or upload',
    () async {
      reply = (_) async => response(clinicState());
      final prior = (await read(BrandingScope.clinic)).state;
      expect(prior.overrideAllowed, isFalse);
      expect(
        () => remote.setColors(prior, '#176B65', '#243D54', () => true),
        throwsA(isA<BrandingUnavailable>()),
      );
      expect(
        () => remote.replaceLogo(
          prior,
          Uint8List.fromList([255, 216, 255]),
          () => true,
        ),
        throwsA(isA<BrandingUnavailable>()),
      );
      reply = (req) async {
        if (req.method == 'PUT') {
          expect(jsonDecode(req.body), {
            'expected_version': 3,
            'primary_color': null,
            'secondary_color': null,
          });
          return response({
            'version': 3,
            'has_logo': false,
            'logo_sha256': null,
            'primary_color': null,
            'secondary_color': null,
          });
        }
        return response(clinicState());
      };
      expect(
        (await remote.setColors(prior, null, null, () => true)).state.version,
        3,
      );
      expect(calls.map((c) => c.method), ['GET', 'PUT', 'GET']);
    },
  );
  test('entitlement unscoped SA only; no logo access; versioned PUT', () async {
    await expectLater(
      remote.read(
        BrandingScope.entitlement,
        organization: org,
        active: () => true,
      ),
      throwsA(isA<BrandingUnavailable>()),
    );
    session = actor('super_admin', tenant: null, clinic: null);
    var allowed = false;
    reply = (req) async {
      expect(
        req.url.path,
        '/api/v1/clinics/organizations/$org/branding-entitlement',
      );
      if (req.method == 'PUT') {
        expect(jsonDecode(req.body), {
          'expected_version': 0,
          'clinic_override_allowed': true,
        });
        allowed = true;
      }
      return response({
        'version': allowed ? 1 : 0,
        'clinic_override_allowed': allowed,
      });
    };
    final prior = (await remote.read(
      BrandingScope.entitlement,
      organization: org,
      active: () => true,
    )).state;
    final next = await remote.setEntitlement(prior, true, () => true);
    expect(next.state.overrideAllowed, isTrue);
    expect(next.logo, isNull);
    expect(calls.map((c) => c.method), ['GET', 'PUT', 'GET']);
  });
  test('entitlement payload containing logo information denied', () async {
    session = actor('super_admin', tenant: null, clinic: null);
    reply = (_) async => response({
      'version': 0,
      'clinic_override_allowed': false,
      'logo_sha256': 'not allowed',
    });
    await expectLater(
      remote.read(
        BrandingScope.entitlement,
        organization: org,
        active: () => true,
      ),
      throwsFormatException,
    );
  });
  test(
    'normalized logo hash verified; upload and delete use fixed wire fields',
    () async {
      final png = base64Decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=',
      );
      final hash = (await Sha256().hash(png)).bytes
          .map((n) => n.toRadixString(16).padLeft(2, '0'))
          .join();
      var version = 2;
      var has = false;
      reply = (req) async {
        if (req.url.path.endsWith('/logo')) {
          expect(req.method, 'GET');
          return http.Response.bytes(
            png,
            200,
            headers: {...headers, 'content-type': 'image/png'},
          );
        }
        if (req.method == 'PUT') {
          expect(
            req.headers['content-type'],
            startsWith('multipart/form-data;'),
          );
          expect(
            latin1.decode(req.bodyBytes),
            contains('name="expected_version"'),
          );
          expect(latin1.decode(req.bodyBytes), contains('name="logo"'));
          expect(latin1.decode(req.bodyBytes), contains('filename="logo.png"'));
          has = true;
          version++;
        }
        if (req.method == 'DELETE') {
          expect(jsonDecode(req.body), {'expected_version': 3});
          has = false;
          version++;
        }
        final data = {
          'version': version,
          'has_logo': has,
          'logo_sha256': has ? hash : null,
          'primary_color': null,
          'secondary_color': null,
        };
        return response(
          req.method == 'GET' ? {...orgState(version: version), ...data} : data,
        );
      };
      final start = (await read()).state;
      final next = await remote.replaceLogo(start, png, () => true);
      expect(next.state.logoSha, hash);
      expect(next.logo, png);
      final cleared = await remote.clearLogo(next.state, () => true);
      expect(cleared.state.version, 4);
      expect(cleared.logo, isNull);
      expect(calls.map((c) => c.method), [
        'GET',
        'PUT',
        'GET',
        'GET',
        'DELETE',
        'GET',
      ]);
    },
  );
  test('changed logo digest fails closed', () async {
    final png = base64Decode(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=',
    );
    reply = (req) async => req.url.path.endsWith('/logo')
        ? http.Response.bytes(
            png,
            200,
            headers: {...headers, 'content-type': 'image/png'},
          )
        : response({
            ...orgState(),
            'has_logo': true,
            'logo_sha256': '0000000000000000000000000000000000000000000000000000000000000000',
          });
    await expectLater(read(), throwsFormatException);
  });
  test('logo MIME and size bounded', () {
    expect(
      BrandingRemote.logoMime(Uint8List.fromList([255, 216, 255])),
      'image/jpeg',
    );
    expect(
      BrandingRemote.logoMime(
        Uint8List.fromList([137, 80, 78, 71, 13, 10, 26, 10]),
      ),
      'image/png',
    );
    expect(
      () => BrandingRemote.logoMime(Uint8List(BrandingRemote.maxLogo + 1)),
      throwsFormatException,
    );
    expect(
      () => BrandingRemote.logoMime(Uint8List.fromList([1, 2, 3])),
      throwsFormatException,
    );
  });
}
