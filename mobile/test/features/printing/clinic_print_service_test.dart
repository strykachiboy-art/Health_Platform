import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/printing/data/clinic_print_service.dart';
import 'package:clinic_system_pro/features/printing/presentation/clinic_print_action.dart';

class _Device implements ClinicPrintDevice {
  bool supported = true;
  bool cancelled = false;
  int presentations = 0;
  int layouts = 0;
  VoidCallbackLike? beforeLayout;
  final copies = <List<int>>[];
  @override
  Future<bool> available() async => supported;
  @override
  Future<bool> present(Future<Uint8List> Function() layout) async {
    presentations++;
    if (cancelled) return false;
    beforeLayout?.call();
    copies.add(await layout()); layouts++;
    copies.add(await layout()); layouts++;
    return true;
  }
}
typedef VoidCallbackLike = void Function();

// A controlled in-memory HTTP transport for the widget's fake-async zone.
// The service still handles the real http.StreamedResponse validation and
// stream consumption; only the test's response delivery is deterministic.
class _ControlledPrintClient extends http.BaseClient {
  _ControlledPrintClient(this.respond);
  final http.StreamedResponse Function(http.BaseRequest) respond;
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async =>
      respond(request);
}

const _pdf = '%PDF-1.7\nsynthetic\n%%EOF\n';
const _digest = '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
Map<String, String> _headers(ClinicPrintSource kind, int id) => {
  'content-type':'application/pdf', 'cache-control':'private, no-store, max-age=0',
  'x-content-type-options':'nosniff', 'x-source-sha256':_digest,
  'content-disposition':'attachment; filename="${switch(kind) {
    ClinicPrintSource.invoice => 'invoice-$id.pdf',
    ClinicPrintSource.appointment => 'appointment-$id.pdf',
    ClinicPrintSource.labOrder => 'lab_order-$id.pdf',
    ClinicPrintSource.radiologyDraft => 'radiology_draft-$id.pdf',
    ClinicPrintSource.releasedLab => 'released-lab-$id-v2.pdf',
    ClinicPrintSource.stagedRadiology => 'staged-radiology-copy.pdf',
  }}"',
  if (kind == ClinicPrintSource.stagedRadiology) 'x-radiology-scope':'synthetic-nondiagnostic-only'
  else 'x-clinical-release': kind == ClinicPrintSource.releasedLab ? 'patient-copy' : 'none',
};
AuthTokenResponse _actor(String role, {int clinic=2}) => AuthTokenResponse(accessToken:'token',refreshToken:'refresh',userId:3,role:role,clinicContextId:clinic,tenantId:'12345678-1234-1234-1234-123456789abc');

void main() {
  late SessionNavigationGuard guard;
  late ApiClient api;
  late _Device device;
  late AuthTokenResponse actor;
  late ClinicPrintSource kind;
  late int calls;
  late http.Response Function(http.Request) reply;
  late ClinicPrintService service;
  setUp(() {
    guard = SessionNavigationGuard()..markSessionAvailable();
    api = ApiClient(config: const BackendConfig(baseUrl:'http://127.0.0.1:5000',allowInsecureHttp:true));
    device = _Device(); actor = _actor('doctor'); kind = ClinicPrintSource.appointment; calls = 0;
    reply = (_) => http.Response(_pdf,200,headers:_headers(kind,7));
    service = ClinicPrintService(api:api,guard:guard,device:device,
      loadSession:() async => AuthSessionState.ready(actor),clientFactory:() => MockClient((req) async {
        calls++;
        expect(req.method,'GET');
        expect(req.url.path,'/${kind.path(7)}');
        expect(req.url.hasQuery,isFalse);
        expect(req.headers['Authorization'],'Bearer token');
        expect(req.followRedirects,isFalse);
        return reply(req);
      }));
  });
  tearDown(() {api.close();guard.dispose();});
  Future<bool> printCopy() => service.printCurrent(source:kind,id:7,active:()=>true);
  for (final source in ClinicPrintSource.values) {
    test('authorized server bytes only ${source.name}', () async {
      kind = source;
      actor = _actor(switch(source) {
        ClinicPrintSource.invoice => 'accountant',
        ClinicPrintSource.labOrder => 'lab_technician',
        ClinicPrintSource.appointment || ClinicPrintSource.radiologyDraft => 'doctor',
        _ => 'patient',
      });
      expect(await printCopy(),isTrue);
      expect(calls,1);expect(device.presentations,1);expect(device.layouts,2);
      expect(device.copies.every((x)=>utf8.decode(x)==_pdf),isTrue);
    });
  }
  for(final status in [302,401,403,404,500]) {
    test('denied HTTP $status never opens printer', () async {
      var rejects = 0;api.onAuthenticationRejected=()=>rejects++;
      reply=(_)=>http.Response('denied',status,headers:_headers(kind,7));
      await expectLater(printCopy(),throwsA(isA<ClinicPrintUnavailable>()));
      expect(calls,1);expect(device.presentations,0);expect(rejects,status==401?1:0);
    });
  }
  for(final header in ['content-type','cache-control','x-content-type-options','x-source-sha256','content-disposition','x-clinical-release']) {
    test('missing $header never opens printer', () async {
      reply=(_)=>http.Response(_pdf,200,headers:_headers(kind,7)..remove(header));
      await expectLater(printCopy(),throwsA(isA<ClinicPrintUnavailable>()));expect(device.presentations,0);
    });
  }
  test('foreign role and unavailable native device stop before HTTP', () async {
    actor=_actor('nurse');await expectLater(printCopy(),throwsA(isA<ClinicPrintUnavailable>()));
    actor=_actor('doctor');device.supported=false;await expectLater(printCopy(),throwsA(isA<ClinicPrintUnavailable>()));expect(calls,0);
  });
  test('cross session response discarded before print dialog', () async {
    reply=(_){actor=_actor('doctor',clinic:8);return http.Response(_pdf,200,headers:_headers(kind,7));};
    await expectLater(printCopy(),throwsA(isA<ClinicPrintUnavailable>()));expect(device.presentations,0);
  });
  test('session invalidation before native layout never releases bytes', () async {
    device.beforeLayout=guard.invalidate;
    await expectLater(printCopy(),throwsA(isA<ClinicPrintUnavailable>()));expect(device.layouts,0);
  });
  test('wrong filename and truncated PDF rejected', () async {
    reply=(_)=>http.Response(_pdf,200,headers:_headers(kind,8));await expectLater(printCopy(),throwsA(isA<ClinicPrintUnavailable>()));
    reply=(_)=>http.Response('%PDF-1.7\ntruncated',200,headers:_headers(kind,7));await expectLater(printCopy(),throwsA(isA<ClinicPrintUnavailable>()));expect(device.presentations,0);
  });
  test('oversized payload rejected', () async {
    reply=(_)=>http.Response.bytes(Uint8List(ClinicPrintService.maxBytes+1),200,headers:_headers(kind,7));
    await expectLater(printCopy(),throwsA(isA<ClinicPrintUnavailable>()));expect(device.presentations,0);
  });
  test('cancelled system dialog does not report success', () async {
    device.cancelled=true;expect(await printCopy(),isFalse);expect(device.layouts,0);expect(calls,1);
  });
  test('one global job per session, no duplicate HTTP request', () async {
    final entered=Completer<void>();final finish=Completer<AuthSessionState>();
    final first=ClinicPrintService(api:api,guard:guard,device:device,
      loadSession:(){if(!entered.isCompleted)entered.complete();return finish.future;});
    final pending=first.printCurrent(source:kind,id:7,active:()=>true);
    await entered.future;
    await expectLater(printCopy(),throwsA(isA<ClinicPrintUnavailable>()));
    final outcome=expectLater(pending,throwsA(isA<ClinicPrintUnavailable>()));
    finish.complete(AuthSessionState.ready(_actor('nurse')));await outcome;expect(calls,0);
  });
  testWidgets('explicit confirmation then one server PDF print', (tester) async {
    late StreamController<List<int>> body;
    late Completer<void> subscribed;
    late Completer<void> finalSessionCheck;
    final payload = utf8.encode(_pdf);
    const deadline = Duration(seconds: 8);
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ClinicPrintAction(
      api: api, loadSession: () async {
        if (device.layouts == 2 && !finalSessionCheck.isCompleted) {
          finalSessionCheck.complete();
        }
        return AuthSessionState.ready(actor);
      }, guard: guard,
      source: kind, recordId: 7, device: device,
      clientFactory: () => _ControlledPrintClient((req) {
        calls++;
        expect(req.method, 'GET');
        expect(req.url.path, '/${kind.path(7)}');
        expect(req.url.hasQuery, isFalse);
        expect(req.headers['Authorization'], 'Bearer token');
        expect(req.followRedirects, isFalse);
        return http.StreamedResponse(body.stream, 200,
          headers: _headers(kind, 7), contentLength: payload.length);
      }),
    ))));
    final action = find.byKey(const ValueKey('clinic-print-current'));
    await tester.tap(action);
    await tester.pumpAndSettle();
    expect(calls, 0);
    expect(device.presentations, 0);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(calls, 0);
    expect(device.presentations, 0);

    // Start the actual widget callback in a real asynchronous zone BEFORE
    // it creates the dialog future, transport, and async-for subscription.
    // The confirmation remains a real dialog; no service or device bypass.
    final started = await tester.runAsync(() async {
      subscribed = Completer<void>();
      finalSessionCheck = Completer<void>();
      body = StreamController<List<int>>(
        onListen: () => subscribed.complete(),
      );
      addTearDown(() {
        if (!body.isClosed) unawaited(body.close());
      });
      final callback = tester.widget<FilledButton>(action).onPressed;
      expect(callback, isNotNull);
      callback!();
      return true;
    });
    expect(started, isTrue, reason: 'real-zone print action started');
    await tester.pumpAndSettle();
    expect(find.text('Print current authorized copy?'), findsOneWidget);
    expect(calls, 0);
    expect(device.presentations, 0);
    await tester.tap(find.text('Continue'));
    await tester.pumpAndSettle();

    // Await actual stream subscription, EOF and the final session check
    // rather than advancing a fake clock or relying on settled frames.
    final delivered = await tester.runAsync(() async {
      await subscribed.future.timeout(deadline, onTimeout: () =>
        throw StateError('PRINT_WIDGET_STAGE: response subscription not reached; GETs=$calls'));
      expect(calls, 1);
      expect(device.presentations, 0, reason: 'no print before complete PDF');
      body.add(payload);
      await body.close().timeout(deadline, onTimeout: () =>
        throw StateError('PRINT_WIDGET_STAGE: PDF stream EOF not consumed'));
      await finalSessionCheck.future.timeout(deadline, onTimeout: () =>
        throw StateError('PRINT_WIDGET_STAGE: final session check not reached; presentations=${device.presentations}; layouts=${device.layouts}'));
      // Drain the remaining real microtasks before rendering the notice.
      await Future<void>.delayed(Duration.zero);
      return true;
    });
    expect(tester.takeException(), isNull, reason: 'real-zone stage failure');
    expect(delivered, isTrue, reason: 'real-zone PDF handoff completed');
    await tester.pumpAndSettle();
    expect(find.text('Print unavailable or outcome unconfirmed. Review the device and server before trying again.'), findsNothing);
    expect(calls, 1);
    expect(device.presentations, 1, reason: 'complete validated PDF must reach device');
    expect(device.layouts, 2);
    expect(device.copies.every((copy) => utf8.decode(copy) == _pdf), isTrue);
    expect(find.text('Print dialog reported completion; physical output is not verified.'), findsOneWidget);
  });
  testWidgets('clinic change while confirmation open denies HTTP', (tester) async {
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ClinicPrintAction(
      api: api, loadSession: () async => AuthSessionState.ready(actor), guard: guard,
      source: kind, recordId: 7, device: device,
      clientFactory: () => MockClient((req) async { calls++; return reply(req); }),
    ))));
    await tester.tap(find.byKey(const ValueKey('clinic-print-current')));
    await tester.pumpAndSettle();
    guard.invalidate();
    await tester.pumpAndSettle();
    await tester.tap(find.text('Continue'));
    await tester.pumpAndSettle();
    expect(calls, 0); expect(device.presentations, 0);
  });
}
