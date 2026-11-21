import 'dart:io';

import 'package:cryptography/cryptography.dart';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';

import '../config/backend_config.dart';

/// A test-release-only trust boundary for the isolated WD07-B Old PC endpoint.
///
/// This never adds a root to Windows, never disables verification, and cannot
/// be enabled accidentally for a different API origin. The public test
/// certificate must also match the SHA-256 compiled into this test build.
const wd07bExpectedOrigin = 'https://10.42.238.29:19454';
const wd07bPublicCertName = 'WD07B_S04C_PUBLIC_TLS_CERT.pem';

Future<void> verifyWd07bCertificate({
  required Uri origin,
  required List<int> certificateBytes,
  required String expectedSha256,
}) async {
  if (origin.toString() != wd07bExpectedOrigin) {
    throw StateError('WD07-B TLS is restricted to the isolated HTTPS origin.');
  }
  if (!RegExp(r'^[0-9A-Fa-f]{64}$').hasMatch(expectedSha256)) {
    throw StateError('WD07-B TLS requires a SHA-256 certificate lock.');
  }
  if (certificateBytes.isEmpty || certificateBytes.length > 32768) {
    throw StateError('Invalid WD07-B public certificate file size.');
  }
  final hash = await Sha256().hash(certificateBytes);
  final actual = hash.bytes
      .map((value) => value.toRadixString(16).padLeft(2, '0'))
      .join()
      .toUpperCase();
  if (actual != expectedSha256.toUpperCase()) {
    throw StateError('WD07-B public certificate SHA-256 mismatch.');
  }
}

/// Returns null in every ordinary build. Only the explicitly enabled Windows
/// test build creates this client. This HTTP client still verifies TLS
/// certificate chains and the backend IP address's subjectAltName.
Future<http.Client?> createWd07bWindowsTlsClient(
  BackendConfig config, {
  String? certificateFilePath,
}) async {
  const enabled = bool.fromEnvironment(
    'CLINIC_WINDOWS_WD07B_TLS',
    defaultValue: false,
  );
  if (!enabled) return null;
  if (!Platform.isWindows) {
    throw StateError('WD07-B test TLS client is supported only on Windows.');
  }
  const expectedSha256 = String.fromEnvironment(
    'CLINIC_WINDOWS_WD07B_CERT_SHA256',
  );
  final certificatePath =
      certificateFilePath ??
      '${File(Platform.resolvedExecutable).parent.path}'
          '${Platform.pathSeparator}$wd07bPublicCertName';
  final certificateBytes = await File(certificatePath).readAsBytes();
  await verifyWd07bCertificate(
    origin: config.baseUri,
    certificateBytes: certificateBytes,
    expectedSha256: expectedSha256,
  );
  final context = SecurityContext(withTrustedRoots: false)
    ..setTrustedCertificatesBytes(certificateBytes);
  return IOClient(HttpClient(context: context));
}
