import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';
import 'package:clinic_system_pro/features/unified_search/presentation/search_screen.dart';

import '../problems/problem_contract_test.dart' as f;
import 'search_contract_test.dart' as c;

final field = find.byKey(const ValueKey('unified-search-query'));
Future<void> mount(WidgetTester tester, c.Harness h) async {
  await tester.pumpWidget(
    MaterialApp(home: UnifiedSearchScreen(source: h.source)),
  );
  await tester.pumpAndSettle();
}

Future<void> type(WidgetTester tester, String value) async {
  await tester.enterText(field, value);
  await tester.pump();
}

Future<void> tick(WidgetTester tester, [int ms = 350]) async {
  await tester.pump(Duration(milliseconds: ms));
  await tester.pump();
}

void main() {
  testWidgets('rapid typing sends one request only after exact 350ms delay', (
    tester,
  ) async {
    final requests = <String>[];
    final h = c.Harness(
      MockClient((r) async {
        requests.add(
          (jsonDecode(r.body) as Map<String, dynamic>)['q'] as String,
        );
        return f.ok(c.page([c.hit()]));
      }),
    );
    await mount(tester, h);
    expect(requests, isEmpty);
    await type(tester, 'A');
    await tick(tester, 400);
    expect(requests, isEmpty);
    await type(tester, 'Ad');
    await tick(tester, 100);
    await type(tester, 'Ade');
    await tick(tester, 100);
    await type(tester, 'Adebayo');
    await tick(tester, 349);
    expect(requests, isEmpty);
    await tick(tester, 1);
    expect(requests, ['Adebayo']);
    expect(find.text('Synthetic Person'), findsOneWidget);
    await type(tester, 'Adebayo ');
    await tick(tester, 400);
    expect(requests, ['Adebayo']);
  });
  testWidgets(
    'older response cannot overwrite newer response or appear while debounce pending',
    (tester) async {
      final old = Completer<Map<String, dynamic>>();
      final newer = Completer<Map<String, dynamic>>();
      var calls = 0;
      final h = c.Harness(
        MockClient((_) async {
          calls++;
          return f.ok(await (calls == 1 ? old.future : newer.future));
        }),
      );
      await mount(tester, h);
      await type(tester, 'Old');
      await tick(tester);
      expect(calls, 1);
      await type(tester, 'New');
      await tick(tester);
      expect(calls, 2);
      newer.complete(c.page([c.hit(name: 'New synthetic result')]));
      await tester.pump();
      await tester.pump();
      expect(find.text('New synthetic result'), findsOneWidget);
      old.complete(c.page([c.hit(name: 'Old synthetic result')]));
      await tester.pump();
      await tester.pump();
      expect(find.text('Old synthetic result'), findsNothing);
      expect(find.text('New synthetic result'), findsOneWidget);
    },
  );
  testWidgets(
    'clear cancels pending timer and erases prior results immediately',
    (tester) async {
      var calls = 0;
      final h = c.Harness(
        MockClient((_) async {
          calls++;
          return f.ok(c.page([c.hit()]));
        }),
      );
      await mount(tester, h);
      await type(tester, 'Syn');
      await tick(tester);
      expect(find.text('Synthetic Person'), findsOneWidget);
      await type(tester, 'Other');
      expect(find.text('Synthetic Person'), findsNothing);
      await type(tester, '');
      await tick(tester, 400);
      expect(calls, 1);
      expect(find.text('Enter at least two characters.'), findsOneWidget);
    },
  );
  testWidgets(
    'clinic/session invalidation during debounce cancels request and clears query',
    (tester) async {
      var calls = 0;
      final h = c.Harness(
        MockClient((_) async {
          calls++;
          return f.ok(c.page([]));
        }),
      );
      await mount(tester, h);
      await type(tester, 'Syn');
      await tick(tester, 200);
      h.guard.invalidate();
      await tester.pump();
      await tick(tester, 400);
      expect(calls, 0);
      expect(tester.widget<TextField>(field).controller!.text, isEmpty);
      expect(tester.widget<TextField>(field).enabled, false);
    },
  );
  testWidgets(
    'clinic/session invalidation while response in flight rejects PHI',
    (tester) async {
      final response = Completer<Map<String, dynamic>>();
      final h = c.Harness(MockClient((_) async => f.ok(await response.future)));
      await mount(tester, h);
      await type(tester, 'Syn');
      await tick(tester);
      h.guard.invalidate();
      await tester.pump();
      response.complete(c.page([c.hit()]));
      await tester.pump();
      await tester.pump();
      expect(find.text('Synthetic Person'), findsNothing);
      expect(tester.widget<TextField>(field).controller!.text, isEmpty);
    },
  );
  testWidgets('disposing screen cancels timer', (tester) async {
    var calls = 0;
    final h = c.Harness(
      MockClient((_) async {
        calls++;
        return f.ok(c.page([]));
      }),
    );
    await mount(tester, h);
    await type(tester, 'Syn');
    await tester.pumpWidget(const MaterialApp(home: SizedBox()));
    await tick(tester, 400);
    expect(calls, 0);
  });
  testWidgets('403 fails closed with no retries', (tester) async {
    var calls = 0;
    final h = c.Harness(
      MockClient((_) async {
        calls++;
        return f.denied(403);
      }),
    );
    await mount(tester, h);
    await type(tester, 'Syn');
    await tick(tester);
    await tester.pumpAndSettle();
    expect(calls, 1);
    expect(tester.widget<TextField>(field).enabled, false);
    expect(
      find.text('Session changed. Reopen unified search.'),
      findsOneWidget,
    );
  });
  testWidgets(
    '404 staged feature shows unavailable and never automatically retries',
    (tester) async {
      var calls = 0;
      final h = c.Harness(
        MockClient((_) async {
          calls++;
          return f.denied(404);
        }),
      );
      await mount(tester, h);
      await type(tester, 'Syn');
      await tick(tester);
      await tester.pumpAndSettle();
      expect(calls, 1);
      expect(
        find.text('Unified search is unavailable or disabled.'),
        findsOneWidget,
      );
    },
  );
  testWidgets(
    'resource filter participates in debounce and invalidates previous result',
    (tester) async {
      final bodies = <Map<String, dynamic>>[];
      final h = c.Harness(
        MockClient((r) async {
          bodies.add(jsonDecode(r.body) as Map<String, dynamic>);
          return f.ok(c.page([]));
        }),
      );
      await mount(tester, h);
      await type(tester, 'Syn');
      await tick(tester);
      expect(bodies.length, 1);
      await tester.tap(find.byKey(const ValueKey('unified-search-resource')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('appointment').last);
      await tester.pumpAndSettle();
      await tick(tester, 400);
      expect(bodies.length, 2);
      expect(bodies.last['resource_types'], ['appointment']);
    },
  );
  testWidgets('next page keeps query and opaque server cursor', (tester) async {
    final bodies = <Map<String, dynamic>>[];
    final h = c.Harness(
      MockClient((r) async {
        bodies.add(jsonDecode(r.body) as Map<String, dynamic>);
        if (bodies.length == 1) {
          return f.ok(
            c.page([
              for (var id = 50; id > 25; id--)
                c.hit(id: id, name: 'Synthetic $id'),
            ], next: 'opaque'),
          );
        }
        return f.ok(c.page([c.hit(id: 25, name: 'Next synthetic result')]));
      }),
    );
    await mount(tester, h);
    await type(tester, 'Syn');
    await tick(tester);
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('unified-search-next')),
      500,
      scrollable: find
          .descendant(
            of: find.byType(ListView),
            matching: find.byType(Scrollable),
          )
          .first,
    );
    await tester.tap(find.byKey(const ValueKey('unified-search-next')));
    await tester.pumpAndSettle();
    expect(bodies.length, 2);
    expect(bodies.last['q'], 'Syn');
    expect(bodies.last['cursor'], 'opaque');
    expect(find.byKey(const ValueKey('unified-search-next')), findsNothing);
  });
  testWidgets('unbound and super admin cannot see home search entry', (
    tester,
  ) async {
    final h = c.Harness(
      MockClient((_) async => throw StateError('No request expected')),
      auth: f.ready(role: 'super_admin'),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: () async => h.state,
          sessionNavigationGuard: h.guard,
          unifiedSearchConfigured: true,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('gap05-open-unified-search')),
      findsNothing,
    );
    h.state = f.ready(organization: null);
    h.guard.invalidate();
    await tester.pump();
    h.guard.markSessionAvailable();
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('gap05-open-unified-search')),
      findsNothing,
    );
  });
  testWidgets('home entry shown only for configured bound clinical staff', (
    tester,
  ) async {
    final h = c.Harness(
      MockClient((_) async => throw StateError('No request expected')),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: () async => h.state,
          sessionNavigationGuard: h.guard,
          unifiedSearchConfigured: true,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('gap05-open-unified-search')),
      findsOneWidget,
    );
    h.guard.invalidate();
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('gap05-open-unified-search')),
      findsNothing,
    );
  });
  testWidgets('direct search route blocked with unavailable session', (
    tester,
  ) async {
    final h = c.Harness(
      MockClient((_) async => throw StateError('No request expected')),
    );
    h.guard.invalidate();
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(
        apiClient: h.source.apiClient,
      ),
      loginScreen: const Scaffold(body: Text('Login sentinel')),
      sessionNavigationGuard: h.guard,
      loadHomeSession: () async => h.state,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/unified-search');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(field, findsNothing);
  });
}
