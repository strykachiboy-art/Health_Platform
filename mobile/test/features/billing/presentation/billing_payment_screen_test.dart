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
import 'package:clinic_system_pro/features/billing/data/billing_payment_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/data/models/outstanding_invoice.dart';
import 'package:clinic_system_pro/features/billing/presentation/billing_payment_screen.dart';

AuthSessionState _admin() => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'ui-payment-token',
    refreshToken: 'ui-payment-refresh',
    userId: 42,
    role: 'ADMIN',
    clinicContextId: 3,
  ),
);

OutstandingInvoice _invoice() => const OutstandingInvoice(
  id: 11,
  clinicId: 3,
  patientId: 7,
  appointmentId: null,
  invoiceNumber: 'INV-11',
  currency: 'NGN',
  totalAmount: '2500.00',
  amountPaid: '0.00',
  status: OutstandingInvoiceStatus.issued,
  dueDate: null,
  isInsuranceClaim: false,
  insuranceProvider: null,
  createdAt: '2026-11-10T10:00:00Z',
  updatedAt: '2026-11-10T10:00:00Z',
);

Map<String, dynamic> _outstanding() => <String, dynamic>{
  'success': true,
  'data': <String, dynamic>{
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
        'created_at': '2026-11-10T10:00:00Z',
        'updated_at': '2026-11-10T10:00:00Z',
      },
    ],
    'total': 1,
    'page': 1,
    'per_page': 20,
    'pages': 1,
    'has_next': false,
    'has_prev': false,
  },
};

Map<String, dynamic> _payment() => <String, dynamic>{
  'success': true,
  'data': <String, dynamic>{
    'outcome': 'pending',
    'payment': <String, dynamic>{
      'id': 9,
      'invoice_id': 11,
      'amount': '1200.00',
      'currency': 'NGN',
      'method': 'card',
      'status': 'pending',
      'gateway': 'paystack',
      'reference': null,
      'gateway_transaction_id': null,
      'failure_reason': null,
      'created_at': '2026-11-10T11:00:00Z',
      'updated_at': '2026-11-10T11:00:00Z',
      'paid_at': null,
    },
  },
};

BillingPaymentRemoteDataSource _source(
  http.Client client,
  SessionNavigationGuard guard,
) => BillingPaymentRemoteDataSource(
  reader: BillingOutstandingRemoteDataSource(
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
  await tester.scrollUntilVisible(
    find.byKey(ValueKey(key)),
    200,
    scrollable: find.byType(Scrollable).first,
  );
  await tester.pump();
}

Future<void> _fill(WidgetTester tester, String amount) async {
  await _scroll(tester, 'p23i-s04-amount');
  await tester.enterText(find.byKey(const ValueKey('p23i-s04-amount')), amount);
  await _scroll(tester, 'p23i-s04-reviewed');
  await tester.tap(find.byKey(const ValueKey('p23i-s04-reviewed')));
  await tester.pump();
  await _scroll(tester, 'p23i-s04-submit');
}

void main() {
  testWidgets('review + confirmation initiates once; pending is never paid', (
    tester,
  ) async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return http.Response(jsonEncode(_outstanding()), 200);
      }
      posts++;
      expect(request.url.path, '/api/v1/billing/payments/initialize');
      expect(request.headers['idempotency-key'], isNotEmpty);
      return http.Response(jsonEncode(_payment()), 202);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await tester.pumpWidget(
      MaterialApp(
        home: BillingPaymentScreen(
          source: source,
          invoice: _invoice(),
          sourcePage: 1,
          sourcePerPage: 20,
        ),
      ),
    );
    await _scroll(tester, 'p23i-s04-submit');
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23i-s04-submit')))
          .onPressed,
      isNull,
    );
    expect(posts, 0);
    await _fill(tester, '1200.00');
    await tester.tap(find.byKey(const ValueKey('p23i-s04-submit')));
    await tester.pumpAndSettle();
    expect(posts, 0);
    expect(find.text('Confirm financial payment operation'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('p23i-s04-dialog-confirm')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(source.writeAttempted, isTrue);
    await _scroll(tester, 'p23i-s04-confirmed');
    expect(
      find.textContaining('PENDING provider confirmation'),
      findsOneWidget,
    );
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23i-s04-submit')))
          .onPressed,
      isNull,
    );
  });

  testWidgets('invalid amount cannot post and editing invalidates review', (
    tester,
  ) async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return http.Response(jsonEncode(_outstanding()), 200);
      }
      posts++;
      return http.Response(jsonEncode(_payment()), 202);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: BillingPaymentScreen(
          source: _source(client, guard),
          invoice: _invoice(),
          sourcePage: 1,
          sourcePerPage: 20,
        ),
      ),
    );
    await _fill(tester, '2500.01');
    await tester.tap(find.byKey(const ValueKey('p23i-s04-submit')));
    await tester.pump();
    expect(posts, 0);
    await _scroll(tester, 'p23i-s04-amount');
    await tester.enterText(
      find.byKey(const ValueKey('p23i-s04-amount')),
      '100.00',
    );
    await _scroll(tester, 'p23i-s04-submit');
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23i-s04-submit')))
          .onPressed,
      isNull,
    );
    expect(posts, 0);
  });

  testWidgets('logout during payment clears result; no second POST', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    var posts = 0;
    final client = MockClient((request) {
      if (request.method == 'GET') {
        return Future<http.Response>.value(
          http.Response(jsonEncode(_outstanding()), 200),
        );
      }
      posts++;
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await tester.pumpWidget(
      MaterialApp(
        home: BillingPaymentScreen(
          source: source,
          invoice: _invoice(),
          sourcePage: 1,
          sourcePerPage: 20,
        ),
      ),
    );
    await _fill(tester, '1200.00');
    await tester.tap(find.byKey(const ValueKey('p23i-s04-submit')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23i-s04-dialog-confirm')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 30));
    expect(posts, 1);
    guard.invalidate();
    await tester.pump();
    pending.complete(http.Response(jsonEncode(_payment()), 202));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23i-s04-confirmed')), findsNothing);
    expect(source.writeAttempted, isTrue);
  });
}
