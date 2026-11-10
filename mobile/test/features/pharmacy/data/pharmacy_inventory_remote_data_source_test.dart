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
import 'package:clinic_system_pro/features/pharmacy/data/pharmacy_inventory_remote_data_source.dart';

AuthSessionState _ready({String role = 'pharmacist', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 's03a-token',
        refreshToken: 's03a-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> _drug({int id = 9, int? clinic = 3}) => {
  'id': id,
  'clinic_id': clinic,
  'name': 'Drug A',
  'category': 'analgesic',
  'strength': '500 mg',
  'is_controlled': false,
  'is_active': true,
};

Map<String, dynamic> _batch({int clinic = 3, int drug = 9}) => {
  'id': 31,
  'clinic_id': clinic,
  'drug_id': drug,
  'batch_number': 'LOT-11',
  'quantity_on_hand': 12,
  'reorder_level': 3,
  'expiry_date': '2027-12-31',
};

Map<String, dynamic> _page(List<Map<String, dynamic>> rows) => {
  'success': true,
  'data': {'items': rows, 'total': rows.length, 'page': 1, 'per_page': 20},
};

Map<String, dynamic> _summary({int clinic = 3, int drug = 9}) => {
  'success': true,
  'data': {
    'clinic_id': clinic,
    'drug_id': drug,
    'drug_name': 'Drug A',
    'quantity_on_hand': 12,
    'batch_count': 1,
  },
};

PharmacyInventoryRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loadSession,
}) => PharmacyInventoryRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: loadSession ?? () async => _ready(),
  sessionNavigationGuard: guard,
);

void main() {
  test(
    'pharmacist catalog GET is authenticated and returns typed drugs',
    () async {
      final paths = <String>[];
      final client = MockClient((request) async {
        paths.add(request.url.path);
        expect(request.method, 'GET');
        expect(request.headers['authorization'], 'Bearer s03a-token');
        expect(request.url.queryParameters, {
          'page': '1',
          'per_page': '20',
          'include_inactive': 'false',
        });
        return http.Response(jsonEncode(_page([_drug()])), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final drugs = await _source(client, guard).listDrugs();
      expect(drugs.items.single.name, 'Drug A');
      expect(drugs.items.single.isActive, isTrue);
      expect(paths, ['/api/v1/pharmacy/drugs']);
    },
  );

  test(
    'global drug catalog records are valid but cross-clinic drugs fail',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final shared = MockClient(
        (_) async =>
            http.Response(jsonEncode(_page([_drug(clinic: null)])), 200),
      );
      final other = MockClient(
        (_) async => http.Response(jsonEncode(_page([_drug(clinic: 8)])), 200),
      );
      addTearDown(shared.close);
      addTearDown(other.close);
      expect(
        (await _source(shared, guard).listDrugs()).items.single.clinicId,
        isNull,
      );
      await expectLater(
        _source(other, guard).listDrugs(),
        throwsA(isA<FormatException>()),
      );
    },
  );

  test('admin may read, doctor and patient cannot dispatch a GET', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_page([_drug()])), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final role in ['doctor', 'patient', 'nurse']) {
      await expectLater(
        _source(
          client,
          guard,
          loadSession: () async => _ready(role: role),
        ).listDrugs(),
        throwsA(isA<PharmacyInventorySessionUnavailable>()),
      );
    }
    expect(calls, 0);
    expect(
      (await _source(
        client,
        guard,
        loadSession: () async => _ready(role: 'admin'),
      ).listDrugs()).items.single.id,
      9,
    );
    expect(calls, 1);
  });

  test(
    'stock detail uses three distinct GETs and exact clinic and drug',
    () async {
      final paths = <String>[];
      final client = MockClient((request) async {
        paths.add(request.url.path);
        if (request.url.path.endsWith('/stock-summary')) {
          return http.Response(jsonEncode(_summary()), 200);
        }
        if (request.url.path.endsWith('/batches')) {
          expect(request.url.queryParameters, {
            'page': '1',
            'per_page': '20',
            'include_expired': 'true',
          });
          return http.Response(jsonEncode(_page([_batch()])), 200);
        }
        return http.Response(
          jsonEncode({'success': true, 'data': _drug()}),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final stock = await _source(client, guard).loadStock(drugId: 9);
      expect(stock.summary.quantityOnHand, 12);
      expect(stock.batches.items.single.batchNumber, 'LOT-11');
      expect(paths, [
        '/api/v1/pharmacy/drugs/9',
        '/api/v1/pharmacy/drugs/9/stock-summary',
        '/api/v1/pharmacy/drugs/9/batches',
      ]);
    },
  );

  test('cross-clinic stock summary or batch is rejected', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final mismatch in ['summary', 'batch']) {
      final client = MockClient((request) async {
        if (request.url.path.endsWith('/stock-summary')) {
          return http.Response(
            jsonEncode(_summary(clinic: mismatch == 'summary' ? 8 : 3)),
            200,
          );
        }
        if (request.url.path.endsWith('/batches')) {
          return http.Response(
            jsonEncode(_page([_batch(clinic: mismatch == 'batch' ? 8 : 3)])),
            200,
          );
        }
        return http.Response(
          jsonEncode({'success': true, 'data': _drug()}),
          200,
        );
      });
      await expectLater(
        _source(client, guard).loadStock(drugId: 9),
        throwsA(isA<FormatException>()),
      );
      client.close();
    }
  });

  test('logout mid-flight invalidates pending pharmacy response', () async {
    final waiting = Completer<http.Response>();
    final started = Completer<void>();
    final client = MockClient((_) {
      if (!started.isCompleted) {
        started.complete();
      }
      return waiting.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final future = _source(client, guard).listDrugs();
    await started.future;
    guard.invalidate();
    waiting.complete(http.Response(jsonEncode(_page([_drug()])), 200));
    await expectLater(
      future,
      throwsA(isA<PharmacyInventorySessionUnavailable>()),
    );
  });

  test('malformed page and negative batch quantity fail closed', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final wrong = MockClient(
      (_) async => http.Response(
        jsonEncode({
          'success': true,
          'data': {
            'items': [_drug()],
            'total': 1,
            'page': 2,
            'per_page': 20,
          },
        }),
        200,
      ),
    );
    await expectLater(
      _source(wrong, guard).listDrugs(),
      throwsA(isA<FormatException>()),
    );
    wrong.close();
    final client = MockClient((request) async {
      if (request.url.path.endsWith('/stock-summary')) {
        return http.Response(jsonEncode(_summary()), 200);
      }
      if (request.url.path.endsWith('/batches')) {
        final invalid = _batch()..['quantity_on_hand'] = -1;
        return http.Response(jsonEncode(_page([invalid])), 200);
      }
      return http.Response(jsonEncode({'success': true, 'data': _drug()}), 200);
    });
    await expectLater(
      _source(client, guard).loadStock(drugId: 9),
      throwsA(isA<FormatException>()),
    );
    client.close();
  });
}
