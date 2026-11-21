import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/coordination/presentation/wd06_coordination_hub.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';
import 'package:clinic_system_pro/features/pending_work/data/offline_contract_policy.dart';

AuthSessionState _ready(
  String role, {
  int? clinic = 7,
  String token = 'test-access',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'test-refresh',
    userId: 54,
    role: role,
    clinicContextId: clinic,
    tenantId: '11111111-1111-1111-1111-111111111111',
  ),
);

List<String> _ids(String role, {int? clinic = 7}) => wd06CoordinationLinks(
  role: role,
  clinicContextId: clinic,
).map((link) => link.id).toList();

class _FakeNetwork implements DeviceConnectivitySource {
  _FakeNetwork(this.result);

  DeviceNetworkStatus result;
  final _events = StreamController<DeviceNetworkStatus>.broadcast();

  @override
  Future<DeviceNetworkStatus> checkStatus() async => result;

  @override
  Stream<DeviceNetworkStatus> get statusChanges => _events.stream;

  void emit(DeviceNetworkStatus status) => _events.add(status);
  Future<void> close() => _events.close();
}

void main() {
  test('WD06 uses existing role-scoped routes only', () {
    expect(
      _ids('doctor'),
      containsAll(<String>[
        'notifications',
        'feedback',
        'devices',
        'session_security',
        'chat',
        'pending_work',
        'clinical_ai',
      ]),
    );
    expect(
      _ids('patient'),
      containsAll(<String>[
        'notifications',
        'feedback',
        'devices',
        'session_security',
      ]),
    );
    expect(_ids('patient'), isNot(contains('chat')));
    expect(_ids('patient'), isNot(contains('clinical_ai')));
    expect(_ids('pharmacist'), contains('clinical_ai'));
    expect(_ids('pharmacist'), isNot(contains('chat')));
    expect(_ids('receptionist'), contains('pending_work'));
    expect(_ids('super_admin'), isNot(contains('devices')));
    expect(_ids('super_admin'), isNot(contains('clinical_ai')));
    expect(_ids('super_admin'), isEmpty);
    for (final role in <String>['admin', 'doctor', 'patient', 'super_admin']) {
      expect(_ids(role, clinic: null), isEmpty);
      expect(_ids(role, clinic: 0), isEmpty);
      expect(_ids(role, clinic: -2), isEmpty);
    }
    expect(_ids('unknown'), isEmpty);
    expect(
      wd06CoordinationLinks(role: 'doctor', clinicContextId: 7).every(
        (item) =>
            item.path.startsWith('/') &&
            !item.path.contains(':') &&
            !item.path.contains('/print') &&
            !item.path.contains('/devices/register'),
      ),
      isTrue,
    );
  });

  test(
    'WD06 home suggestion uses existing role dashboard, no duplicate metrics',
    () {
      List<String> ids(String role, {int? clinic = 7}) => homeDestinationsFor(
        role: role,
        clinicContextId: clinic,
      ).map((value) => value.id).toList();
      expect(ids('doctor'), contains('coordination'));
      expect(ids('admin'), containsAll(['coordination', 'administration']));
      expect(
        ids('admin').indexOf('coordination'),
        greaterThan(ids('admin').indexOf('administration')),
      );
      expect(ids('patient'), contains('coordination'));
      expect(ids('super_admin'), isNot(contains('coordination')));
      expect(ids('admin', clinic: null), isNot(contains('coordination')));
      expect(ids('unrecognized'), isNot(contains('coordination')));
    },
  );

  test(
    'WD06 network link and HTTP response status never pretend write success',
    () {
      expect(
        wd06DeviceNetworkLabel(DeviceNetworkStatus.connected),
        contains('Flask reachability is not verified'),
      );
      expect(
        wd06DeviceNetworkLabel(DeviceNetworkStatus.disconnected),
        contains('disconnected'),
      );
      expect(
        wd06DeviceNetworkLabel(DeviceNetworkStatus.unknown),
        contains('unknown'),
      );
      expect(
        wd06HttpResponseLabel(ApiCommunicationStatus.notChecked),
        contains('No HTTP response observed'),
      );
      expect(
        wd06HttpResponseLabel(ApiCommunicationStatus.responseNotReceived),
        contains('may be uncertain'),
      );
      expect(
        wd06HttpResponseLabel(ApiCommunicationStatus.responseReceived),
        contains('server readback'),
      );
    },
  );

  test(
    'WD06 preserves the existing deny-by-default offline operation policy',
    () {
      expect(
        OfflineContractRegistry.classify('appointment.create'),
        OfflineContractClass.provedSafeQueued,
      );
      expect(
        OfflineContractRegistry.classify('chat.message'),
        OfflineContractClass.onlineOnly,
      );
      expect(
        OfflineContractRegistry.classify('feedback.create'),
        OfflineContractClass.onlineOnly,
      );
      for (final operation in <String>[
        'medication.administer',
        'financial.payment',
        'emergency-access.decision',
        'medication.reconcile',
        'unknown.operation',
      ]) {
        expect(
          OfflineContractRegistry.classify(operation),
          OfflineContractClass.neverAutoReplay,
          reason: operation,
        );
      }
    },
  );

  testWidgets('WD06 hub rechecks live session and clears after revocation', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final network = _FakeNetwork(DeviceNetworkStatus.connected);
    addTearDown(network.close);
    final opened = <String>[];
    var state = _ready('doctor');
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Wd06CoordinationHub(
            loadSession: () async => state,
            sessionNavigationGuard: guard,
            connectivity: network,
            onOpen: opened.add,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('wd06-coordination-workspace')),
      findsOneWidget,
    );
    expect(
      find.textContaining('Flask reachability is not verified'),
      findsOneWidget,
    );
    network.emit(DeviceNetworkStatus.disconnected);
    await tester.pumpAndSettle();
    expect(find.textContaining('Device network disconnected'), findsOneWidget);
    network.emit(DeviceNetworkStatus.connected);
    await tester.pump();
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('wd06-module-chat')),
      180,
      scrollable: find.byType(Scrollable).last,
    );
    expect(find.byKey(const ValueKey('wd06-module-chat')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('wd06-module-chat')));
    await tester.pumpAndSettle();
    expect(opened, <String>['/chat']);
    state = _ready('patient');
    await tester.tap(find.byKey(const ValueKey('wd06-module-chat')));
    await tester.pumpAndSettle();
    expect(opened, hasLength(1));
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('wd06-coordination-unavailable')),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('wd06-module-chat')), findsNothing);
  });

  testWidgets(
    'WD06 rejects delayed session restore following a clinic/session change',
    (tester) async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final pending = Completer<AuthSessionState>();
      final opened = <String>[];
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Wd06CoordinationHub(
              loadSession: () => pending.future,
              sessionNavigationGuard: guard,
              onOpen: opened.add,
            ),
          ),
        ),
      );
      expect(
        find.byKey(const ValueKey('wd06-coordination-loading')),
        findsOneWidget,
      );
      guard.invalidate();
      pending.complete(_ready('admin'));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('wd06-coordination-unavailable')),
        findsOneWidget,
      );
      expect(opened, isEmpty);
    },
  );

  testWidgets(
    'WD06 anonymous deep link fails closed even with default router guard',
    (tester) async {
      final guard = SessionNavigationGuard();
      addTearDown(guard.dispose);
      final client = MockClient(
        (request) async =>
            throw StateError('No HTTP expected for ${request.url}'),
      );
      addTearDown(client.close);
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(
          apiClient: ApiClient(
            config: const BackendConfig(
              baseUrl: 'http://127.0.0.1:5000',
              allowInsecureHttp: true,
            ),
            client: client,
          ),
        ),
        loginScreen: const Scaffold(
          body: Center(child: Text('Login sentinel')),
        ),
        sessionNavigationGuard: guard,
        loadHomeSession: () async => _ready('doctor'),
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/coordination');
      await tester.pumpAndSettle();
      expect(find.text('Login sentinel'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('wd06-coordination-workspace')),
        findsNothing,
      );
    },
  );
}
