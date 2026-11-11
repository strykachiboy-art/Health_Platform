import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_lifecycle_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/presentation/ambulance_lifecycle_screen.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

AuthSessionState _ready() => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 's07-token',
    refreshToken: 's07-refresh',
    userId: 42,
    role: 'driver',
    clinicContextId: 3,
  ),
);

Map<String, dynamic> _trip(String status) => <String, dynamic>{
  'id': 9,
  'clinic_id': 3,
  'vehicle_id': 5,
  'patient_id': 17,
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
  'dispatched_at': '2026-11-10T08:10:00',
  'pickup_at': null,
  'completed_at': null,
  'cancelled_at': null,
  'cancellation_reason': null,
  'notes': null,
  'invoice_id': null,
};

Map<String, dynamic> _vehicle() => <String, dynamic>{
  'id': 5,
  'clinic_id': 3,
  'plate_number': 'AMB-5',
  'equipment_level': 'bls',
  'capacity': 1,
  'status': 'on_trip',
  'last_service_date': null,
  'created_at': '2026-11-10T08:00:00',
  'updated_at': '2026-11-10T08:00:00',
};

http.Response _ok(Map<String, dynamic> row) => http.Response(
  jsonEncode(<String, dynamic>{'success': true, 'data': row}),
  200,
);

AmbulanceLifecycleRemoteDataSource _source(
  http.Client client,
  SessionNavigationGuard guard,
) => AmbulanceLifecycleRemoteDataSource(
  reader: AmbulanceReadRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: () async => _ready(),
    sessionNavigationGuard: guard,
  ),
);

Future<void> _visible(WidgetTester tester, String key) async {
  await tester.ensureVisible(find.byKey(ValueKey(key)));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('no POST before explicit read, review and confirmation', (
    tester,
  ) async {
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return _ok(
          request.url.path.contains('vehicles')
              ? _vehicle()
              : _trip('dispatched'),
        );
      }
      writes++;
      expect(request.method, 'PATCH');
      expect(request.url.path, '/api/v1/ambulance/trips/9/status');
      return _ok(_trip('en_route_to_pickup'));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await tester.pumpWidget(
      MaterialApp(home: AmbulanceLifecycleScreen(source: source)),
    );
    await _visible(tester, 'p23i-s07-submit');
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23i-s07-submit')))
          .onPressed,
      isNull,
    );
    expect(writes, 0);
    await _visible(tester, 'p23i-s07-trip-id');
    await tester.enterText(find.byKey(const ValueKey('p23i-s07-trip-id')), '9');
    await _visible(tester, 'p23i-s07-status');
    await tester.enterText(
      find.byKey(const ValueKey('p23i-s07-status')),
      'en_route_to_pickup',
    );
    await _visible(tester, 'p23i-s07-inspect');
    await tester.tap(find.byKey(const ValueKey('p23i-s07-inspect')));
    await tester.pumpAndSettle();
    expect(writes, 0);
    await _visible(tester, 'p23i-s07-reviewed');
    await tester.tap(find.byKey(const ValueKey('p23i-s07-reviewed')));
    await tester.pumpAndSettle();
    await _visible(tester, 'p23i-s07-submit');
    await tester.tap(find.byKey(const ValueKey('p23i-s07-submit')));
    await tester.pumpAndSettle();
    expect(writes, 0);
    expect(find.text('Confirm one ambulance change'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('p23i-s07-confirm-once')));
    await tester.pumpAndSettle();
    expect(writes, 1);
    expect(source.writeAttempted, isTrue);
    expect(find.byKey(const ValueKey('p23i-s07-confirmed')), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23i-s07-submit')))
          .onPressed,
      isNull,
    );
  });

  testWidgets('logout invalidates already inspected evidence without write', (
    tester,
  ) async {
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method != 'GET') writes++;
      return _ok(
        request.url.path.contains('vehicles')
            ? _vehicle()
            : _trip('dispatched'),
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await tester.pumpWidget(
      MaterialApp(home: AmbulanceLifecycleScreen(source: source)),
    );
    await _visible(tester, 'p23i-s07-trip-id');
    await tester.enterText(find.byKey(const ValueKey('p23i-s07-trip-id')), '9');
    await _visible(tester, 'p23i-s07-status');
    await tester.enterText(
      find.byKey(const ValueKey('p23i-s07-status')),
      'en_route_to_pickup',
    );
    await _visible(tester, 'p23i-s07-inspect');
    await tester.tap(find.byKey(const ValueKey('p23i-s07-inspect')));
    await tester.pumpAndSettle();
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23i-s07-submit')))
          .onPressed,
      isNull,
    );
    expect(find.textContaining('Session or clinic changed'), findsOneWidget);
    expect(writes, 0);
  });
}
