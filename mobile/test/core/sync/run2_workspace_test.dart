import 'dart:convert';

import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/core/sync/operation_lifecycle.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';
import 'package:clinic_system_pro/core/sync/operation_queue_store.dart';
import 'package:clinic_system_pro/core/sync/run2_policy.dart';
import 'package:clinic_system_pro/core/sync/run2_transport.dart';
import 'package:clinic_system_pro/core/sync/run2_workspace.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

const tenant = '12345678-1234-1234-1234-123456789abc';
const otherTenant = '87654321-4321-4321-4321-cba987654321';
final initial = DateTime.utc(2026, 11, 26, 12);

AuthTokenResponse actor({
  String role = 'doctor',
  String? organization = tenant,
  int clinic = 3,
  int user = 7,
  DateTime? expires,
}) => AuthTokenResponse(
  accessToken:
      'header.${base64UrlEncode(utf8.encode(jsonEncode({'exp': (expires ?? initial.add(const Duration(days: 3))).millisecondsSinceEpoch ~/ 1000}))).replaceAll('=', '')}.signature',
  refreshToken: 'test-refresh',
  userId: user,
  role: role,
  clinicContextId: clinic,
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

class _Transport implements Run2Transport {
  int checks = 0;
  int healthChecks = 0;
  int chatChecks = 0;
  bool denied = false;
  bool chatDenied = false;
  final keys = <String>[];
  final types = <String>[];
  final payloads = <Map<String, dynamic>>[];
  Future<void> Function()? afterSend;
  @override
  Future<void> verifyScope(AuthTokenResponse session) async {
    checks++;
    if (denied) {
      throw StateError('denied');
    }
  }

  @override
  Future<bool> checkHealth() async {
    healthChecks++;
    return true;
  }

  @override
  Future<List<Run2ConversationChoice>> listOrdinaryChats(
    AuthTokenResponse session,
  ) async => const [
    Run2ConversationChoice(id: 11, label: 'Synthetic ordinary group'),
  ];
  @override
  Future<void> verifyOrdinaryChat(
    AuthTokenResponse session,
    int conversationId,
  ) async {
    chatChecks++;
    if (chatDenied || conversationId != 11) {
      throw StateError('conversation unavailable');
    }
  }

  @override
  Future<void> deliver(
    AuthTokenResponse session,
    String type,
    Map<String, dynamic> payload,
    String key,
  ) async {
    keys.add(key);
    types.add(type);
    payloads.add(Map.of(payload));
    await afterSend?.call();
  }
}

void main() {
  late AppDatabase db;
  late OperationQueueStore store;
  late SessionNavigationGuard guard;
  late _Transport transport;
  late AuthTokenResponse current;
  late DateTime clock;
  late Duration elapsed;
  late Run2Workspace workspace;
  late int notifications;

  Run2Workspace makeWorkspace() => Run2Workspace(
    store: store,
    guard: guard,
    loadSession: () async => current,
    transport: transport,
    notifyQueued: () {
      notifications++;
    },
    clock: () => clock,
    elapsed: () => elapsed,
  );
  void advance(Duration duration) {
    clock = clock.add(duration);
    elapsed += duration;
  }

  Future<OperationQueueEntryData> row(String type) async =>
      (await store.listScopedOperationPage(
        context: Run2Policy.context(current)!,
        operationType: type,
      )).single;

  setUp(() {
    db = AppDatabase(NativeDatabase.memory());
    store = OperationQueueStore(
      db,
      payloadCrypto: OperationPayloadCrypto(secureStorage: _MemoryStorage()),
    );
    guard = SessionNavigationGuard()..markSessionAvailable();
    transport = _Transport();
    current = actor();
    clock = initial;
    elapsed = Duration.zero;
    notifications = 0;
    workspace = makeWorkspace();
  });
  tearDown(() async {
    guard.dispose();
    await db.close();
  });

  test(
    'all 14 tenant roles have bounded reminder scope and super admin is denied',
    () {
      expect(Run2Policy.roles.length, 14);
      for (final role in Run2Policy.roles) {
        expect(Run2Policy.context(actor(role: role)), isNotNull);
        expect(Run2Policy.domains(role), isNotEmpty);
      }
      expect(Run2Policy.context(actor(role: 'super_admin')), isNull);
      expect(Run2Policy.context(actor(role: 'invented')), isNull);
      expect(Run2Policy.context(actor(organization: null)), isNull);
      expect(Run2Policy.domains('driver'), isNot(contains('blood_bank')));
    },
  );
  test('no lease before online verification; server denial leaves storage inaccessible', () async {
    await expectLater(
      workspace.view(),
      throwsA(isA<Run2WorkspaceUnavailable>()),
    );
    await expectLater(
      workspace.saveReminder('lab', 'Review online'),
      throwsA(isA<Run2WorkspaceUnavailable>()),
    );
    transport.denied = true;
    await expectLater(workspace.refreshOnline(), throwsStateError);
    expect(workspace.leaseValid, isFalse);
    expect(await db.select(db.operationQueueEntry).get(), isEmpty);
  });
  test(
    'verified liveness snapshot and reminder are encrypted and never delivered',
    () async {
      await workspace.refreshOnline();
      await workspace.saveReminder('clinical_orders', 'Review online');
      final view = await workspace.view();
      expect(view.items.map((e) => e.type).toSet(), {
        Run2Policy.snapshot,
        Run2Policy.draft,
      });
      expect(await db.select(db.operationQueuePayload).get(), hasLength(2));
      expect(notifications, 0);
      await workspace.runDelivery();
      expect(transport.keys, isEmpty);
      final reminder = view.items.firstWhere((e) => e.type == Run2Policy.draft);
      await workspace.deleteReminder(reminder.id);
      expect((await workspace.view()).items.map((e) => e.type), [
        Run2Policy.snapshot,
      ]);
    },
  );
  test('no free text, clinical identifiers or arbitrary reminder domains can be persisted', () async {
    await workspace.refreshOnline();
    await expectLater(
      workspace.saveReminder('clinical_orders', 'patient name or note'),
      throwsFormatException,
    );
    await expectLater(
      workspace.saveReminder('invented', 'Review online'),
      throwsFormatException,
    );
    await expectLater(
      workspace.queueFeedback('free text'),
      throwsFormatException,
    );
    expect((await workspace.view()).items, hasLength(1));
  });
  test(
    'expiry uses monotonic and wall time and does not survive a restart',
    () async {
      await workspace.refreshOnline();
      await workspace.saveReminder('lab', 'Review online');
      expect(makeWorkspace().leaseValid, isFalse);
      await expectLater(
        makeWorkspace().view(),
        throwsA(isA<Run2WorkspaceUnavailable>()),
      );
      elapsed = const Duration(minutes: 15);
      expect(workspace.leaseValid, isFalse);
      await expectLater(
        workspace.view(),
        throwsA(isA<Run2WorkspaceUnavailable>()),
      );
      await workspace.refreshOnline();
      expect(workspace.leaseValid, isTrue);
      clock = initial.subtract(const Duration(minutes: 1));
      expect(workspace.leaseValid, isFalse);
    },
  );
  test('JWT expiry is an upper limit on offline authorization', () async {
    current = actor(expires: initial.add(const Duration(minutes: 2)));
    await workspace.refreshOnline();
    advance(const Duration(minutes: 2));
    expect(workspace.leaseValid, isFalse);
    await expectLater(
      workspace.view(),
      throwsA(isA<Run2WorkspaceUnavailable>()),
    );
    await expectLater(
      workspace.refreshOnline(),
      throwsA(isA<Run2WorkspaceUnavailable>()),
    );
  });
  test(
    'same clinic and user cannot access foreign tenant or prior role work',
    () async {
      await workspace.refreshOnline();
      await workspace.saveReminder('lab', 'Review online');
      current = actor(organization: otherTenant);
      await expectLater(
        workspace.view(),
        throwsA(isA<Run2WorkspaceUnavailable>()),
      );
      await workspace.refreshOnline();
      expect(
        (await workspace.view()).items.every(
          (item) => item.type != Run2Policy.draft,
        ),
        isTrue,
      );
      current = actor(role: 'nurse');
      await workspace.refreshOnline();
      expect(
        (await workspace.view()).items.every(
          (item) => item.type != Run2Policy.draft,
        ),
        isTrue,
      );
    },
  );
  test('session revision invalidates local lease', () async {
    await workspace.refreshOnline();
    guard.invalidate();
    expect(workspace.leaseValid, isFalse);
    await expectLater(
      workspace.view(),
      throwsA(isA<Run2WorkspaceUnavailable>()),
    );
  });
  test('fixed product feedback queues and delivers exact original encrypted intent', () async {
    await workspace.refreshOnline();
    await workspace.queueFeedback('navigation');
    final queued = await row(Run2Policy.feedback);
    expect(notifications, 1);
    await expectLater(workspace.queueFeedback('performance'), throwsStateError);
    final result = await workspace.runDelivery();
    expect(result.acknowledged, 1);
    expect(transport.types, [Run2Policy.feedback]);
    expect(transport.keys, [queued.idempotencyKey]);
    expect(transport.payloads.single['preset'], 'navigation');
    expect(
      (await row(Run2Policy.feedback)).lifecycleState,
      OperationLifecycleState.acknowledged.name,
    );
  });
  test('lost response retry preserves key and payload across reconstructed adapter', () async {
    await workspace.refreshOnline();
    await workspace.queueFeedback('performance');
    transport.afterSend = () async {
      throw StateError('lost response');
    };
    final first = await workspace.runDelivery();
    expect(first.retryScheduled, 1);
    advance(const Duration(minutes: 2));
    transport.afterSend = null;
    final second = await makeWorkspace().runDelivery();
    expect(second.acknowledged, 1);
    expect(transport.keys, hasLength(2));
    expect(transport.keys.toSet(), hasLength(1));
    expect(transport.payloads[0], transport.payloads[1]);
  });
  test(
    'old feedback is held for review, never delivered or locally discarded',
    () async {
      await workspace.refreshOnline();
      await workspace.queueFeedback('accessibility');
      advance(const Duration(hours: 25));
      await makeWorkspace().runDelivery();
      expect(transport.keys, isEmpty);
      final held = await row(Run2Policy.feedback);
      expect(
        held.lifecycleState,
        OperationLifecycleState.reconciliationRequired.name,
      );
      await expectLater(
        store.removeRun2LocalOnly(
          operationId: held.operationId,
          context: Run2Policy.context(current)!,
          type: Run2Policy.feedback,
        ),
        throwsStateError,
      );
    },
  );
  test('ordinary chat requires a current live choice and revalidation before delivery', () async {
    await workspace.refreshOnline();
    await expectLater(
      workspace.queueChat(11, 'thanks'),
      throwsA(isA<Run2WorkspaceUnavailable>()),
    );
    expect(await workspace.loadOrdinaryChats(), hasLength(1));
    await expectLater(
      workspace.queueChat(12, 'thanks'),
      throwsA(isA<Run2WorkspaceUnavailable>()),
    );
    await workspace.queueChat(11, 'thanks');
    await workspace.runDelivery();
    expect(transport.chatChecks, 1);
    expect(transport.types, [Run2Policy.chat]);
    expect(transport.payloads.single['conversation_id'], 11);
    expect(
      (await row(Run2Policy.chat)).lifecycleState,
      OperationLifecycleState.acknowledged.name,
    );
  });
  test(
    'closed or inaccessible conversation never receives a queued greeting',
    () async {
      await workspace.refreshOnline();
      await workspace.loadOrdinaryChats();
      await workspace.queueChat(11, 'greeting');
      transport.chatDenied = true;
      await workspace.runDelivery();
      expect(transport.keys, isEmpty);
      expect(
        (await row(Run2Policy.chat)).lifecycleState,
        OperationLifecycleState.reconciliationRequired.name,
      );
    },
  );
  test('chat expires before reconnect without sending', () async {
    await workspace.refreshOnline();
    await workspace.loadOrdinaryChats();
    await workspace.queueChat(11, 'thanks');
    advance(const Duration(minutes: 31));
    await workspace.runDelivery();
    expect(transport.keys, isEmpty);
    expect(
      (await row(Run2Policy.chat)).lifecycleState,
      OperationLifecycleState.reconciliationRequired.name,
    );
  });
  test(
    'role change after send preserves unknown outcome in the original scope',
    () async {
      await workspace.refreshOnline();
      await workspace.queueFeedback('navigation');
      final original = Run2Policy.context(current)!;
      final queued = await row(Run2Policy.feedback);
      transport.afterSend = () async {
        current = actor(role: 'nurse');
      };
      await workspace.runDelivery();
      final held = await store.findByOperationId(queued.operationId);
      expect(
        held?.lifecycleState,
        OperationLifecycleState.reconciliationRequired.name,
      );
      expect(held?.clinicContextId, original.clinicContextId);
      expect(transport.keys, hasLength(1));
      await workspace.runDelivery();
      expect(transport.keys, hasLength(1));
    },
  );
  test('explicit 4xx never automatically retries a preset', () async {
    await workspace.refreshOnline();
    await workspace.queueFeedback('navigation');
    transport.afterSend = () async {
      throw ApiException(
        kind: ApiFailureKind.httpStatus,
        message: 'denied',
        uri: Uri.parse('https://example.invalid'),
        statusCode: 403,
      );
    };
    await workspace.runDelivery();
    expect(
      (await row(Run2Policy.feedback)).lifecycleState,
      OperationLifecycleState.reconciliationRequired.name,
    );
    advance(const Duration(minutes: 2));
    await workspace.runDelivery();
    expect(transport.keys, hasLength(1));
  });
  test(
    'new role scoped data cannot be decrypted by another tenant or role',
    () async {
      await workspace.refreshOnline();
      await workspace.saveReminder('lab', 'Review online');
      final local = await row(Run2Policy.draft);
      await expectLater(
        store.loadPayload(
          operationId: local.operationId,
          context: Run2Policy.context(actor(role: 'nurse'))!,
        ),
        throwsA(anything),
      );
      await expectLater(
        store.loadPayload(
          operationId: local.operationId,
          context: Run2Policy.context(actor(organization: otherTenant))!,
        ),
        throwsA(anything),
      );
    },
  );
  test('arbitrary operations are never accepted by preset catalogue', () {
    for (final type in [
      'appointment.create',
      'blood.transfusion.start',
      'feedback.create',
      'chat.message',
      'medication.administer',
      'financial.payment',
    ]) {
      expect(() => Run2Policy.identity(type), throwsArgumentError);
    }
  });
}
