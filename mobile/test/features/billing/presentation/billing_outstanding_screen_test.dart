import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/billing/data/billing_outstanding_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/presentation/billing_outstanding_screen.dart';

AuthSessionState _ready({String role = 'ADMIN'}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'widget-token',
    refreshToken: 'widget-refresh',
    userId: 15,
    role: role,
    clinicContextId: 3,
  ),
);

Map<String, dynamic> _invoice(int id) => {
  'id': id,
  'clinic_id': 3,
  'patient_id': 7,
  'appointment_id': null,
  'invoice_number': 'INV-$id',
  'currency': 'NGN',
  'total_amount': '3250.00',
  'amount_paid': '0.00',
  'status': 'issued',
  'due_date': '2026-12-31',
  'is_insurance_claim': false,
  'insurance_provider': null,
  'created_at': '2026-11-10T10:00:00+00:00',
  'updated_at': '2026-11-10T10:01:00+00:00',
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
      'total': count,
      'page': page,
      'per_page': perPage,
      'pages': pages,
      'has_next': page < pages,
      'has_prev': page > 1,
    },
  };
}

BillingOutstandingRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'ADMIN',
}) => BillingOutstandingRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => _ready(role: role),
  sessionNavigationGuard: guard,
);

Widget _host(BillingOutstandingRemoteDataSource source) =>
    MaterialApp(home: BillingOutstandingScreen(dataSource: source));

void main() {
  testWidgets('GET-only list shows server invoices and no payment button', (
    tester,
  ) async {
    var gets = 0;
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      gets++;
      return http.Response(jsonEncode(_page([_invoice(11)])), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    expect(gets, 1);
    expect(find.text('Invoice INV-11'), findsOneWidget);
    expect(find.textContaining('3250.00'), findsOneWidget);
    expect(find.text('Pay invoice'), findsNothing);
    expect(find.byKey(const ValueKey('p23i-s02-refresh')), findsOneWidget);
  });

  testWidgets('non-admin sees no invoice and sends zero GETs', (tester) async {
    var count = 0;
    final client = MockClient((_) async {
      count++;
      return http.Response(jsonEncode(_page([_invoice(11)])), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard, role: 'doctor')));
    await tester.pumpAndSettle();
    expect(count, 0);
    expect(find.byKey(const ValueKey('p23i-s02-error')), findsOneWidget);
    expect(find.text('Invoice INV-11'), findsNothing);
  });

  testWidgets('empty list is explicit server response', (tester) async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page([])), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23i-s02-empty')), findsOneWidget);
    expect(find.text('Invoices shown: 0 of 0'), findsOneWidget);
  });

  testWidgets('refresh failure clears previous sensitive invoice data', (
    tester,
  ) async {
    var reads = 0;
    final client = MockClient((request) async {
      reads++;
      if (reads == 1) {
        return http.Response(jsonEncode(_page([_invoice(11)])), 200);
      }
      return http.Response('{"success":false}', 503);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    expect(find.text('Invoice INV-11'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('p23i-s02-refresh')));
    await tester.pumpAndSettle();
    expect(find.text('Invoice INV-11'), findsNothing);
    expect(find.byKey(const ValueKey('p23i-s02-error')), findsOneWidget);
  });

  testWidgets('session invalidation clears invoice and blocks late response', (
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
    pending.complete(http.Response(jsonEncode(_page([_invoice(11)])), 200));
    await tester.pumpAndSettle();
    expect(find.text('Invoice INV-11'), findsNothing);
    expect(find.byKey(const ValueKey('p23i-s02-error')), findsOneWidget);
  });

  testWidgets('explicit refresh re-reads only the outstanding GET', (
    tester,
  ) async {
    var reads = 0;
    final client = MockClient((request) async {
      reads++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/billing/invoices/outstanding');
      return http.Response(jsonEncode(_page([_invoice(11)])), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23i-s02-refresh')));
    await tester.pumpAndSettle();
    expect(reads, 2);
  });
}
