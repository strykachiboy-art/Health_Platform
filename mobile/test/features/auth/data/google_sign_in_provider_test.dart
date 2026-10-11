import 'dart:async';

import 'package:flutter/foundation.dart' show TargetPlatform;
import 'package:flutter_test/flutter_test.dart';
import 'package:google_sign_in/google_sign_in.dart';

import 'package:clinic_system_pro/features/auth/data/google_sign_in_provider.dart';

const _serverClientId = 'server-client-id.apps.googleusercontent.com';
const _iosClientId = 'ios-client-id.apps.googleusercontent.com';
const _idToken = 'header.payload.signature';

GoogleSignInConfiguration _configuration({
  String iosClientId = _iosClientId,
  String serverClientId = _serverClientId,
}) => GoogleSignInConfiguration(
  iosClientId: iosClientId,
  serverClientId: serverClientId,
);

class _FakeGoogleSignInGateway implements GoogleSignInGateway {
  int initializeCount = 0;
  int authenticateCount = 0;

  String? initializedClientId;
  String? initializedServerClientId;
  bool authenticateSupported = true;
  String? token = _idToken;

  Future<void> Function()? onInitialize;
  Future<String?> Function()? onAuthenticate;

  @override
  Future<void> initialize({
    String? clientId,
    required String serverClientId,
  }) async {
    initializeCount++;
    initializedClientId = clientId;
    initializedServerClientId = serverClientId;

    final handler = onInitialize;
    if (handler != null) await handler();
  }

  @override
  bool supportsAuthenticate() => authenticateSupported;

  @override
  Future<String?> authenticateForIdToken() async {
    authenticateCount++;
    final handler = onAuthenticate;
    if (handler != null) return handler();
    return token;
  }
}

GoogleSignInProvider _provider(
  _FakeGoogleSignInGateway gateway, {
  GoogleSignInConfiguration? configuration,
  TargetPlatform platform = TargetPlatform.android,
}) => GoogleSignInProvider(
  configuration: configuration ?? _configuration(),
  gateway: gateway,
  platform: () => platform,
);

void main() {
  group('GoogleSignInProvider', () {
    test('Android configures server ID and returns the ID token', () async {
      final gateway = _FakeGoogleSignInGateway();
      final provider = _provider(gateway);

      expect(await provider.signInForIdToken(), _idToken);
      expect(gateway.initializeCount, 1);
      expect(gateway.initializedClientId, isNull);
      expect(gateway.initializedServerClientId, _serverClientId);
      expect(gateway.authenticateCount, 1);
    });

    test('iOS configures both client IDs', () async {
      final gateway = _FakeGoogleSignInGateway();
      final provider = _provider(gateway, platform: TargetPlatform.iOS);

      expect(await provider.signInForIdToken(), _idToken);
      expect(gateway.initializedClientId, _iosClientId);
      expect(gateway.initializedServerClientId, _serverClientId);
    });

    test('concurrent initializations share one SDK operation', () async {
      final gateway = _FakeGoogleSignInGateway();
      final started = Completer<void>();
      final release = Completer<void>();

      gateway.onInitialize = () {
        if (!started.isCompleted) started.complete();
        return release.future;
      };

      final provider = _provider(gateway);
      final first = provider.initialize();
      final second = provider.initialize();

      await started.future;
      expect(gateway.initializeCount, 1);

      release.complete();
      await Future.wait([first, second]);
      await provider.initialize();
      expect(gateway.initializeCount, 1);
    });

    test('concurrent sign-ins share one native authentication', () async {
      final gateway = _FakeGoogleSignInGateway();
      final started = Completer<void>();
      final release = Completer<String?>();

      gateway.onAuthenticate = () {
        if (!started.isCompleted) started.complete();
        return release.future;
      };

      final provider = _provider(gateway);
      final first = provider.signInForIdToken();
      final second = provider.signInForIdToken();

      expect(identical(first, second), isTrue);
      await started.future;
      expect(gateway.authenticateCount, 1);

      release.complete(_idToken);
      expect(await first, _idToken);
      expect(await second, _idToken);
    });

    test('missing server ID fails before SDK initialization', () async {
      final gateway = _FakeGoogleSignInGateway();
      final provider = _provider(
        gateway,
        configuration: _configuration(serverClientId: ''),
      );

      await expectLater(provider.initialize(), throwsA(isA<StateError>()));
      expect(gateway.initializeCount, 0);
    });

    test('missing iOS ID fails before SDK initialization', () async {
      final gateway = _FakeGoogleSignInGateway();
      final provider = _provider(
        gateway,
        configuration: _configuration(iosClientId: ''),
        platform: TargetPlatform.iOS,
      );

      await expectLater(provider.initialize(), throwsA(isA<StateError>()));
      expect(gateway.initializeCount, 0);
    });

    test('unsupported platforms fail before SDK initialization', () async {
      final gateway = _FakeGoogleSignInGateway();
      final provider = _provider(gateway, platform: TargetPlatform.linux);

      await expectLater(
        provider.signInForIdToken(),
        throwsA(isA<UnsupportedError>()),
      );

      expect(gateway.initializeCount, 0);
      expect(gateway.authenticateCount, 0);
    });

    test('unsupported interactive authentication fails closed', () async {
      final gateway = _FakeGoogleSignInGateway()..authenticateSupported = false;
      final provider = _provider(gateway);

      await expectLater(
        provider.signInForIdToken(),
        throwsA(isA<UnsupportedError>()),
      );

      expect(gateway.initializeCount, 1);
      expect(gateway.authenticateCount, 0);
    });

    test('cancellation returns null without an ID token', () async {
      final gateway = _FakeGoogleSignInGateway();
      gateway.onAuthenticate = () async {
        throw const GoogleSignInException(
          code: GoogleSignInExceptionCode.canceled,
        );
      };

      expect(await _provider(gateway).signInForIdToken(), isNull);
      expect(gateway.authenticateCount, 1);
    });

    test(
      'missing, empty, untrimmed, and oversized tokens fail closed',
      () async {
        final invalidTokens = <String?>[
          null,
          '',
          ' leading-space',
          'trailing-space ',
          List<String>.filled(8193, 'x').join(),
        ];

        for (final token in invalidTokens) {
          final gateway = _FakeGoogleSignInGateway()..token = token;
          await expectLater(
            _provider(gateway).signInForIdToken(),
            throwsA(isA<StateError>()),
          );
        }
      },
    );

    test('non-cancellation SDK errors propagate', () async {
      final gateway = _FakeGoogleSignInGateway();
      gateway.onAuthenticate = () async {
        throw const GoogleSignInException(
          code: GoogleSignInExceptionCode.clientConfigurationError,
        );
      };

      await expectLater(
        _provider(gateway).signInForIdToken(),
        throwsA(
          isA<GoogleSignInException>().having(
            (error) => error.code,
            'code',
            GoogleSignInExceptionCode.clientConfigurationError,
          ),
        ),
      );
    });
  });
}
