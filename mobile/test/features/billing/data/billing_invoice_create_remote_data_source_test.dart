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
import 'package:clinic_system_pro/features/billing/data/billing_invoice_create_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/data/billing_outstanding_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/data/models/invoice_creation.dart';

AuthSessionState _session({String role = 'ADMIN', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'invoice-token',
        refreshToken: 'invoice-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinic,
      ),
    );

InvoiceDraft _draft({int patientId = 7, String price = '1250.00'}) =>
    InvoiceDraft(
      patientId: patientId,
      currency: 'NGN',
      items: <InvoiceDraftItem>[
        InvoiceDraftItem(
          description: 'Consultation charge',
          quantity: 2,
          unitPrice: price,
        ),
      ],
    );

Map<String, dynamic> _created({
  int clinicId = 3,
  int patientId = 7,
  String status = 'issued',
}) => <String, dynamic>{
  'success': true,
  'data': <String, dynamic>{
    'id': 11,
    'clinic_id': clinicId,
    'patient_id': patientId,
    'appointment_id': null,
    'invoice_number': 'INV-11',
    'currency': 'NGN',
    'total_amount': '2500.00',
    'amount_paid': '0.00',
    'status': status,
    'due_date': null,
    'is_insurance_claim': false,
    'insurance_provider': null,
    'created_at': '2026-11-10T10:00:00+00:00',
    'updated_at': '2026-11-10T10:00:00+00:00',
    'items': <Map<String, dynamic>>[
      <String, dynamic>{
        'id': 20,
        'invoice_id': 11,
        'description': 'Consultation charge',
        'quantity': 2,
        'unit_price': '1250.00',
        'subtotal': '2500.00',
      },
    ],
  },
};

BillingInvoiceCreateRemoteDataSource _source(
  http.Client client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loadSession,
}) => BillingInvoiceCreateRemoteDataSource(
  reader: BillingOutstandingRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: loadSession ?? () async => _session(),
    sessionNavigationGuard: guard,
  ),
);

void main() {
  test(
    'exact invoice POST path, decimal strings, admin bearer and key',
    () async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/billing/invoices');
        expect(request.headers['authorization'], 'Bearer invoice-token');
        expect(request.headers['idempotency-key'], 's03-key-1');
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body.keys.toSet(), <String>{
          'patient_id',
          'currency',
          'is_insurance_claim',
          'items',
        });
        expect(body['patient_id'], 7);
        expect(body['currency'], 'NGN');
        final items = body['items'] as List<dynamic>;
        expect((items.single as Map<String, dynamic>)['unit_price'], '1250.00');
        expect((items.single as Map<String, dynamic>)['quantity'], 2);
        return http.Response(jsonEncode(_created()), 201);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final result = await source.createOnce(
        draft: _draft(),
        idempotencyKey: 's03-key-1',
      );
      expect(result.invoiceNumber, 'INV-11');
      expect(result.status, InvoiceCreatedStatus.issued);
      expect(result.totalAmount, '2500.00');
      expect(source.writeAttempted, isTrue);
      expect(requests, 1);
      await expectLater(
        source.createOnce(draft: _draft(), idempotencyKey: 's03-key-1'),
        throwsA(isA<BillingInvoiceCreateAlreadyAttempted>()),
      );
      expect(requests, 1);
    },
  );

  test('invalid forms and illegal key cause zero POSTs', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_created()), 201);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    for (final draft in <InvoiceDraft>[
      _draft(patientId: 0),
      _draft(price: 'NaN'),
      _draft(price: '-1.50'),
      const InvoiceDraft(
        patientId: 7,
        currency: 'NGN',
        items: <InvoiceDraftItem>[],
      ),
      InvoiceDraft(
        patientId: 7,
        currency: 'NGN',
        items: <InvoiceDraftItem>[
          const InvoiceDraftItem(
            description: 'Care',
            quantity: 1,
            unitPrice: '1.00',
          ),
        ],
        isInsuranceClaim: true,
      ),
      InvoiceDraft(
        patientId: 7,
        currency: 'NGN',
        items: <InvoiceDraftItem>[
          const InvoiceDraftItem(
            description: 'Care',
            quantity: 1,
            unitPrice: '1.00',
          ),
        ],
        dueDate: '2026-02-30',
      ),
    ]) {
      await expectLater(
        source.createOnce(draft: draft, idempotencyKey: 'safe'),
        throwsA(isA<FormatException>()),
      );
    }
    await expectLater(
      source.createOnce(draft: _draft(), idempotencyKey: 'x\nInjected'),
      throwsA(isA<FormatException>()),
    );
    expect(source.writeAttempted, isFalse);
    expect(calls, 0);
  });

  test('unauthorized roles and missing scope never submit', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_created()), 201);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final role in <String>['patient', 'doctor', 'nurse', 'super_admin']) {
      await expectLater(
        _source(
          client,
          guard,
          loadSession: () async => _session(role: role),
        ).createOnce(draft: _draft(), idempotencyKey: 'safe'),
        throwsA(isA<BillingInvoiceCreateScopeUnavailable>()),
      );
    }
    await expectLater(
      _source(
        client,
        guard,
        loadSession: () async => _session(clinic: 0),
      ).createOnce(draft: _draft(), idempotencyKey: 'safe'),
      throwsA(isA<BillingInvoiceCreateScopeUnavailable>()),
    );
    guard.invalidate();
    await expectLater(
      _source(
        client,
        guard,
      ).createOnce(draft: _draft(), idempotencyKey: 'safe'),
      throwsA(isA<BillingInvoiceCreateScopeUnavailable>()),
    );
    expect(calls, 0);
  });

  test('concurrent identical submissions only send one POST', () async {
    final pending = Completer<http.Response>();
    var calls = 0;
    final client = MockClient((_) {
      calls++;
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final first = source.createOnce(draft: _draft(), idempotencyKey: 'safe');
    await expectLater(
      source.createOnce(draft: _draft(), idempotencyKey: 'safe'),
      throwsA(isA<BillingInvoiceCreateAlreadyAttempted>()),
    );
    await Future<void>.delayed(const Duration(milliseconds: 30));
    expect(calls, 1);
    pending.complete(http.Response(jsonEncode(_created()), 201));
    final created = await first;
    expect(created.id, 11);
    expect(calls, 1);
  });

  test('cross-patient, cross-clinic, wrong item and malformed server data fail closed', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final cases = <Map<String, dynamic>>[
      _created(clinicId: 8),
      _created(patientId: 8),
      _created(status: 'not-an-invoice-status'),
      _created()..['success'] = false,
      _created()..['data'] = <String, dynamic>{'id': 11},
    ];
    for (final body in cases) {
      final client = MockClient(
        (_) async => http.Response(jsonEncode(body), 201),
      );
      await expectLater(
        _source(
          client,
          guard,
        ).createOnce(draft: _draft(), idempotencyKey: 'safe'),
        throwsA(isA<BillingInvoiceCreateUnconfirmed>()),
      );
      client.close();
    }
  });

  test(
    'HTTP timeout and uncertain response never trigger automatic retry',
    () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        throw http.ClientException('offline');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      await expectLater(
        source.createOnce(draft: _draft(), idempotencyKey: 'safe'),
        throwsA(isA<BillingInvoiceCreateUnconfirmed>()),
      );
      await expectLater(
        source.createOnce(draft: _draft(), idempotencyKey: 'safe'),
        throwsA(isA<BillingInvoiceCreateAlreadyAttempted>()),
      );
      expect(calls, 1);
    },
  );

  test('logout during pending POST hides result and forbids replay', () async {
    final pending = Completer<http.Response>();
    var calls = 0;
    final client = MockClient((_) {
      calls++;
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final future = source.createOnce(draft: _draft(), idempotencyKey: 'safe');
    await Future<void>.delayed(const Duration(milliseconds: 30));
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_created()), 201));
    await expectLater(future, throwsA(isA<BillingInvoiceCreateUnconfirmed>()));
    await expectLater(
      source.createOnce(draft: _draft(), idempotencyKey: 'safe'),
      throwsA(isA<BillingInvoiceCreateAlreadyAttempted>()),
    );
    expect(calls, 1);
  });
}
