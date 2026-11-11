import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/presentation/ambulance_read_screen.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

AuthSessionState _ready({String role = 'DRIVER', int clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'ambulance-widget-token',
        refreshToken: 'ambulance-widget-refresh',
        userId: 71,
        role: role,
        clinicContextId: clinicId,
      ),
    );

Map<String, dynamic> _vehicle(int id, {int clinic = 3}) => {
  'id': id,
  'clinic_id': clinic,
  'plate_number': 'ABC-$id',
  'equipment_level': 'bls',
  'capacity': 1,
  'status': 'available',
  'last_service_date': null,
  'created_at': '2026-10-01T10:00:00',
  'updated_at': '2026-11-10T10:00:00',
};

Map<String, dynamic> _trip(int id) => {
  'id': id,
  'clinic_id': 3,
  'vehicle_id': 4,
  'patient_id': 17,
  'driver_id': 71,
  'paramedic_id': null,
  'admission_id': null,
  'trip_type': 'emergency_pickup',
  'status': 'dispatched',
  'pickup_address': 'Clinic gate',
  'pickup_lat': null,
  'pickup_lng': null,
  'destination_address': 'Ward entrance',
  'destination_lat': null,
  'destination_lng': null,
  'requested_at': '2026-11-10T10:00:00',
  'dispatched_at': null,
  'pickup_at': null,
  'completed_at': null,
  'cancelled_at': null,
  'cancellation_reason': null,
  'notes': null,
  'invoice_id': null,
};

Map<String, dynamic> _page(
  List<Map<String, dynamic>> rows, {
  int? total,
  int page = 1,
  int perPage = 20,
}) {
  final count = total ?? rows.length;
  final pages = (count + perPage - 1) ~/ perPage;
  return {
    'success': true,
    'data': {
      'items': rows,
      'page': page,
      'per_page': perPage,
      'total': count,
      'pages': pages,
      'has_next': page < pages,
      'has_prev': page > 1,
    },
  };
}

AmbulanceReadRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? session,
}) => AmbulanceReadRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: session ?? () async => _ready(),
  sessionNavigationGuard: guard,
);

Widget _host(AmbulanceReadRemoteDataSource source) =>
    MaterialApp(home: AmbulanceReadScreen(dataSource: source));

void main() {
  testWidgets('fleet GET renders server vehicle, no dispatch action', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/ambulance/vehicles');
      return http.Response(jsonEncode(_page([_vehicle(4)])), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    expect(find.text('Vehicle ABC-4'), findsOneWidget);
    expect(find.byKey(const ValueKey('p23i-s05-refresh')), findsOneWidget);
    expect(find.text('Dispatch'), findsNothing);
    expect(calls, 1);
  });

  testWidgets('trips switch GET and detail GET are read-only', (tester) async {
    final methods = <String>[];
    final client = MockClient((req) async {
      methods.add(req.method);
      final data = req.url.path.endsWith('/8')
          ? <String, dynamic>{'success': true, 'data': _trip(8)}
          : req.url.path.endsWith('/trips')
          ? _page([_trip(8)])
          : _page([_vehicle(4)]);
      return http.Response(jsonEncode(data), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23i-s05-trips-tab')));
    await tester.pumpAndSettle();
    expect(find.text('Trip #8'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('p23i-s05-trip-8')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23i-s05-trip-detail')), findsOneWidget);
    expect(find.textContaining('Vehicle: 4'), findsOneWidget);
    expect(methods, ['GET', 'GET', 'GET']);
  });

  testWidgets('unauthorized patient sees no records and sends zero requests', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return http.Response(jsonEncode(_page([_vehicle(4)])), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _host(
        _source(client, guard, session: () async => _ready(role: 'patient')),
      ),
    );
    await tester.pumpAndSettle();
    expect(requests, 0);
    expect(find.byKey(const ValueKey('p23i-s05-error')), findsOneWidget);
    expect(find.text('Vehicle ABC-4'), findsNothing);
  });

  testWidgets('refresh failure erases previously displayed vehicle data', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return requests == 1
          ? http.Response(jsonEncode(_page([_vehicle(4)])), 200)
          : http.Response('{"success":false}', 503);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    expect(find.text('Vehicle ABC-4'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('p23i-s05-refresh')));
    await tester.pumpAndSettle();
    expect(find.text('Vehicle ABC-4'), findsNothing);
    expect(find.byKey(const ValueKey('p23i-s05-error')), findsOneWidget);
  });

  testWidgets('logout clears sensitive data and rejects late GET response', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    final client = MockClient((_) => pending.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pump();
    guard.invalidate();
    await tester.pump();
    pending.complete(http.Response(jsonEncode(_page([_vehicle(4)])), 200));
    await tester.pumpAndSettle();
    expect(find.text('Vehicle ABC-4'), findsNothing);
    expect(find.byKey(const ValueKey('p23i-s05-error')), findsOneWidget);
  });

  testWidgets('cross-clinic vehicle cannot appear in fleet screen', (
    tester,
  ) async {
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode(_page([_vehicle(4, clinic: 22)])), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    expect(find.text('Vehicle ABC-4'), findsNothing);
    expect(find.byKey(const ValueKey('p23i-s05-error')), findsOneWidget);
  });

  testWidgets('explicit refresh sends only fresh GET; no mutations', (
    tester,
  ) async {
    final methods = <String>[];
    final client = MockClient((request) async {
      methods.add(request.method);
      return http.Response(jsonEncode(_page([_vehicle(4)])), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23i-s05-refresh')));
    await tester.pumpAndSettle();
    expect(methods, ['GET', 'GET']);
  });
}
