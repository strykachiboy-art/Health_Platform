import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/sync/operation_lifecycle.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';

class _MemorySecureStorage implements SecureKeyValueStorage {
  final Map<String, String> values = <String, String>{};
  int reads = 0;
  int writes = 0;
  int deletes = 0;

  @override
  Future<String?> read({required String key}) async {
    reads++;
    return values[key];
  }

  @override
  Future<void> write({required String key, required String value}) async {
    writes++;
    values[key] = value;
  }

  @override
  Future<void> delete({required String key}) async {
    deletes++;
    values.remove(key);
  }
}

OperationPayloadContext _context({
  String userId = 'user-1',
  String clinicContextId = 'clinic-1',
}) {
  return OperationPayloadContext(
    userId: userId,
    clinicContextId: clinicContextId,
  );
}

OperationIdentity _identity({
  String operationId = 'operation-1',
  String operationType = 'appointment.create',
  String idempotencyKey = 'stable-key-1',
}) {
  return OperationIdentity(
    operationId: operationId,
    operationType: operationType,
    idempotencyKey: idempotencyKey,
  );
}

Map<String, dynamic> _payload() => <String, dynamic>{
  'patient_reference': 'patient-123',
  'notes': 'private clinical content',
  'items': <Object>['item-a', 'item-b'],
};

EncryptedOperationPayload _copy(
  EncryptedOperationPayload source, {
  String? scheme,
  int? keyVersion,
  List<int>? nonce,
  List<int>? ciphertext,
  List<int>? mac,
}) {
  return EncryptedOperationPayload(
    scheme: scheme ?? source.scheme,
    keyVersion: keyVersion ?? source.keyVersion,
    nonce: nonce ?? source.nonce,
    ciphertext: ciphertext ?? source.ciphertext,
    mac: mac ?? source.mac,
  );
}

void main() {
  group('OperationPayloadCrypto', () {
    test('encrypts and decrypts payload with authenticated metadata', () async {
      final storage = _MemorySecureStorage();
      final crypto = OperationPayloadCrypto(secureStorage: storage);
      final payload = _payload();

      final encrypted = await crypto.encrypt(
        identity: _identity(),
        context: _context(),
        payload: payload,
      );

      expect(encrypted.scheme, OperationPayloadCrypto.encryptionScheme);
      expect(encrypted.keyVersion, 1);
      expect(encrypted.nonce, hasLength(12));
      expect(encrypted.mac, hasLength(16));
      expect(encrypted.ciphertext, isNotEmpty);
      expect(storage.writes, 1);
      expect(storage.values.length, 1);

      final decrypted = await crypto.decrypt(
        identity: _identity(),
        context: _context(),
        encrypted: encrypted,
      );
      expect(decrypted, payload);
    });

    test('uses fresh nonces for repeated encryption', () async {
      final crypto = OperationPayloadCrypto(
        secureStorage: _MemorySecureStorage(),
      );

      final first = await crypto.encrypt(
        identity: _identity(),
        context: _context(),
        payload: _payload(),
      );
      final second = await crypto.encrypt(
        identity: _identity(),
        context: _context(),
        payload: _payload(),
      );

      expect(first.nonce, isNot(equals(second.nonce)));
    });

    test('binds decryption to the operation identity', () async {
      final crypto = OperationPayloadCrypto(
        secureStorage: _MemorySecureStorage(),
      );
      final encrypted = await crypto.encrypt(
        identity: _identity(),
        context: _context(),
        payload: _payload(),
      );

      await expectLater(
        crypto.decrypt(
          identity: _identity(operationId: 'operation-other'),
          context: _context(),
          encrypted: encrypted,
        ),
        throwsA(isA<OperationPayloadAuthenticationException>()),
      );
      await expectLater(
        crypto.decrypt(
          identity: _identity(operationType: 'billing.create'),
          context: _context(),
          encrypted: encrypted,
        ),
        throwsA(isA<OperationPayloadAuthenticationException>()),
      );
      await expectLater(
        crypto.decrypt(
          identity: _identity(idempotencyKey: 'different-key'),
          context: _context(),
          encrypted: encrypted,
        ),
        throwsA(isA<OperationPayloadAuthenticationException>()),
      );
    });

    test('binds decryption to user and clinic context', () async {
      final crypto = OperationPayloadCrypto(
        secureStorage: _MemorySecureStorage(),
      );
      final identity = _identity();
      final encrypted = await crypto.encrypt(
        identity: identity,
        context: _context(),
        payload: _payload(),
      );

      // Create another context's key as well, so this tests authenticated
      // context mismatch rather than only the missing-key path.
      await crypto.encrypt(
        identity: identity,
        context: _context(clinicContextId: 'clinic-2'),
        payload: _payload(),
      );

      await expectLater(
        crypto.decrypt(
          identity: identity,
          context: _context(clinicContextId: 'clinic-2'),
          encrypted: encrypted,
        ),
        throwsA(isA<OperationPayloadAuthenticationException>()),
      );
      await expectLater(
        crypto.decrypt(
          identity: identity,
          context: _context(userId: 'user-2'),
          encrypted: encrypted,
        ),
        throwsA(isA<OperationPayloadKeyUnavailableException>()),
      );
    });

    test('rejects ciphertext tampering', () async {
      final crypto = OperationPayloadCrypto(
        secureStorage: _MemorySecureStorage(),
      );
      final encrypted = await crypto.encrypt(
        identity: _identity(),
        context: _context(),
        payload: _payload(),
      );

      final changed = List<int>.from(encrypted.ciphertext);
      changed[0] = changed[0] ^ 1;

      await expectLater(
        crypto.decrypt(
          identity: _identity(),
          context: _context(),
          encrypted: _copy(encrypted, ciphertext: changed),
        ),
        throwsA(isA<OperationPayloadAuthenticationException>()),
      );
    });

    test('rejects MAC tampering', () async {
      final crypto = OperationPayloadCrypto(
        secureStorage: _MemorySecureStorage(),
      );
      final encrypted = await crypto.encrypt(
        identity: _identity(),
        context: _context(),
        payload: _payload(),
      );

      final changed = List<int>.from(encrypted.mac);
      changed[0] = changed[0] ^ 1;

      await expectLater(
        crypto.decrypt(
          identity: _identity(),
          context: _context(),
          encrypted: _copy(encrypted, mac: changed),
        ),
        throwsA(isA<OperationPayloadAuthenticationException>()),
      );
    });

    test('rejects nonce tampering', () async {
      final crypto = OperationPayloadCrypto(
        secureStorage: _MemorySecureStorage(),
      );
      final encrypted = await crypto.encrypt(
        identity: _identity(),
        context: _context(),
        payload: _payload(),
      );

      final changed = List<int>.from(encrypted.nonce);
      changed[0] = changed[0] ^ 1;

      await expectLater(
        crypto.decrypt(
          identity: _identity(),
          context: _context(),
          encrypted: _copy(encrypted, nonce: changed),
        ),
        throwsA(isA<OperationPayloadAuthenticationException>()),
      );
    });

    test('decryption never creates a missing key', () async {
      final sourceStorage = _MemorySecureStorage();
      final sourceCrypto = OperationPayloadCrypto(secureStorage: sourceStorage);
      final encrypted = await sourceCrypto.encrypt(
        identity: _identity(),
        context: _context(),
        payload: _payload(),
      );

      final emptyStorage = _MemorySecureStorage();
      final emptyCrypto = OperationPayloadCrypto(secureStorage: emptyStorage);

      await expectLater(
        emptyCrypto.decrypt(
          identity: _identity(),
          context: _context(),
          encrypted: encrypted,
        ),
        throwsA(isA<OperationPayloadKeyUnavailableException>()),
      );
      expect(emptyStorage.writes, 0);
      expect(emptyStorage.values, isEmpty);
    });

    test('rejects malformed stored keys without replacing them', () async {
      final storage = _MemorySecureStorage();
      final crypto = OperationPayloadCrypto(secureStorage: storage);
      await crypto.encrypt(
        identity: _identity(),
        context: _context(),
        payload: _payload(),
      );

      final keyName = storage.values.keys.single;
      storage.values[keyName] = 'not-a-valid-key';
      final writeCount = storage.writes;

      await expectLater(
        crypto.encrypt(
          identity: _identity(operationId: 'operation-2'),
          context: _context(),
          payload: _payload(),
        ),
        throwsA(isA<OperationPayloadKeyException>()),
      );

      expect(storage.writes, writeCount);
      expect(storage.values[keyName], 'not-a-valid-key');
    });

    test('rejects unsupported encryption scheme and key version', () async {
      final crypto = OperationPayloadCrypto(
        secureStorage: _MemorySecureStorage(),
      );
      final encrypted = await crypto.encrypt(
        identity: _identity(),
        context: _context(),
        payload: _payload(),
      );

      await expectLater(
        crypto.decrypt(
          identity: _identity(),
          context: _context(),
          encrypted: _copy(encrypted, scheme: 'unknown'),
        ),
        throwsA(isA<OperationPayloadFormatException>()),
      );
      await expectLater(
        crypto.decrypt(
          identity: _identity(),
          context: _context(),
          encrypted: _copy(encrypted, keyVersion: 2),
        ),
        throwsA(isA<OperationPayloadFormatException>()),
      );
    });

    test('rejects credential fields, including nested fields', () async {
      final storage = _MemorySecureStorage();
      final crypto = OperationPayloadCrypto(secureStorage: storage);

      for (final badPayload in <Map<String, dynamic>>[
        <String, dynamic>{'access_token': 'secret'},
        <String, dynamic>{
          'request': <String, dynamic>{'refreshToken': 'secret'},
        },
        <String, dynamic>{
          'headers': <String, dynamic>{'Authorization': 'Bearer secret'},
        },
      ]) {
        await expectLater(
          crypto.encrypt(
            identity: _identity(),
            context: _context(),
            payload: badPayload,
          ),
          throwsA(isA<OperationPayloadValidationException>()),
        );
      }

      expect(storage.writes, 0);
    });

    test('rejects payloads exceeding the configured size limit', () async {
      final storage = _MemorySecureStorage();
      final crypto = OperationPayloadCrypto(secureStorage: storage);

      await expectLater(
        crypto.encrypt(
          identity: _identity(),
          context: _context(),
          payload: <String, dynamic>{
            'large_field':
                'x' * (OperationPayloadCrypto.maximumPayloadBytes + 1),
          },
        ),
        throwsA(isA<OperationPayloadValidationException>()),
      );

      expect(storage.writes, 0);
    });

    test('rejects invalid context identifiers', () {
      expect(
        () => OperationPayloadContext(userId: ' ', clinicContextId: 'clinic-1'),
        throwsArgumentError,
      );
      expect(
        () => OperationPayloadContext(
          userId: 'user-1',
          clinicContextId: 'c' * 129,
        ),
        throwsArgumentError,
      );
    });

    test('concurrent first encryptions use one stable context key', () async {
      final storage = _MemorySecureStorage();
      final crypto = OperationPayloadCrypto(secureStorage: storage);

      final encryptedItems = await Future.wait(
        List<Future<EncryptedOperationPayload>>.generate(8, (index) {
          return crypto.encrypt(
            identity: _identity(
              operationId: 'operation-$index',
              idempotencyKey: 'key-$index',
            ),
            context: _context(),
            payload: <String, dynamic>{'sequence': index},
          );
        }),
      );

      expect(storage.writes, 1);
      expect(storage.values.length, 1);

      for (var index = 0; index < encryptedItems.length; index++) {
        expect(
          await crypto.decrypt(
            identity: _identity(
              operationId: 'operation-$index',
              idempotencyKey: 'key-$index',
            ),
            context: _context(),
            encrypted: encryptedItems[index],
          ),
          <String, dynamic>{'sequence': index},
        );
      }
    });
  });
}
