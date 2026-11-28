import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/branding/data/branding_logo_pixels.dart';
import 'package:clinic_system_pro/features/branding/data/branding_studio.dart';
import 'package:clinic_system_pro/features/branding/presentation/branding_logo_editor.dart';

import 'branding_studio_test.dart' as prior;

LogoPixels fixture({int background = 0xffffff}) {
  final bytes = Uint8List(5 * 5 * 4);
  for (var y = 0; y < 5; y++) {
    for (var x = 0; x < 5; x++) {
      final border = x == 0 || x == 4 || y == 0 || y == 4;
      final rgb = border || (x == 2 && y == 2) ? background : 0xb02020;
      final i = (y * 5 + x) * 4;
      bytes.setRange(i, i + 4, [rgb >> 16, (rgb >> 8) & 255, rgb & 255, 255]);
    }
  }
  return LogoPixels(5, 5, bytes);
}

LogoPixels remove(
  LogoPixels p, {
  int background = 0xffffff,
  int tolerance = 0,
  bool crop = true,
}) => p.edit(
  remove: true,
  background: background,
  tolerance: tolerance,
  crop: crop,
);
Finder key(String value) => find.byKey(ValueKey(value));

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test(
    'logo edge removal keeps enclosed white foreground and source bytes',
    () {
      final p = fixture();
      final before = Uint8List.fromList(p.rgba);
      final edited = remove(p);
      expect([edited.width, edited.height], [3, 3]);
      expect(edited.rgbAt(1, 1), 0xffffff);
      expect(edited.rgba[(1 * 3 + 1) * 4 + 3], 255);
      expect(p.rgba, before);
    },
  );
  test('logo custom background sample and tolerance are bounded', () {
    final p = fixture(background: 0xe9d8c1);
    expect(p.rgbAt(0, 0), 0xe9d8c1);
    expect(remove(p, background: 0xe9d8c1).width, 3);
    expect(remove(p, background: 0xe9d8c2, tolerance: 0).width, 5);
    expect(remove(p, background: 0xe9d8c2, tolerance: 1).width, 3);
    expect(() => remove(p, tolerance: 101), throwsFormatException);
    expect(() => p.rgbAt(-1, 0), throwsFormatException);
  });
  test('logo removal without crop preserves bounds and alpha', () {
    final p = remove(fixture(), crop: false);
    expect([p.width, p.height], [5, 5]);
    expect(p.rgba[3], 0);
    expect(p.rgba[(2 * 5 + 2) * 4 + 3], 255);
  });
  test(
    'logo transparent crop preserves partial alpha and no original mutation',
    () {
      final raw = Uint8List(4 * 3 * 4);
      raw.setRange((1 * 4 + 2) * 4, (1 * 4 + 2) * 4 + 4, [120, 10, 20, 90]);
      final p = LogoPixels(4, 3, raw);
      final edited = p.edit(
        remove: false,
        background: 0,
        tolerance: 0,
        crop: true,
      );
      expect([edited.width, edited.height], [1, 1]);
      expect(edited.rgba, [120, 10, 20, 90]);
      expect(p.rgba, raw);
    },
  );
  test('logo cannot erase everything or exceed bounded pixel dimensions', () {
    final p = LogoPixels(1, 1, Uint8List.fromList([255, 255, 255, 255]));
    expect(() => remove(p), throwsFormatException);
    expect(() => LogoPixels(513, 1, Uint8List(513 * 4)), throwsFormatException);
    expect(() => LogoPixels(2, 2, Uint8List(4)), throwsFormatException);
  });
  test(
    'logo PNG encoding contains actual transparency and roundtrips',
    () async {
      final edited = remove(fixture(), crop: false);
      final bytes = edited.png();
      expect(bytes.take(8), [137, 80, 78, 71, 13, 10, 26, 10]);
      final decoded = await LogoPixels.decode(bytes);
      expect([decoded.width, decoded.height], [5, 5]);
      expect(decoded.rgba, edited.rgba);
    },
  );
  test('logo fits square portrait and landscape at each backend size', () {
    for (final size in studioLogoSizes) {
      final maxH = {'small': 48.0, 'medium': 64.0, 'large': 80.0}[size]!;
      final maxW = {'small': 100.0, 'medium': 120.0, 'large': 144.0}[size]!;
      for (final dims in [
        [64, 64],
        [48, 180],
        [200, 42],
      ]) {
        final fit = logoFit(dims[0], dims[1], size);
        expect(fit.width / fit.height, closeTo(dims[0] / dims[1], 1e-8));
        expect(fit.height, lessThanOrEqualTo(maxH + 1e-8));
        expect(fit.width, lessThanOrEqualTo(maxW + 1e-8));
      }
    }
    expect(logoFit(64, 64, 'medium'), const Size(64, 64));
    expect(() => logoFit(64, 64, 'giant'), throwsFormatException);
  });
  test('logo size v2 strict inheritance and entitlement', () {
    final p = prior.studio(local: {'logo_size': 'large'});
    expect(p.effective['logo_size'], 'large');
    expect(p.draft({})['logo_size'], 'medium');
    expect(p.accepts({'logo_size': 'giant'}), isFalse);
    final c = prior.studio(
      local: {'logo_size': 'small'},
      inherited: {...studioDefaults, 'logo_size': 'large'},
      clinicId: 2,
      allowed: false,
    );
    expect(c.effective['logo_size'], 'large');
    expect(c.accepts({'logo_size': 'small'}), isTrue);
    expect(c.accepts({'logo_size': 'medium'}), isFalse);
    expect(c.accepts({}), isTrue);
    expect(
      studioValidation({...studioDefaults, 'logo_size': '#123456'}),
      isNotNull,
    );
  });
  test('logo v2 missing size and mismatched source fail closed', () {
    final response = prior.studioResponse();
    for (final field in ['inherited', 'effective', 'sources']) {
      final broken = {
        ...response,
        field: (Map<String, String>.from(response[field] as Map)
          ..remove('logo_size')),
      };
      expect(
        () => BrandingStudioState.parse(
          broken,
          organizationId: response['organization_id'] as String,
          clinicId: null,
        ),
        throwsFormatException,
      );
    }
  });
  testWidgets('logo size selection is a draft and reset preserves colours', (
    t,
  ) async {
    Map<String, String>? sent;
    await prior.setupScreen(
      t,
      prior.view(local: {'primary_color': '#136228'}),
      (v) => sent = v,
    );
    await prior.choose(t, 'studio-logo-size', 'Large');
    expect(
      t.widget<DropdownButton<String>>(key('studio-logo-size')).value,
      'large',
    );
    expect(sent, isNull);
    final save = key('studio-save');
    await t.ensureVisible(save);
    await t.tap(save);
    await t.pumpAndSettle();
    expect(sent, {'primary_color': '#136228', 'logo_size': 'large'});
    final reset = key('studio-reset-logo_size');
    await t.ensureVisible(reset);
    await t.tap(reset);
    await t.pumpAndSettle();
    expect(
      t.widget<DropdownButton<String>>(key('studio-logo-size')).value,
      'medium',
    );
    expect(prior.previewText(t, 'title'), const Color(0xff136228));
  });
  testWidgets('logo revoked clinic size selector cannot add override', (
    t,
  ) async {
    await prior.setupScreen(
      t,
      prior.view(local: {'logo_size': 'large'}, clinic: true, allowed: false),
      (_) => fail('Unexpected write'),
      customize: false,
    );
    final selector = t.widget<DropdownButton<String>>(key('studio-logo-size'));
    expect(selector.value, 'medium');
    expect(selector.onChanged, isNull);
    expect(
      t.widget<TextButton>(key('studio-reset-logo_size')).onPressed,
      isNotNull,
    );
  });
  testWidgets(
    'logo draft dialog samples background and restores original before use',
    (t) async {
      final original = fixture();
      final raw = original.png();
      final session = ValueNotifier<bool>(true);
      addTearDown(session.dispose);
      t.view.physicalSize = const Size(1000, 1000);
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.resetPhysicalSize);
      addTearDown(t.view.resetDevicePixelRatio);
      Uint8List? result;
      await t.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Builder(
              builder: (context) => TextButton(
                onPressed: () async {
                  result = await showDialog<Uint8List>(
                    context: context,
                    builder: (_) => BrandingLogoEditor(
                      original: original,
                      raw: raw,
                      paper: const Color(0xfffff8e7),
                      size: 'medium',
                      active: () => session.value,
                      session: session,
                    ),
                  );
                },
                child: const Text('Open logo'),
              ),
            ),
          ),
        ),
      );
      await t.tap(find.text('Open logo'));
      await t.pumpAndSettle();
      final sample = key('logo-sample');
      final r = t.getRect(sample);
      await t.tapAt(r.topLeft + const Offset(2, 2));
      await t.pumpAndSettle();
      expect(
        t.widget<CheckboxListTile>(key('logo-remove-background')).value,
        isTrue,
      );
      expect(
        t.widget<Container>(key('logo-paper-preview')).color,
        const Color(0xfffff8e7),
      );
      expect(result, isNull);
      final restore = key('logo-restore');
      await t.ensureVisible(restore);
      await t.tap(restore);
      await t.pumpAndSettle();
      expect(
        t.widget<CheckboxListTile>(key('logo-remove-background')).value,
        isFalse,
      );
      expect(t.widget<CheckboxListTile>(key('logo-crop')).value, isFalse);
      await t.tap(key('logo-use-edited'));
      await t.pumpAndSettle();
      expect(result, raw);
    },
  );
  testWidgets(
    'logo editor returns processed transparent PNG only when approved',
    (t) async {
      final original = fixture();
      final session = ValueNotifier<bool>(true);
      addTearDown(session.dispose);
      t.view.physicalSize = const Size(1000, 1000);
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.resetPhysicalSize);
      addTearDown(t.view.resetDevicePixelRatio);
      Uint8List? result;
      await t.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Builder(
              builder: (context) => TextButton(
                onPressed: () async {
                  result = await showDialog<Uint8List>(
                    context: context,
                    builder: (_) => BrandingLogoEditor(
                      original: original,
                      raw: original.png(),
                      paper: Colors.white,
                      size: 'large',
                      active: () => session.value,
                      session: session,
                    ),
                  );
                },
                child: const Text('Open logo'),
              ),
            ),
          ),
        ),
      );
      await t.tap(find.text('Open logo'));
      await t.pumpAndSettle();
      final checkbox = key('logo-remove-background');
      await t.ensureVisible(checkbox);
      await t.tap(checkbox);
      await t.pumpAndSettle();
      expect(result, isNull);
      await t.tap(key('logo-use-edited'));
      await t.pumpAndSettle();
      expect(result, remove(original, tolerance: 30).png());
    },
  );
  testWidgets('logo editor cancel leaves source intact and returns no draft', (
    t,
  ) async {
    final original = fixture();
    final raw = original.png();
    final session = ValueNotifier<bool>(true);
    addTearDown(session.dispose);
    t.view.physicalSize = const Size(1000, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    Uint8List? result;
    var closed = false;
    await t.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () async {
                result = await showDialog<Uint8List>(
                  context: context,
                  builder: (_) => BrandingLogoEditor(
                    original: original,
                    raw: raw,
                    paper: Colors.white,
                    size: 'medium',
                    active: () => session.value,
                    session: session,
                  ),
                );
                closed = true;
              },
              child: const Text('Open logo'),
            ),
          ),
        ),
      ),
    );
    await t.tap(find.text('Open logo'));
    await t.pumpAndSettle();
    final checkbox = key('logo-remove-background');
    await t.ensureVisible(checkbox);
    await t.tap(checkbox);
    await t.pumpAndSettle();
    await t.tap(find.text('Cancel'));
    await t.pumpAndSettle();
    expect(closed, isTrue);
    expect(result, isNull);
    expect(original.png(), raw);
  });
  testWidgets(
    'logo dialog session invalidation closes without returning bytes',
    (t) async {
      final original = fixture();
      final session = ValueNotifier<bool>(true);
      addTearDown(session.dispose);
      Uint8List? result;
      var completed = false;
      await t.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Builder(
              builder: (context) => TextButton(
                onPressed: () async {
                  result = await showDialog<Uint8List>(
                    context: context,
                    builder: (_) => BrandingLogoEditor(
                      original: original,
                      raw: original.png(),
                      paper: Colors.white,
                      size: 'medium',
                      active: () => session.value,
                      session: session,
                    ),
                  );
                  completed = true;
                },
                child: const Text('Open logo'),
              ),
            ),
          ),
        ),
      );
      await t.tap(find.text('Open logo'));
      await t.pumpAndSettle();
      session.value = false;
      await t.pumpAndSettle();
      expect(completed, isTrue);
      expect(result, isNull);
      expect(find.text('Edit hospital logo'), findsNothing);
    },
  );
}
