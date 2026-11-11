import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_lifecycle_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_lifecycle.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

AuthSessionState _ready({String role = 'admin', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'lifecycle-token',
        refreshToken: 'lifecycle-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> _trip({
  String status = 'dispatched',
  int clinic = 3,
  int? patient = 17,
  int? vehicle = 5,
  String? cancelledReason,
}) => <String, dynamic>{
  'id': 9,
  'clinic_id': clinic,
  'vehicle_id': vehicle,
  'patient_id': patient,
  'driver_id': 42,
  'paramedic_id': null,
  'admission_id': null,
  'trip_type': 'emergency_pickup',
  'status': status,
  'pickup_address': 'Gate',
  'pickup_lat': null,
  'pickup_lng': null,
  'destination_address': 'Ward',
  'destination_lat': null,
  'destination_lng': null,
  'requested_at': '2026-11-10T08:00:00',
  'dispatched_at': '2026-11-10T09:00:00',
  'pickup_at':
      status == 'at_pickup' ||
          status == 'patient_on_board' ||
          status == 'en_route_to_destination' ||
          status == 'completed'
      ? '2026-11-10T09:20:00'
      : null,
  'completed_at': status == 'completed' ? '2026-11-10T10:00:00' : null,
  'cancelled_at': status == 'cancelled' ? '2026-11-10T10:00:00' : null,
  'cancellation_reason': cancelledReason,
  'notes': null,
  'invoice_id': null,
};

Map<String, dynamic> _vehicle({
  int clinic = 3,
  String status = 'on_trip',
  String plate = 'AMB-501',
}) => <String, dynamic>{
  'id': 5,
  'clinic_id': clinic,
  'plate_number': plate,
  'equipment_level': 'bls',
  'capacity': 1,
  'status': status,
  'last_service_date': null,
  'created_at': '2026-11-10T08:00:00',
  'updated_at': '2026-11-10T08:00:00',
};

http.Response _ok(Map<String, dynamic> row, [int status = 200]) =>
    http.Response(
      jsonEncode(<String, dynamic>{'success': true, 'data': row}),
      status,
    );

AmbulanceLifecycleRemoteDataSource _source(
  http.Client client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loadSession,
}) => AmbulanceLifecycleRemoteDataSource(
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

const _progress = AmbulanceLifecycleCommand(
  kind: AmbulanceLifecycleKind.tripProgress,
  tripId: 9,
  nextStatus: 'en_route_to_pickup',
);

void main() {
  test(
    'crew roles may advance only the exact next backend status; PATCH once',
    () async {
      for (final role in <String>[
        'admin',
        'ambulance_coordinator',
        'ambulance_dispatcher',
        'driver',
        'paramedic',
        'emt',
      ]) {
        var posts = 0;
        final client = MockClient((request) async {
          if (request.method == 'GET') {
            return _ok(
              request.url.path.contains('vehicles') ? _vehicle() : _trip(),
            );
          }
          posts++;
          expect(request.method, 'PATCH');
          expect(request.url.path, '/api/v1/ambulance/trips/9/status');
          expect(jsonDecode(request.body), <String, dynamic>{
            'status': 'en_route_to_pickup',
          });
          expect(request.headers['authorization'], 'Bearer lifecycle-token');
          return _ok(_trip(status: 'en_route_to_pickup'));
        });
        final guard = SessionNavigationGuard()..markSessionAvailable();
        final source = _source(
          client,
          guard,
          loadSession: () async => _ready(role: role),
        );
        final proof = await source.inspect(_progress);
        final result = await source.applyOnce(
          command: _progress,
          inspection: proof,
        );
        expect(result.trip!.status, 'en_route_to_pickup');
        expect(source.writeAttempted, isTrue);
        await expectLater(
          source.applyOnce(command: _progress, inspection: proof),
          throwsA(isA<AmbulanceLifecycleAlreadyAttempted>()),
        );
        expect(posts, 1);
        guard.dispose();
        client.close();
      }
    },
  );

  test(
    'unapproved role, invalid status, and skipped transition never write',
    () async {
      var writes = 0;
      final client = MockClient((request) async {
        if (request.method != 'GET') writes++;
        return _ok(
          request.url.path.contains('vehicles') ? _vehicle() : _trip(),
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(
          client,
          guard,
          loadSession: () async => _ready(role: 'patient'),
        ).inspect(_progress),
        throwsA(isA<AmbulanceLifecycleUnauthorized>()),
      );
      await expectLater(
        _source(client, guard).inspect(
          const AmbulanceLifecycleCommand(
            kind: AmbulanceLifecycleKind.tripProgress,
            tripId: 9,
            nextStatus: 'at_pickup',
          ),
        ),
        throwsA(isA<AmbulanceLifecycleEvidenceChanged>()),
      );
      expect(writes, 0);
    },
  );

  test('stale trip changes during second GET prevent write', () async {
    var tripReads = 0;
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method != 'GET') {
        writes++;
        return _ok(_trip(status: 'en_route_to_pickup'));
      }
      if (request.url.path.contains('/trips/')) {
        tripReads++;
        return _ok(
          _trip(status: tripReads == 1 ? 'dispatched' : 'en_route_to_pickup'),
        );
      }
      return _ok(_vehicle());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final first = await source.inspect(_progress);
    await expectLater(
      source.applyOnce(command: _progress, inspection: first),
      throwsA(isA<AmbulanceLifecycleEvidenceChanged>()),
    );
    expect(source.writeAttempted, isFalse);
    expect(writes, 0);
  });

  test('cross-clinic GET, logout and unsupported crew cannot write', () async {
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method != 'GET') writes++;
      return _ok(
        request.url.path.contains('vehicles') ? _vehicle() : _trip(clinic: 12),
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).inspect(_progress),
      throwsA(isA<FormatException>()),
    );
    guard.markSignedOut();
    await expectLater(
      _source(client, guard).inspect(_progress),
      throwsA(isA<AmbulanceLifecycleUnauthorized>()),
    );
    expect(writes, 0);
  });

  test(
    'patient link is management-only, requires an unassigned active trip',
    () async {
      const command = AmbulanceLifecycleCommand(
        kind: AmbulanceLifecycleKind.patientLink,
        tripId: 9,
        patientId: 17,
      );
      var writes = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return _ok(
            request.url.path.contains('vehicles')
                ? _vehicle()
                : _trip(patient: null),
          );
        }
        writes++;
        expect(request.url.path, '/api/v1/ambulance/trips/9/patient');
        expect(jsonDecode(request.body), <String, dynamic>{'patient_id': 17});
        return _ok(_trip());
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final denied = _source(
        client,
        guard,
        loadSession: () async => _ready(role: 'driver'),
      );
      await expectLater(
        denied.inspect(command),
        throwsA(isA<AmbulanceLifecycleUnauthorized>()),
      );
      final source = _source(client, guard);
      final proof = await source.inspect(command);
      final result = await source.applyOnce(
        command: command,
        inspection: proof,
      );
      expect(result.trip!.patientId, 17);
      expect(writes, 1);
    },
  );

  test(
    'completion requires patient, correct final state and ON_TRIP vehicle',
    () async {
      const command = AmbulanceLifecycleCommand(
        kind: AmbulanceLifecycleKind.completeTrip,
        tripId: 9,
      );
      var writes = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return _ok(
            request.url.path.contains('vehicles')
                ? _vehicle()
                : _trip(status: 'en_route_to_destination'),
          );
        }
        writes++;
        expect(request.url.path, '/api/v1/ambulance/trips/9/complete');
        return _ok(_trip(status: 'completed'));
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final proof = await source.inspect(command);
      final result = await source.applyOnce(
        command: command,
        inspection: proof,
      );
      expect(result.trip!.completedAt, isNotNull);
      expect(writes, 1);
    },
  );

  test(
    'cancel trip requires nonempty reason; verifies server cancellation',
    () async {
      const command = AmbulanceLifecycleCommand(
        kind: AmbulanceLifecycleKind.cancelTrip,
        tripId: 9,
        reason: 'Vehicle unavailable',
      );
      var writes = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return _ok(
            request.url.path.contains('vehicles') ? _vehicle() : _trip(),
          );
        }
        writes++;
        expect(request.url.path, '/api/v1/ambulance/trips/9/cancel');
        expect(jsonDecode(request.body), <String, dynamic>{
          'reason': 'Vehicle unavailable',
        });
        return _ok(
          _trip(status: 'cancelled', cancelledReason: 'Vehicle unavailable'),
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final proof = await source.inspect(command);
      final result = await source.applyOnce(
        command: command,
        inspection: proof,
      );
      expect(result.trip!.cancellationReason, 'Vehicle unavailable');
      expect(writes, 1);
    },
  );

  test(
    'vehicle status: fleet managers only, cannot manually enter ON_TRIP',
    () async {
      const command = AmbulanceLifecycleCommand(
        kind: AmbulanceLifecycleKind.vehicleStatus,
        vehicleId: 5,
        nextStatus: 'maintenance',
      );
      var writes = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') return _ok(_vehicle(status: 'available'));
        writes++;
        expect(request.method, 'PATCH');
        expect(request.url.path, '/api/v1/ambulance/vehicles/5/status');
        return _ok(_vehicle(status: 'maintenance'));
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(
          client,
          guard,
          loadSession: () async => _ready(role: 'ambulance_dispatcher'),
        ).inspect(command),
        throwsA(isA<AmbulanceLifecycleUnauthorized>()),
      );
      final source = _source(
        client,
        guard,
        loadSession: () async => _ready(role: 'ambulance_coordinator'),
      );
      final proof = await source.inspect(command);
      final result = await source.applyOnce(
        command: command,
        inspection: proof,
      );
      expect(result.vehicle!.status, 'maintenance');
      expect(writes, 1);
      await expectLater(
        source.inspect(
          const AmbulanceLifecycleCommand(
            kind: AmbulanceLifecycleKind.vehicleStatus,
            vehicleId: 5,
            nextStatus: 'on_trip',
          ),
        ),
        throwsA(isA<FormatException>()),
      );
    },
  );

  test('vehicle registration uses one POST with no client clinic or injected status', () async {
    const command = AmbulanceLifecycleCommand(
      kind: AmbulanceLifecycleKind.registerVehicle,
      plateNumber: 'AMB-501',
      equipmentLevel: 'bls',
      capacity: 1,
    );
    var writes = 0;
    final client = MockClient((request) async {
      writes++;
      expect(request.method, 'POST');
      expect(request.url.path, '/api/v1/ambulance/vehicles');
      expect(jsonDecode(request.body), <String, dynamic>{
        'plate_number': 'AMB-501',
        'equipment_level': 'bls',
        'capacity': 1,
      });
      return _ok(_vehicle(status: 'available'), 201);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final proof = await source.inspect(command);
    final result = await source.applyOnce(command: command, inspection: proof);
    expect(result.vehicle!.plateNumber, 'AMB-501');
    expect(writes, 1);
  });

  test('unknown POST outcome is blocked and never replayed', () async {
    const command = AmbulanceLifecycleCommand(
      kind: AmbulanceLifecycleKind.cancelTrip,
      tripId: 9,
      reason: 'Weather closure',
    );
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return _ok(
          request.url.path.contains('vehicles') ? _vehicle() : _trip(),
        );
      }
      writes++;
      return http.Response('{"success":false}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final proof = await source.inspect(command);
    await expectLater(
      source.applyOnce(command: command, inspection: proof),
      throwsA(isA<AmbulanceLifecycleOutcomeUnconfirmed>()),
    );
    await expectLater(
      source.applyOnce(command: command, inspection: proof),
      throwsA(isA<AmbulanceLifecycleAlreadyAttempted>()),
    );
    expect(writes, 1);
  });

  test('session expiry during mutation leaves outcome unconfirmed', () async {
    const command = AmbulanceLifecycleCommand(
      kind: AmbulanceLifecycleKind.tripProgress,
      tripId: 9,
      nextStatus: 'en_route_to_pickup',
    );
    var writes = 0;
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return _ok(
          request.url.path.contains('vehicles') ? _vehicle() : _trip(),
        );
      }
      writes++;
      guard.invalidate();
      return _ok(_trip(status: 'en_route_to_pickup'));
    });
    addTearDown(client.close);
    final source = _source(client, guard);
    final proof = await source.inspect(command);
    await expectLater(
      source.applyOnce(command: command, inspection: proof),
      throwsA(isA<AmbulanceLifecycleOutcomeUnconfirmed>()),
    );
    expect(writes, 1);
  });
}
