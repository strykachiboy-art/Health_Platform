import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/printing/data/rad7_print_contract.dart';
import 'package:clinic_system_pro/features/printing/data/rad7_print_remote_data_source.dart';

class _Connectivity implements DeviceConnectivitySource {
  _Connectivity(this.state);
  DeviceNetworkStatus state;
  @override
  Future<DeviceNetworkStatus> checkStatus() async => state;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

http.Response _pdfResponse() => http.Response(
  '%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF',
  200,
  headers: <String, String>{
    'content-type': 'application/pdf',
    'content-disposition': 'attachment; filename="test.pdf"',
    'cache-control': 'private, no-store',
    'x-content-type-options': 'nosniff',
    'x-clinical-release': 'none',
    'x-source-sha256': List<String>.filled(64, 'a').join(),
  },
);

void main() {
  late SessionNavigationGuard guard;
  late MockClient mock;
  late ApiClient api;
  late _Connectivity connectivity;
  late Rad7PrintRemoteDataSource source;
  var requests = 0;
  var role = 'admin';
  var clinic = 2;
  var token = 'test-token';
  Completer<void>? pending;

  setUp(() {
    requests = 0;
    role = 'admin';
    clinic = 2;
    token = 'test-token';
    pending = null;
    guard = SessionNavigationGuard()..markSessionAvailable();
    connectivity = _Connectivity(DeviceNetworkStatus.connected);
    mock = MockClient((req) async {
      requests++;
      expect(req.method, 'GET');
      expect(req.headers['authorization'], 'Bearer test-token');
      expect(req.url.path, '/api/v1/printing/invoice/41.pdf');
      final waiter = pending;
      if (waiter != null) await waiter.future;
      return _pdfResponse();
    });
    api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: mock,
    );
    final dashboard = DashboardRemoteDataSource(
      apiClient: api,
      sessionNavigationGuard: guard,
      loadSession: () async => AuthSessionState.ready(
        AuthTokenResponse(
          accessToken: token,
          refreshToken: 'refresh',
          userId: 8,
          role: role,
          clinicContextId: clinic,
        ),
      ),
    );
    source = Rad7PrintRemoteDataSource(
      dashboard: dashboard,
      guard: guard,
      connectivity: connectivity,
    );
    addTearDown(() {
      api.close();
      mock.close();
      guard.dispose();
    });
  });

  test('authorized online invoice uses one GET and memory-only PDF', () async {
    final result = await source.fetchAuthorizedCopy(Rad7PrintKind.invoice, 41);
    expect(ascii.decode(result).startsWith('%PDF-'), isTrue);
    expect(requests, 1);
  });

  test('wrong role and missing clinic deny before transport', () async {
    role = 'patient';
    await expectLater(
      source.fetchAuthorizedCopy(Rad7PrintKind.invoice, 41),
      throwsA(isA<Rad7PrintUnavailable>()),
    );
    role = 'admin';
    clinic = 0;
    await expectLater(
      source.fetchAuthorizedCopy(Rad7PrintKind.invoice, 41),
      throwsA(isA<Rad7PrintUnavailable>()),
    );
    expect(requests, 0);
  });

  test('offline and unknown network never send a print GET', () async {
    for (final state in <DeviceNetworkStatus>[
      DeviceNetworkStatus.disconnected,
      DeviceNetworkStatus.unknown,
    ]) {
      connectivity.state = state;
      await expectLater(
        source.fetchAuthorizedCopy(Rad7PrintKind.invoice, 41),
        throwsA(isA<Rad7PrintUnavailable>()),
      );
    }
    expect(requests, 0);
  });

  test('invalid IDs and signed-out guard deny before transport', () async {
    await expectLater(
      source.fetchAuthorizedCopy(Rad7PrintKind.invoice, 0),
      throwsA(isA<Rad7PrintUnavailable>()),
    );
    guard.invalidate();
    await expectLater(
      source.fetchAuthorizedCopy(Rad7PrintKind.invoice, 41),
      throwsA(isA<Rad7PrintUnavailable>()),
    );
    expect(requests, 0);
  });

  test('in-flight clinic or session transition discards PDF bytes', () async {
    pending = Completer<void>();
    final attempted = source.fetchAuthorizedCopy(Rad7PrintKind.invoice, 41);
    // Allow the request to reach the MockClient before changing identity.
    for (var i = 0; i < 40 && requests == 0; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 2));
    }
    expect(requests, 1);
    clinic = 4;
    guard.invalidate();
    pending!.complete();
    await expectLater(attempted, throwsA(isA<Rad7PrintUnavailable>()));
    expect(requests, 1);
  });
}
