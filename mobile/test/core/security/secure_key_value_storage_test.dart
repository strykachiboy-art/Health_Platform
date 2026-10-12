import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_secure_storage/test/test_flutter_secure_storage_platform.dart';
import 'package:flutter_secure_storage_platform_interface/flutter_secure_storage_platform_interface.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';

class _FailureInjectingPlatform extends TestFlutterSecureStoragePlatform {
  _FailureInjectingPlatform(
    super.data, {
    this.readFailure,
    this.writeFailure,
    this.deleteFailure,
  });

  final Object? readFailure;
  final Object? writeFailure;
  final Object? deleteFailure;

  @override
  Future<String?> read({
    required String key,
    required Map<String, String> options,
  }) async {
    if (readFailure != null) {
      throw readFailure!;
    }
    return super.read(key: key, options: options);
  }

  @override
  Future<void> write({
    required String key,
    required String value,
    required Map<String, String> options,
  }) async {
    if (writeFailure != null) {
      throw writeFailure!;
    }
    await super.write(key: key, value: value, options: options);
  }

  @override
  Future<void> delete({
    required String key,
    required Map<String, String> options,
  }) async {
    if (deleteFailure != null) {
      throw deleteFailure!;
    }
    await super.delete(key: key, options: options);
  }
}

void main() {
  setUp(() {
    FlutterSecureStorage.setMockInitialValues(<String, String>{});
  });

  group('FlutterSecureKeyValueStorage', () {
    test('returns null for a key that does not exist', () async {
      final storage = FlutterSecureKeyValueStorage();

      expect(await storage.read(key: 'access_token'), isNull);
    });

    test('writes and reads a stored value', () async {
      final storage = FlutterSecureKeyValueStorage();

      await storage.write(key: 'access_token', value: 'test-token');

      expect(await storage.read(key: 'access_token'), 'test-token');
    });

    test('overwrites the value for an existing key', () async {
      final storage = FlutterSecureKeyValueStorage();

      await storage.write(key: 'access_token', value: 'old-token');
      await storage.write(key: 'access_token', value: 'new-token');

      expect(await storage.read(key: 'access_token'), 'new-token');
    });

    test('deletes only the requested key', () async {
      final storage = FlutterSecureKeyValueStorage();

      await storage.write(key: 'access_token', value: 'access');
      await storage.write(key: 'refresh_token', value: 'refresh');

      await storage.delete(key: 'access_token');

      expect(await storage.read(key: 'access_token'), isNull);
      expect(await storage.read(key: 'refresh_token'), 'refresh');
    });
    test(
      'propagates platform read failures and preserves stored data',
      () async {
        final values = <String, String>{'access_token': 'preserved'};
        final failure = StateError('Injected secure-storage read failure.');
        FlutterSecureStoragePlatform.instance = _FailureInjectingPlatform(
          values,
          readFailure: failure,
        );
        final storage = FlutterSecureKeyValueStorage();

        await expectLater(
          storage.read(key: 'access_token'),
          throwsA(same(failure)),
        );
        expect(values['access_token'], 'preserved');
      },
    );

    test(
      'propagates platform write failures without reporting success',
      () async {
        final values = <String, String>{};
        final failure = StateError('Injected secure-storage write failure.');
        FlutterSecureStoragePlatform.instance = _FailureInjectingPlatform(
          values,
          writeFailure: failure,
        );
        final storage = FlutterSecureKeyValueStorage();

        await expectLater(
          storage.write(key: 'access_token', value: 'secret'),
          throwsA(same(failure)),
        );
        expect(values, isEmpty);
      },
    );

    test(
      'propagates platform delete failures and preserves stored data',
      () async {
        final values = <String, String>{'access_token': 'preserved'};
        final failure = StateError('Injected secure-storage delete failure.');
        FlutterSecureStoragePlatform.instance = _FailureInjectingPlatform(
          values,
          deleteFailure: failure,
        );
        final storage = FlutterSecureKeyValueStorage();

        await expectLater(
          storage.delete(key: 'access_token'),
          throwsA(same(failure)),
        );
        expect(values['access_token'], 'preserved');
      },
    );
  });
}
