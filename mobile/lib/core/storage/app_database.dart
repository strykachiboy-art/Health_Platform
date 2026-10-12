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

@DriftDatabase(tables: [AppMetadata])
class AppDatabase extends _$AppDatabase {
  AppDatabase([QueryExecutor? executor]) : super(executor ?? _openConnection());

  @override
  int get schemaVersion => 2;

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
