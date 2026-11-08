import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/nursing/data/ward_dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/nursing/data/models/ward_overview.dart';

AuthSessionState ready({
  String role = 'nurse',
  int clinic = 3,
  String token = 'ward-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'refresh',
    userId: 42,
    role: role,
    clinicContextId: clinic,
  ),
);

WardDashboardRemoteDataSource source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) => WardDashboardRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: loader ?? () async => ready(),
  sessionNavigationGuard: guard,
);

Map<String, dynamic> ward({int clinic = 3}) => {
  'id': 9,
  'clinic_id': clinic,
  'name': 'ICU',
  'ward_type': 'icu',
  'capacity': 2,
};

void main() {
  test('GET ward page is raw, scoped and bearer-authenticated', () async {
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/wards');
      expect(request.url.queryParameters, {
        'page': '2',
        'per_page': '20',
        'ward_type': 'icu',
      });
      expect(request.headers['authorization'], 'Bearer ward-token');
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      return http.Response(
        jsonEncode({
          'items': [ward()],
          'total': 21,
          'page': 2,
          'per_page': 20,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final result = await source(
      client,
      guard,
    ).fetchWards(page: 2, type: WardKind.icu);
    expect(result.items.single.name, 'ICU');
    expect(result.hasPrevious, isTrue);
    expect(requests, 1);
  });

  test('bed pagination path and scope match the selected ward', () async {
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/wards/9/beds');
      expect(request.url.queryParameters['per_page'], '30');
      return http.Response(
        jsonEncode({
          'items': [
            {
              'id': 15,
              'ward_id': 9,
              'bed_number': 'ICU-1',
              'status': 'maintenance',
            },
          ],
          'total': 1,
          'page': 1,
          'per_page': 30,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final beds = await source(client, guard).fetchBeds(wardId: 9);
    expect(beds.items.single.number, 'ICU-1');
  });

  test('occupancy refuses cross-clinic response', () async {
    final client = MockClient(
      (request) async => http.Response(
        jsonEncode({
          'ward_id': 9,
          'clinic_id': 100,
          'ward_name': 'ICU',
          'capacity': 2,
          'total_beds': 2,
          'available': 2,
          'occupied': 0,
          'reserved': 0,
          'maintenance': 0,
        }),
        200,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      source(client, guard).fetchOccupancy(wardId: 9),
      throwsFormatException,
    );
  });

  test('patient/unknown role is denied before making HTTP request', () async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      source(
        client,
        guard,
        loader: () async => ready(role: 'patient'),
      ).fetchWards(),
      throwsA(isA<WardDashboardSessionException>()),
    );
    expect(requests, 0);
  });

  test('receptionist role may read; backend still authorizes', () async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode({
          'items': [ward()],
          'total': 1,
          'page': 1,
          'per_page': 20,
        }),
        200,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final page = await source(
      client,
      guard,
      loader: () async => ready(role: 'receptionist'),
    ).fetchWards();
    expect(page.items.length, 1);
  });

  test('invalidated guard forbids network and stale results', () async {
    final pending = Completer<http.Response>();
    var requests = 0;
    final client = MockClient((_) {
      requests++;
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final future = source(client, guard).fetchWards();
    await Future<void>.delayed(const Duration(milliseconds: 10));
    expect(requests, 1);
    guard.invalidate();
    pending.complete(
      http.Response(
        jsonEncode({
          'items': [ward()],
          'total': 1,
          'page': 1,
          'per_page': 20,
        }),
        200,
      ),
    );
    await expectLater(future, throwsA(isA<WardDashboardSessionException>()));
  });

  test('backend error remains an error, never empty success', () async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode({'success': false, 'error': 'Access denied'}),
        403,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      source(client, guard).fetchWards(),
      throwsA(isA<ApiException>()),
    );
  });

  test('bad pagination fails locally without HTTP', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    expect(
      () => source(client, guard).fetchWards(perPage: 501),
      throwsArgumentError,
    );
    expect(
      () => source(client, guard).fetchBeds(wardId: -4),
      throwsArgumentError,
    );
    expect(calls, 0);
  });
}
