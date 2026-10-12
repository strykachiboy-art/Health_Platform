import 'dart:io';

import 'package:drift/drift.dart' show driftRuntimeOptions;
import 'package:drift/native.dart';
import 'package:drift_dev/api/migrations_native.dart';
import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:flutter_test/flutter_test.dart';

import 'generated/schema.dart';
import 'generated/schema_v1.dart' as v1;
import 'generated/schema_v2.dart' as v2;

class _FailAfterMigrationDatabase extends AppDatabase {
  _FailAfterMigrationDatabase([super.executor]);

  @override
  Future<T> transaction<T>(
    Future<T> Function() action, {
    bool requireNew = false,
  }) {
    return super.transaction(() async {
      await action();
      throw StateError('Injected failure after migration steps.');
    }, requireNew: requireNew);
  }
}

void main() {
  driftRuntimeOptions.dontWarnAboutMultipleDatabases = true;
  late SchemaVerifier verifier;

  setUpAll(() {
    verifier = SchemaVerifier(GeneratedHelper());
  });

  group('simple database migrations', () {
    const versions = GeneratedHelper.versions;

    for (final (i, fromVersion) in versions.indexed) {
      group('from $fromVersion', () {
        for (final toVersion in versions.skip(i + 1)) {
          test('to $toVersion', () async {
            final schema = await verifier.schemaAt(fromVersion);
            final db = AppDatabase(schema.newConnection());

            try {
              await verifier.migrateAndValidate(db, toVersion);
            } finally {
              await db.close();
            }
          });
        }
      });
    }
  });

  test('migration from v1 to v2 preserves metadata', () async {
    const oldAppMetadataData = <v1.AppMetadataData>[
      v1.AppMetadataData(key: 'app.locale', value: 'en'),
    ];

    const expectedNewAppMetadataData = <v2.AppMetadataData>[
      v2.AppMetadataData(key: 'app.locale', value: 'en', updatedAt: null),
    ];

    await verifier.testWithDataIntegrity(
      oldVersion: 1,
      newVersion: 2,
      createOld: v1.DatabaseAtV1.new,
      createNew: v2.DatabaseAtV2.new,
      openTestedDatabase: AppDatabase.new,
      createItems: (batch, oldDb) {
        batch.insertAll(oldDb.appMetadata, oldAppMetadataData);
      },
      validateItems: (newDb) async {
        final rows = await newDb.select(newDb.appMetadata).get();

        expect(rows, expectedNewAppMetadataData);
        expect(rows.single.key, 'app.locale');
        expect(rows.single.value, 'en');
        expect(rows.single.updatedAt, isNull);
      },
    );
  });

  test('failed v1-to-v2 migration rolls back and preserves metadata', () async {
    final directory = await Directory.systemTemp.createTemp(
      'clinic_migration_failure_test_',
    );
    final file = File(
      '${directory.path}${Platform.pathSeparator}clinic.sqlite',
    );

    try {
      final database = _FailAfterMigrationDatabase(
        NativeDatabase(
          file,
          setup: (nativeDatabase) {
            nativeDatabase.execute(
              'CREATE TABLE app_metadata ("key" TEXT NOT NULL PRIMARY KEY, "value" TEXT NOT NULL)',
            );
            nativeDatabase.execute(
              'INSERT INTO app_metadata ("key", "value") VALUES (?, ?)',
              ['app.locale', 'en'],
            );
            nativeDatabase.userVersion = 1;
          },
        ),
      );

      try {
        await expectLater(
          database.select(database.appMetadata).get(),
          throwsA(isA<StateError>()),
        );
      } finally {
        await database.close();
      }

      final inspector = AppDatabase(
        NativeDatabase(file, enableMigrations: false),
      );

      try {
        final rows = await inspector
            .customSelect('SELECT "key", "value" FROM app_metadata')
            .get();

        expect(rows, hasLength(1));
        expect(rows.single.read<String>('key'), 'app.locale');
        expect(rows.single.read<String>('value'), 'en');

        final versionRow = await inspector
            .customSelect('PRAGMA user_version')
            .getSingle();
        expect(versionRow.read<int>('user_version'), 1);

        final columns = await inspector
            .customSelect('PRAGMA table_info(app_metadata)')
            .get();
        expect(
          columns.any((row) => row.read<String>('name') == 'updated_at'),
          isFalse,
        );
      } finally {
        await inspector.close();
      }
    } finally {
      await directory.delete(recursive: true);
    }
  });
}
