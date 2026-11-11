import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_invoice_link_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_invoice_link.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/billing/data/billing_outstanding_remote_data_source.dart';

AuthSessionState _ready({String role = 'admin', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'link-token',
        refreshToken: 'link-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> _trip({
  String status = 'completed',
  int clinic = 3,
  int? patient = 7,
  int? invoice,
}) => <String, dynamic>{
  'id': 9,
  'clinic_id': clinic,
  'vehicle_id': 5,
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
  'dispatched_at': '2026-11-10T08:20:00',
  'pickup_at': '2026-11-10T08:40:00',
  'completed_at': status == 'completed' ? '2026-11-10T09:00:00' : null,
  'cancelled_at': null,
  'cancellation_reason': null,
  'notes': null,
  'invoice_id': invoice,
};

Map<String, dynamic> _invoice({
  int patient = 7,
  int clinic = 3,
  int id = 11,
  String status = 'issued',
  String amount = '2500.00',
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'appointment_id': null,
  'invoice_number': 'INV-$id',
  'currency': 'NGN',
  'total_amount': amount,
  'amount_paid': '0.00',
  'status': status,
  'due_date': null,
  'is_insurance_claim': false,
  'insurance_provider': null,
  'created_at': '2026-11-10T07:00:00+00:00',
  'updated_at': '2026-11-10T07:10:00+00:00',
};

Map<String, dynamic> _page(List<Map<String, dynamic>> rows) =>
    <String, dynamic>{
      'items': rows,
      'total': rows.length,
      'page': 1,
      'per_page': 20,
      'pages': rows.isEmpty ? 0 : 1,
      'has_next': false,
      'has_prev': false,
    };

http.Response _ok(Map<String, dynamic> data, [int code = 200]) => http.Response(
  jsonEncode(<String, dynamic>{'success': true, 'data': data}),
  code,
);

AmbulanceInvoiceLinkRemoteDataSource _source(
  http.Client client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? load,
}) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  final session = load ?? () async => _ready();
  return AmbulanceInvoiceLinkRemoteDataSource(
    ambulanceReader: AmbulanceReadRemoteDataSource(
      apiClient: api,
      loadSession: session,
      sessionNavigationGuard: guard,
    ),
    billingReader: BillingOutstandingRemoteDataSource(
      apiClient: api,
      loadSession: session,
      sessionNavigationGuard: guard,
    ),
  );
}

const _command = AmbulanceInvoiceLinkCommand(tripId: 9, invoiceId: 11);

void main() {
  test(
    'admin reviews both GET records and posts exactly one invoice link',
    () async {
      var writes = 0;
      final client = MockClient((request) async {
        expect(request.headers['authorization'], 'Bearer link-token');
        if (request.method == 'GET') {
          if (request.url.path.contains('billing')) {
            expect(request.url.queryParameters, <String, String>{
              'page': '1',
              'per_page': '20',
            });
            return _ok(_page(<Map<String, dynamic>>[_invoice()]));
          }
          expect(request.url.path, '/api/v1/ambulance/trips/9');
          return _ok(_trip());
        }
        writes++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/ambulance/trips/9/invoice');
        expect(jsonDecode(request.body), <String, dynamic>{'invoice_id': 11});
        return _ok(_trip(invoice: 11));
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final inspection = await source.inspect(_command);
      expect(writes, 0);
      expect(inspection.invoice.totalAmount, '2500.00');
      final confirmation = await source.linkOnce(
        command: _command,
        inspection: inspection,
      );
      expect(confirmation.trip.invoiceId, 11);
      expect(source.writeAttempted, isTrue);
      expect(writes, 1);
      await expectLater(
        source.linkOnce(command: _command, inspection: inspection),
        throwsA(isA<AmbulanceInvoiceLinkAlreadyAttempted>()),
      );
      expect(writes, 1);
    },
  );

  test(
    'nonadmin trip managers and crew cannot inspect invoice or write',
    () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return _ok(_trip());
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      for (final role in <String>[
        'ambulance_coordinator',
        'ambulance_dispatcher',
        'driver',
        'emt',
        'paramedic',
        'doctor',
        'patient',
      ]) {
        final source = _source(
          client,
          guard,
          load: () async => _ready(role: role),
        );
        await expectLater(
          source.inspect(_command),
          throwsA(isA<AmbulanceInvoiceLinkUnauthorized>()),
        );
      }
      expect(calls, 0);
    },
  );

  test(
    'mismatched patient, clinic, status, and linked trip refuse POST',
    () async {
      final variants = <Map<String, dynamic>>[
        _trip(patient: 99),
        _trip(clinic: 8),
        _trip(status: 'dispatched'),
        _trip(invoice: 37),
        _trip(patient: null),
      ];
      for (final variant in variants) {
        var writes = 0;
        final client = MockClient((request) async {
          if (request.method == 'POST') writes++;
          return request.url.path.contains('billing')
              ? _ok(_page(<Map<String, dynamic>>[_invoice()]))
              : _ok(variant);
        });
        final guard = SessionNavigationGuard()..markSessionAvailable();
        final source = _source(client, guard);
        await expectLater(
          source.inspect(_command),
          throwsA(
            anyOf(
              isA<AmbulanceInvoiceLinkEvidenceChanged>(),
              isA<FormatException>(),
            ),
          ),
        );
        expect(writes, 0);
        guard.dispose();
        client.close();
      }
    },
  );

  test('missing invoice or cross-clinic invoice fails without write', () async {
    for (final entries in <List<Map<String, dynamic>>>[
      <Map<String, dynamic>>[],
      <Map<String, dynamic>>[_invoice(id: 12)],
      <Map<String, dynamic>>[_invoice(clinic: 8)],
      <Map<String, dynamic>>[_invoice(patient: 99)],
    ]) {
      var writes = 0;
      final client = MockClient((request) async {
        if (request.method == 'POST') writes++;
        return request.url.path.contains('billing')
            ? _ok(_page(entries))
            : _ok(_trip());
      });
      final guard = SessionNavigationGuard()..markSessionAvailable();
      await expectLater(
        _source(client, guard).inspect(_command),
        throwsA(
          anyOf(
            isA<AmbulanceInvoiceLinkEvidenceChanged>(),
            isA<FormatException>(),
          ),
        ),
      );
      expect(writes, 0);
      guard.dispose();
      client.close();
    }
  });

  test(
    'stale completed-trip evidence during fresh inspection blocks POST',
    () async {
      var readCount = 0;
      var writes = 0;
      final client = MockClient((request) async {
        if (request.method == 'POST') {
          writes++;
          return _ok(_trip(invoice: 11));
        }
        if (request.url.path.contains('billing')) {
          return _ok(_page(<Map<String, dynamic>>[_invoice()]));
        }
        readCount++;
        return _ok(_trip(invoice: readCount > 1 ? 11 : null));
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final proof = await source.inspect(_command);
      await expectLater(
        source.linkOnce(command: _command, inspection: proof),
        throwsA(isA<AmbulanceInvoiceLinkEvidenceChanged>()),
      );
      expect(source.writeAttempted, isFalse);
      expect(writes, 0);
    },
  );

  test('invoice balance updated after review blocks write', () async {
    var billingReads = 0;
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        writes++;
        return _ok(_trip(invoice: 11));
      }
      if (request.url.path.contains('billing')) {
        billingReads++;
        return _ok(
          _page(<Map<String, dynamic>>[
            _invoice(amount: billingReads == 1 ? '2500.00' : '2100.00'),
          ]),
        );
      }
      return _ok(_trip());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final proof = await source.inspect(_command);
    await expectLater(
      source.linkOnce(command: _command, inspection: proof),
      throwsA(isA<AmbulanceInvoiceLinkEvidenceChanged>()),
    );
    expect(source.writeAttempted, isFalse);
    expect(writes, 0);
  });

  test(
    'session invalidated after POST is uncertain and never replayed',
    () async {
      var writes = 0;
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return request.url.path.contains('billing')
              ? _ok(_page(<Map<String, dynamic>>[_invoice()]))
              : _ok(_trip());
        }
        writes++;
        guard.invalidate();
        return _ok(_trip(invoice: 11));
      });
      addTearDown(client.close);
      final source = _source(client, guard);
      final proof = await source.inspect(_command);
      await expectLater(
        source.linkOnce(command: _command, inspection: proof),
        throwsA(isA<AmbulanceInvoiceLinkOutcomeUnconfirmed>()),
      );
      expect(writes, 1);
      await expectLater(
        source.linkOnce(command: _command, inspection: proof),
        throwsA(isA<AmbulanceInvoiceLinkAlreadyAttempted>()),
      );
    },
  );

  test(
    'server response mismatched or malformed is unconfirmed and blocked',
    () async {
      var writes = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return request.url.path.contains('billing')
              ? _ok(_page(<Map<String, dynamic>>[_invoice()]))
              : _ok(_trip());
        }
        writes++;
        return _ok(_trip(invoice: 900));
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final proof = await source.inspect(_command);
      await expectLater(
        source.linkOnce(command: _command, inspection: proof),
        throwsA(isA<AmbulanceInvoiceLinkOutcomeUnconfirmed>()),
      );
      await expectLater(
        source.linkOnce(command: _command, inspection: proof),
        throwsA(isA<AmbulanceInvoiceLinkAlreadyAttempted>()),
      );
      expect(writes, 1);
    },
  );

  test('changed command rejects existing proof without POST', () async {
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method != 'GET') writes++;
      return request.url.path.contains('billing')
          ? _ok(_page(<Map<String, dynamic>>[_invoice()]))
          : _ok(_trip());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final proof = await source.inspect(_command);
    await expectLater(
      source.linkOnce(
        command: const AmbulanceInvoiceLinkCommand(tripId: 9, invoiceId: 12),
        inspection: proof,
      ),
      throwsA(isA<AmbulanceInvoiceLinkEvidenceChanged>()),
    );
    expect(writes, 0);
  });
}
