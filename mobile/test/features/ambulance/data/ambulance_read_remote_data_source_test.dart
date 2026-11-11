import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

AuthSessionState _ready({
  String role = 'DRIVER',
  int clinic = 3,
  String token = 'ambulance-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'ambulance-refresh',
    userId: 71,
    role: role,
    clinicContextId: clinic,
  ),
);

Map<String, dynamic> _vehicle({
  int id = 4,
  int clinic = 3,
  String status = 'available',
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'plate_number': 'AB-123-CD',
  'equipment_level': 'als',
  'capacity': 2,
  'status': status,
  'last_service_date': '2026-10-01',
  'created_at': '2026-10-01T12:00:00',
  'updated_at': '2026-10-02T12:00:00',
};

Map<String, dynamic> _trip({
  int id = 8,
  int clinic = 3,
  String status = 'dispatched',
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'vehicle_id': 4,
  'patient_id': 17,
  'driver_id': 71,
  'paramedic_id': 23,
  'admission_id': null,
  'trip_type': 'emergency_pickup',
  'status': status,
  'pickup_address': 'Clinic gate',
  'pickup_lat': null,
  'pickup_lng': null,
  'destination_address': 'Ward entrance',
  'destination_lat': null,
  'destination_lng': null,
  'requested_at': '2026-11-10T08:00:00',
  'dispatched_at': '2026-11-10T08:01:00',
  'pickup_at': null,
  'completed_at': null,
  'cancelled_at': null,
  'cancellation_reason': null,
  'notes': null,
  'invoice_id': null,
};

Map<String, dynamic> _page(
  List<Map<String, dynamic>> items, {
  int? total,
  int page = 1,
  int perPage = 20,
}) {
  final count = total ?? items.length;
  final pages = (count + perPage - 1) ~/ perPage;
  return <String, dynamic>{
    'success': true,
    'data': <String, dynamic>{
      'items': items,
      'total': count,
      'page': page,
      'per_page': perPage,
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

void main() {
  test('vehicle GET uses bearer, clinic and server-owned pagination', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      expect(req.method, 'GET');
      expect(req.url.path, '/api/v1/ambulance/vehicles');
      expect(req.url.queryParameters, <String, String>{
        'page': '1',
        'per_page': '20',
        'status': 'available',
      });
      expect(req.headers['authorization'], 'Bearer ambulance-token');
      return http.Response(jsonEncode(_page([_vehicle()])), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final result = await _source(
      client,
      guard,
    ).fetchVehicles(status: 'available');
    expect(result.items.single.plateNumber, 'AB-123-CD');
    expect(result.items.single.capacity, 2);
    expect(result.total, 1);
    expect(calls, 1);
  });

  test('trip GET returns server status; staff-only role is accepted', () async {
    final client = MockClient((req) async {
      expect(req.method, 'GET');
      expect(req.url.path, '/api/v1/ambulance/trips');
      return http.Response(jsonEncode(_page([_trip()])), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final role in <String>[
      'ADMIN',
      'AMBULANCE_COORDINATOR',
      'AMBULANCE_DISPATCHER',
      'DRIVER',
      'PARAMEDIC',
      'EMT',
    ]) {
      final page = await _source(
        client,
        guard,
        session: () async => _ready(role: role),
      ).fetchTrips();
      expect(page.items.single.status, 'dispatched');
    }
  });

  test(
    'unauthorized roles, missing clinic and signed-out guard send no GET',
    () async {
      var requests = 0;
      final client = MockClient((_) async {
        requests++;
        return http.Response(jsonEncode(_page([_vehicle()])), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      for (final role in <String>[
        'patient',
        'doctor',
        'nurse',
        'pharmacist',
        'super_admin',
      ]) {
        await expectLater(
          _source(
            client,
            guard,
            session: () async => _ready(role: role),
          ).fetchVehicles(),
          throwsA(isA<AmbulanceReadSessionUnavailable>()),
        );
      }
      await expectLater(
        _source(
          client,
          guard,
          session: () async => _ready(clinic: 0),
        ).fetchTrips(),
        throwsA(isA<AmbulanceReadSessionUnavailable>()),
      );
      guard.markSignedOut();
      await expectLater(
        _source(client, guard).fetchVehicles(),
        throwsA(isA<AmbulanceReadSessionUnavailable>()),
      );
      expect(requests, 0);
    },
  );

  test('cross-clinic rows and unexpected enums fail closed', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final invalidVehicles = <Map<String, dynamic>>[
      _vehicle(clinic: 5),
      _vehicle(status: 'dispatching'),
      _vehicle()..['capacity'] = 0,
      _vehicle()..['equipment_level'] = 'military',
      _vehicle()..['plate_number'] = '',
    ];
    for (final row in invalidVehicles) {
      final client = MockClient(
        (_) async => http.Response(jsonEncode(_page([row])), 200),
      );
      await expectLater(
        _source(client, guard).fetchVehicles(),
        throwsA(isA<FormatException>()),
      );
      client.close();
    }
    for (final row in <Map<String, dynamic>>[
      _trip(clinic: 5),
      _trip(status: 'teleported'),
      _trip()..['trip_type'] = 'unknown',
      _trip()..['pickup_lat'] = 'fake-location',
    ]) {
      final client = MockClient(
        (_) async => http.Response(jsonEncode(_page([row])), 200),
      );
      await expectLater(
        _source(client, guard).fetchTrips(),
        throwsA(isA<FormatException>()),
      );
      client.close();
    }
  });

  test('unexpected envelope and duplicate rows rejected', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final envelope in <Map<String, dynamic>>[
      {
        'success': false,
        'data': _page([_vehicle()])['data'],
      },
      _page([_vehicle(), _vehicle()]),
      _page([_vehicle()], total: 0),
      _page([_vehicle()], page: 2),
    ]) {
      final client = MockClient(
        (_) async => http.Response(jsonEncode(envelope), 200),
      );
      await expectLater(
        _source(client, guard).fetchVehicles(),
        throwsA(isA<FormatException>()),
      );
      client.close();
    }
  });

  test('detail GET requires exact ID, clinic and valid envelope', () async {
    final client = MockClient((req) async {
      expect(req.method, 'GET');
      return http.Response(
        jsonEncode(<String, dynamic>{
          'success': true,
          'data': req.url.path.endsWith('/4') ? _vehicle() : _trip(),
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    expect((await source.fetchVehicleDetail(4)).status, 'available');
    expect((await source.fetchTripDetail(8)).patientId, 17);
    expect(() => source.fetchVehicleDetail(0), throwsArgumentError);
    expect(() => source.fetchTripDetail(-8), throwsArgumentError);
    final invalid = MockClient(
      (_) async => http.Response(
        jsonEncode(<String, dynamic>{'success': true, 'data': _trip(id: 9)}),
        200,
      ),
    );
    addTearDown(invalid.close);
    await expectLater(
      _source(invalid, guard).fetchTripDetail(8),
      throwsA(isA<FormatException>()),
    );
  });

  test('invalid filters and pagination send zero requests', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_page([])), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    expect(() => source.fetchVehicles(page: 0), throwsArgumentError);
    expect(() => source.fetchVehicles(perPage: 501), throwsArgumentError);
    expect(() => source.fetchVehicles(status: 'unknown'), throwsArgumentError);
    expect(() => source.fetchTrips(status: 'available'), throwsArgumentError);
    expect(calls, 0);
  });

  test(
    'logout during in-flight GET rejects stale transport response',
    () async {
      final pending = Completer<http.Response>();
      final client = MockClient((_) => pending.future);
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final future = _source(client, guard).fetchTrips();
      await Future<void>.delayed(const Duration(milliseconds: 25));
      guard.invalidate();
      pending.complete(http.Response(jsonEncode(_page([_trip()])), 200));
      await expectLater(
        future,
        throwsA(isA<AmbulanceReadSessionUnavailable>()),
      );
    },
  );

  test(
    'clinic switch while response pending cannot leak prior clinic',
    () async {
      var clinic = 3;
      final pending = Completer<http.Response>();
      final client = MockClient((_) => pending.future);
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final future = _source(
        client,
        guard,
        session: () async => _ready(clinic: clinic),
      ).fetchVehicles();
      await Future<void>.delayed(const Duration(milliseconds: 25));
      clinic = 10;
      pending.complete(http.Response(jsonEncode(_page([_vehicle()])), 200));
      await expectLater(
        future,
        throwsA(isA<AmbulanceReadSessionUnavailable>()),
      );
    },
  );

  test('later pages obey server pagination and status query', () async {
    final client = MockClient((req) async {
      expect(req.url.queryParameters, <String, String>{
        'page': '2',
        'per_page': '1',
        'status': 'dispatched',
      });
      return http.Response(
        jsonEncode(_page([_trip(id: 9)], total: 2, page: 2, perPage: 1)),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final page = await _source(
      client,
      guard,
    ).fetchTrips(page: 2, perPage: 1, status: 'dispatched');
    expect(page.items.single.id, 9);
    expect(page.hasNext, isFalse);
  });
}
