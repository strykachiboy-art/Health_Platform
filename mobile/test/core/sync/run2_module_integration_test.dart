import 'dart:convert';

import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';
import 'package:clinic_system_pro/core/sync/operation_queue_store.dart';
import 'package:clinic_system_pro/core/sync/run2_module_catalog.dart';
import 'package:clinic_system_pro/core/sync/run2_module_transport.dart';
import 'package:clinic_system_pro/core/sync/run2_policy.dart';
import 'package:clinic_system_pro/core/sync/run2_transport.dart';
import 'package:clinic_system_pro/core/sync/run2_workspace.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/pending_work/presentation/run2_module_screen.dart';

const tenant = '12345678-1234-1234-1234-123456789abc';
const foreignTenant = '87654321-4321-4321-4321-cba987654321';
final start = DateTime.utc(2026, 11, 26, 12);
AuthTokenResponse actor({
  String role = 'doctor',
  String organization = tenant,
}) => AuthTokenResponse(
  accessToken:
      'header.${base64UrlEncode(utf8.encode(jsonEncode({'exp': start.add(const Duration(days: 4)).millisecondsSinceEpoch ~/ 1000}))).replaceAll('=', '')}.signature',
  refreshToken: 'test-refresh',
  userId: 7,
  role: role,
  clinicContextId: 3,
  tenantId: organization,
);

class _MemoryStorage implements SecureKeyValueStorage {
  final values = <String, String>{};
  @override
  Future<String?> read({required String key}) async => values[key];
  @override
  Future<void> write({required String key, required String value}) async {
    values[key] = value;
  }

  @override
  Future<void> delete({required String key}) async {
    values.remove(key);
  }
}

http.Response response(Object data, {String? cache, int status = 200}) =>
    http.Response(
      jsonEncode(data),
      status,
      headers: {
        'content-type': 'application/json; charset=utf-8',
        'cache-control': ?cache,
      },
    );
Map<String, dynamic> envelope(Object data) => {'success': true, 'data': data};
Map<String, dynamic> patient({int clinic = 3, int id = 9}) => {
  'id': id,
  'clinic_id': clinic,
  'patient_number': 'SYN-9',
  'first_name': 'Synthetic',
  'last_name': 'Patient',
  'date_of_birth': '2000-01-01',
  'gender': 'female',
  'is_active': true,
  'phone': 'NEVER_PERSIST_PHONE',
  'address': 'NEVER_PERSIST_ADDRESS',
  'allergies': 'NEVER_PERSIST_ALLERGIES',
};

void main() {
  late AppDatabase db;
  late OperationQueueStore store;
  late SessionNavigationGuard guard;
  late AuthTokenResponse current;
  late DateTime clock;
  late Duration elapsed;
  late ApiClient api;
  late Run2Workspace workspace;
  late int posts;
  late int notices;
  late int admission;
  late bool denyTarget;
  late bool noStore;
  late int returnedClinic;
  late int returnedPatient;
  late List<String> paths;
  late MockClient client;
  Run2Workspace build({bool managed = true}) => Run2Workspace(
    store: store,
    guard: guard,
    loadSession: () async => current,
    transport: ApiRun2Transport(api),
    moduleTransport: Run2ModuleTransport(api),
    managedRecords: managed,
    notifyQueued: () {
      notices++;
    },
    clock: () => clock,
    elapsed: () => elapsed,
  );
  void advance(Duration delta) {
    clock = clock.add(delta);
    elapsed += delta;
  }

  setUp(() {
    db = AppDatabase(NativeDatabase.memory());
    store = OperationQueueStore(
      db,
      payloadCrypto: OperationPayloadCrypto(secureStorage: _MemoryStorage()),
    );
    guard = SessionNavigationGuard()..markSessionAvailable();
    current = actor();
    clock = start;
    elapsed = Duration.zero;
    posts = 0;
    notices = 0;
    admission = 91;
    denyTarget = false;
    noStore = false;
    returnedClinic = 3;
    returnedPatient = 9;
    paths = [];
    client = MockClient((r) async {
      paths.add(r.url.path);
      if (r.method != 'GET') {
        posts++;
        throw StateError('Local draft attempted network mutation');
      }
      expect(
        r.headers.keys.any(
          (key) => key.toLowerCase() == 'x-emergency-access-id',
        ),
        isFalse,
      );
      if (r.url.path == '/api/v1/auth/tenant-context') {
        return response(
          envelope({'tenant_id': current.tenantId, 'clinic_id': 3}),
        );
      }
      if (r.url.path == '/health/live') {
        return response({'success': true, 'status': 'ok'});
      }
      if (denyTarget) {
        return response({'success': false}, status: 403);
      }
      final cache = noStore ? 'private, no-store, max-age=0' : null;
      switch (r.url.path) {
        case '/api/v1/patients/9':
          return response(
            envelope(patient(clinic: returnedClinic, id: returnedPatient)),
            cache: cache,
          );
        case '/api/v1/wards/patients/9/current-admission':
          return response({
            'id': admission,
            'patient_id': 9,
            'bed_id': 12,
            'status': 'active',
            'admitted_at': '2026-11-25T10:00:00',
          }, cache: cache);
        case '/api/v1/lab/orders/8':
          return response(
            envelope({
              'id': 8,
              'clinic_id': 3,
              'patient_id': 9,
              'status': 'processing',
              'items': [
                {'id': 81, 'order_id': 8, 'result_value': 'DO_NOT_STORE_RAW'},
              ],
            }),
            cache: cache,
          );
        case '/api/v1/lab/tests':
          return response(
            envelope({
              'items': [
                {
                  'id': 2,
                  'clinic_id': 3,
                  'name': 'Synthetic test',
                  'code': 'SYN',
                  'is_active': true,
                },
              ],
            }),
            cache: cache,
          );
        case '/api/v1/radiology/exams/4':
          return response(
            envelope({
              'exam_id': 4,
              'clinic_id': 3,
              'imaging_order_id': 5,
              'version': 1,
              'status': 'planned',
              'accession_number': 'NO_STORE_ACCESSION',
            }),
            cache: 'no-store',
          );
        case '/api/v1/surgery/bookings/6':
          return response(
            envelope({
              'id': 6,
              'clinic_id': 3,
              'surgical_case_id': 7,
              'version': 1,
              'status': 'scheduled',
            }),
            cache: 'no-store',
          );
        default:
          throw StateError('Unexpected test GET');
      }
    });
    api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    workspace = build();
  });
  tearDown(() async {
    api.close();
    client.close();
    guard.dispose();
    await db.close();
  });

  test(
    'catalogue has exact bounded reads and draft forms with no generic path',
    () {
      expect(Run2ModuleCatalog.reads.length, 17);
      expect(Run2ModuleCatalog.drafts.length, 12);
      expect(Run2ModuleCatalog.anchorOnly.keys.toSet(), {
        'radiology_exam',
        'surgery_booking',
      });
      expect(Run2ModuleCatalog.readsFor('super_admin', true), isEmpty);
      expect(Run2ModuleCatalog.draftsFor('super_admin', true), isEmpty);
      expect(Run2ModuleCatalog.draftsFor('doctor', false), isEmpty);
      expect(Run2ModuleCatalog.readsFor('doctor', false).map((s) => s.key), [
        'lab_catalog',
      ]);
      expect(
        Run2Policy.replayTypes.intersection(Run2ModuleCatalog.localTypes),
        isEmpty,
      );
      expect(
        () => Run2ModuleCatalog.reads['patient']!.path(-1),
        throwsFormatException,
      );
    },
  );
  test('managed policy defaults off and denies clinical cache and draft before HTTP', () async {
    final disabled = build(managed: false);
    await disabled.refreshOnline();
    final before = paths.length;
    await expectLater(
      disabled.captureModuleSnapshot('patient', 9),
      throwsA(isA<Run2WorkspaceUnavailable>()),
    );
    await expectLater(
      disabled.saveModuleDraft('consultation_note', 9, {'notes': 'Synthetic'}),
      throwsA(isA<Run2WorkspaceUnavailable>()),
    );
    expect(paths.length, before);
    await disabled.captureModuleSnapshot('lab_catalog', 0);
    expect(
      (await disabled.moduleItems()).single.rows.single['name'],
      'Synthetic test',
    );
  });
  test(
    'patient snapshot is minimized, encrypted and historical, not raw response',
    () async {
      await workspace.refreshOnline();
      final id = await workspace.captureModuleSnapshot('patient', 9);
      final items = await workspace.moduleItems();
      expect(items.single.visible, isTrue);
      expect(items.single.rows.single['first_name'], 'Synthetic');
      expect(items.single.rows.single.containsKey('phone'), isFalse);
      final p = await store.loadPayload(
        operationId: id,
        context: Run2Policy.context(current)!,
      );
      expect(jsonEncode(p), isNot(contains('NEVER_PERSIST')));
      final payloads = await db.select(db.operationQueuePayload).get();
      expect(payloads, hasLength(2));
      expect(
        utf8.decode(
          payloads.firstWhere((p) => p.operationId == id).ciphertext,
          allowMalformed: true,
        ),
        isNot(contains('Synthetic')),
      );
      expect(posts, 0);
      expect(notices, 0);
    },
  );
  test(
    'foreign clinic and wrong record identity both deny persistence',
    () async {
      await workspace.refreshOnline();
      returnedClinic = 4;
      await expectLater(
        workspace.captureModuleSnapshot('patient', 9),
        throwsFormatException,
      );
      returnedClinic = 3;
      returnedPatient = 10;
      await expectLater(
        workspace.captureModuleSnapshot('patient', 9),
        throwsFormatException,
      );
      expect(await workspace.moduleItems(), isEmpty);
    },
  );
  test(
    'no-store response is never persisted even when managed policy enabled',
    () async {
      await workspace.refreshOnline();
      noStore = true;
      await expectLater(
        workspace.captureModuleSnapshot('patient', 9),
        throwsA(isA<ApiException>()),
      );
      expect(await workspace.moduleItems(), isEmpty);
    },
  );
  test('unknown endpoint and wrong role fail before any HTTP', () async {
    current = actor(role: 'driver');
    await workspace.refreshOnline();
    final before = paths.length;
    await expectLater(
      workspace.captureModuleSnapshot('patient', 9),
      throwsA(isA<Run2WorkspaceUnavailable>()),
    );
    await expectLater(
      workspace.captureModuleSnapshot('api/v1/patients/9', 9),
      throwsA(isA<Run2WorkspaceUnavailable>()),
    );
    expect(paths.length, before);
  });
  test(
    'nursing note requires live admission grant then saves without network',
    () async {
      current = actor(role: 'nurse');
      await workspace.refreshOnline();
      await expectLater(
        workspace.saveModuleDraft('nursing_note', 9, {
          'note_text': 'Synthetic observation',
        }),
        throwsA(isA<Run2WorkspaceUnavailable>()),
      );
      expect(await workspace.prepareModuleDraft('nursing_note', 9), 91);
      final before = paths.length;
      await workspace.saveModuleDraft('nursing_note', 9, {
        'note_text': 'Synthetic observation',
      });
      expect(paths.length, before);
      final item = (await workspace.moduleItems()).single;
      expect(item.targetId, 91);
      expect(item.fields['note_text'], 'Synthetic observation');
      expect(item.isDraft, isTrue);
      await workspace.runDelivery();
      expect(posts, 0);
      expect(notices, 0);
    },
  );
  test('lab result draft validates exact order item before saving', () async {
    current = actor(role: 'lab_technician');
    await workspace.refreshOnline();
    expect(await workspace.prepareModuleDraft('lab_result', 8), 8);
    await expectLater(
      workspace.saveModuleDraft('lab_result', 8, {
        'order_item_id': '82',
        'result_value': 'synthetic',
      }),
      throwsFormatException,
    );
    final id = await workspace.saveModuleDraft('lab_result', 8, {
      'order_item_id': '81',
      'result_value': 'synthetic',
      'result_notes': 'working copy',
    });
    final raw = await store.loadPayload(
      operationId: id,
      context: Run2Policy.context(current)!,
    );
    expect(jsonEncode(raw), isNot(contains('DO_NOT_STORE_RAW')));
    expect(raw['fields']['order_item_id'], '81');
  });
  test('radiology and surgery no-store anchors permit only user-authored unsigned drafts', () async {
    await workspace.refreshOnline();
    await expectLater(
      workspace.captureModuleSnapshot('radiology_exam', 4),
      throwsA(isA<Run2WorkspaceUnavailable>()),
    );
    expect(await workspace.prepareModuleDraft('radiology_report', 4), 4);
    final id = await workspace.saveModuleDraft('radiology_report', 4, {
      'content': 'Synthetic draft',
    });
    final raw = await store.loadPayload(
      operationId: id,
      context: Run2Policy.context(current)!,
    );
    expect(jsonEncode(raw), isNot(contains('NO_STORE_ACCESSION')));
    expect(await workspace.prepareModuleDraft('surgery_report', 6), 6);
    await workspace.saveModuleDraft('surgery_report', 6, {
      'procedure_narrative': 'Synthetic unsigned working narrative',
    });
    expect((await workspace.moduleItems()).length, 2);
    await workspace.runDelivery();
    expect(posts, 0);
  });
  test('restart requires fresh lease and separate resource authorization before disclosure', () async {
    await workspace.refreshOnline();
    final id = await workspace.captureModuleSnapshot('patient', 9);
    workspace = build();
    await expectLater(
      workspace.moduleItems(),
      throwsA(isA<Run2WorkspaceUnavailable>()),
    );
    await workspace.refreshOnline();
    final locked = (await workspace.moduleItems()).single;
    expect(locked.visible, isFalse);
    expect(locked.rows, isEmpty);
    expect(locked.selector, isNull);
    denyTarget = true;
    await expectLater(
      workspace.reopenModuleItemOnline(id),
      throwsA(isA<ApiException>()),
    );
    expect((await workspace.moduleItems()).single.visible, isFalse);
    denyTarget = false;
    await workspace.reopenModuleItemOnline(id);
    expect(
      (await workspace.moduleItems()).single.rows.single['first_name'],
      'Synthetic',
    );
  });
  test(
    'new admission cannot be substituted for original draft target',
    () async {
      current = actor(role: 'nurse');
      await workspace.refreshOnline();
      await workspace.prepareModuleDraft('nursing_note', 9);
      final id = await workspace.saveModuleDraft('nursing_note', 9, {
        'note_text': 'Synthetic old admission',
      });
      await workspace.refreshOnline();
      admission = 92;
      await expectLater(
        workspace.reopenModuleItemOnline(id),
        throwsA(isA<Run2WorkspaceUnavailable>()),
      );
      expect((await workspace.moduleItems()).single.visible, isFalse);
    },
  );
  test('tenant and role changes do not reveal retained module data', () async {
    await workspace.refreshOnline();
    await workspace.captureModuleSnapshot('patient', 9);
    current = actor(organization: foreignTenant);
    await expectLater(
      workspace.moduleItems(),
      throwsA(isA<Run2WorkspaceUnavailable>()),
    );
    await workspace.refreshOnline();
    expect(await workspace.moduleItems(), isEmpty);
    current = actor(role: 'nurse');
    await workspace.refreshOnline();
    expect(await workspace.moduleItems(), isEmpty);
  });
  test(
    'lease expiry and retention expiry hide and remove only local module data',
    () async {
      await workspace.refreshOnline();
      await workspace.captureModuleSnapshot('patient', 9);
      await workspace.queueFeedback('navigation');
      advance(const Duration(minutes: 15));
      await expectLater(
        workspace.moduleItems(),
        throwsA(isA<Run2WorkspaceUnavailable>()),
      );
      advance(const Duration(hours: 24));
      expect(await store.purgeRun2ExpiredModuleRecords(now: clock), 1);
      await workspace.refreshOnline();
      expect(await workspace.moduleItems(), isEmpty);
      expect(
        (await store.listScopedOperationPage(
          context: Run2Policy.context(current)!,
          operationType: Run2Policy.feedback,
        )),
        hasLength(1),
      );
    },
  );
  test('local-only deletion cannot delete preset replay identities', () async {
    await workspace.refreshOnline();
    final id = await workspace.captureModuleSnapshot('patient', 9);
    await workspace.deleteModuleItem(id, Run2ModuleCatalog.snapshotType);
    expect(await workspace.moduleItems(), isEmpty);
    await workspace.queueFeedback('navigation');
    final row = (await store.listScopedOperationPage(
      context: Run2Policy.context(current)!,
      operationType: Run2Policy.feedback,
    )).single;
    await expectLater(
      workspace.deleteModuleItem(row.operationId, Run2Policy.feedback),
      throwsA(isA<Run2WorkspaceUnavailable>()),
    );
    expect(await store.findByOperationId(row.operationId), isNotNull);
  });
  test('draft field schemas reject extra credentials, empty, bad date and quantity', () {
    final registration = Run2ModuleCatalog.drafts['patient_registration']!;
    expect(
      () => Run2ModuleCatalog.validateDraft(registration, {
        'first_name': 'A',
        'last_name': 'B',
        'password': 'secret',
      }),
      throwsFormatException,
    );
    expect(
      () => Run2ModuleCatalog.validateDraft(registration, {'first_name': 'A'}),
      throwsFormatException,
    );
    expect(
      () => Run2ModuleCatalog.validateDraft(registration, {
        'first_name': 'A',
        'last_name': 'B',
        'date_of_birth': '2026-02-31',
      }),
      throwsFormatException,
    );
    final bill = Run2ModuleCatalog.drafts['invoice_draft']!;
    expect(
      () => Run2ModuleCatalog.validateDraft(bill, {
        'currency': 'NGN',
        'description': 'Synthetic',
        'quantity': '0',
        'unit_price': '50.00',
      }),
      throwsFormatException,
    );
    expect(
      Run2ModuleCatalog.validateDraft(bill, {
        'currency': 'NGN',
        'description': 'Synthetic',
        'quantity': '2',
        'unit_price': '50.00',
      })['quantity'],
      '2',
    );
  });
  test('notification history must belong to current recipient and clinic', () {
    final spec = Run2ModuleCatalog.reads['notifications']!;
    final row = {
      'id': 1,
      'clinic_id': 3,
      'user_id': 7,
      'title': 'Synthetic',
      'message': 'Synthetic notification',
      'notification_type': 'system',
      'priority': 'normal',
      'status': 'sent',
      'is_read': false,
    };
    expect(
      Run2ModuleTransport.project(
        spec,
        envelope({
          'items': [row],
        }),
        0,
        3,
        ownerId: 7,
      ),
      hasLength(1),
    );
    expect(
      () => Run2ModuleTransport.project(
        spec,
        envelope({
          'items': [row],
        }),
        0,
        3,
        ownerId: 8,
      ),
      throwsFormatException,
    );
    expect(
      () => Run2ModuleTransport.project(
        spec,
        envelope({
          'items': [row],
        }),
        0,
        4,
        ownerId: 7,
      ),
      throwsFormatException,
    );
  });
  test('medication reconciliation and pharmacy stock use their actual distinct shapes', () {
    final med = Run2ModuleCatalog.reads['medrec']!;
    final medRow = {
      'id': 7,
      'clinic_id': 3,
      'patient_id': 4,
      'admission_id': 5,
      'event_type': 'admission',
      'record_status': 'draft',
      'record_version': 1,
      'notes': 'Synthetic',
      'items': [],
    };
    expect(
      Run2ModuleTransport.project(
        med,
        {'reconciliation': medRow},
        7,
        3,
      ).single['notes'],
      'Synthetic',
    );
    final stock = Run2ModuleCatalog.reads['pharmacy_stock']!;
    final stockRow = {
      'clinic_id': 3,
      'drug_id': 4,
      'drug_name': 'Synthetic',
      'quantity_on_hand': 3,
      'batch_count': 1,
    };
    expect(
      Run2ModuleTransport.project(
        stock,
        envelope(stockRow),
        4,
        3,
      ).single['quantity_on_hand'],
      3,
    );
    expect(
      () => Run2ModuleTransport.project(stock, envelope(stockRow), 5, 3),
      throwsFormatException,
    );
  });
  testWidgets(
    'module work page shows governance lock and clears after invalidation',
    (tester) async {
      final disabled = build(managed: false);
      await tester.runAsync(disabled.refreshOnline);
      await tester.pumpWidget(
        MaterialApp(home: Run2ModuleScreen(workspace: disabled)),
      );
      await tester.runAsync(() async {
        await Future<void>.delayed(const Duration(milliseconds: 100));
      });
      await tester.pump();
      expect(
        find.textContaining('Managed clinical/financial storage is disabled'),
        findsOneWidget,
      );
      guard.invalidate();
      await tester.pump();
      expect(find.text('No verified offline access.'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    },
  );
}
