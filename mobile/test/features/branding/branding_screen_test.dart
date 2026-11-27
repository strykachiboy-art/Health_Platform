import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/administration/presentation/wd05_administration_hub.dart';
import 'package:clinic_system_pro/features/branding/data/branding_remote.dart';
import 'package:clinic_system_pro/features/branding/presentation/branding_screen.dart';

import 'branding_remote_test.dart' show org, actor, orgState, clinicState;

class _ScreenSource extends BrandingRemote {
  _ScreenSource(
    ApiClient api,
    SessionNavigationGuard guard, {
    super.staging = true,
  }) : super(
         api: api,
         guard: guard,
         loadSession: () async => AuthSessionState.ready(actor('admin')),
       );
  int reads = 0;
  int writes = 0;
  bool deny = false;
  @override
  Future<BrandingCapabilities> capabilities({
    required BrandingActive active,
  }) async => const BrandingCapabilities(
    organizationId: org,
    name: 'Clinic Organisation',
    membershipRole: 'organization_admin',
    clinicId: 2,
    organization: true,
    clinic: true,
    customize: false,
    reset: true,
  );
  @override
  Future<BrandingDirectoryPage> directory({
    String? after,
    required BrandingActive active,
  }) async => const BrandingDirectoryPage([
    BrandingOrganization(org, 'Clinic Organisation', 0, false),
  ], null);
  @override
  Future<BrandingOrganization> organizationDetail(
    String id, {
    required BrandingActive active,
  }) async => BrandingOrganization(id, 'Clinic Organisation', 0, false);
  @override
  Future<BrandingView> read(
    BrandingScope scope, {
    String? organization,
    required BrandingActive active,
  }) async {
    reads++;
    if (deny) throw const BrandingUnavailable();
    return BrandingView(
      BrandingState.parse(
        scope,
        scope == BrandingScope.entitlement
            ? {'version': 0, 'clinic_override_allowed': false}
            : scope == BrandingScope.clinic
            ? clinicState()
            : orgState(),
        organizationId: organization ?? org,
        clinicId: 2,
      ),
      null,
    );
  }

  @override
  Future<BrandingView> setColors(
    BrandingState prior,
    String? primary,
    String? secondary,
    BrandingActive active,
  ) async {
    writes++;
    return BrandingView(
      BrandingState.parse(
        BrandingScope.organization,
        orgState(version: 3, primary: primary, secondary: secondary),
        organizationId: org,
      ),
      null,
    );
  }
}

void main() {
  late ApiClient api;
  late SessionNavigationGuard guard;
  setUp(() {
    api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
    );
    guard = SessionNavigationGuard()..markSessionAvailable();
  });
  tearDown(() {
    api.close();
    guard.dispose();
  });
  test('staged navigation separates platform and clinic roles', () {
    expect(
      brandingStaged,
      isTrue,
      reason: 'run this new suite with CSP_STAGED_BRANDING=true',
    );
    List<String> paths(String role, int? clinic) => wd05AdministrationLinks(
      role: role,
      clinicContextId: clinic,
    ).map((x) => x.path).toList();
    expect(
      paths('super_admin', null),
      contains('/administration/branding-entitlements'),
    );
    expect(
      paths('super_admin', null),
      isNot(contains('/administration/document-branding')),
    );
    expect(
      paths('super_admin', 2),
      isNot(contains('/administration/branding-entitlements')),
    );
    expect(paths('admin', 2), contains('/administration/document-branding'));
    expect(
      paths('doctor', 2),
      isNot(contains('/administration/document-branding')),
    );
  });
  testWidgets('release-locked workspace never requests branding', (
    tester,
  ) async {
    final source = _ScreenSource(api, guard, staging: false);
    await tester.pumpWidget(
      MaterialApp(home: BrandingScreen(source: source, platform: false)),
    );
    await tester.pumpAndSettle();
    expect(
      find.text('Branding is locked pending release certification.'),
      findsOneWidget,
    );
    expect(source.reads, 0);
    expect(source.writes, 0);
  });
  testWidgets('backend denial never exposes mutation controls', (tester) async {
    final source = _ScreenSource(api, guard)..deny = true;
    await tester.pumpWidget(
      MaterialApp(home: BrandingScreen(source: source, platform: false)),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('branding-save-colors')), findsNothing);
    await tester.tap(find.byKey(const ValueKey('branding-read-organization')));
    await tester.pumpAndSettle();
    expect(source.reads, 1);
    expect(find.byKey(const ValueKey('branding-save-colors')), findsNothing);
    expect(source.writes, 0);
  });
  testWidgets(
    'confirmed organization change; guard invalidation clears editor',
    (tester) async {
      tester.view.physicalSize = const Size(1000, 1500);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final source = _ScreenSource(api, guard);
      await tester.pumpWidget(
        MaterialApp(home: BrandingScreen(source: source, platform: false)),
      );
      await tester.pumpAndSettle();
      await tester.tap(
        find.byKey(const ValueKey('branding-read-organization')),
      );
      await tester.pumpAndSettle();
      final save = find.byKey(const ValueKey('branding-save-colors'));
      await tester.ensureVisible(save);
      await tester.tap(save);
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.text('Confirm branding change'), findsOneWidget);
      expect(source.writes, 0);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(source.writes, 0);
      await tester.tap(save);
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.text('Confirm branding change'), findsOneWidget);
      await tester.tap(find.text('Confirm'));
      await tester.pumpAndSettle();
      expect(source.writes, 1);
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(save, findsNothing);
      expect(
        find.text('Session context changed. Reopen branding.'),
        findsOneWidget,
      );
    },
  );
  testWidgets('platform directory selection requires no UUID input', (
    tester,
  ) async {
    final source = _ScreenSource(api, guard);
    await tester.pumpWidget(
      MaterialApp(home: BrandingScreen(source: source, platform: true)),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('branding-organization-id')),
      findsNothing,
    );
    await tester.tap(find.byKey(const ValueKey('branding-org-$org')));
    await tester.pumpAndSettle();
    expect(source.reads, 1);
    expect(find.text('Enable customization'), findsOneWidget);
  });
  testWidgets('revoked clinic can reset but not save custom colors', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1000, 1500);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final source = _ScreenSource(api, guard);
    await tester.pumpWidget(
      MaterialApp(home: BrandingScreen(source: source, platform: false)),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('branding-read-clinic')));
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<FilledButton>(
            find.byKey(const ValueKey('branding-save-colors')),
          )
          .onPressed,
      isNull,
    );
    expect(source.writes, 0);
  });
}
