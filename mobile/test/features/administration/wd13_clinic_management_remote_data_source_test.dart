import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/administration/data/wd13_clinic_management_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

AuthSessionState _session(String role, {int? context}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'wd13-access',
        refreshToken: 'wd13-refresh',
        userId: 7,
        role: role,
        clinicContextId: context,
      ),
    );

Map<String, Object?> _clinic({int id = 12, String name = 'Root A'}) => {
  'id': id,
  'name': name,
  'clinic_type': 'general',
  'status': 'active',
  'is_headquarters': false,
  'parent_clinic_id': null,
  'timezone': 'Africa/Lagos',
};

Wd13ClinicManagementRemoteDataSource _source(
  MockClient fake,
  SessionNavigationGuard guard, {
  required Future<AuthSessionState> Function() load,
  Wd13RootClinicAttemptGate? gate,
}) => Wd13ClinicManagementRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: fake,
  ),
  loadSession: load,
  sessionNavigationGuard: guard,
  createGate: gate ?? Wd13RootClinicAttemptGate(),
);

void main() {
  test('all backend-supported profile fields and root-only parent payload', () {
    const draft = Wd13RootClinicDraft(
      name: ' New Clinic ',
      type: 'specialist',
      headquarters: true,
      timezone: 'Africa/Lagos',
      address: '123 Main St',
      city: 'Owerri',
      country: 'Nigeria',
      phone: '+2348000000000',
      email: 'clinic@example.org',
      openingTime: '08:00',
      closingTime: '17:30',
    );
    final body = draft.toBackendJson();
    expect(body['name'], 'New Clinic');
    expect(body['parent_clinic_id'], isNull);
    expect(body['is_headquarters'], true);
    expect(body['clinic_type'], 'specialist');
    expect(body['opening_time'], '08:00:00');
    expect(body['closing_time'], '17:30:00');
    expect(body['address'], '123 Main St');
    expect(body['city'], 'Owerri');
    expect(body['country'], 'Nigeria');
    expect(body['phone'], '+2348000000000');
    expect(body['email'], 'clinic@example.org');
    expect(body['timezone'], 'Africa/Lagos');
    expect(
      body.keys,
      containsAll(<String>[
        'name',
        'clinic_type',
        'parent_clinic_id',
        'is_headquarters',
        'timezone',
        'address',
        'city',
        'country',
        'phone',
        'email',
        'opening_time',
        'closing_time',
      ]),
    );
    expect(
      () => const Wd13RootClinicDraft(
        name: '',
        type: 'general',
        headquarters: false,
        timezone: 'UTC',
      ).toBackendJson(),
      throwsFormatException,
    );
    expect(
      () => const Wd13RootClinicDraft(
        name: 'A',
        type: 'general',
        headquarters: false,
        timezone: 'UTC',
        openingTime: '20:00',
        closingTime: '08:00',
      ).toBackendJson(),
      throwsFormatException,
    );
    expect(
      () => const Wd13RootClinicDraft(
        name: 'A',
        type: 'general',
        headquarters: false,
        timezone: 'UTC',
        openingTime: '99:00',
      ).toBackendJson(),
      throwsFormatException,
    );
  });

  test(
    'Super Admin without selected clinic can list and read; server filters',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final paths = <String>[];
      final fake = MockClient((request) async {
        paths.add(
          request.url.path +
              (request.url.hasQuery ? '?${request.url.query}' : ''),
        );
        expect(request.headers['authorization'], 'Bearer wd13-access');
        if (request.url.path.endsWith('/clinics')) {
          return http.Response(
            jsonEncode({
              'data': [_clinic()],
            }),
            200,
          );
        }
        return http.Response(jsonEncode({'data': _clinic()}), 200);
      });
      addTearDown(fake.close);
      final source = _source(
        fake,
        guard,
        load: () async => _session('super_admin'),
      );
      expect((await source.list(status: 'active')).single.id, 12);
      expect((await source.read(12)).name, 'Root A');
      expect(paths, <String>[
        '/api/v1/clinics?status=active',
        '/api/v1/clinics/12',
      ]);
    },
  );

  test('Admin and doctor cannot use Super Admin clinic registry', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var calls = 0;
    final fake = MockClient((request) async {
      calls++;
      throw StateError('Unauthorized request: $request');
    });
    addTearDown(fake.close);
    for (final role in <String>['admin', 'doctor', 'patient']) {
      final source = _source(fake, guard, load: () async => _session(role));
      await expectLater(
        source.list(),
        throwsA(isA<Wd13ClinicAccessUnavailable>()),
      );
      await expectLater(
        source.createRoot(
          const Wd13RootClinicDraft(
            name: 'No',
            type: 'general',
            headquarters: false,
            timezone: 'UTC',
          ),
        ),
        throwsA(isA<Wd13ClinicAccessUnavailable>()),
      );
    }
    expect(calls, 0);
  });

  test(
    'one confirmed POST, exact payload and no replay of same name',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var posts = 0;
      final fake = MockClient((request) async {
        expect(request.url.path, '/api/v1/clinics');
        expect(request.method, 'POST');
        posts++;
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body['parent_clinic_id'], isNull);
        expect(body['is_headquarters'], false);
        expect(body['name'], 'Root A');
        expect(request.headers.containsKey('idempotency-key'), false);
        return http.Response(
          jsonEncode({
            'message': 'Clinic created successfully',
            'data': _clinic(),
          }),
          201,
        );
      });
      addTearDown(fake.close);
      final gate = Wd13RootClinicAttemptGate();
      final source = _source(
        fake,
        guard,
        load: () async => _session('super_admin'),
        gate: gate,
      );
      const draft = Wd13RootClinicDraft(
        name: 'Root A',
        type: 'general',
        headquarters: false,
        timezone: 'Africa/Lagos',
      );
      expect((await source.createRoot(draft)).id, 12);
      await expectLater(
        source.createRoot(draft),
        throwsA(isA<Wd13ClinicMutationAlreadyAttempted>()),
      );
      expect(posts, 1);
    },
  );

  test(
    'ambiguous POST does not retry, even when a new source is constructed',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var calls = 0;
      final fake = MockClient((request) async {
        calls++;
        throw const FormatException('simulated response loss');
      });
      addTearDown(fake.close);
      final gate = Wd13RootClinicAttemptGate();
      const draft = Wd13RootClinicDraft(
        name: 'Root A',
        type: 'general',
        headquarters: false,
        timezone: 'Africa/Lagos',
      );
      await expectLater(
        _source(
          fake,
          guard,
          load: () async => _session('super_admin'),
          gate: gate,
        ).createRoot(draft),
        throwsFormatException,
      );
      await expectLater(
        _source(
          fake,
          guard,
          load: () async => _session('super_admin'),
          gate: gate,
        ).createRoot(draft),
        throwsA(isA<Wd13ClinicMutationAlreadyAttempted>()),
      );
      expect(calls, 1);
    },
  );

  test(
    'invalid list duplicate IDs and mismatched read IDs fail closed',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final fake = MockClient(
        (request) async => http.Response(
          jsonEncode({
            'data': request.url.path.endsWith('/clinics')
                ? [_clinic(), _clinic()]
                : _clinic(id: 99),
          }),
          200,
        ),
      );
      addTearDown(fake.close);
      final source = _source(
        fake,
        guard,
        load: () async => _session('super_admin'),
      );
      await expectLater(source.list(), throwsFormatException);
      await expectLater(source.read(12), throwsFormatException);
    },
  );

  test(
    'session invalidated before GET returns cannot render stale data',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final fake = MockClient((request) async {
        guard.invalidate();
        return http.Response(
          jsonEncode({
            'data': [_clinic()],
          }),
          200,
        );
      });
      addTearDown(fake.close);
      final source = _source(
        fake,
        guard,
        load: () async => _session('super_admin'),
      );
      await expectLater(
        source.list(),
        throwsA(isA<Wd13ClinicAccessUnavailable>()),
      );
    },
  );
}
