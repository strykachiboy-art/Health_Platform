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
import 'package:clinic_system_pro/features/billing/data/billing_invoice_create_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/data/billing_outstanding_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/presentation/billing_invoice_create_screen.dart';

AuthSessionState _ready() => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'ui-token',
    refreshToken: 'ui-refresh',
    userId: 42,
    role: 'ADMIN',
    clinicContextId: 3,
  ),
);

BillingInvoiceCreateRemoteDataSource _source(
  http.Client client,
  SessionNavigationGuard guard,
) => BillingInvoiceCreateRemoteDataSource(
  reader: BillingOutstandingRemoteDataSource(
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

Map<String, dynamic> _confirmed() => <String, dynamic>{
  'success': true,
  'data': <String, dynamic>{
    'id': 44,
    'clinic_id': 3,
    'patient_id': 7,
    'appointment_id': null,
    'invoice_number': 'S03-44',
    'currency': 'NGN',
    'total_amount': '2000.00',
    'amount_paid': '0.00',
    'status': 'issued',
    'due_date': null,
    'is_insurance_claim': false,
    'insurance_provider': null,
    'created_at': '2026-11-10T10:00:00Z',
    'updated_at': '2026-11-10T10:00:00Z',
    'items': <Map<String, dynamic>>[
      <String, dynamic>{
        'id': 50,
        'invoice_id': 44,
        'description': 'Consultation',
        'quantity': 2,
        'unit_price': '1000.00',
        'subtotal': '2000.00',
      },
    ],
  },
};

Future<void> _scrollTo(WidgetTester tester, String key) async {
  final finder = find.byKey(ValueKey(key));
  await tester.scrollUntilVisible(
    finder,
    200,
    scrollable: find.byType(Scrollable).first,
  );
  await tester.pump();
}

Future<void> _fill(WidgetTester tester, {String unitPrice = '1000.00'}) async {
  await tester.enterText(
    find.byKey(const ValueKey('p23i-s03-currency')),
    'NGN',
  );
  await tester.enterText(
    find.byKey(const ValueKey('p23i-s03-description')),
    'Consultation',
  );
  await tester.enterText(find.byKey(const ValueKey('p23i-s03-quantity')), '2');
  await tester.enterText(
    find.byKey(const ValueKey('p23i-s03-price')),
    unitPrice,
  );
  await _scrollTo(tester, 'p23i-s03-reviewed');
  await tester.tap(find.byKey(const ValueKey('p23i-s03-reviewed')));
  await tester.pump();
  await _scrollTo(tester, 'p23i-s03-create');
}

void main() {
  testWidgets('explicit review and dialog send exactly one invoice POST', (
    tester,
  ) async {
    var postCount = 0;
    final client = MockClient((request) async {
      postCount++;
      expect(request.method, 'POST');
      expect(request.url.path, '/api/v1/billing/invoices');
      expect(request.headers['authorization'], 'Bearer ui-token');
      expect(request.headers['idempotency-key'], isNotEmpty);
      return http.Response(jsonEncode(_confirmed()), 201);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await tester.pumpWidget(
      MaterialApp(
        home: BillingInvoiceCreateScreen(source: source, patientId: 7),
      ),
    );
    await _scrollTo(tester, 'p23i-s03-create');
    final before = tester.widget<FilledButton>(
      find.byKey(const ValueKey('p23i-s03-create')),
    );
    expect(before.onPressed, isNull);
    expect(postCount, 0);
    await tester.drag(find.byType(Scrollable).first, const Offset(0, 650));
    await tester.pump();
    await _fill(tester);
    await tester.tap(find.byKey(const ValueKey('p23i-s03-create')));
    await tester.pumpAndSettle();
    expect(postCount, 0);
    expect(find.text('Confirm invoice creation'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('p23i-s03-dialog-confirm')));
    await tester.pumpAndSettle();
    expect(postCount, 1);
    expect(source.writeAttempted, isTrue);
    await _scrollTo(tester, 'p23i-s03-confirmed');
    expect(find.text('Invoice S03-44'), findsOneWidget);
    final after = tester.widget<FilledButton>(
      find.byKey(const ValueKey('p23i-s03-create')),
    );
    expect(after.onPressed, isNull);
  });

  testWidgets(
    'invalid amount is not posted, and an altered review is invalidated',
    (tester) async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response(jsonEncode(_confirmed()), 201);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: BillingInvoiceCreateScreen(
            source: _source(client, guard),
            patientId: 7,
          ),
        ),
      );
      await _fill(tester, unitPrice: 'not-money');
      await tester.tap(find.byKey(const ValueKey('p23i-s03-create')));
      await tester.pump();
      expect(calls, 0);
      await _scrollTo(tester, 'p23i-s03-price');
      await tester.enterText(
        find.byKey(const ValueKey('p23i-s03-price')),
        '10.00',
      );
      await _scrollTo(tester, 'p23i-s03-create');
      expect(
        tester
            .widget<FilledButton>(find.byKey(const ValueKey('p23i-s03-create')))
            .onPressed,
        isNull,
      );
      expect(calls, 0);
    },
  );

  testWidgets('session switch clears invoice fields and blocks late display', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    var count = 0;
    final client = MockClient((_) {
      count++;
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await tester.pumpWidget(
      MaterialApp(
        home: BillingInvoiceCreateScreen(source: source, patientId: 7),
      ),
    );
    await _fill(tester);
    await tester.tap(find.byKey(const ValueKey('p23i-s03-create')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23i-s03-dialog-confirm')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 30));
    expect(count, 1);
    guard.invalidate();
    await tester.pump();
    pending.complete(http.Response(jsonEncode(_confirmed()), 201));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23i-s03-confirmed')), findsNothing);
    expect(source.writeAttempted, isTrue);
  });
}
