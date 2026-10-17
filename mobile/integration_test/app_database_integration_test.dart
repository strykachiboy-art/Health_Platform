import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import 'package:clinic_system_pro/core/storage/app_database.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets(
    'default Drift connection persists metadata across close and reopen',
    (_) async {
      final key =
          '__clinic_system_db_integration_${DateTime.now().microsecondsSinceEpoch}';

      AppDatabase? activeDatabase;

      addTearDown(() async {
        final database = activeDatabase ?? AppDatabase();

        try {
          await (database.delete(
            database.appMetadata,
          )..where((row) => row.key.equals(key))).go();
        } finally {
          await database.close();
        }
      });

      final firstDatabase = AppDatabase();
      activeDatabase = firstDatabase;

      expect(firstDatabase.schemaVersion, 3);

      await firstDatabase
          .into(firstDatabase.appMetadata)
          .insert(
            AppMetadataCompanion.insert(
              key: key,
              value: 'storage-integration-value',
            ),
          );

      final initialRows = await firstDatabase
          .select(firstDatabase.appMetadata)
          .get();
      final initialMatches = initialRows
          .where((row) => row.key == key)
          .toList();

      expect(initialMatches, hasLength(1));
      expect(initialMatches.single.value, 'storage-integration-value');

      await firstDatabase.close();
      activeDatabase = null;

      final reopenedDatabase = AppDatabase();
      activeDatabase = reopenedDatabase;

      expect(reopenedDatabase.schemaVersion, 3);

      final reopenedRows = await reopenedDatabase
          .select(reopenedDatabase.appMetadata)
          .get();
      final reopenedMatches = reopenedRows
          .where((row) => row.key == key)
          .toList();

      expect(reopenedMatches, hasLength(1));
      expect(reopenedMatches.single.value, 'storage-integration-value');
    },
  );
}
