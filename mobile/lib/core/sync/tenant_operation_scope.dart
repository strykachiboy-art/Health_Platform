import '../../features/auth/data/models/auth_token_response.dart';
import 'operation_payload_crypto.dart';

/// Local namespace only. Never serialize this clinic key into an API request.
/// The existing queue metadata, key derivation and AES-GCM associated data all
/// use the same opaque clinic key. Old numeric clinic keys remain distinct.
class TenantOperationScope {
  const TenantOperationScope._();

  static final RegExp _uuid = RegExp(
    r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$',
  );
  static const _appointmentRoles = <String>{
    'admin',
    'doctor',
    'nurse',
    'receptionist',
  };

  static OperationPayloadContext? fromSession(AuthTokenResponse? session) {
    if (session == null ||
        session.userId <= 0 ||
        session.clinicContextId == null ||
        session.clinicContextId! <= 0 ||
        session.accessToken.trim().isEmpty ||
        session.accessToken.trim() != session.accessToken ||
        session.refreshToken.trim().isEmpty ||
        session.role.trim().toLowerCase() == 'super_admin') {
      return null;
    }
    final tenant = session.tenantId;
    if (tenant == null || !_uuid.hasMatch(tenant)) {
      return null;
    }
    return OperationPayloadContext(
      userId: session.userId.toString(),
      clinicContextId:
          'tenant-v1:${tenant.toLowerCase()}:${session.clinicContextId}',
    );
  }

  static OperationPayloadContext? appointmentContext(
    AuthTokenResponse? session,
  ) {
    if (session == null ||
        !_appointmentRoles.contains(session.role.trim().toLowerCase())) {
      return null;
    }
    return fromSession(session);
  }

  static bool matchesAppointment(
    AuthTokenResponse? session,
    OperationPayloadContext expected,
  ) {
    final actual = appointmentContext(session);
    return actual != null &&
        actual.userId == expected.userId &&
        actual.clinicContextId == expected.clinicContextId;
  }
}
