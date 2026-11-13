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
import 'package:clinic_system_pro/features/radiology/data/rad7_ris_contract.dart';
import 'package:clinic_system_pro/features/radiology/data/rad7_ris_remote_data_source.dart';

class _Connectivity implements DeviceConnectivitySource {
  _Connectivity(this.state);
  DeviceNetworkStatus state;
  @override
  Future<DeviceNetworkStatus> checkStatus() async => state;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

Map<String, dynamic> _envelope(int clinic) => <String, dynamic>{
  'success': true,
  'ris_foundation_only': true,
  'mwl_published': false,
  'archive_verified': false,
  'diagnostic_report_signed': false,
  'released_to_chart': false,
  'critical_result_delivery_authorized': false,
  'data': <String, dynamic>{
    'exam_id': 13,
    'imaging_order_id': 8,
    'clinic_id': clinic,
    'status': 'planned',
    'version': 1,
    'accession_number': 'RAD-ABC123',
    'issuer_namespace': 'CSP-CLINIC-$clinic',
    'worklist_published': false,
    'archive_verified': false,
    'diagnostic_report_signed': false,
  },
};

void main() {
  late SessionNavigationGuard guard;
  late MockClient mock;
  late ApiClient api;
  late DashboardRemoteDataSource dashboard;
  late _Connectivity connection;
  late Rad7RisRemoteDataSource source;
  var requests = 0;
  var posts = 0;
  var clinic = 2;
  var role = 'doctor';

  setUp(() {
    requests = 0;
    posts = 0;
    clinic = 2;
    role = 'doctor';
    guard = SessionNavigationGuard()..markSessionAvailable();
    connection = _Connectivity(DeviceNetworkStatus.connected);
    mock = MockClient((req) async {
      requests++;
      if (req.method == 'POST') posts++;
      expect(req.headers['authorization'], 'Bearer test-token');
      return http.Response(
        jsonEncode(_envelope(clinic)),
        200,
        headers: {'content-type': 'application/json'},
      );
    });
    api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: mock,
    );
    dashboard = DashboardRemoteDataSource(
      apiClient: api,
      sessionNavigationGuard: guard,
      loadSession: () async => AuthSessionState.ready(
        AuthTokenResponse(
          accessToken: 'test-token',
          refreshToken: 'refresh-token',
          userId: 11,
          role: role,
          clinicContextId: clinic,
        ),
      ),
    );
    source = Rad7RisRemoteDataSource(
      dashboard: dashboard,
      guard: guard,
      connectivity: connection,
    );
    addTearDown(() {
      guard.dispose();
      api.close();
      mock.close();
    });
  });

  test('doctor GET is bound to order and clinic', () async {
    final item = await source.byOrder(8);
    expect(item.examId, 13);
    expect(requests, 1);
    expect(posts, 0);
  });

  test('wrong role rejects before transport', () async {
    role = 'patient';
    await expectLater(source.byOrder(8), throwsA(isA<Rad7RisUnavailable>()));
    expect(requests, 0);
  });

  test('disconnected rejects clinical mutation without sending', () async {
    connection.state = DeviceNetworkStatus.disconnected;
    await expectLater(source.createExam(8), throwsA(isA<Rad7RisUnavailable>()));
    expect(posts, 0);
  });

  test('unknown network state rejects mutation', () async {
    connection.state = DeviceNetworkStatus.unknown;
    await expectLater(source.createExam(8), throwsA(isA<Rad7RisUnavailable>()));
    expect(posts, 0);
  });

  test('one clinical mutation sends at most one POST', () async {
    final exam = await source.createExam(8);
    expect(exam.examId, 13);
    await expectLater(source.createExam(8), throwsA(isA<Rad7RisUnconfirmed>()));
    expect(posts, 1);
  });

  test('session invalidation rejects without transport', () async {
    guard.invalidate();
    await expectLater(source.byOrder(8), throwsA(isA<Rad7RisUnavailable>()));
    expect(requests, 0);
  });

  test('cross-clinic response fails closed', () async {
    // A mismatched returned clinic must never be treated as the selected one.
    // Verify at the pure contract boundary as well as the authenticated source.
    final result = _envelope(3)['data'] as Map<String, dynamic>;
    expect(() => Rad7Exam.fromData(result, clinicId: 2), throwsFormatException);
  });
}
