import 'dart:convert';
import 'dart:io';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/windows_wd07b_tls_client.dart';
import 'package:cryptography/cryptography.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test(
    'rejects a different origin even with a valid certificate hash',
    () async {
      final certificateBytes = utf8.encode('synthetic-public-cert');
      final hash = await Sha256().hash(certificateBytes);
      final expected = hash.bytes
          .map((value) => value.toRadixString(16).padLeft(2, '0'))
          .join();
      await expectLater(
        verifyWd07bCertificate(
          origin: Uri.parse('https://example.org'),
          certificateBytes: certificateBytes,
          expectedSha256: expected,
        ),
        throwsStateError,
      );
    },
  );

  test('rejects a wrong SHA-256 pin', () async {
    await expectLater(
      verifyWd07bCertificate(
        origin: Uri.parse(wd07bExpectedOrigin),
        certificateBytes: utf8.encode('synthetic-public-cert'),
        expectedSha256: List<String>.filled(64, '0').join(),
      ),
      throwsStateError,
    );
  });

  test('accepts matching SHA-256 for the exact isolated origin', () async {
    final certificateBytes = utf8.encode('synthetic-public-cert');
    final hash = await Sha256().hash(certificateBytes);
    final expected = hash.bytes
        .map((value) => value.toRadixString(16).padLeft(2, '0'))
        .join();
    await verifyWd07bCertificate(
      origin: Uri.parse(wd07bExpectedOrigin),
      certificateBytes: certificateBytes,
      expectedSha256: expected,
    );
  });

  test('Windows Dart TLS verifies live disposable Flask readiness', () async {
    const enabled = bool.fromEnvironment('CLINIC_WINDOWS_WD07B_TLS');
    if (!enabled || !Platform.isWindows) return;
    const certFile = String.fromEnvironment(
      'CLINIC_WINDOWS_WD07B_TEST_CERT_PATH',
    );
    expect(certFile, isNotEmpty);
    const config = BackendConfig(baseUrl: wd07bExpectedOrigin);
    final client = await createWd07bWindowsTlsClient(
      config,
      certificateFilePath: certFile,
    );
    expect(client, isNotNull);
    final verifiedClient = client!;
    try {
      final live = await verifiedClient
          .get(config.endpoint('health/live'))
          .timeout(const Duration(seconds: 15));
      expect(live.statusCode, 200);
      final ready = await verifiedClient
          .get(config.endpoint('health/ready'))
          .timeout(const Duration(seconds: 15));
      expect(ready.statusCode, 200);
    } finally {
      verifiedClient.close();
    }
  });
}
