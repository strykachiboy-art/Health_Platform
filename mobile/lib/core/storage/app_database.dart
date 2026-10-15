import 'package:drift/drift.dart';
import 'package:drift_flutter/drift_flutter.dart';

import 'app_database.steps.dart';

part 'app_database.g.dart';

/// Non-sensitive, client-local application metadata.
///
/// Never store authentication secrets or clinical records in this table.
/// Credentials belong in secure storage; the backend remains authoritative
/// for clinical and financial records.
class AppMetadata extends Table {
  TextColumn get key => text()();

  TextColumn get value => text()();

  DateTimeColumn get updatedAt => dateTime().nullable()();

  @override
  Set<Column> get primaryKey => {key};
}

/// Metadata for one logical operation awaiting or undergoing synchronization.
///
/// This table deliberately excludes request and response payloads.
/// Do not store credentials or clinical or financial records here.
class OperationQueueEntry extends Table {
  TextColumn get operationId => text()();

  TextColumn get operationType => text()();

  TextColumn get idempotencyKey => text().unique()();

  /// Owner at queue creation; delivery must validate against the active session.
  /// Nullable only for metadata-only entries, which must not be replayed.
  TextColumn get ownerUserId => text().nullable()();

  TextColumn get clinicContextId => text().nullable()();

  TextColumn get lifecycleState =>
      text().named('lifecycle_state').withDefault(const Constant('queued'))();

  IntColumn get attemptsCompleted => integer().withDefault(const Constant(0))();

  DateTimeColumn get createdAt => dateTime()();

  DateTimeColumn get updatedAt => dateTime()();

  DateTimeColumn get nextAttemptAt => dateTime().nullable()();

  /// A classified failure category only, never a raw server-error message.
  TextColumn get lastFailureCategory => text().nullable()();

  @override
  Set<Column> get primaryKey => {operationId};
}

/// Encrypted request body for a queued operation.
/// Never store plaintext payloads or encryption keys in this table.
class OperationQueuePayload extends Table {
  TextColumn get operationId => text().references(
    OperationQueueEntry,
    #operationId,
    onDelete: KeyAction.cascade,
  )();

  BlobColumn get ciphertext => blob()();

  BlobColumn get nonce => blob()();

  BlobColumn get mac => blob()();

  TextColumn get encryptionScheme => text().named('encryption_scheme')();

  IntColumn get encryptionKeyVersion =>
      integer().named('encryption_key_version')();

  @override
  Set<Column> get primaryKey => {operationId};
}

@DriftDatabase(
  tables: [AppMetadata, OperationQueueEntry, OperationQueuePayload],
)
class AppDatabase extends _$AppDatabase {
  AppDatabase([QueryExecutor? executor]) : super(executor ?? _openConnection());

  @override
  int get schemaVersion => 3;

  @override
  MigrationStrategy get migration => MigrationStrategy(
    onCreate: (migrator) async {
      await migrator.createAll();
    },
    onUpgrade: (migrator, from, to) async {
      await transaction(
        () => stepByStep(
          from1To2: (migrator, schema) async {
            await migrator.addColumn(
              schema.appMetadata,
              schema.appMetadata.updatedAt,
            );
          },
          from2To3: (migrator, schema) async {
            await migrator.createTable(schema.operationQueueEntry);
            await migrator.createTable(schema.operationQueuePayload);
          },
        )(migrator, from, to),
      );
    },
    beforeOpen: (_) async {
      await customStatement('PRAGMA foreign_keys = ON');
    },
  );

  static QueryExecutor _openConnection() {
    return driftDatabase(name: 'clinic_system_local');
  }
}
