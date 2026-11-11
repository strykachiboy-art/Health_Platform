import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_dispatch_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_dispatch.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

AuthSessionState _ready({String role = 'ADMIN', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'dispatch-token',
        refreshToken: 'dispatch-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> _trip({
  int id = 9,
  int clinic = 3,
  String status = 'requested',
  int? vehicleId,
  int? driverId,
  int? paramedicId,
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'vehicle_id': vehicleId,
  'patient_id': 17,
  'driver_id': driverId,
  'paramedic_id': paramedicId,
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
  'dispatched_at': status == 'dispatched' ? '2026-11-10T09:00:00' : null,
  'pickup_at': null,
  'completed_at': null,
  'cancelled_at': null,
  'cancellation_reason': null,
  'notes': null,
  'invoice_id': null,
};

Map<String, dynamic> _vehicle({
  int id = 5,
  int clinic = 3,
  String status = 'available',
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'plate_number': 'AMB-500',
  'equipment_level': 'als',
  'capacity': 2,
  'status': status,
  'last_service_date': null,
  'created_at': '2026-11-10T08:00:00',
  'updated_at': '2026-11-10T08:00:00',
};

http.Response _ok(Map<String, dynamic> row, [int code = 200]) => http.Response(
  jsonEncode(<String, dynamic>{'success': true, 'data': row}),
  code,
);

AmbulanceDispatchRemoteDataSource _source(
  http.Client client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loadSession,
}) => AmbulanceDispatchRemoteDataSource(
  reader: AmbulanceReadRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: loadSession ?? () async => _ready(),
    sessionNavigationGuard: guard,
  ),
);

const _draft = AmbulanceTripDraft(
  tripType: 'emergency_pickup',
  patientId: 17,
  pickupAddress: 'Clinic gate',
  destinationAddress: 'Ward entrance',
);

void main() {
  test('trip request: one management POST, no clinic injection and confirmed status', () async {
    var posts = 0;
    final client = MockClient((request) async {
      posts++;
      expect(request.method, 'POST');
      expect(request.url.path, '/api/v1/ambulance/trips');
      expect(request.headers['authorization'], 'Bearer dispatch-token');
      expect(jsonDecode(request.body), <String, dynamic>{
        'trip_type': 'emergency_pickup',
        'patient_id': 17,
        'pickup_address': 'Clinic gate',
        'destination_address': 'Ward entrance',
      });
      return _ok(_trip(), 201);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final confirmed = await source.createTripOnce(_draft);
    expect(confirmed.action, 'created');
    expect(confirmed.trip.status, 'requested');
    expect(source.createWriteAttempted, isTrue);
    await expectLater(
      source.createTripOnce(_draft),
      throwsA(isA<AmbulanceDispatchAlreadyAttempted>()),
    );
    expect(posts, 1);
  });

  test('invalid trip types, transfer without admission and unsupported payload reject pre-POST', () async {
    var posts = 0;
    final client = MockClient((_) async {
      posts++;
      return _ok(_trip());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    for (final draft in <AmbulanceTripDraft>[
      const AmbulanceTripDraft(tripType: 'fictional'),
      const AmbulanceTripDraft(tripType: 'discharge_transport', patientId: 17),
      const AmbulanceTripDraft(tripType: 'emergency_pickup', patientId: -1),
      const AmbulanceTripDraft(tripType: 'emergency_pickup', notes: '  '),
    ]) {
      await expectLater(
        source.createTripOnce(draft),
        throwsA(isA<FormatException>()),
      );
    }
    expect(posts, 0);
    expect(source.createWriteAttempted, isFalse);
  });

  test(
    'management roles accepted; crew and patient roles cannot create trips',
    () async {
      var posts = 0;
      final client = MockClient((request) async {
        posts++;
        return _ok(_trip(), 201);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      for (final role in <String>[
        'admin',
        'ambulance_coordinator',
        'ambulance_dispatcher',
      ]) {
        final result = await _source(
          client,
          guard,
          loadSession: () async => _ready(role: role),
        ).createTripOnce(_draft);
        expect(result.trip.id, 9);
      }
      for (final role in <String>[
        'driver',
        'paramedic',
        'emt',
        'doctor',
        'patient',
      ]) {
        await expectLater(
          _source(
            client,
            guard,
            loadSession: () async => _ready(role: role),
          ).createTripOnce(_draft),
          throwsA(isA<AmbulanceDispatchScopeUnavailable>()),
        );
      }
      expect(posts, 3);
    },
  );

  test('server-created cross-clinic/incorrect status never claims success or retries', () async {
    for (final row in <Map<String, dynamic>>[
      _trip(clinic: 4),
      _trip(status: 'dispatched', vehicleId: 5, driverId: 42),
      _trip()..['patient_id'] = 99,
    ]) {
      var posts = 0;
      final client = MockClient((_) async {
        posts++;
        return _ok(row, 201);
      });
      final guard = SessionNavigationGuard()..markSessionAvailable();
      await expectLater(
        _source(client, guard).createTripOnce(_draft),
        throwsA(isA<AmbulanceDispatchOutcomeUnconfirmed>()),
      );
      expect(posts, 1);
      guard.dispose();
      client.close();
    }
  });

  test('inspection GETs trip/vehicle and dispatch POST exactly once', () async {
    var gets = 0;
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        gets++;
        if (request.url.path == '/api/v1/ambulance/trips/9') {
          return _ok(_trip());
        }
        expect(request.url.path, '/api/v1/ambulance/vehicles/5');
        return _ok(_vehicle());
      }
      posts++;
      expect(request.method, 'POST');
      expect(request.url.path, '/api/v1/ambulance/trips/9/dispatch');
      expect(jsonDecode(request.body), <String, dynamic>{
        'vehicle_id': 5,
        'driver_id': 42,
        'paramedic_id': 23,
      });
      return _ok(
        _trip(
          status: 'dispatched',
          vehicleId: 5,
          driverId: 42,
          paramedicId: 23,
        ),
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final evidence = await source.inspectDispatch(tripId: 9, vehicleId: 5);
    expect(evidence.vehicle.status, 'available');
    final result = await source.dispatchOnce(
      inspection: evidence,
      driverId: 42,
      paramedicId: 23,
    );
    expect(result.trip.status, 'dispatched');
    expect(result.trip.vehicleId, 5);
    expect(gets, 4);
    expect(posts, 1);
    await expectLater(
      source.dispatchOnce(inspection: evidence, driverId: 42),
      throwsA(isA<AmbulanceDispatchAlreadyAttempted>()),
    );
    expect(posts, 1);
  });

  test(
    'stale requested trip or vehicle availability stops before POST',
    () async {
      for (final changed in <String>['trip', 'vehicle']) {
        var tripReads = 0;
        var vehicleReads = 0;
        var posts = 0;
        final client = MockClient((request) async {
          if (request.method == 'POST') {
            posts++;
            return _ok(_trip());
          }
          if (request.url.path.contains('/trips/')) {
            tripReads++;
            return _ok(
              _trip(
                status: changed == 'trip' && tripReads == 2
                    ? 'dispatched'
                    : 'requested',
              ),
            );
          }
          vehicleReads++;
          return _ok(
            _vehicle(
              status: changed == 'vehicle' && vehicleReads == 2
                  ? 'on_trip'
                  : 'available',
            ),
          );
        });
        final guard = SessionNavigationGuard()..markSessionAvailable();
        final source = _source(client, guard);
        final evidence = await source.inspectDispatch(tripId: 9, vehicleId: 5);
        await expectLater(
          source.dispatchOnce(inspection: evidence, driverId: 42),
          throwsA(isA<AmbulanceDispatchEvidenceStale>()),
        );
        expect(posts, 0);
        expect(source.dispatchWriteAttempted, isFalse);
        guard.dispose();
        client.close();
      }
    },
  );

  test(
    'invalid driver, same crew ID, and stale revision cannot send POST',
    () async {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'POST') {
          posts++;
          return _ok(_trip());
        }
        return request.url.path.contains('/vehicles/')
            ? _ok(_vehicle())
            : _ok(_trip());
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final evidence = await source.inspectDispatch(tripId: 9, vehicleId: 5);
      await expectLater(
        source.dispatchOnce(inspection: evidence, driverId: 0),
        throwsA(isA<FormatException>()),
      );
      await expectLater(
        source.dispatchOnce(
          inspection: evidence,
          driverId: 42,
          paramedicId: 42,
        ),
        throwsA(isA<FormatException>()),
      );
      guard.markSignedOut();
      guard.markSessionAvailable();
      await expectLater(
        source.dispatchOnce(inspection: evidence, driverId: 42),
        throwsA(isA<AmbulanceDispatchEvidenceStale>()),
      );
      expect(posts, 0);
    },
  );

  test(
    'cross-clinic vehicle cannot be dispatched and no POST is sent',
    () async {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'POST') {
          posts++;
          return _ok(_trip());
        }
        return request.url.path.contains('/vehicles/')
            ? _ok(_vehicle(clinic: 5))
            : _ok(_trip());
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(client, guard).inspectDispatch(tripId: 9, vehicleId: 5),
        throwsA(isA<FormatException>()),
      );
      expect(posts, 0);
    },
  );

  test(
    'logout during in-flight dispatch suppresses success and prevents replay',
    () async {
      final pending = Completer<http.Response>();
      var posts = 0;
      final client = MockClient((request) {
        if (request.method == 'POST') {
          posts++;
          return pending.future;
        }
        return Future<http.Response>.value(
          request.url.path.contains('/vehicles/')
              ? _ok(_vehicle())
              : _ok(_trip()),
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final evidence = await source.inspectDispatch(tripId: 9, vehicleId: 5);
      final future = source.dispatchOnce(inspection: evidence, driverId: 42);
      await Future<void>.delayed(const Duration(milliseconds: 30));
      expect(posts, 1);
      guard.invalidate();
      pending.complete(
        _ok(_trip(status: 'dispatched', vehicleId: 5, driverId: 42)),
      );
      await expectLater(
        future,
        throwsA(isA<AmbulanceDispatchOutcomeUnconfirmed>()),
      );
      await expectLater(
        source.dispatchOnce(inspection: evidence, driverId: 42),
        throwsA(isA<AmbulanceDispatchAlreadyAttempted>()),
      );
      expect(posts, 1);
    },
  );

  test('malformed dispatch confirmation cannot fabricate success', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return request.url.path.contains('/vehicles/')
            ? _ok(_vehicle())
            : _ok(_trip());
      }
      posts++;
      return _ok(_trip(status: 'requested'));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final inspection = await source.inspectDispatch(tripId: 9, vehicleId: 5);
    await expectLater(
      source.dispatchOnce(inspection: inspection, driverId: 42),
      throwsA(isA<AmbulanceDispatchOutcomeUnconfirmed>()),
    );
    expect(posts, 1);
    expect(source.dispatchWriteAttempted, isTrue);
  });
}
