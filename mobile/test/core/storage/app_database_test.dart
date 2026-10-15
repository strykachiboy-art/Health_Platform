import 'dart:io';

import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/storage/app_database.dart';

void main() {
  group('AppDatabase', () {
    test('declares schema version 3 and initializes queue metadata tables', () async {
      final database = AppDatabase(NativeDatabase.memory());
      addTearDown(database.close);

      expect(database.schemaVersion, 3);
      expect(await database.select(database.appMetadata).get(), isEmpty);
      expect(await database.select(database.operationQueueEntry).get(), isEmpty);
    });

    test('writes and reads non-sensitive metadata', () async {
      final database = AppDatabase(NativeDatabase.memory());
      addTearDown(database.close);

      await database
          .into(database.appMetadata)
          .insert(AppMetadataCompanion.insert(key: 'app.locale', value: 'en'));

      final rows = await database.select(database.appMetadata).get();

      expect(rows, hasLength(1));
      expect(rows.single.key, 'app.locale');
      expect(rows.single.value, 'en');
    });

    test(
      'preserves metadata after closing and reopening the file database',
      () async {
        final directory = await Directory.systemTemp.createTemp(
          'clinic_system_database_test_',
        );

        try {
          final file = File(
            '${directory.path}${Platform.pathSeparator}clinic.sqlite',
          );

          final firstDatabase = AppDatabase(NativeDatabase(file));

          try {
            await firstDatabase
                .into(firstDatabase.appMetadata)
                .insert(
                  AppMetadataCompanion.insert(key: 'app.theme', value: 'dark'),
                );
          } finally {
            await firstDatabase.close();
          }

          final reopenedDatabase = AppDatabase(NativeDatabase(file));

          try {
            final rows = await reopenedDatabase
                .select(reopenedDatabase.appMetadata)
                .get();

            expect(rows, hasLength(1));
            expect(rows.single.key, 'app.theme');
            expect(rows.single.value, 'dark');
          } finally {
            await reopenedDatabase.close();
          }
        } finally {
          await directory.delete(recursive: true);
        }
      },
    );
  });
}
