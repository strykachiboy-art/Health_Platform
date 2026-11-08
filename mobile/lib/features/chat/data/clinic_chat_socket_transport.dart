import 'package:socket_io_client/socket_io_client.dart' as io;

/// CHAT-D1 Socket.IO is a notification transport, never a write channel.
/// A separate transport seam permits complete offline contract testing.
abstract class ClinicChatSocketTransport {
  void on(String event, void Function(Object? payload) callback);
  void emit(String event, Map<String, dynamic> payload);
  void connect();
  void dispose();
}

typedef ClinicChatSocketFactory = ClinicChatSocketTransport Function({
  required Uri origin,
  required String accessToken,
});

/// Socket.IO v4: namespace /chat; Engine.IO default path /socket.io/.
/// Every start gets a fresh Manager to prevent cached credentials carrying
/// between clinic contexts. No automatic reconnect with stale JWTs.
class IoClinicChatSocketTransport implements ClinicChatSocketTransport {
  IoClinicChatSocketTransport({
    required Uri origin,
    required String accessToken,
  }) : _socket = _create(origin: origin, accessToken: accessToken);

  final io.Socket _socket;

  static io.Socket _create({required Uri origin, required String accessToken}) {
    if ((origin.scheme != 'https' && origin.scheme != 'http') ||
        origin.host.isEmpty ||
        origin.userInfo.isNotEmpty ||
        origin.hasQuery ||
        origin.hasFragment ||
        (origin.path.isNotEmpty && origin.path != '/') ||
        accessToken.isEmpty ||
        accessToken.trim() != accessToken) {
      throw const FormatException('Invalid chat socket configuration.');
    }
    return io.io(
      '${origin.origin}/chat',
      io.OptionBuilder()
          .setTransports(['websocket'])
          .disableAutoConnect()
          .disableReconnection()
          .enableForceNew()
          .setAuth({'access_token': accessToken})
          .build(),
    );
  }

  @override
  void on(String event, void Function(Object? payload) callback) {
    _socket.on(event, (dynamic payload) => callback(payload));
  }

  @override
  void emit(String event, Map<String, dynamic> payload) {
    _socket.emit(event, payload);
  }

  @override
  void connect() => _socket.connect();

  @override
  void dispose() => _socket.dispose();
}

ClinicChatSocketTransport clinicChatSocketFactory({
  required Uri origin,
  required String accessToken,
}) => IoClinicChatSocketTransport(origin: origin, accessToken: accessToken);
