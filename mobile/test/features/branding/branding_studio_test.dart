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
import 'package:clinic_system_pro/features/branding/data/branding_remote.dart';
import 'package:clinic_system_pro/features/branding/data/branding_studio.dart';
import 'package:clinic_system_pro/features/branding/presentation/branding_screen.dart';
import 'package:clinic_system_pro/features/branding/presentation/branding_studio_editor.dart';

import 'branding_remote_test.dart'
    show org, foreignOrg, actor, orgState, clinicState, response;

const chosen = <String, String>{
  'primary_color': '#136228',
  'secondary_color': '#8A4B26',
  'text_color': '#263D36',
  'warning_color': '#92400E',
  'background_color': '#FFF8E7',
  'output_mode': 'full',
  'logo_size': 'large',
};
Map<String, dynamic> studioResponse({
  Map<String, String> local = const {},
  Map<String, String> inherited = studioDefaults,
  int version = 2,
  int? organizationVersion,
  int? clinicId,
  bool allowed = true,
  String organization = org,
}) => {
  'organization_id': organization,
  'clinic_id': clinicId,
  'version': version,
  'organization_version': organizationVersion ?? version,
  'clinic_override_allowed': allowed,
  'local': local,
  'inherited': inherited,
  'effective': {...inherited, if (clinicId == null || allowed) ...local},
  'sources': {
    for (final k in studioDefaults.keys)
      k: (clinicId != null && allowed && local.containsKey(k))
          ? 'clinic'
          : (clinicId == null
                ? local.containsKey(k)
                : inherited[k] != studioDefaults[k])
          ? 'organization'
          : 'default',
  },
};
BrandingStudioState studio({
  Map<String, String> local = const {},
  Map<String, String> inherited = studioDefaults,
  int? clinicId,
  bool allowed = true,
}) => BrandingStudioState.parse(
  studioResponse(
    local: local,
    inherited: inherited,
    clinicId: clinicId,
    allowed: allowed,
  ),
  organizationId: org,
  clinicId: clinicId,
);
BrandingView view({
  Map<String, String> local = const {},
  bool clinic = false,
  bool allowed = true,
}) {
  final s = studio(local: local, clinicId: clinic ? 2 : null, allowed: allowed);
  final state = BrandingState.parse(
    clinic ? BrandingScope.clinic : BrandingScope.organization,
    clinic
        ? {
            ...clinicState(allowed: allowed),
            'clinic_version': s.version,
            'organization_version': s.organizationVersion,
            'primary_color': s.effective['primary_color'],
            'secondary_color': s.effective['secondary_color'],
          }
        : orgState(
            version: s.version,
            primary: s.effective['primary_color'],
            secondary: s.effective['secondary_color'],
          ),
    organizationId: org,
    clinicId: clinic ? 2 : null,
  );
  return BrandingView(state, null, studio: s);
}

Finder key(String name) => find.byKey(ValueKey(name));
Color? previewText(WidgetTester tester, String id) =>
    tester.widget<Text>(key('studio-preview-$id')).style?.color;
Color? previewFill(WidgetTester tester, String id) =>
    tester.widget<Container>(key('studio-preview-$id')).color;
Future<void> setupScreen(
  WidgetTester t,
  BrandingView v,
  ValueChanged<Map<String, String>> save, {
  bool customize = true,
}) async {
  t.view.physicalSize = const Size(1200, 1500);
  t.view.devicePixelRatio = 1;
  addTearDown(t.view.resetPhysicalSize);
  addTearDown(t.view.resetDevicePixelRatio);
  await t.pumpWidget(
    MaterialApp(
      theme: ThemeData.dark(),
      home: Scaffold(
        body: SingleChildScrollView(
          child: BrandingStudioEditor(
            view: v,
            image: null,
            organizationName: 'Synthetic Alpha',
            enabled: true,
            canCustomize: customize,
            onSave: save,
          ),
        ),
      ),
    ),
  );
  await t.pumpAndSettle();
}

Future<void> selectField(WidgetTester t, String field) async {
  final f = key('studio-select-$field');
  await t.ensureVisible(f);
  await t.tap(f);
  await t.pumpAndSettle();
}

Future<void> editHex(WidgetTester t, String field, String hex) async {
  await selectField(t, field);
  final f = key('studio-hex');
  await t.ensureVisible(f);
  await t.enterText(f, hex);
  await t.pumpAndSettle();
}

Future<void> choose(WidgetTester t, String name, String option) async {
  final f = key(name);
  await t.ensureVisible(f);
  await t.tap(f);
  await t.pumpAndSettle();
  await t.tap(find.text(option).last);
  await t.pumpAndSettle();
}

void main() {
  test(
    'studio validation accepts arbitrary safe HEX and matches Flask limits',
    () {
      expect(studioValidation(chosen), isNull);
      expect(
        studioValidation({...studioDefaults, 'primary_color': '#767676'}),
        isNull,
      );
      for (final entry in <String, String>{
        'primary_color': '#39E380',
        'secondary_color': '#FFFFFF',
        'text_color': '#666666',
        'warning_color': '#136228',
        'background_color': '#000000',
        'output_mode': 'invalid',
      }.entries) {
        expect(
          studioValidation({...chosen, entry.key: entry.value}),
          isNotNull,
          reason: entry.key,
        );
      }
      expect(
        studioValidation({...chosen, 'warning_color': '#FF0000'}),
        isNotNull,
      );
      expect(
        studioValidation({
          ...studioDefaults,
          'primary_color': '#000000',
          'background_color': '#EEEEEE',
          'secondary_color': '#767676',
        }),
        isNotNull,
      );
      expect(studioPaint('#842020', 'monochrome'), '#000000');
      expect(studioPaint('#FFFFFF', 'monochrome'), '#FFFFFF');
    },
  );
  test('studio independent inheritance and revocation are strict', () {
    final branch = studio(
      local: {'primary_color': '#1D4ED8', 'text_color': '#000000'},
      inherited: chosen,
      clinicId: 2,
    );
    expect(branch.effective['warning_color'], chosen['warning_color']);
    expect(
      branch.draft({'text_color': '#000000'})['primary_color'],
      chosen['primary_color'],
    );
    final revoked = studio(
      local: branch.local,
      inherited: chosen,
      clinicId: 2,
      allowed: false,
    );
    expect(revoked.effective, chosen);
    expect(revoked.accepts({}), isTrue);
    expect(revoked.accepts({'primary_color': '#000000'}), isFalse);
  });
  test('studio rejects foreign identity unsafe and inconsistent response', () {
    for (final data in [
      studioResponse(organization: foreignOrg),
      {
        ...studioResponse(),
        'effective': {...chosen, 'text_color': '#FFFFFF'},
      },
      {...studioResponse(), 'effective': chosen},
      {...studioResponse(), 'version': true},
      {...studioResponse(), 'unexpected': true},
      {
        ...studioResponse(),
        'sources': {for (final k in studioDefaults.keys) k: 'clinic'},
      },
    ]) {
      expect(
        () => BrandingStudioState.parse(
          data,
          organizationId: org,
          clinicId: null,
        ),
        throwsFormatException,
      );
    }
  });
  group('transport', () {
    late SessionNavigationGuard guard;
    late ApiClient api;
    late BrandingRemote remote;
    late AuthTokenResponse session;
    late List<http.Request> requests;
    late Future<http.Response> Function(http.Request) reply;
    setUp(() {
      guard = SessionNavigationGuard()..markSessionAvailable();
      session = actor('admin');
      requests = [];
      reply = (r) async => response(
        r.url.path.endsWith('/studio-v2')
            ? studioResponse()
            : orgState(
                primary: studioDefaults['primary_color'],
                secondary: studioDefaults['secondary_color'],
              ),
      );
      api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: MockClient((r) async {
          requests.add(r);
          expect(r.headers['Authorization'], 'Bearer token');
          expect(r.followRedirects, isFalse);
          expect(r.url.hasQuery, isFalse);
          return reply(r);
        }),
      );
      remote = BrandingRemote(
        api: api,
        guard: guard,
        loadSession: () async => AuthSessionState.ready(session),
        staging: true,
      );
    });
    tearDown(() {
      api.close();
      guard.dispose();
    });
    Future<BrandingView> load() async => remote.attachStudio(
      await remote.read(BrandingScope.organization, active: () => true),
      () => true,
    );
    test('studio PUT versions and authoritative readback', () async {
      final prior = await load();
      var version = 2;
      Map<String, String> saved = {};
      reply = (r) async {
        if (r.method == 'PUT') {
          expect(r.url.path, '/api/v1/clinics/branding/organization/studio-v2');
          expect(jsonDecode(r.body), {
            'expected_version': 2,
            'expected_organization_version': 2,
            'overrides': chosen,
          });
          version++;
          saved = chosen;
        }
        if (r.url.path.endsWith('/studio-v2')) {
          return response(studioResponse(local: saved, version: version));
        }
        return response(
          orgState(
            version: version,
            primary: saved['primary_color'],
            secondary: saved['secondary_color'],
          ),
        );
      };
      final next = await remote.setStudio(prior, chosen, () => true);
      expect(next.studio!.effective, chosen);
      expect(next.state.version, 3);
      expect(requests.map((r) => r.method), [
        'GET',
        'GET',
        'PUT',
        'GET',
        'GET',
      ]);
    });
    test(
      'studio read denies mismatch between legacy identity and studio versions',
      () async {
        reply = (r) async => response(
          r.url.path.endsWith('/studio-v2')
              ? studioResponse(version: 3)
              : orgState(
                  primary: studioDefaults['primary_color'],
                  secondary: studioDefaults['secondary_color'],
                ),
        );
        await expectLater(load(), throwsA(isA<BrandingUnavailable>()));
        expect(requests.length, 2);
      },
    );
    test(
      'studio rejects invalid draft and changed actor before write',
      () async {
        final prior = await load();
        await expectLater(
          remote.setStudio(prior, {'text_color': '#FFFFFF'}, () => true),
          throwsA(isA<BrandingUnavailable>()),
        );
        session = actor('admin', user: 9);
        await expectLater(
          remote.setStudio(prior, chosen, () => true),
          throwsA(isA<BrandingUnavailable>()),
        );
        expect(requests.length, 2);
      },
    );
    test('studio conflict is never automatically retried', () async {
      final prior = await load();
      reply = (_) async => http.Response('conflict', 409);
      await expectLater(
        remote.setStudio(prior, chosen, () => true),
        throwsA(anything),
      );
      expect(requests.map((r) => r.method), ['GET', 'GET', 'PUT']);
    });
    test('studio tampered readback fails closed after one write', () async {
      final prior = await load();
      reply = (r) async {
        if (r.method == 'PUT') {
          return response(studioResponse(local: chosen, version: 3));
        }
        if (r.url.path.endsWith('/studio-v2')) {
          return response(
            studioResponse(
              local: {...chosen, 'text_color': '#000000'},
              version: 3,
            ),
          );
        }
        return response(
          orgState(
            version: 3,
            primary: chosen['primary_color'],
            secondary: chosen['secondary_color'],
          ),
        );
      };
      await expectLater(
        remote.setStudio(prior, chosen, () => true),
        throwsA(isA<BrandingUnavailable>()),
      );
      expect(requests.where((r) => r.method == 'PUT').length, 1);
    });
    test('studio transport rejects unsupported DELETE and foreign URL', () async {
      for (final uri in [
        'http://127.0.0.1:5000/api/v1/clinics/branding/organization/studio-v2',
        'http://127.0.0.2:5000/api/v1/clinics/branding/organization/studio-v2',
      ]) {
        final req = http.Request('DELETE', Uri.parse(uri))
          ..followRedirects = false
          ..headers['Authorization'] = 'Bearer token';
        await expectLater(
          api.sendPrivateBranding(req, image: false, active: () => true),
          throwsStateError,
        );
      }
      expect(requests, isEmpty);
    });
  });
  testWidgets(
    'studio pointer tap drag and sliders change real draft and preserve other colours',
    (t) async {
      var saves = 0;
      await setupScreen(t, view(local: chosen), (_) => saves++);
      final board = key('studio-colour-board');
      await t.ensureVisible(board);
      await t.pumpAndSettle();
      final rect = t.getRect(board);
      await t.tapAt(rect.topLeft + Offset(rect.width * .65, rect.height * .65));
      await t.pumpAndSettle();
      final first = t.widget<TextField>(key('studio-hex')).controller!.text;
      expect(first, isNot(chosen['primary_color']));
      await t.dragFrom(
        rect.topLeft + Offset(rect.width * .65, rect.height * .65),
        Offset(-rect.width * .3, rect.height * .2),
      );
      await t.pumpAndSettle();
      final next = t.widget<TextField>(key('studio-hex')).controller!.text;
      expect(next, isNot(first));
      expect(
        previewText(t, 'title'),
        Color(0xff000000 | int.parse(next.substring(1), radix: 16)),
      );
      await selectField(t, 'secondary_color');
      expect(
        t.widget<TextField>(key('studio-hex')).controller!.text,
        chosen['secondary_color'],
      );
      final hue = t.widget<Slider>(key('studio-hue'));
      hue.onChanged!(200);
      await t.pumpAndSettle();
      expect(
        t.widget<TextField>(key('studio-hex')).controller!.text,
        isNot(chosen['secondary_color']),
      );
      await selectField(t, 'primary_color');
      expect(t.widget<TextField>(key('studio-hex')).controller!.text, next);
      expect(saves, 0);
    },
  );
  testWidgets(
    'studio five independent HEX fields paint full document and mode preview',
    (t) async {
      Map<String, String>? sent;
      await setupScreen(t, view(), (v) => sent = v);
      for (final k in chosen.keys.where(
        (k) => k != 'output_mode' && k != 'logo_size',
      )) {
        await editHex(t, k, chosen[k]!);
      }
      await choose(t, 'studio-logo-size', 'Large');
      expect(
        t.widget<DropdownButton<String>>(key('studio-logo-size')).value,
        'large',
      );
      expect(previewText(t, 'title'), const Color(0xff136228));
      expect(previewText(t, 'organization'), const Color(0xff136228));
      expect(previewFill(t, 'footer'), const Color(0xff136228));
      expect(previewText(t, 'clinic'), const Color(0xff8a4b26));
      expect(previewFill(t, 'table'), const Color(0xff8a4b26));
      expect(previewText(t, 'body'), const Color(0xff263d36));
      expect(previewText(t, 'table-body'), const Color(0xff263d36));
      expect(previewText(t, 'warning'), const Color(0xff92400e));
      expect(previewText(t, 'disclaimer'), const Color(0xff92400e));
      expect(previewFill(t, 'paper'), const Color(0xfffff8e7));
      expect(previewText(t, 'balance'), const Color(0xff842020));
      await choose(t, 'studio-document', 'Laboratory Results');
      expect(previewText(t, 'critical'), const Color(0xff842020));
      await choose(t, 'studio-mode', 'Ink saving');
      expect(previewFill(t, 'paper'), Colors.white);
      expect(previewText(t, 'title'), const Color(0xff136228));
      await choose(t, 'studio-mode', 'Monochrome');
      expect(previewText(t, 'title'), Colors.black);
      expect(previewText(t, 'critical'), Colors.black);
      await choose(t, 'studio-mode', 'Full branding');
      expect(previewFill(t, 'paper'), const Color(0xfffff8e7));
      for (final title in [
        'Invoice',
        'Appointment',
        'Lab Order',
        'Laboratory Results',
        'Radiology Draft',
        'Radiology Report Copy',
      ]) {
        await choose(t, 'studio-document', title);
        expect(t.widget<Text>(key('studio-preview-title')).data, title);
      }
      final save = key('studio-save');
      await t.ensureVisible(save);
      await t.tap(save);
      await t.pumpAndSettle();
      expect(sent, chosen);
    },
  );
  testWidgets(
    'studio invalid colour cannot save and individual reset preserves draft',
    (t) async {
      Map<String, String>? sent;
      await setupScreen(t, view(local: chosen), (v) => sent = v);
      await editHex(t, 'text_color', '#FFFFFF');
      expect(t.widget<FilledButton>(key('studio-save')).onPressed, isNull);
      final reset = key('studio-reset-text_color');
      await t.ensureVisible(reset);
      await t.tap(reset);
      await t.pumpAndSettle();
      expect(previewText(t, 'body'), const Color(0xff142a3a));
      expect(previewText(t, 'warning'), const Color(0xff92400e));
      final save = key('studio-save');
      await t.ensureVisible(save);
      await t.tap(save);
      await t.pumpAndSettle();
      expect(sent, {...chosen}..remove('text_color'));
      final discard = key('studio-discard');
      await t.ensureVisible(discard);
      await t.tap(discard);
      await t.pumpAndSettle();
      expect(previewText(t, 'body'), const Color(0xff263d36));
      final all = key('studio-reset-all');
      await t.ensureVisible(all);
      await t.tap(all);
      await t.pumpAndSettle();
      expect(previewFill(t, 'paper'), Colors.white);
      await t.ensureVisible(save);
      await t.tap(save);
      await t.pumpAndSettle();
      expect(sent, isEmpty);
    },
  );
  testWidgets('studio revoked clinic only resets dormant local overrides', (
    t,
  ) async {
    Map<String, String>? sent;
    await setupScreen(
      t,
      view(local: chosen, clinic: true, allowed: false),
      (v) => sent = v,
      customize: false,
    );
    expect(t.widget<TextField>(key('studio-hex')).enabled, isFalse);
    expect(t.widget<Slider>(key('studio-hue')).onChanged, isNull);
    expect(
      t.widget<Listener>(key('studio-colour-board')).onPointerMove,
      isNull,
    );
    expect(previewFill(t, 'paper'), Colors.white);
    final reset = key('studio-reset-all');
    await t.ensureVisible(reset);
    await t.tap(reset);
    await t.pumpAndSettle();
    final save = key('studio-save');
    await t.ensureVisible(save);
    await t.tap(save);
    await t.pumpAndSettle();
    expect(sent, isEmpty);
  });
  testWidgets('studio screen confirms once and invalidation clears editor', (
    t,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
    );
    addTearDown(api.close);
    addTearDown(guard.dispose);
    final source = _StudioSource(api, guard);
    t.view.physicalSize = const Size(1200, 1500);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    await t.pumpWidget(
      MaterialApp(home: BrandingScreen(source: source, platform: false)),
    );
    await t.pumpAndSettle();
    await t.tap(key('branding-read-organization'));
    await t.pumpAndSettle();
    await editHex(t, 'warning_color', '#92400E');
    final save = key('studio-save');
    await t.ensureVisible(save);
    await t.tap(save);
    await t.pump();
    await t.pump(const Duration(milliseconds: 300));
    expect(find.text('Confirm branding change'), findsOneWidget);
    expect(source.writes, 0);
    await t.tap(find.text('Cancel'));
    await t.pumpAndSettle();
    expect(source.writes, 0);
    await t.tap(save);
    await t.pump();
    await t.pump(const Duration(milliseconds: 300));
    await t.tap(find.text('Confirm'));
    await t.pumpAndSettle();
    expect(source.writes, 1);
    final pageScroll = t.state<ScrollableState>(
      find
          .descendant(
            of: find.byType(ListView),
            matching: find.byType(Scrollable),
          )
          .first,
    );
    pageScroll.position.jumpTo(pageScroll.position.minScrollExtent);
    await t.pumpAndSettle();
    final notice = find.byKey(const ValueKey('branding-notice'));
    // The notice is above the tall editor in a lazily built ListView.
    // Navigate by widget identity rather than assuming a scroll offset.
    final noticeScrollable = find
        .descendant(
          of: find.byType(ListView),
          matching: find.byType(Scrollable),
        )
        .first;
    final noticeTopControl = key('branding-read-organization');
    await t.scrollUntilVisible(
      noticeTopControl,
      -300,
      scrollable: noticeScrollable,
    );
    await t.ensureVisible(noticeTopControl);
    await t.pumpAndSettle();
    await t.scrollUntilVisible(notice, 100, scrollable: noticeScrollable);
    await t.ensureVisible(notice);
    await t.pumpAndSettle();
    expect(notice, findsOneWidget);
    expect(
      t.widget<Text>(notice).data,
      'Server confirmed and read back the branding change.',
    );
    expect(
      find.text('Server confirmed and read back the branding change.'),
      findsOneWidget,
    );
    guard.invalidate();
    await t.pumpAndSettle();
    expect(key('studio-colour-board'), findsNothing);
    expect(
      find.text('Session context changed. Reopen branding.'),
      findsOneWidget,
    );
    expect(source.writes, 1);
  });
}

class _StudioSource extends BrandingRemote {
  _StudioSource(ApiClient api, SessionNavigationGuard guard)
    : super(
        api: api,
        guard: guard,
        loadSession: () async => AuthSessionState.ready(actor('admin')),
        staging: true,
      );
  int writes = 0;
  @override
  Future<BrandingCapabilities> capabilities({
    required BrandingActive active,
  }) async => const BrandingCapabilities(
    organizationId: org,
    name: 'Synthetic Alpha',
    membershipRole: 'organization_admin',
    clinicId: 2,
    organization: true,
    clinic: true,
    customize: true,
    reset: true,
  );
  @override
  Future<BrandingView> read(
    BrandingScope scope, {
    String? organization,
    required BrandingActive active,
  }) async => view();
  @override
  Future<BrandingView> attachStudio(
    BrandingView v,
    BrandingActive active,
  ) async => v;
  @override
  Future<BrandingView> setStudio(
    BrandingView prior,
    Map<String, String> overrides,
    BrandingActive active,
  ) async {
    writes++;
    return view(local: overrides);
  }
}
