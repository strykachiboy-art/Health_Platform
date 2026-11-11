import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_invoice_link_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/presentation/ambulance_invoice_link_screen.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/billing/data/billing_outstanding_remote_data_source.dart';

AuthSessionState _ready() => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'link-token',
    refreshToken: 'refresh-token',
    userId: 42,
    role: 'admin',
    clinicContextId: 3,
  ),
);

Map<String, dynamic> _trip({int? invoice}) => <String, dynamic>{
  'id': 9,
  'clinic_id': 3,
  'vehicle_id': 5,
  'patient_id': 7,
  'driver_id': 42,
  'paramedic_id': null,
  'admission_id': null,
  'trip_type': 'emergency_pickup',
  'status': 'completed',
  'pickup_address': 'Gate',
  'pickup_lat': null,
  'pickup_lng': null,
  'destination_address': 'Ward',
  'destination_lat': null,
  'destination_lng': null,
  'requested_at': '2026-11-10T08:00:00',
  'dispatched_at': '2026-11-10T08:10:00',
  'pickup_at': '2026-11-10T08:30:00',
  'completed_at': '2026-11-10T09:00:00',
  'cancelled_at': null,
  'cancellation_reason': null,
  'notes': null,
  'invoice_id': invoice,
};

Map<String, dynamic> _page() => <String, dynamic>{
  'items': <Map<String, dynamic>>[
    <String, dynamic>{
      'id': 11,
      'clinic_id': 3,
      'patient_id': 7,
      'appointment_id': null,
      'invoice_number': 'INV-11',
      'currency': 'NGN',
      'total_amount': '2500.00',
      'amount_paid': '0.00',
      'status': 'issued',
      'due_date': null,
      'is_insurance_claim': false,
      'insurance_provider': null,
      'created_at': '2026-11-10T08:00:00+00:00',
      'updated_at': '2026-11-10T08:01:00+00:00',
    },
  ],
  'total': 1,
  'page': 1,
  'per_page': 20,
  'pages': 1,
  'has_next': false,
  'has_prev': false,
};

http.Response _ok(Map<String, dynamic> row) => http.Response(
  jsonEncode(<String, dynamic>{'success': true, 'data': row}),
  200,
);

AmbulanceInvoiceLinkRemoteDataSource _source(
  http.Client client,
  SessionNavigationGuard guard,
) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  Future<AuthSessionState> load() async => _ready();
  return AmbulanceInvoiceLinkRemoteDataSource(
    ambulanceReader: AmbulanceReadRemoteDataSource(
      apiClient: api,
      loadSession: load,
      sessionNavigationGuard: guard,
    ),
    billingReader: BillingOutstandingRemoteDataSource(
      apiClient: api,
      loadSession: load,
      sessionNavigationGuard: guard,
    ),
  );
}

Future<void> _visible(WidgetTester tester, String key) async {
  await tester.ensureVisible(find.byKey(ValueKey(key)));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('no invoice-link POST without inspect, review and confirmation', (
    tester,
  ) async {
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return request.url.path.contains('billing')
            ? _ok(_page())
            : _ok(_trip());
      }
      writes++;
      expect(request.url.path, '/api/v1/ambulance/trips/9/invoice');
      return _ok(_trip(invoice: 11));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await tester.pumpWidget(
      MaterialApp(home: AmbulanceInvoiceLinkScreen(source: source)),
    );
    await _visible(tester, 'p23i-s08-submit');
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23i-s08-submit')))
          .onPressed,
      isNull,
    );
    expect(writes, 0);
    await _visible(tester, 'p23i-s08-trip-id');
    await tester.enterText(find.byKey(const ValueKey('p23i-s08-trip-id')), '9');
    await _visible(tester, 'p23i-s08-invoice-id');
    await tester.enterText(
      find.byKey(const ValueKey('p23i-s08-invoice-id')),
      '11',
    );
    await _visible(tester, 'p23i-s08-inspect');
    await tester.tap(find.byKey(const ValueKey('p23i-s08-inspect')));
    await tester.pumpAndSettle();
    expect(writes, 0);
    await _visible(tester, 'p23i-s08-reviewed');
    await tester.tap(find.byKey(const ValueKey('p23i-s08-reviewed')));
    await tester.pumpAndSettle();
    await _visible(tester, 'p23i-s08-submit');
    await tester.tap(find.byKey(const ValueKey('p23i-s08-submit')));
    await tester.pumpAndSettle();
    expect(writes, 0);
    expect(find.text('Confirm one ambulance invoice link'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('p23i-s08-confirm-once')));
    await tester.pumpAndSettle();
    expect(writes, 1);
    expect(source.writeAttempted, isTrue);
    expect(find.byKey(const ValueKey('p23i-s08-confirmed')), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23i-s08-submit')))
          .onPressed,
      isNull,
    );
  });

  testWidgets('logout after inspect disables action without a write', (
    tester,
  ) async {
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') writes++;
      return request.url.path.contains('billing') ? _ok(_page()) : _ok(_trip());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await tester.pumpWidget(
      MaterialApp(home: AmbulanceInvoiceLinkScreen(source: source)),
    );
    await _visible(tester, 'p23i-s08-trip-id');
    await tester.enterText(find.byKey(const ValueKey('p23i-s08-trip-id')), '9');
    await _visible(tester, 'p23i-s08-invoice-id');
    await tester.enterText(
      find.byKey(const ValueKey('p23i-s08-invoice-id')),
      '11',
    );
    await _visible(tester, 'p23i-s08-inspect');
    await tester.tap(find.byKey(const ValueKey('p23i-s08-inspect')));
    await tester.pumpAndSettle();
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23i-s08-submit')))
          .onPressed,
      isNull,
    );
    expect(writes, 0);
    expect(find.textContaining('Session or clinic changed'), findsOneWidget);
  });
}
