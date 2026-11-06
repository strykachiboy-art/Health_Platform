import 'package:drift/drift.dart' show Value, driftRuntimeOptions;
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/features/status/data/scoped_sync_status_source.dart';

void main() {
  driftRuntimeOptions.dontWarnAboutMultipleDatabases = true;
  late AppDatabase database;
  late ScopedSyncStatusSource source;
  final now = DateTime.utc(2026, 11, 6);

  setUp(() {
    database = AppDatabase(NativeDatabase.memory());
    source = ScopedSyncStatusSource(database: database);
  });
  tearDown(() async => database.close());

  Future<void> addEntry(
    String id,
    String state, {
    String? owner = '44',
    String? clinic = '3',
  }) async {
    await database
        .into(database.operationQueueEntry)
        .insert(
          OperationQueueEntryCompanion.insert(
            operationId: id,
            operationType: 'appointment.create',
            idempotencyKey: 'key-$id',
            createdAt: now,
            updatedAt: now,
            ownerUserId: Value(owner),
            clinicContextId: Value(clinic),
            lifecycleState: Value(state),
          ),
        );
  }

  test('zero is confirmed only by a successful scoped DB query', () async {
    final counts = await source.load(userId: 44, clinicContextId: 3);
    expect(counts.pending, 0);
    expect(counts.needsReview, 0);
  });

  test('includes queue, in-flight, retry and separate review states', () async {
    for (final name in const <String>[
      'queued',
      'inFlight',
      'retryScheduled',
      'reconciliationRequired',
      'userInterventionRequired',
      'acknowledged',
      'rejected',
      'cancelled',
    ]) {
      await addEntry('op-$name', name);
    }
    final counts = await source.load(userId: 44, clinicContextId: 3);
    expect(counts.pending, 3);
    expect(counts.needsReview, 2);
    expect(
      await database.select(database.operationQueueEntry).get(),
      hasLength(8),
    );
  });

  test(
    'never includes another owner or clinic or contextless entries',
    () async {
      await addEntry('own', 'queued');
      await addEntry('other-owner', 'queued', owner: '45');
      await addEntry('other-clinic', 'reconciliationRequired', clinic: '4');
      await addEntry('legacy', 'queued', owner: null, clinic: null);
      final counts = await source.load(userId: 44, clinicContextId: 3);
      expect(counts.pending, 1);
      expect(counts.needsReview, 0);
      final other = await source.load(userId: 45, clinicContextId: 3);
      expect(other.pending, 1);
    },
  );

  test(
    'unknown lifecycle state fails closed instead of reporting zero',
    () async {
      await addEntry('unknown', 'future_server_pending');
      await expectLater(
        source.load(userId: 44, clinicContextId: 3),
        throwsFormatException,
      );
    },
  );

  test('invalid user and clinic IDs are rejected before a query', () async {
    for (final pair in <(int, int)>[(0, 3), (44, 0), (-1, 3), (44, -5)]) {
      await expectLater(
        source.load(userId: pair.$1, clinicContextId: pair.$2),
        throwsArgumentError,
      );
    }
  });
}
