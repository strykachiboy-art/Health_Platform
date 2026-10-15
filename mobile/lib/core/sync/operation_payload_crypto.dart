import 'dart:convert';

import 'package:cryptography/cryptography.dart';

import '../security/secure_key_value_storage.dart';
import 'operation_lifecycle.dart';

/// Authenticated owner and clinic context for one offline operation.
///
/// These values are checked against the active authenticated session by the
/// caller. Context stored with an operation is never itself an authorization
/// grant.
class OperationPayloadContext {
  OperationPayloadContext({
    required String userId,
    required String clinicContextId,
  }) : userId = _requiredContextValue(userId, 'userId'),
       clinicContextId = _requiredContextValue(
         clinicContextId,
         'clinicContextId',
       );

  final String userId;
  final String clinicContextId;

  static String _requiredContextValue(String value, String name) {
    final normalized = value.trim();
    if (normalized.isEmpty || normalized.length > 128) {
      throw ArgumentError.value(
        value,
        name,
        'Must contain between 1 and 128 characters.',
      );
    }
    return normalized;
  }
}

/// Encrypted request payload for local persistence.
///
/// Persist [nonce], [ciphertext], and [mac] as binary columns. Never persist
/// plaintext payloads or encryption keys in the queue database.
class EncryptedOperationPayload {
  EncryptedOperationPayload({
    required this.scheme,
    required this.keyVersion,
    required List<int> nonce,
    required List<int> ciphertext,
    required List<int> mac,
  }) : nonce = List<int>.unmodifiable(nonce),
       ciphertext = List<int>.unmodifiable(ciphertext),
       mac = List<int>.unmodifiable(mac);

  final String scheme;
  final int keyVersion;
  final List<int> nonce;
  final List<int> ciphertext;
  final List<int> mac;
}

class OperationPayloadValidationException implements Exception {
  const OperationPayloadValidationException(this.message);
  final String message;

  @override
  String toString() => message;
}

class OperationPayloadFormatException implements Exception {
  const OperationPayloadFormatException(this.message);
  final String message;

  @override
  String toString() => message;
}

class OperationPayloadKeyUnavailableException implements Exception {
  const OperationPayloadKeyUnavailableException();

  @override
  String toString() => 'The encryption key for this context is unavailable.';
}

class OperationPayloadKeyException implements Exception {
  const OperationPayloadKeyException();

  @override
  String toString() =>
      'The encryption key is malformed or has an unsupported length.';
}

class OperationPayloadAuthenticationException implements Exception {
  const OperationPayloadAuthenticationException();

  @override
  String toString() => 'The encrypted operation payload failed authentication.';
}

/// AES-256-GCM codec for locally persisted offline request payloads.
///
/// Keys are generated in secure random storage and stored only through
/// [SecureKeyValueStorage]. Associated data binds ciphertext to the operation
/// identity and the authenticated user/clinic context. Decryption never
/// creates a missing key and never falls back to plaintext.
///
/// Use a single application-scoped instance to coordinate key creation.
class OperationPayloadCrypto {
  factory OperationPayloadCrypto({
    required SecureKeyValueStorage secureStorage,
  }) => OperationPayloadCrypto._(secureStorage);

  OperationPayloadCrypto._(this._secureStorage);

  static const String encryptionScheme = 'aes-256-gcm';
  static const int encryptionKeyVersion = 1;
  static const int maximumPayloadBytes = 64 * 1024;
  static const int _keyBytesLength = 32;
  static const int _nonceBytesLength = 12;
  static const int _macBytesLength = 16;

  static const Set<String> _forbiddenCredentialFields = <String>{
    'access',
    'accesstoken',
    'refreshtoken',
    'authtoken',
    'bearertoken',
    'sessiontoken',
    'token',
    'authorization',
    'authorizationheader',
    'password',
    'passwd',
    'clientsecret',
    'apikey',
    'apisecret',
    'secretkey',
    'privatekey',
    'credential',
    'credentials',
  };

  final SecureKeyValueStorage _secureStorage;
  final AesGcm _algorithm = AesGcm.with256bits();

  /// Shares concurrent first-use key creation for the same context.
  ///
  /// Completed futures are removed; key bytes are not cached for the lifetime
  /// of this object.
  final Map<String, Future<List<int>>> _keyLoads =
      <String, Future<List<int>>>{};

  Future<EncryptedOperationPayload> encrypt({
    required OperationIdentity identity,
    required OperationPayloadContext context,
    required Map<String, dynamic> payload,
  }) async {
    final cleartext = _encodeValidatedPayload(payload);
    final keyName = await _secureKeyName(context);
    final keyBytes = await _loadOrCreateKey(keyName);

    final secretBox = await _algorithm.encrypt(
      cleartext,
      secretKey: SecretKey(keyBytes),
      aad: _associatedData(
        identity: identity,
        context: context,
        scheme: encryptionScheme,
        keyVersion: encryptionKeyVersion,
      ),
    );

    return EncryptedOperationPayload(
      scheme: encryptionScheme,
      keyVersion: encryptionKeyVersion,
      nonce: secretBox.nonce,
      ciphertext: secretBox.cipherText,
      mac: secretBox.mac.bytes,
    );
  }

  /// Decrypts only with an existing key and matching authenticated context.
  ///
  /// The caller must first verify [context] against the current authenticated
  /// session. AAD prevents moving a stored ciphertext to another identity or
  /// user/clinic context without detection.
  Future<Map<String, dynamic>> decrypt({
    required OperationIdentity identity,
    required OperationPayloadContext context,
    required EncryptedOperationPayload encrypted,
  }) async {
    if (encrypted.scheme != encryptionScheme) {
      throw const OperationPayloadFormatException(
        'Unsupported operation payload encryption scheme.',
      );
    }
    if (encrypted.keyVersion != encryptionKeyVersion) {
      throw const OperationPayloadFormatException(
        'Unsupported operation payload key version.',
      );
    }
    if (encrypted.nonce.length != _nonceBytesLength ||
        encrypted.mac.length != _macBytesLength ||
        encrypted.ciphertext.isEmpty ||
        encrypted.ciphertext.length > maximumPayloadBytes) {
      throw const OperationPayloadFormatException(
        'Invalid encrypted operation payload dimensions.',
      );
    }

    final keyName = await _secureKeyName(context);
    final keyBytes = await _readExistingKey(keyName);

    late final List<int> cleartext;
    try {
      cleartext = await _algorithm.decrypt(
        SecretBox(
          encrypted.ciphertext,
          nonce: encrypted.nonce,
          mac: Mac(encrypted.mac),
        ),
        secretKey: SecretKey(keyBytes),
        aad: _associatedData(
          identity: identity,
          context: context,
          scheme: encrypted.scheme,
          keyVersion: encrypted.keyVersion,
        ),
      );
    } on Object {
      throw const OperationPayloadAuthenticationException();
    }

    late final Object? decoded;
    try {
      decoded = jsonDecode(utf8.decode(cleartext));
    } on Object {
      throw const OperationPayloadAuthenticationException();
    }

    if (decoded is! Map<String, dynamic>) {
      throw const OperationPayloadAuthenticationException();
    }

    // Re-apply payload restrictions even after successful authentication.
    _encodeValidatedPayload(decoded);
    return decoded;
  }

  List<int> _encodeValidatedPayload(Map<String, dynamic> payload) {
    _validateCredentialFields(payload);

    late final List<int> bytes;
    try {
      bytes = utf8.encode(jsonEncode(payload));
    } on Object {
      throw const OperationPayloadValidationException(
        'Operation payload must be JSON-serializable.',
      );
    }

    if (bytes.isEmpty || bytes.length > maximumPayloadBytes) {
      throw const OperationPayloadValidationException(
        'Operation payload exceeds the permitted size.',
      );
    }
    return bytes;
  }

  void _validateCredentialFields(Object? value) {
    if (value is Map) {
      for (final entry in value.entries) {
        if (entry.key is! String) {
          throw const OperationPayloadValidationException(
            'Payload object keys must be strings.',
          );
        }

        final normalizedKey = (entry.key as String).toLowerCase().replaceAll(
          RegExp('[^a-z0-9]'),
          '',
        );

        if (_forbiddenCredentialFields.contains(normalizedKey)) {
          throw const OperationPayloadValidationException(
            'Payload contains a forbidden credential field.',
          );
        }

        _validateCredentialFields(entry.value);
      }
    } else if (value is List) {
      for (final item in value) {
        _validateCredentialFields(item);
      }
    }
  }

  Future<List<int>> _loadOrCreateKey(String keyName) async {
    final existingLoad = _keyLoads[keyName];
    if (existingLoad != null) {
      return List<int>.unmodifiable(await existingLoad);
    }

    final createdLoad = _readOrCreateKeyByName(keyName);
    _keyLoads[keyName] = createdLoad;

    try {
      return List<int>.unmodifiable(await createdLoad);
    } finally {
      if (identical(_keyLoads[keyName], createdLoad)) {
        _keyLoads.remove(keyName);
      }
    }
  }

  Future<List<int>> _readOrCreateKeyByName(String keyName) async {
    final stored = await _secureStorage.read(key: keyName);
    if (stored != null) {
      return _decodeStoredKey(stored);
    }

    final generated = await _algorithm.newSecretKey();
    final bytes = await generated.extractBytes();
    if (bytes.length != _keyBytesLength) {
      throw const OperationPayloadKeyException();
    }

    await _secureStorage.write(
      key: keyName,
      value: base64UrlEncode(bytes).replaceAll('=', ''),
    );

    return List<int>.unmodifiable(bytes);
  }

  Future<List<int>> _readExistingKey(String keyName) async {
    final stored = await _secureStorage.read(key: keyName);
    if (stored == null) {
      throw const OperationPayloadKeyUnavailableException();
    }
    return _decodeStoredKey(stored);
  }

  List<int> _decodeStoredKey(String stored) {
    late final List<int> decoded;
    try {
      decoded = base64Url.decode(base64Url.normalize(stored));
    } on Object {
      throw const OperationPayloadKeyException();
    }

    if (decoded.length != _keyBytesLength) {
      throw const OperationPayloadKeyException();
    }
    return List<int>.unmodifiable(decoded);
  }

  Future<String> _secureKeyName(OperationPayloadContext context) async {
    // Hash the context so raw user and clinic identifiers do not appear in
    // secure-storage key names.
    final contextBytes = utf8.encode(
      jsonEncode(<String>[context.userId, context.clinicContextId]),
    );
    final digest = await Sha256().hash(contextBytes);
    final contextFingerprint = base64UrlEncode(digest.bytes)
        .replaceAll('=', '');

    return 'clinic_system.offline_payload_key.v$encryptionKeyVersion.'
        '$contextFingerprint';
  }

  List<int> _associatedData({
    required OperationIdentity identity,
    required OperationPayloadContext context,
    required String scheme,
    required int keyVersion,
  }) {
    // Explicit insertion order forms the canonical representation used for
    // both encryption and decryption.
    return utf8.encode(
      jsonEncode(<String, Object>{
        'purpose': 'clinic-system-offline-operation-payload',
        'operation_id': identity.operationId,
        'operation_type': identity.operationType,
        'idempotency_key': identity.idempotencyKey,
        'user_id': context.userId,
        'clinic_context_id': context.clinicContextId,
        'encryption_scheme': scheme,
        'encryption_key_version': keyVersion,
      }),
    );
  }
}
