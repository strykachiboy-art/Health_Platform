import 'dart:async';
import 'dart:convert';

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
import 'package:clinic_system_pro/features/billing/data/models/billing_payment.dart';
import 'package:clinic_system_pro/features/billing/data/models/outstanding_invoice.dart';

AuthSessionState _session({String role = 'ADMIN', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'payment-token',
        refreshToken: 'payment-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinic,
      ),
    );

OutstandingInvoice _invoice({int clinic = 3, String paid = '0.00'}) =>
    OutstandingInvoice(
      id: 11,
      clinicId: clinic,
      patientId: 7,
      appointmentId: null,
      invoiceNumber: 'INV-11',
      currency: 'NGN',
      totalAmount: '2500.00',
      amountPaid: paid,
      status: OutstandingInvoiceStatus.issued,
      dueDate: null,
      isInsuranceClaim: false,
      insuranceProvider: null,
      createdAt: '2026-11-10T10:00:00Z',
      updatedAt: '2026-11-10T10:00:00Z',
    );

Map<String, dynamic> _outstanding({int clinic = 3, String paid = '0.00'}) =>
    <String, dynamic>{
      'success': true,
      'data': <String, dynamic>{
        'items': <Map<String, dynamic>>[
          <String, dynamic>{
            'id': 11,
            'clinic_id': clinic,
            'patient_id': 7,
            'appointment_id': null,
            'invoice_number': 'INV-11',
            'currency': 'NGN',
            'total_amount': '2500.00',
            'amount_paid': paid,
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

Map<String, dynamic> _payment({
  String status = 'pending',
  String? gateway = 'paystack',
  String method = 'card',
  String? transaction,
  int invoiceId = 11,
  String amount = '1200.00',
}) => <String, dynamic>{
  'id': 99,
  'invoice_id': invoiceId,
  'amount': amount,
  'currency': 'NGN',
  'method': method,
  'status': status,
  'gateway': gateway,
  'reference': null,
  'gateway_transaction_id': transaction,
  'failure_reason': null,
  'created_at': '2026-11-10T11:00:00Z',
  'updated_at': '2026-11-10T11:00:00Z',
  'paid_at': status == 'successful' ? '2026-11-10T11:00:00Z' : null,
};

BillingPaymentDraft _draft({
  BillingPaymentFlow flow = BillingPaymentFlow.gatewayInitialize,
  String amount = '1200.00',
  OutstandingInvoice? invoice,
}) => BillingPaymentDraft(
  invoice: invoice ?? _invoice(),
  flow: flow,
  amount: amount,
  method: flow == BillingPaymentFlow.gatewayInitialize ? 'card' : 'cash',
  gateway: flow == BillingPaymentFlow.gatewayInitialize ? 'paystack' : null,
);

BillingPaymentRemoteDataSource _source(
  http.Client client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? session,
}) => BillingPaymentRemoteDataSource(
  reader: BillingOutstandingRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: session ?? () async => _session(),
    sessionNavigationGuard: guard,
  ),
);

void main() {
  test('gateway sends one POST, idempotency key, exact decimals; pending is NOT paid', () async {
    var gets = 0;
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        gets++;
        expect(request.url.path, '/api/v1/billing/invoices/outstanding');
        expect(request.url.queryParameters['page'], '1');
        expect(request.headers['authorization'], 'Bearer payment-token');
        return http.Response(jsonEncode(_outstanding()), 200);
      }
      posts++;
      expect(request.method, 'POST');
      expect(request.url.path, '/api/v1/billing/payments/initialize');
      expect(request.headers['idempotency-key'], 'payment-s04-key');
      expect(request.headers['authorization'], 'Bearer payment-token');
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      expect(body.keys.toSet(), <String>{
        'invoice_id',
        'amount',
        'method',
        'gateway',
        'currency',
      });
      expect(body['amount'], '1200.00');
      return http.Response(
        jsonEncode(<String, dynamic>{
          'success': true,
          'data': <String, dynamic>{
            'payment': _payment(),
            'outcome': 'pending',
          },
        }),
        202,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final result = await source.submitOnce(
      draft: _draft(),
      idempotencyKey: 'payment-s04-key',
      sourcePage: 1,
      sourcePerPage: 20,
    );
    expect(result.status, BillingPaymentStatus.pending);
    expect(result.pendingProviderConfirmation, isTrue);
    expect(source.writeAttempted, isTrue);
    expect(gets, 1);
    expect(posts, 1);
    await expectLater(
      source.submitOnce(
        draft: _draft(),
        idempotencyKey: 'payment-s04-key',
        sourcePage: 1,
        sourcePerPage: 20,
      ),
      throwsA(isA<BillingPaymentAlreadyAttempted>()),
    );
    expect(posts, 1);
  });

  test('direct cash recording is separate endpoint; only server-successful confirms', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return http.Response(jsonEncode(_outstanding()), 200);
      }
      posts++;
      expect(request.url.path, '/api/v1/billing/payments');
      expect(request.headers['idempotency-key'], isNotEmpty);
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      expect(body.keys.toSet(), <String>{'invoice_id', 'amount', 'method'});
      expect(body['method'], 'cash');
      return http.Response(
        jsonEncode(<String, dynamic>{
          'success': true,
          'data': _payment(status: 'successful', gateway: null, method: 'cash'),
        }),
        201,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final result = await _source(client, guard).submitOnce(
      draft: _draft(flow: BillingPaymentFlow.directRecord),
      idempotencyKey: 'record-once',
      sourcePage: 1,
      sourcePerPage: 20,
    );
    expect(result.status, BillingPaymentStatus.successful);
    expect(result.pendingProviderConfirmation, isFalse);
    expect(posts, 1);
  });

  test(
    'invalid amount and unsafe method/gateway combinations send no requests',
    () async {
      var requests = 0;
      final client = MockClient((_) async {
        requests++;
        return http.Response(jsonEncode(_outstanding()), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final invalid = <BillingPaymentDraft>[
        _draft(amount: '0.00'),
        _draft(amount: '2500.01'),
        _draft(amount: 'NaN'),
        _draft(amount: '-1.00'),
        BillingPaymentDraft(
          invoice: _invoice(),
          flow: BillingPaymentFlow.directRecord,
          amount: '5.00',
          method: 'card',
          gateway: 'stripe',
        ),
        BillingPaymentDraft(
          invoice: _invoice(),
          flow: BillingPaymentFlow.gatewayInitialize,
          amount: '5.00',
          method: 'cash',
          gateway: 'paystack',
        ),
      ];
      for (final draft in invalid) {
        await expectLater(
          source.submitOnce(
            draft: draft,
            idempotencyKey: 'x',
            sourcePage: 1,
            sourcePerPage: 20,
          ),
          throwsA(isA<FormatException>()),
        );
      }
      expect(requests, 0);
      expect(source.writeAttempted, isFalse);
    },
  );

  test(
    'unauthorized roles and mismatched clinic cause zero GET and POST',
    () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response(jsonEncode(_outstanding()), 200);
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
            session: () async => _session(role: role),
          ).submitOnce(
            draft: _draft(),
            idempotencyKey: 'safe',
            sourcePage: 1,
            sourcePerPage: 20,
          ),
          throwsA(isA<BillingPaymentScopeUnavailable>()),
        );
      }
      await expectLater(
        _source(client, guard).submitOnce(
          draft: _draft(invoice: _invoice(clinic: 9)),
          idempotencyKey: 'safe',
          sourcePage: 1,
          sourcePerPage: 20,
        ),
        throwsA(isA<BillingPaymentScopeUnavailable>()),
      );
      guard.invalidate();
      await expectLater(
        _source(client, guard).submitOnce(
          draft: _draft(),
          idempotencyKey: 'safe',
          sourcePage: 1,
          sourcePerPage: 20,
        ),
        throwsA(isA<BillingPaymentScopeUnavailable>()),
      );
      expect(calls, 0);
    },
  );

  test('stale invoice amount prevents payment POST', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return http.Response(jsonEncode(_outstanding(paid: '5.00')), 200);
      }
      posts++;
      return http.Response('{}', 201);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await expectLater(
      source.submitOnce(
        draft: _draft(),
        idempotencyKey: 'safe',
        sourcePage: 1,
        sourcePerPage: 20,
      ),
      throwsA(isA<BillingPaymentEvidenceStale>()),
    );
    expect(source.writeAttempted, isFalse);
    expect(posts, 0);
  });

  test('concurrent attempts never make duplicate POSTs', () async {
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
    final first = source.submitOnce(
      draft: _draft(),
      idempotencyKey: 'safe',
      sourcePage: 1,
      sourcePerPage: 20,
    );
    await expectLater(
      source.submitOnce(
        draft: _draft(),
        idempotencyKey: 'safe',
        sourcePage: 1,
        sourcePerPage: 20,
      ),
      throwsA(isA<BillingPaymentAlreadyAttempted>()),
    );
    await Future<void>.delayed(const Duration(milliseconds: 30));
    expect(posts, 1);
    pending.complete(
      http.Response(
        jsonEncode(<String, dynamic>{
          'success': true,
          'data': <String, dynamic>{
            'payment': _payment(),
            'outcome': 'pending',
          },
        }),
        202,
      ),
    );
    await first;
    expect(posts, 1);
  });

  test(
    'HTTP failure, false success and mismatched invoice never auto replay',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      for (final response in <http.Response>[
        http.Response('server-error', 500),
        http.Response(jsonEncode(<String, dynamic>{'success': false}), 200),
        http.Response(
          jsonEncode(<String, dynamic>{
            'success': true,
            'data': <String, dynamic>{
              'payment': _payment(invoiceId: 19),
              'outcome': 'pending',
            },
          }),
          202,
        ),
        http.Response(
          jsonEncode(<String, dynamic>{
            'success': true,
            'data': <String, dynamic>{
              'payment': _payment(status: 'pending'),
              'outcome': 'successful',
            },
          }),
          201,
        ),
      ]) {
        var posts = 0;
        final client = MockClient((request) async {
          if (request.method == 'GET') {
            return http.Response(jsonEncode(_outstanding()), 200);
          }
          posts++;
          return response;
        });
        final source = _source(client, guard);
        await expectLater(
          source.submitOnce(
            draft: _draft(),
            idempotencyKey: 'safe',
            sourcePage: 1,
            sourcePerPage: 20,
          ),
          throwsA(isA<BillingPaymentOutcomeUnconfirmed>()),
        );
        await expectLater(
          source.submitOnce(
            draft: _draft(),
            idempotencyKey: 'safe',
            sourcePage: 1,
            sourcePerPage: 20,
          ),
          throwsA(isA<BillingPaymentAlreadyAttempted>()),
        );
        expect(posts, 1);
        client.close();
      }
    },
  );

  test(
    'session invalidation during in-flight POST hides result forever',
    () async {
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
      final future = source.submitOnce(
        draft: _draft(),
        idempotencyKey: 'safe',
        sourcePage: 1,
        sourcePerPage: 20,
      );
      await Future<void>.delayed(const Duration(milliseconds: 30));
      expect(posts, 1);
      guard.invalidate();
      pending.complete(
        http.Response(
          jsonEncode(<String, dynamic>{
            'success': true,
            'data': <String, dynamic>{
              'payment': _payment(),
              'outcome': 'pending',
            },
          }),
          202,
        ),
      );
      await expectLater(
        future,
        throwsA(isA<BillingPaymentOutcomeUnconfirmed>()),
      );
      expect(source.writeAttempted, isTrue);
    },
  );
}
