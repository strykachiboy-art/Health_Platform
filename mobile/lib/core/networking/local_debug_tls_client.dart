import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart' show kDebugMode;
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';

import '../config/backend_config.dart';

/// Adds the local development CA only for the explicitly
/// configured Android debug test endpoint.
///
/// Does not modify the global trust store or bypass TLS checks.
http.Client? createLocalDebugTlsClient(BackendConfig config) {
  const enabled = bool.fromEnvironment(
    'CLINIC_LOCAL_DEV_TLS',
    defaultValue: false,
  );

  if (!enabled) return null;

  if (!kDebugMode ||
      config.baseUri.toString() != 'https://localhost:55126') {
    throw StateError(
      'Local development CA trust is permitted only in debug '
      'mode for https://localhost:55126.',
    );
  }

  const caBase64 = String.fromEnvironment(
    'CLINIC_LOCAL_DEV_CA_PEM_B64',
  );

  if (caBase64.isEmpty) {
    throw StateError(
      'CLINIC_LOCAL_DEV_CA_PEM_B64 is required for '
      'local development HTTPS.',
    );
  }

  final caBytes = base64Decode(caBase64);

  final context = SecurityContext(withTrustedRoots: true)
    ..setTrustedCertificatesBytes(caBytes);

  return IOClient(HttpClient(context: context));
}