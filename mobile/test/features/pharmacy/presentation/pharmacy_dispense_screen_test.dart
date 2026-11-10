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
import 'package:clinic_system_pro/features/pharmacy/data/pharmacy_dispense_remote_data_source.dart';
import 'package:clinic_system_pro/features/pharmacy/presentation/pharmacy_dispense_screen.dart';

import '../data/pharmacy_dispense_remote_data_source_test.dart' as fixtures;

PharmacyDispenseRemoteDataSource source(
  MockClient client,
  SessionNavigationGuard guard,
) => PharmacyDispenseRemoteDataSource(
  inventory: PharmacyInventoryRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: () async => const AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 's03b-widget-token',
        refreshToken: 's03b-widget-refresh',
        userId: 42,
        clinicContextId: 3,
        role: 'pharmacist',
      ),
    ),
    sessionNavigationGuard: guard,
  ),
);

void main() {
  testWidgets('empty form cannot dispatch a clinical dispense POST', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = source(client, guard);
    await tester.pumpWidget(
      MaterialApp(
        home: PharmacyDispenseScreen(
          source: s,
          patientId: 7,
          prescriptionId: 11,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('s03b-review')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('s03b-message')), findsOneWidget);
    expect(find.byKey(const ValueKey('s03b-confirm-dispense')), findsNothing);
    expect(requests, 0);
    expect(s.writeAttempted, isFalse);
  });

  testWidgets('review is read-only; explicit confirmation does one POST', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1100, 2100);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final paths = <String>[];
    final client = MockClient((request) async {
      paths.add('${request.method} ${request.url.path}');
      expect(request.headers['authorization'], 'Bearer s03b-widget-token');
      if (request.method == 'POST') {
        expect(request.headers['idempotency-key'], isNotEmpty);
        final decoded = jsonDecode(request.body) as Map<String, dynamic>;
        expect(decoded['items'], [
          {'prescription_item_id': 111, 'batch_id': 501, 'quantity': 2},
        ]);
      }
      return fixtures.respond(request);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = source(client, guard);
    await tester.pumpWidget(
      MaterialApp(
        home: PharmacyDispenseScreen(
          source: s,
          patientId: 7,
          prescriptionId: 11,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('s03b-item-id')), '111');
    await tester.enterText(find.byKey(const ValueKey('s03b-batch-id')), '501');
    await tester.enterText(find.byKey(const ValueKey('s03b-quantity')), '2');
    await tester.tap(find.byKey(const ValueKey('s03b-review')));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('s03b-reviewed-evidence')),
      findsOneWidget,
    );
    expect(paths.where((x) => x.startsWith('POST')), isEmpty);
    final dispense = find.byKey(const ValueKey('s03b-confirm-dispense'));
    await tester.ensureVisible(dispense);
    await tester.tap(dispense);
    await tester.pumpAndSettle();
    expect(find.text('Confirm one pharmacy dispense?'), findsOneWidget);
    await tester.tap(find.text('Confirm dispense once'));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('s03b-confirmed-receipt')),
      findsOneWidget,
    );
    expect(paths.where((x) => x.startsWith('POST')).length, 1);
    expect(s.writeAttempted, isTrue);
    expect(find.byKey(const ValueKey('s03b-confirm-dispense')), findsNothing);
  });

  testWidgets('session invalidation clears displayed pharmacy evidence', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1100, 2100);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final client = MockClient((request) async => fixtures.respond(request));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: PharmacyDispenseScreen(
          source: source(client, guard),
          patientId: 7,
          prescriptionId: 11,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('s03b-item-id')), '111');
    await tester.enterText(find.byKey(const ValueKey('s03b-batch-id')), '501');
    await tester.enterText(find.byKey(const ValueKey('s03b-quantity')), '2');
    await tester.tap(find.byKey(const ValueKey('s03b-review')));
    await tester.pumpAndSettle();
    expect(find.text('Medication: Verified drug'), findsOneWidget);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Medication: Verified drug'), findsNothing);
    expect(find.text('Pharmacy session unavailable.'), findsOneWidget);
  });
}
