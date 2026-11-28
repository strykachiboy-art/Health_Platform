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
  bool customize = false;
  @override
  Future<BrandingCapabilities> capabilities({
    required BrandingActive active,
  }) async => BrandingCapabilities(
    organizationId: org,
    name: 'Clinic Organisation',
    membershipRole: 'organization_admin',
    clinicId: 2,
    organization: true,
    clinic: true,
    customize: customize,
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
            ? clinicState(allowed: customize)
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
      MaterialApp(
        home: BrandingScreen(source: source, studio: false, platform: false),
      ),
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
      MaterialApp(
        home: BrandingScreen(source: source, studio: false, platform: false),
      ),
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
        MaterialApp(
          home: BrandingScreen(source: source, studio: false, platform: false),
        ),
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
      MaterialApp(
        home: BrandingScreen(source: source, studio: false, platform: true),
      ),
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
      MaterialApp(
        home: BrandingScreen(source: source, studio: false, platform: false),
      ),
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
  testWidgets('visual picker drafts preset and confirms exactly one write', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1000, 1500);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final source = _ScreenSource(api, guard);
    await tester.pumpWidget(
      MaterialApp(
        home: BrandingScreen(source: source, studio: false, platform: false),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('branding-read-organization')));
    await tester.pumpAndSettle();
    final picker = find.byKey(const ValueKey('branding-picker-Primary colour'));
    await tester.ensureVisible(picker);
    await tester.tap(picker);
    await tester.pumpAndSettle();
    final swatch = find.byKey(const ValueKey('branding-swatch-#1D4ED8'));
    await tester.ensureVisible(swatch);
    await tester.tap(swatch);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(source.writes, 0);
    expect(
      tester
          .widget<TextField>(
            find.ancestor(of: picker, matching: find.byType(TextField)),
          )
          .controller!
          .text,
      '#176B65',
    );
    await tester.tap(picker);
    await tester.pumpAndSettle();
    await tester.ensureVisible(swatch);
    await tester.tap(swatch);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('branding-use-colour')));
    await tester.pumpAndSettle();
    expect(source.writes, 0);
    expect(
      tester
          .widget<TextField>(
            find.ancestor(of: picker, matching: find.byType(TextField)),
          )
          .controller!
          .text,
      '#1D4ED8',
    );
    expect(
      tester.widget<Text>(find.text('Clinic Organisation').last).style?.color,
      const Color(0xff1d4ed8),
    );
    final save = find.byKey(const ValueKey('branding-save-colors'));
    await tester.ensureVisible(save);
    await tester.tap(save);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('Confirm branding change'), findsOneWidget);
    expect(source.writes, 0);
    await tester.tap(find.text('Confirm'));
    await tester.pumpAndSettle();
    expect(source.writes, 1);
    expect(
      find.text('Server confirmed and read back the branding change.'),
      findsOneWidget,
    );
    expect(
      tester
          .widget<TextField>(
            find.ancestor(of: picker, matching: find.byType(TextField)),
          )
          .controller!
          .text,
      '#1D4ED8',
    );
  });
  testWidgets(
    'custom picker rejects white and cannot apply after invalidation',
    (tester) async {
      tester.view.physicalSize = const Size(1000, 1500);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final source = _ScreenSource(api, guard);
      await tester.pumpWidget(
        MaterialApp(
          home: BrandingScreen(source: source, studio: false, platform: false),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(
        find.byKey(const ValueKey('branding-read-organization')),
      );
      await tester.pumpAndSettle();
      final picker = find.byKey(
        const ValueKey('branding-picker-Secondary colour'),
      );
      await tester.ensureVisible(picker);
      await tester.tap(picker);
      await tester.pumpAndSettle();
      tester
          .widget<Slider>(find.byKey(const ValueKey('branding-saturation')))
          .onChanged!(0);
      await tester.pumpAndSettle();
      tester
          .widget<Slider>(find.byKey(const ValueKey('branding-brightness')))
          .onChanged!(1);
      await tester.pumpAndSettle();
      final use = find.byKey(const ValueKey('branding-use-colour'));
      expect(tester.widget<FilledButton>(use).onPressed, isNull);
      expect(
        find.text('Too light for document headings. Reduce brightness.'),
        findsOneWidget,
      );
      tester
          .widget<Slider>(find.byKey(const ValueKey('branding-brightness')))
          .onChanged!(0.25);
      await tester.pumpAndSettle();
      expect(tester.widget<FilledButton>(use).onPressed, isNotNull);
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(use, findsNothing);
      expect(find.text('Colour selection unavailable'), findsOneWidget);
      await tester.tap(find.text('Close'));
      await tester.pumpAndSettle();
      expect(source.writes, 0);
      expect(picker, findsNothing);
    },
  );
  testWidgets('clinic picker respects entitlement independently of reset', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1000, 1500);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final source = _ScreenSource(api, guard);
    await tester.pumpWidget(
      MaterialApp(
        home: BrandingScreen(source: source, studio: false, platform: false),
      ),
    );
    await tester.pumpAndSettle();
    final clinic = find.byKey(const ValueKey('branding-read-clinic'));
    await tester.tap(clinic);
    await tester.pumpAndSettle();
    final primary = find.byKey(
      const ValueKey('branding-picker-Primary colour'),
    );
    final secondary = find.byKey(
      const ValueKey('branding-picker-Secondary colour'),
    );
    expect(tester.widget<TextButton>(primary).onPressed, isNull);
    expect(tester.widget<TextButton>(secondary).onPressed, isNull);
    expect(
      tester
          .widget<OutlinedButton>(
            find.widgetWithText(OutlinedButton, 'Reset colours'),
          )
          .onPressed,
      isNotNull,
    );
    source.customize = true;
    await tester.tap(clinic);
    await tester.pumpAndSettle();
    expect(tester.widget<TextButton>(primary).onPressed, isNotNull);
    expect(tester.widget<TextButton>(secondary).onPressed, isNotNull);
    await tester.ensureVisible(secondary);
    await tester.tap(secondary);
    await tester.pumpAndSettle();
    final swatch = find.byKey(const ValueKey('branding-swatch-#8A4B26'));
    await tester.ensureVisible(swatch);
    await tester.tap(swatch);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('branding-use-colour')));
    await tester.pumpAndSettle();
    expect(find.text('#8A4B26'), findsOneWidget);
    expect(source.writes, 0);
  });
  testWidgets('colour contract matches paper sample and keeps safety colours', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1000, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final source = _ScreenSource(api, guard);
    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.dark(),
        home: BrandingScreen(source: source, studio: false, platform: false),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('branding-read-organization')));
    await tester.pumpAndSettle();
    Color? textColor(String suffix) => tester
        .widget<Text>(find.byKey(ValueKey('branding-sample-$suffix')))
        .style
        ?.color;
    Color? fill(String suffix) => tester
        .widget<Container>(find.byKey(ValueKey('branding-sample-$suffix')))
        .color;
    void check(Color primary, Color secondary) {
      for (final suffix in ['organization', 'title']) {
        expect(textColor(suffix), primary);
      }
      for (final suffix in ['stripe', 'footer']) {
        expect(fill(suffix), primary);
      }
      for (final suffix in ['clinic', 'section']) {
        expect(textColor(suffix), secondary);
      }
      for (final suffix in ['divider', 'table']) {
        expect(fill(suffix), secondary);
      }
      expect(textColor('warning'), const Color(0xff842020));
      expect(textColor('body'), const Color(0xff142a3a));
      expect(
        tester
            .widget<Container>(
              find.byKey(const ValueKey('branding-paper-sample')),
            )
            .color,
        Colors.white,
      );
      expect(
        tester
            .widget<Text>(find.text('Sample document table heading'))
            .style
            ?.color,
        Colors.white,
      );
      expect(source.writes, 0);
    }

    Future<void> edit(String label, String value) async {
      final field = find.ancestor(
        of: find.byKey(ValueKey('branding-picker-$label')),
        matching: find.byType(TextField),
      );
      await tester.ensureVisible(field);
      await tester.enterText(field, value);
      await tester.pumpAndSettle();
    }

    check(const Color(0xff176b65), const Color(0xff243d54));
    await edit('Primary colour', '#136228');
    await edit('Secondary colour', '#000000');
    check(const Color(0xff136228), const Color(0xff000000));
    await edit('Primary colour', '#1D4ED8');
    await edit('Secondary colour', '#8A4B26');
    check(const Color(0xff1d4ed8), const Color(0xff8a4b26));
    await edit('Secondary colour', '#FFFFFF');
    check(const Color(0xff1d4ed8), const Color(0xff243d54));
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('branding-paper-sample')), findsNothing);
    expect(source.writes, 0);
  });
}
