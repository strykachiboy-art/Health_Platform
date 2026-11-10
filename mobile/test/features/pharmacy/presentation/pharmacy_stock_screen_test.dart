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
import 'package:clinic_system_pro/features/pharmacy/data/pharmacy_inventory_remote_data_source.dart';
import 'package:clinic_system_pro/features/pharmacy/presentation/pharmacy_stock_screen.dart';

AuthSessionState _ready({String role = 'pharmacist'}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 's03a-widget-token',
    refreshToken: 's03a-widget-refresh',
    userId: 27,
    role: role,
    clinicContextId: 3,
  ),
);

Map<String, dynamic> _drug() => {
  'id': 9,
  'clinic_id': 3,
  'name': 'Drug A',
  'category': 'analgesic',
  'strength': '500 mg',
  'is_controlled': false,
  'is_active': true,
};

Map<String, dynamic> _page(List<Object> items) => {
  'success': true,
  'data': {'items': items, 'total': items.length, 'page': 1, 'per_page': 20},
};

Map<String, dynamic> _batch() => {
  'id': 31,
  'clinic_id': 3,
  'drug_id': 9,
  'batch_number': 'L-1',
  'quantity_on_hand': 12,
  'reorder_level': 2,
  'expiry_date': '2027-08-12',
};

Widget _host(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'pharmacist',
}) => MaterialApp(
  home: PharmacyStockScreen(
    dataSource: PharmacyInventoryRemoteDataSource(
      apiClient: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      ),
      loadSession: () async => _ready(role: role),
      sessionNavigationGuard: guard,
    ),
  ),
);

http.Response _response(String path) {
  if (path.endsWith('/stock-summary')) {
    return http.Response(
      jsonEncode({
        'success': true,
        'data': {
          'clinic_id': 3,
          'drug_id': 9,
          'drug_name': 'Drug A',
          'quantity_on_hand': 12,
          'batch_count': 1,
        },
      }),
      200,
    );
  }
  if (path.endsWith('/batches')) {
    return http.Response(jsonEncode(_page([_batch()])), 200);
  }
  if (path.endsWith('/drugs/9')) {
    return http.Response(jsonEncode({'success': true, 'data': _drug()}), 200);
  }
  return http.Response(jsonEncode(_page([_drug()])), 200);
}

void main() {
  testWidgets('catalog and selected drug show only backend-confirmed stock', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1100, 1800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final paths = <String>[];
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      paths.add(request.url.path);
      return _response(request.url.path);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(client, guard));
    await tester.pumpAndSettle();
    expect(find.text('Catalog: 1 of 1'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('p23h-s03a-drug-9')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23h-s03a-stock-title')), findsOneWidget);
    expect(find.text('On hand (server summary): 12'), findsOneWidget);
    expect(find.byKey(const ValueKey('p23h-s03a-batch-31')), findsOneWidget);
    expect(paths, [
      '/api/v1/pharmacy/drugs',
      '/api/v1/pharmacy/drugs/9',
      '/api/v1/pharmacy/drugs/9/stock-summary',
      '/api/v1/pharmacy/drugs/9/batches',
    ]);
  });

  testWidgets('unprivileged role never dispatches pharmacy HTTP', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return _response('/api/v1/pharmacy/drugs');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(client, guard, role: 'doctor'));
    await tester.pumpAndSettle();
    expect(calls, 0);
    expect(find.byKey(const ValueKey('p23h-s03a-error')), findsOneWidget);
    expect(find.text('Drug A'), findsNothing);
  });

  testWidgets('signout removes pharmacy PHI from the mounted screen', (
    tester,
  ) async {
    final client = MockClient((request) async => _response(request.url.path));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(client, guard));
    await tester.pumpAndSettle();
    expect(find.text('Drug A'), findsOneWidget);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Drug A'), findsNothing);
    expect(find.byKey(const ValueKey('p23h-s03a-error')), findsOneWidget);
  });

  testWidgets('403 is denied and no inventory is displayed', (tester) async {
    final client = MockClient(
      (_) async => http.Response('{"error":"Forbidden"}', 403),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(client, guard));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23h-s03a-error')), findsOneWidget);
    expect(find.text('Drug A'), findsNothing);
  });
}
