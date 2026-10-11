import 'package:flutter/foundation.dart'
    show TargetPlatform, defaultTargetPlatform;
import 'package:google_sign_in/google_sign_in.dart';

/// Public OAuth client IDs supplied through build-time configuration.
class GoogleSignInConfiguration {
  const GoogleSignInConfiguration({
    required this.iosClientId,
    required this.serverClientId,
  });

  factory GoogleSignInConfiguration.fromEnvironment() =>
      const GoogleSignInConfiguration(
        iosClientId: String.fromEnvironment('GOOGLE_IOS_CLIENT_ID'),
        serverClientId: String.fromEnvironment('GOOGLE_SERVER_CLIENT_ID'),
      );

  final String iosClientId;
  final String serverClientId;

  void validateFor(TargetPlatform platform) {
    if (platform != TargetPlatform.android && platform != TargetPlatform.iOS) {
      throw UnsupportedError(
        'Native Google Sign-In is supported only on Android and iOS.',
      );
    }

    _requireClientId(serverClientId, 'GOOGLE_SERVER_CLIENT_ID');

    if (platform == TargetPlatform.iOS) {
      _requireClientId(iosClientId, 'GOOGLE_IOS_CLIENT_ID');
    }
  }

  void _requireClientId(String value, String settingName) {
    if (value.isEmpty || value.trim() != value) {
      throw StateError('Google Sign-In configuration requires $settingName.');
    }
  }
}

/// Injectable boundary around the native Google Sign-In SDK.
abstract interface class GoogleSignInGateway {
  Future<void> initialize({String? clientId, required String serverClientId});

  bool supportsAuthenticate();

  Future<String?> authenticateForIdToken();
}

/// Adapter for the google_sign_in 7.x singleton API.
class GoogleSignInPluginGateway implements GoogleSignInGateway {
  GoogleSignInPluginGateway({GoogleSignIn? googleSignIn})
    : _googleSignIn = googleSignIn ?? GoogleSignIn.instance;

  final GoogleSignIn _googleSignIn;

  @override
  Future<void> initialize({String? clientId, required String serverClientId}) {
    return _googleSignIn.initialize(
      clientId: clientId,
      serverClientId: serverClientId,
    );
  }

  @override
  bool supportsAuthenticate() => _googleSignIn.supportsAuthenticate();

  @override
  Future<String?> authenticateForIdToken() async {
    final account = await _googleSignIn.authenticate();
    return account.authentication.idToken;
  }
}

/// Acquires an ID token only. Backend login and app-session persistence
/// remain separate responsibilities.
class GoogleSignInProvider {
  GoogleSignInProvider({
    required this.configuration,
    GoogleSignInGateway? gateway,
    TargetPlatform Function()? platform,
  }) : _gateway = gateway ?? GoogleSignInPluginGateway(),
       _platform = platform ?? (() => defaultTargetPlatform);

  final GoogleSignInConfiguration configuration;
  final GoogleSignInGateway _gateway;
  final TargetPlatform Function() _platform;

  bool _initialized = false;
  Future<void>? _initializationInFlight;
  Future<String?>? _authenticationInFlight;

  /// Initializes once. Concurrent callers share the same operation.
  Future<void> initialize() {
    if (_initialized) return Future<void>.value();

    final active = _initializationInFlight;
    if (active != null) return active;

    late final Future<void> operation;
    operation = _initialize().whenComplete(() {
      if (identical(_initializationInFlight, operation)) {
        _initializationInFlight = null;
      }
    });

    _initializationInFlight = operation;
    return operation;
  }

  Future<void> _initialize() async {
    final platform = _platform();
    configuration.validateFor(platform);

    await _gateway.initialize(
      clientId: platform == TargetPlatform.iOS
          ? configuration.iosClientId
          : null,
      serverClientId: configuration.serverClientId,
    );

    _initialized = true;
  }

  /// Returns null on an explicitly reported cancellation.
  /// SDK/configuration errors and invalid ID tokens propagate as failures.
  Future<String?> signInForIdToken() {
    final active = _authenticationInFlight;
    if (active != null) return active;

    late final Future<String?> operation;
    operation = _authenticate().whenComplete(() {
      if (identical(_authenticationInFlight, operation)) {
        _authenticationInFlight = null;
      }
    });

    _authenticationInFlight = operation;
    return operation;
  }

  Future<String?> _authenticate() async {
    await initialize();

    if (!_gateway.supportsAuthenticate()) {
      throw UnsupportedError(
        'Interactive Google Sign-In is unsupported on this platform.',
      );
    }

    final String? idToken;
    try {
      idToken = await _gateway.authenticateForIdToken();
    } on GoogleSignInException catch (error) {
      if (error.code == GoogleSignInExceptionCode.canceled) {
        return null;
      }
      rethrow;
    }

    if (idToken == null ||
        idToken.isEmpty ||
        idToken.trim() != idToken ||
        idToken.length > 8192) {
      throw StateError(
        'Google Sign-In did not return a valid ID-token representation.',
      );
    }

    return idToken;
  }
}
