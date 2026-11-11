import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_dispatch_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/presentation/ambulance_dispatch_screen.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

AuthSessionState _admin() => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'ambulance-ui-token',
    refreshToken: 'ambulance-ui-refresh',
    userId: 42,
    role: 'ADMIN',
    clinicContextId: 3,
  ),
);

Map<String, dynamic> _trip({
  String status = 'requested',
  int? vehicleId,
  int? driverId,
}) => <String, dynamic>{
  'id': 9,
  'clinic_id': 3,
  'vehicle_id': vehicleId,
  'patient_id': 17,
  'driver_id': driverId,
  'paramedic_id': null,
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

Map<String, dynamic> _vehicle() => <String, dynamic>{
  'id': 5,
  'clinic_id': 3,
  'plate_number': 'AMB-500',
  'equipment_level': 'als',
  'capacity': 2,
  'status': 'available',
  'last_service_date': null,
  'created_at': '2026-11-10T08:00:00',
  'updated_at': '2026-11-10T08:00:00',
};

http.Response _ok(Map<String, dynamic> data, [int status = 200]) =>
    http.Response(
      jsonEncode(<String, dynamic>{'success': true, 'data': data}),
      status,
    );

AmbulanceDispatchRemoteDataSource _source(
  http.Client client,
  SessionNavigationGuard guard,
) => AmbulanceDispatchRemoteDataSource(
  reader: AmbulanceReadRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: () async => _admin(),
    sessionNavigationGuard: guard,
  ),
);

Future<void> _scroll(WidgetTester tester, String key) async {
  // Tests revisit earlier fields after viewing bottom-of-form actions.
  // Start from the top so a one-direction scroll also finds earlier controls.
  await tester.drag(find.byType(Scrollable).first, const Offset(0, 1600));
  await tester.pumpAndSettle();
  await tester.scrollUntilVisible(
    find.byKey(ValueKey(key)),
    200,
    scrollable: find.byType(Scrollable).first,
  );
  await tester.pump();
}

Future<void> _enter(WidgetTester tester, String key, String value) async {
  await _scroll(tester, key);
  await tester.enterText(find.byKey(ValueKey(key)), value);
  await tester.pump();
}

Future<void> _review(WidgetTester tester) async {
  await _scroll(tester, 'p23i-s06-reviewed');
  await tester.tap(find.byKey(const ValueKey('p23i-s06-reviewed')));
  await tester.pump();
  await _scroll(tester, 'p23i-s06-submit');
}

void main() {
  testWidgets(
    'unreviewed trip cannot write; explicit confirmation sends one POST',
    (tester) async {
      var posts = 0;
      final client = MockClient((request) async {
        posts++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/ambulance/trips');
        return _ok(_trip(), 201);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      await tester.pumpWidget(
        MaterialApp(home: AmbulanceDispatchScreen(source: source)),
      );
      await _scroll(tester, 'p23i-s06-submit');
      expect(
        tester
            .widget<FilledButton>(find.byKey(const ValueKey('p23i-s06-submit')))
            .onPressed,
        isNull,
      );
      expect(posts, 0);
      await _enter(tester, 'p23i-s06-patient-id', '17');
      await _enter(tester, 'p23i-s06-pickup', 'Clinic gate');
      await _enter(tester, 'p23i-s06-destination', 'Ward entrance');
      await _review(tester);
      await tester.tap(find.byKey(const ValueKey('p23i-s06-submit')));
      await tester.pumpAndSettle();
      expect(posts, 0);
      expect(find.text('Confirm ambulance operation'), findsOneWidget);
      await tester.tap(
        find.byKey(const ValueKey('p23i-s06-confirm-operation')),
      );
      await tester.pumpAndSettle();
      expect(posts, 1);
      expect(source.createWriteAttempted, isTrue);
      await _scroll(tester, 'p23i-s06-confirmed');
      expect(find.textContaining('Server-confirmed created'), findsOneWidget);
      expect(
        tester
            .widget<FilledButton>(find.byKey(const ValueKey('p23i-s06-submit')))
            .onPressed,
        isNull,
      );
    },
  );

  testWidgets('dispatch requires server inspection and explicit confirmation', (
    tester,
  ) async {
    var gets = 0;
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        gets++;
        return request.url.path.contains('/vehicles/')
            ? _ok(_vehicle())
            : _ok(_trip());
      }
      posts++;
      expect(request.url.path, '/api/v1/ambulance/trips/9/dispatch');
      return _ok(_trip(status: 'dispatched', vehicleId: 5, driverId: 42));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await tester.pumpWidget(
      MaterialApp(
        home: AmbulanceDispatchScreen(
          source: source,
          initialMode: AmbulanceDispatchMode.dispatch,
        ),
      ),
    );
    await _enter(tester, 'p23i-s06-trip-id', '9');
    await _enter(tester, 'p23i-s06-vehicle-id', '5');
    await _enter(tester, 'p23i-s06-driver-id', '42');
    await _review(tester);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23i-s06-submit')))
          .onPressed,
      isNull,
    );
    expect(posts, 0);
    await _scroll(tester, 'p23i-s06-inspect');
    await tester.tap(find.byKey(const ValueKey('p23i-s06-inspect')));
    await tester.pumpAndSettle();
    await _scroll(tester, 'p23i-s06-dispatch-evidence');
    expect(gets, 2);
    await _review(tester);
    await tester.tap(find.byKey(const ValueKey('p23i-s06-submit')));
    await tester.pumpAndSettle();
    expect(posts, 0);
    await tester.tap(find.byKey(const ValueKey('p23i-s06-confirm-operation')));
    await tester.pumpAndSettle();
    expect(gets, 4);
    expect(posts, 1);
    expect(source.dispatchWriteAttempted, isTrue);
    await _scroll(tester, 'p23i-s06-confirmed');
    expect(find.textContaining('Server-confirmed dispatched'), findsOneWidget);
  });

  testWidgets('changing dispatch ID invalidates inspection and review', (
    tester,
  ) async {
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
    await tester.pumpWidget(
      MaterialApp(
        home: AmbulanceDispatchScreen(
          source: _source(client, guard),
          initialMode: AmbulanceDispatchMode.dispatch,
        ),
      ),
    );
    await _enter(tester, 'p23i-s06-trip-id', '9');
    await _enter(tester, 'p23i-s06-vehicle-id', '5');
    await _enter(tester, 'p23i-s06-driver-id', '42');
    await _scroll(tester, 'p23i-s06-inspect');
    await tester.tap(find.byKey(const ValueKey('p23i-s06-inspect')));
    await tester.pumpAndSettle();
    await _review(tester);
    await _enter(tester, 'p23i-s06-vehicle-id', '6');
    await _scroll(tester, 'p23i-s06-submit');
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23i-s06-submit')))
          .onPressed,
      isNull,
    );
    expect(
      find.byKey(const ValueKey('p23i-s06-dispatch-evidence')),
      findsNothing,
    );
    expect(posts, 0);
  });

  testWidgets('logout during in-flight create clears confirmation, no replay', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    var posts = 0;
    final client = MockClient((request) {
      posts++;
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await tester.pumpWidget(
      MaterialApp(home: AmbulanceDispatchScreen(source: source)),
    );
    await _enter(tester, 'p23i-s06-patient-id', '17');
    await _enter(tester, 'p23i-s06-pickup', 'Clinic gate');
    await _enter(tester, 'p23i-s06-destination', 'Ward entrance');
    await _review(tester);
    await tester.tap(find.byKey(const ValueKey('p23i-s06-submit')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23i-s06-confirm-operation')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 30));
    expect(posts, 1);
    guard.invalidate();
    await tester.pump();
    pending.complete(_ok(_trip(), 201));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23i-s06-confirmed')), findsNothing);
    expect(source.createWriteAttempted, isTrue);
    expect(posts, 1);
  });
}
