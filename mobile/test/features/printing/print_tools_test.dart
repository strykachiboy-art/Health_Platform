import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/printing/data/print_tools_remote.dart';
import 'package:clinic_system_pro/features/branding/data/branding_remote.dart';

const token = '0123456789abcdef0123456789abcdef';
const digest =
    '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
const headers = {
  'content-type': 'application/json',
  'cache-control': 'private, no-store, max-age=0',
  'x-content-type-options': 'nosniff',
};
const org = '12345678-1234-1234-1234-123456789abc';
AuthTokenResponse actor(String role, {int? clinic = 2, String? tenant = org}) =>
    AuthTokenResponse(
      accessToken: 'token',
      refreshToken: 'refresh',
      userId: 3,
      role: role,
      clinicContextId: clinic,
      tenantId: tenant,
    );
http.Response json(Map<String, dynamic> data) =>
    http.Response(jsonEncode({'data': data}), 200, headers: headers);
Map<String, dynamic> caps({
  bool orgAllowed = true,
  bool local = true,
  bool custom = true,
}) => {
  'organization_id': org,
  'organization_name': 'Synthetic Hospital',
  'membership_role': orgAllowed ? 'organization_admin' : 'member',
  'clinic_id': 2,
  'can_manage_organization_branding': orgAllowed,
  'can_manage_clinic_branding': local,
  'can_customize_clinic_branding': custom,
  'can_reset_clinic_branding': local,
};
Map<String, dynamic> item(String id) => {
  'organization_id': id,
  'organization_name': 'Synthetic Hospital',
  'version': 1,
  'clinic_override_allowed': false,
};
Map<String, dynamic> verified(bool? match) => {
  'issuance_record_found': true,
  'file_hash_match': match,
  'status': 'historical_issuance_only',
  'notice': HistoricalVerification.notice,
};
void main() {
  late ApiClient api;
  late SessionNavigationGuard guard;
  late AuthTokenResponse current;
  late PrintToolsRemote tools;
  late BrandingRemote branding;
  late List<http.Request> calls;
  late Future<http.Response> Function(http.Request) reply;
  setUp(() {
    current = actor('admin');
    guard = SessionNavigationGuard()..markSessionAvailable();
    calls = [];
    reply = (_) async => json(verified(null));
    api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: MockClient((req) async {
        calls.add(req);
        expect(req.followRedirects, isFalse);
        return reply(req);
      }),
    );
    tools = PrintToolsRemote(
      api: api,
      guard: guard,
      loadSession: () async => AuthSessionState.ready(current),
      staging: true,
    );
    branding = BrandingRemote(
      api: api,
      guard: guard,
      loadSession: () async => AuthSessionState.ready(current),
      staging: true,
    );
  });
  tearDown(() {
    api.close();
    guard.dispose();
  });
  bool active() => true;
  test(
    'historical lookup sends no bearer and does not claim clinical validity',
    () async {
      expect((await tools.verify(token, null, active)).hashMatch, isNull);
      expect(calls.single.url.path, '/api/v1/printing/verify/$token');
      expect(calls.single.headers.containsKey('Authorization'), isFalse);
    },
  );
  test(
    'PDF hash comparison sends only digest and preserves mismatch',
    () async {
      reply = (req) async {
        expect(req.method, 'POST');
        expect(jsonDecode(req.body), {'pdf_sha256': digest});
        return json(verified(false));
      };
      expect((await tools.verify(token, digest, active)).hashMatch, isFalse);
    },
  );
  test('wrong verification status and malformed token fail closed', () async {
    await expectLater(
      tools.verify('https://foreign.invalid', null, active),
      throwsFormatException,
    );
    expect(calls, isEmpty);
    reply = (_) async => json({...verified(null), 'status': 'current'});
    await expectLater(tools.verify(token, null, active), throwsFormatException);
  });
  test(
    'public verification rejection does not invalidate auth session',
    () async {
      var rejections = 0;
      api.onAuthenticationRejected = () => rejections++;
      reply = (_) async => http.Response('denied', 401);
      await expectLater(tools.verify(token, null, active), throwsA(anything));
      expect(rejections, 0);
    },
  );
  for (final paper in [58, 80]) {
    test('validated $paper mm stream has correct reference and no dispatch', () async {
      final text =
          'STRYKA-MAX\nPAYMENT RECORD COPY\nNOT A BANK SETTLEMENT\nCERTIFICATE\nClinic reference: 2\nInvoice: SYNTH\nPayment reference: 7\nPayment status: successful\nPaid amount: NGN 40.00\nPowered by Stryka-Max\n\n\n';
      reply = (req) async {
        expect(
          req.url.path,
          '/api/v1/printing/thermal/$paper/payment/7.escpos',
        );
        return http.Response.bytes(
          [27, 64, ...ascii.encode(text)],
          200,
          headers: {
            ...headers,
            'content-type': 'application/octet-stream',
            'content-disposition': 'attachment; filename=payment-7.escpos',
          },
        );
      };
      final result = await tools.thermal(7, paper, active);
      expect(result.paper, paper);
      expect(result.length, 2 + text.length);
      expect(result.sha256.length, 64);
      expect(calls.length, 1);
    });
  }
  test('thermal role and invalid paper deny before HTTP', () async {
    current = actor('doctor');
    await expectLater(
      tools.thermal(7, 80, active),
      throwsA(isA<BrandingUnavailable>()),
    );
    current = actor('admin');
    await expectLater(tools.thermal(7, 40, active), throwsFormatException);
    expect(calls, isEmpty);
  });
  test('thermal command injection rejected', () async {
    reply = (_) async => http.Response.bytes(
      [27, 64, 27, 112, 0, 0, 0, 65, 10, 10],
      200,
      headers: {
        ...headers,
        'content-type': 'application/octet-stream',
        'content-disposition': 'attachment; filename=payment-7.escpos',
      },
    );
    await expectLater(tools.thermal(7, 80, active), throwsFormatException);
  });
  test('capabilities distinguish organization and clinic grants', () async {
    reply = (req) async {
      expect(req.url.path, '/api/v1/clinics/branding/capabilities');
      return json(caps(orgAllowed: false, custom: false));
    };
    final result = await branding.capabilities(active: active);
    expect(result.organization, isFalse);
    expect(result.clinic, isTrue);
    expect(result.customize, isFalse);
    expect(result.reset, isTrue);
  });
  test('forged scope and inconsistent capability denied', () async {
    reply = (_) async => json({...caps(), 'clinic_id': 9});
    await expectLater(
      branding.capabilities(active: active),
      throwsFormatException,
    );
    reply = (_) async => json(caps(local: false, custom: true));
    await expectLater(
      branding.capabilities(active: active),
      throwsFormatException,
    );
  });
  test(
    'directory requires unscoped platform actor and permits only cursor',
    () async {
      await expectLater(
        branding.directory(active: active),
        throwsA(isA<BrandingUnavailable>()),
      );
      expect(calls, isEmpty);
      current = actor('super_admin', clinic: null, tenant: null);
      reply = (req) async {
        expect(req.url.queryParameters, {'after': org});
        return json({'items': [], 'next_cursor': null});
      };
      expect(
        (await branding.directory(after: org, active: active)).items,
        isEmpty,
      );
    },
  );
  test('directory duplicate and foreign detail rejected', () async {
    current = actor('super_admin', clinic: null, tenant: null);
    reply = (_) async => json({
      'items': [item(org), item(org)],
      'next_cursor': null,
    });
    await expectLater(
      branding.directory(active: active),
      throwsFormatException,
    );
    reply = (_) async => json(item('12345678-1234-1234-1234-123456789abd'));
    await expectLater(
      branding.organizationDetail(org, active: active),
      throwsA(isA<BrandingUnavailable>()),
    );
  });
  test('session switch discards verification result', () async {
    reply = (_) async {
      current = actor('admin', clinic: 3);
      return json(verified(null));
    };
    await expectLater(
      tools.verify(token, null, active),
      throwsA(isA<BrandingUnavailable>()),
    );
  });
}
