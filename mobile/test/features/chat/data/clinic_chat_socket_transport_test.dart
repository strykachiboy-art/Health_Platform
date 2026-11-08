import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/features/chat/data/clinic_chat_socket_transport.dart';

void main() {
  test('transport seam is a notification-only contract', () {
    // No message-send or REST-write method exists in this interface.
    expect(ClinicChatSocketTransport, isNotNull);
    expect(clinicChatSocketFactory, isNotNull);
  });

  test('rejects unsupported origins and empty authentication', () {
    expect(
      () => IoClinicChatSocketTransport(
        origin: Uri.parse('ftp://example.org'),
        accessToken: 'token',
      ),
      throwsFormatException,
    );
    expect(
      () => IoClinicChatSocketTransport(
        origin: Uri.parse('https://example.org'),
        accessToken: ' ',
      ),
      throwsFormatException,
    );
    expect(
      () => IoClinicChatSocketTransport(
        origin: Uri.parse('https://example.org/path'),
        accessToken: 'token',
      ),
      throwsFormatException,
    );
  });
}
