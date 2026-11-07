import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/blood_product_order.dart';

class BloodProductSessionException implements Exception {
  const BloodProductSessionException();

  @override
  String toString() => 'Blood-product session unavailable or changed.';
}

/// Online-only blood-product operations. No idempotency header, retry, queue,
/// replay, local cache, or inference of clinical compatibility.
class BloodProductRemoteDataSource {
  const BloodProductRemoteDataSource({required this.unifiedSource});

  final ClinicalOrdersRemoteDataSource unifiedSource;
  ApiClient get _api => unifiedSource.apiClient;
  SessionNavigationGuard get guard => unifiedSource.sessionNavigationGuard;

  static const readRoles = <String>{
    'admin',
    'doctor',
    'nurse',
    'lab_technician',
  };

  bool _current(int revision) =>
      guard.canAccessProtectedRoutes && guard.revision == revision;

  Future<AuthTokenResponse> _session(String permission) async {
    try {
      final state = await unifiedSource.loadSession();
      final session = state.session;
      final role = session?.role.trim().toLowerCase();
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.trim().isEmpty ||
          session.accessToken != session.accessToken.trim() ||
          !readRoles.contains(role) ||
          (permission == 'doctor' && role != 'doctor') ||
          (permission == 'lab' && role != 'lab_technician')) {
        throw const BloodProductSessionException();
      }
      return session;
    } on BloodProductSessionException {
      rethrow;
    } on Object {
      throw const BloodProductSessionException();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AuthTokenResponse> authorizedSession() async {
    final revision = guard.revision;
    if (!_current(revision)) throw const BloodProductSessionException();
    final session = await _session('read');
    if (!_current(revision)) throw const BloodProductSessionException();
    return session;
  }

  Future<T> _execute<T>({
    required String permission,
    required Future<T> Function(String token, int clinicId) request,
  }) async {
    final revision = guard.revision;
    if (!_current(revision)) throw const BloodProductSessionException();
    final before = await _session(permission);
    if (!_current(revision)) throw const BloodProductSessionException();
    final result = await request(before.accessToken, before.clinicContextId!);
    if (!_current(revision)) throw const BloodProductSessionException();
    final after = await _session(permission);
    if (!_current(revision) || !_same(before, after)) {
      throw const BloodProductSessionException();
    }
    return result;
  }

  static void _positive(int value, String name) {
    if (value <= 0 || value > 2147483647) {
      throw ArgumentError.value(value, name, 'Invalid order scope ID.');
    }
  }

  static String _required(String value, String name, int max) {
    if (value.trim().isEmpty || value.trim() != value || value.length > max) {
      throw ArgumentError.value(value, name, 'Invalid clinical order field.');
    }
    return value;
  }

  static BloodProductOrder _record(
    Map<String, dynamic> response, {
    required int clinicId,
    int? patientId,
    int? admissionId,
    int? orderId,
    bool envelope = false,
  }) {
    final raw = envelope ? response['order'] : response;
    if (raw is! Map<String, dynamic>) {
      throw const FormatException('Missing blood-product order record.');
    }
    return BloodProductOrder.fromJson(
      raw,
      expectedClinicId: clinicId,
      expectedPatientId: patientId,
      expectedAdmissionId: admissionId,
      expectedId: orderId,
    );
  }

  Future<BloodProductOrderList> list({
    required int patientId,
    required int admissionId,
  }) {
    _positive(patientId, 'patientId');
    _positive(admissionId, 'admissionId');
    return _execute(
      permission: 'read',
      request: (token, clinic) async {
        final json = await _api.getJson(
          'api/v1/clinical-orders/blood-products?admission_id=$admissionId',
          bearerToken: token,
        );
        return BloodProductOrderList.fromJson(
          json,
          expectedClinicId: clinic,
          expectedPatientId: patientId,
          expectedAdmissionId: admissionId,
        );
      },
    );
  }

  /// A lab technician can look up a verified order by its server ID without
  /// using the separate current-admission API, which is not lab-authorized.
  Future<BloodProductOrder> get({
    required int orderId,
    int? patientId,
    int? admissionId,
  }) {
    _positive(orderId, 'orderId');
    if (patientId != null) _positive(patientId, 'patientId');
    if (admissionId != null) _positive(admissionId, 'admissionId');
    return _execute(
      permission: 'read',
      request: (token, clinic) async {
        final json = await _api.getJson(
          'api/v1/clinical-orders/blood-products/$orderId',
          bearerToken: token,
        );
        return _record(
          json,
          clinicId: clinic,
          patientId: patientId,
          admissionId: admissionId,
          orderId: orderId,
        );
      },
    );
  }

  Future<BloodProductOrder> create({
    required int patientId,
    required int admissionId,
    required String productType,
    required int unitsRequested,
    required String indication,
  }) {
    _positive(patientId, 'patientId');
    _positive(admissionId, 'admissionId');
    if (!BloodProductOrder.productTypes.contains(productType)) {
      throw ArgumentError.value(productType, 'productType');
    }
    if (unitsRequested < 1 || unitsRequested > 100) {
      throw ArgumentError.value(unitsRequested, 'unitsRequested');
    }
    _required(indication, 'indication', 10000);
    return _execute(
      permission: 'doctor',
      request: (token, clinic) async {
        final json = await _api.postJson(
          'api/v1/clinical-orders/blood-products',
          body: <String, dynamic>{
            'admission_id': admissionId,
            'product_type': productType,
            'units_requested': unitsRequested,
            'indication': indication,
          },
          bearerToken: token,
        );
        final order = _record(
          json,
          clinicId: clinic,
          patientId: patientId,
          admissionId: admissionId,
          envelope: true,
        );
        if (!order.isOpen ||
            order.compatibilityStatus != 'pending' ||
            order.unitsRequested != unitsRequested ||
            order.productType != productType ||
            order.version != 1) {
          throw const FormatException('Unconfirmed new blood-product order.');
        }
        return order;
      },
    );
  }

  /// Only standard verified crossmatches are offered in this Flutter UI.
  /// Emergency release is a separate high-risk protocol, not a fallback.
  Future<BloodProductOrder> verifyStandard({
    required BloodProductOrder order,
    required String crossmatchReference,
    required bool laboratoryAttested,
  }) {
    if (!order.canVerify || !laboratoryAttested) {
      throw ArgumentError('Manual laboratory verification is required.');
    }
    _required(crossmatchReference, 'crossmatchReference', 128);
    return _execute(
      permission: 'lab',
      request: (token, clinic) async {
        if (order.clinicId != clinic) {
          throw const BloodProductSessionException();
        }
        final json = await _api.postJson(
          'api/v1/clinical-orders/blood-products/${order.id}/verify-compatibility',
          body: <String, dynamic>{
            'emergency_release': false,
            'crossmatch_reference': crossmatchReference,
          },
          bearerToken: token,
        );
        final updated = _record(
          json,
          clinicId: clinic,
          patientId: order.patientId,
          admissionId: order.admissionId,
          orderId: order.id,
          envelope: true,
        );
        if (updated.compatibilityStatus != 'compatible' ||
            updated.crossmatchReference != crossmatchReference ||
            updated.version <= order.version) {
          throw const FormatException(
            'Compatibility verification not confirmed.',
          );
        }
        return updated;
      },
    );
  }

  Future<BloodProductOrder> fulfill({
    required BloodProductOrder order,
    required int unitsIssued,
    required String note,
    required bool issuanceAttested,
  }) {
    if (!order.canFulfill ||
        order.compatibilityStatus != 'compatible' ||
        !issuanceAttested ||
        unitsIssued < 1 ||
        unitsIssued > order.unitsRequested) {
      throw ArgumentError('Verified order and valid issue quantity required.');
    }
    _required(note, 'note', 10000);
    return _execute(
      permission: 'lab',
      request: (token, clinic) async {
        if (order.clinicId != clinic) {
          throw const BloodProductSessionException();
        }
        final json = await _api.postJson(
          'api/v1/clinical-orders/blood-products/${order.id}/fulfill',
          body: <String, dynamic>{
            'units_issued': unitsIssued,
            'fulfillment_note': note,
          },
          bearerToken: token,
        );
        final updated = _record(
          json,
          clinicId: clinic,
          patientId: order.patientId,
          admissionId: order.admissionId,
          orderId: order.id,
          envelope: true,
        );
        if (updated.status != 'completed' ||
            updated.unitsIssued != unitsIssued ||
            updated.version <= order.version) {
          throw const FormatException('Blood-product issue not confirmed.');
        }
        return updated;
      },
    );
  }

  Future<BloodProductOrder> cancel({
    required BloodProductOrder order,
    required String reason,
  }) {
    if (!order.isOpen) {
      throw ArgumentError('Terminal order cannot be cancelled.');
    }
    _required(reason, 'reason', 10000);
    return _execute(
      permission: 'doctor',
      request: (token, clinic) async {
        if (order.clinicId != clinic) {
          throw const BloodProductSessionException();
        }
        final json = await _api.postJson(
          'api/v1/clinical-orders/blood-products/${order.id}/cancel',
          body: <String, dynamic>{'reason': reason},
          bearerToken: token,
        );
        final updated = _record(
          json,
          clinicId: clinic,
          patientId: order.patientId,
          admissionId: order.admissionId,
          orderId: order.id,
          envelope: true,
        );
        if (updated.status != 'cancelled' || updated.version <= order.version) {
          throw const FormatException(
            'Blood-product cancellation not confirmed.',
          );
        }
        return updated;
      },
    );
  }
}
