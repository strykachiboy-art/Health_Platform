import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_feature_frame.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';
import 'package:clinic_system_pro/features/home/presentation/windows_workstation_shortcuts.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_directory_screen.dart';

AuthSessionState _ready(String role, {int clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'wd02-fake-access',
        refreshToken: 'wd02-fake-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinicId,
      ),
    );

Future<void> _shortcut(WidgetTester tester, LogicalKeyboardKey key) async {
  await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
  await tester.sendKeyEvent(key);
  await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
  await tester.pumpAndSettle();
}

class _WorkspaceProbe extends StatefulWidget {
  const _WorkspaceProbe({required this.onMount});
  final VoidCallback onMount;

  @override
  State<_WorkspaceProbe> createState() => _WorkspaceProbeState();
}

class _WorkspaceProbeState extends State<_WorkspaceProbe> {
  @override
  void initState() {
    super.initState();
    widget.onMount();
  }

  @override
  Widget build(BuildContext context) => const Text('Scoped workspace');
}

// The test variant resets the platform override before Flutter's invariant
// verification, even when a widget test fails. No global debug override.
void wd02WindowsTestWidgets(String description, WidgetTesterCallback callback) {
  testWidgets(
    description,
    callback,
    variant: TargetPlatformVariant.only(TargetPlatform.windows),
  );
}

void main() {
  wd02WindowsTestWidgets(
    'WD02 Windows keyboard commands invoke only supplied actions',
    (tester) async {
      var home = 0;
      var patients = 0;
      var search = 0;
      await tester.pumpWidget(
        MaterialApp(
          home: WindowsWorkstationShortcuts(
            onHome: () => home++,
            onPatients: () => patients++,
            onSearch: () => search++,
            child: const Scaffold(body: Text('Workstation')),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await _shortcut(tester, LogicalKeyboardKey.digit1);
      await _shortcut(tester, LogicalKeyboardKey.digit2);
      await _shortcut(tester, LogicalKeyboardKey.keyF);
      expect([home, patients, search], [1, 1, 1]);
      expect(tester.takeException(), isNull);
    },
  );

  for (final width in <double>[1280, 1920]) {
    wd02WindowsTestWidgets(
      'WD02 desktop rail at ${width.toInt()} without overflow',
      (tester) async {
        tester.view.physicalSize = Size(width, width == 1280 ? 720 : 1080);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        final router = GoRouter(
          initialLocation: '/home',
          routes: [
            GoRoute(
              path: '/home',
              builder: (_, _) => AuthenticatedHomeScreen(
                loadSession: () async => _ready('doctor'),
              ),
            ),
            GoRoute(
              path: '/patients',
              builder: (_, _) =>
                  const Scaffold(body: Text('Patient worklist destination')),
            ),
          ],
        );
        addTearDown(router.dispose);
        await tester.pumpWidget(MaterialApp.router(routerConfig: router));
        await tester.pumpAndSettle();
        expect(find.byType(NavigationRail), findsOneWidget);
        expect(find.byKey(const ValueKey('p22-home-patients')), findsOneWidget);
        expect(tester.takeException(), isNull);
        await _shortcut(tester, LogicalKeyboardKey.digit2);
        expect(find.text('Patient worklist destination'), findsOneWidget);
        expect(tester.takeException(), isNull);
      },
    );
  }

  wd02WindowsTestWidgets(
    'WD02 unscoped patient shortcut never opens staff worklist',
    (tester) async {
      final router = GoRouter(
        initialLocation: '/home',
        routes: [
          GoRoute(
            path: '/home',
            builder: (_, _) => AuthenticatedHomeScreen(
              loadSession: () async => _ready('patient'),
            ),
          ),
          GoRoute(
            path: '/patients',
            builder: (_, _) =>
                const Scaffold(body: Text('Forbidden worklist destination')),
          ),
        ],
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      await tester.pumpAndSettle();
      await _shortcut(tester, LogicalKeyboardKey.digit2);
      expect(find.text('Forbidden worklist destination'), findsNothing);
      expect(find.byKey(const ValueKey('p22-home-shell')), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  wd02WindowsTestWidgets(
    'WD02 feature workspace drops old child on session revocation',
    (tester) async {
      final guard = SessionNavigationGuard(
        protectedRoutePrefixes: const ['/patients', '/home'],
      )..markSessionAvailable();
      addTearDown(guard.dispose);
      var clinic = 3;
      var mounts = 0;
      await tester.pumpWidget(
        MaterialApp(
          home: AuthenticatedFeatureFrame(
            location: '/patients',
            loadSession: () async => _ready('doctor', clinicId: clinic),
            sessionNavigationGuard: guard,
            child: _WorkspaceProbe(onMount: () => mounts++),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(mounts, 1);
      expect(find.text('Scoped workspace'), findsOneWidget);
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.text('Scoped workspace'), findsNothing);
      expect(
        find.byKey(const ValueKey('p22-feature-session-unavailable')),
        findsOneWidget,
      );
      clinic = 4;
      guard.markSessionAvailable();
      await tester.pumpAndSettle();
      expect(mounts, 2);
      expect(find.text('Scoped workspace'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  wd02WindowsTestWidgets(
    'WD02 Ctrl+F focuses search; Enter runs one existing API query',
    (tester) async {
      final queries = <String?>[];
      final client = MockClient((request) async {
        queries.add(request.url.queryParameters['search']);
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {'items': [], 'total': 0, 'page': 1, 'per_page': 25},
          }),
          200,
        );
      });
      addTearDown(client.close);
      final source = PatientRemoteDataSource(
        apiClient: ApiClient(
          config: const BackendConfig(
            baseUrl: 'http://127.0.0.1:5000',
            allowInsecureHttp: true,
          ),
          client: client,
        ),
        loadAccessToken: () async => 'wd02-fake-token',
      );
      await tester.pumpWidget(
        MaterialApp(home: PatientDirectoryScreen(dataSource: source)),
      );
      await tester.pumpAndSettle();
      expect(queries, [null]);
      await _shortcut(tester, LogicalKeyboardKey.keyF);
      final field = tester.widget<TextField>(
        find.byKey(const ValueKey('patient-search-field')),
      );
      expect(field.focusNode!.hasFocus, isTrue);
      await tester.enterText(
        find.byKey(const ValueKey('patient-search-field')),
        'Nobody',
      );
      await tester.pump();
      expect(queries, [null]);
      await tester.testTextInput.receiveAction(TextInputAction.search);
      await tester.pumpAndSettle();
      expect(queries, [null, 'Nobody']);
      expect(find.text('No matching patients'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );
}
