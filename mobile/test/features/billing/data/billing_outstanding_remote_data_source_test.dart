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
import 'package:clinic_system_pro/features/billing/data/models/outstanding_invoice.dart';

AuthSessionState _ready({
  String role = 'ADMIN',
  int clinicId = 3,
  String token = 'billing-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'billing-refresh',
    userId: 42,
    role: role,
    clinicContextId: clinicId,
  ),
);

Map<String, dynamic> _invoice({
  int id = 11,
  int clinicId = 3,
  String status = 'issued',
}) => {
  'id': id,
  'clinic_id': clinicId,
  'patient_id': 7,
  'appointment_id': null,
  'invoice_number': 'INV-$id',
  'currency': 'NGN',
  'total_amount': '2500.00',
  'amount_paid': '250.00',
  'status': status,
  'due_date': '2026-12-31',
  'is_insurance_claim': false,
  'insurance_provider': null,
  'created_at': '2026-11-10T10:00:00+00:00',
  'updated_at': '2026-11-10T10:01:00+00:00',
};

Map<String, dynamic> _page(
  List<Map<String, dynamic>> items, {
  int? total,
  int page = 1,
  int perPage = 20,
}) {
  final count = total ?? items.length;
  final pages = (count + perPage - 1) ~/ perPage;
  return {
    'success': true,
    'data': {
      'items': items,
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
  MockClient httpClient,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? session,
}) => BillingOutstandingRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: httpClient,
  ),
  loadSession: session ?? () async => _ready(),
  sessionNavigationGuard: guard,
);

void main() {
  test('admin outstanding GET has exact query and token, no POST', () async {
    final paths = <String>[];
    final client = MockClient((request) async {
      paths.add(request.url.path);
      expect(request.method, 'GET');
      expect(request.headers['authorization'], 'Bearer billing-token');
      expect(request.url.queryParameters, {'page': '1', 'per_page': '20'});
      return http.Response(jsonEncode(_page([_invoice()])), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final page = await _source(client, guard).fetchOutstanding();
    expect(paths, ['/api/v1/billing/invoices/outstanding']);
    expect(page.items.single.totalAmount, '2500.00');
    expect(page.items.single.amountPaid, '250.00');
    expect(page.items.single.currency, 'NGN');
    expect(page.items.single.status, OutstandingInvoiceStatus.issued);
  });

  test(
    'no request for unauthorized roles, missing clinic, or invalid guard',
    () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response(jsonEncode(_page([_invoice()])), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      for (final role in [
        'doctor',
        'nurse',
        'patient',
        'super_admin',
        'pharmacist',
      ]) {
        await expectLater(
          _source(
            client,
            guard,
            session: () async => _ready(role: role),
          ).fetchOutstanding(),
          throwsA(isA<BillingReadSessionUnavailable>()),
        );
      }
      await expectLater(
        _source(
          client,
          guard,
          session: () async => _ready(clinicId: 0),
        ).fetchOutstanding(),
        throwsA(isA<BillingReadSessionUnavailable>()),
      );
      guard.invalidate();
      await expectLater(
        _source(client, guard).fetchOutstanding(),
        throwsA(isA<BillingReadSessionUnavailable>()),
      );
      expect(calls, 0);
    },
  );

  test(
    'cross-clinic invoice, wrong status and financial mismatch fail closed',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final rows = [
        _invoice(clinicId: 10),
        _invoice(status: 'paid'),
        _invoice()..['amount_paid'] = '2501.00',
        _invoice()..['total_amount'] = 2500.00,
        _invoice()..['currency'] = 'NGN NGN',
      ];
      for (final row in rows) {
        final client = MockClient(
          (_) async => http.Response(jsonEncode(_page([row])), 200),
        );
        await expectLater(
          _source(client, guard).fetchOutstanding(),
          throwsA(isA<FormatException>()),
        );
        client.close();
      }
    },
  );

  test(
    'duplicate IDs, inconsistent pages and invalid pagination reject',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final client = MockClient(
        (_) async =>
            http.Response(jsonEncode(_page([_invoice(), _invoice()])), 200),
      );
      addTearDown(client.close);
      await expectLater(
        _source(client, guard).fetchOutstanding(),
        throwsA(isA<FormatException>()),
      );
      await expectLater(
        _source(client, guard).fetchOutstanding(page: 0),
        throwsArgumentError,
      );
      await expectLater(
        _source(client, guard).fetchOutstanding(perPage: 501),
        throwsArgumentError,
      );
    },
  );

  test(
    'pagination preserves server page, total and no client totals',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final client = MockClient((request) async {
        expect(request.url.queryParameters, {'page': '2', 'per_page': '1'});
        return http.Response(
          jsonEncode(_page([_invoice(id: 12)], total: 2, page: 2, perPage: 1)),
          200,
        );
      });
      addTearDown(client.close);
      final page = await _source(
        client,
        guard,
      ).fetchOutstanding(page: 2, perPage: 1);
      expect(page.items.single.id, 12);
      expect(page.total, 2);
      expect(page.hasNext, isFalse);
    },
  );

  test('logout while GET pending rejects late response', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final pending = Completer<http.Response>();
    final client = MockClient((_) => pending.future);
    addTearDown(client.close);
    final future = _source(client, guard).fetchOutstanding();
    await Future<void>.delayed(const Duration(milliseconds: 30));
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_page([_invoice()])), 200));
    await expectLater(future, throwsA(isA<BillingReadSessionUnavailable>()));
  });

  test('clinic switch while GET pending rejects late response', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var clinic = 3;
    final pending = Completer<http.Response>();
    final client = MockClient((_) => pending.future);
    addTearDown(client.close);
    final future = _source(
      client,
      guard,
      session: () async => _ready(clinicId: clinic),
    ).fetchOutstanding();
    await Future<void>.delayed(const Duration(milliseconds: 30));
    clinic = 4;
    pending.complete(http.Response(jsonEncode(_page([_invoice()])), 200));
    await expectLater(future, throwsA(isA<BillingReadSessionUnavailable>()));
  });
}
