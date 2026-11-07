import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/diet_nursing_order.dart';

class DietNursingSessionException implements Exception {
  const DietNursingSessionException();

  @override
  String toString() => 'Diet/nursing session unavailable or changed.';
}

/// Server-owned diet and nursing workflows. Never queue/replay a clinical write.
class DietNursingRemoteDataSource {
  const DietNursingRemoteDataSource({required this.unifiedSource});

  final ClinicalOrdersRemoteDataSource unifiedSource;
  ApiClient get _api => unifiedSource.apiClient;
  SessionNavigationGuard get guard => unifiedSource.sessionNavigationGuard;

  static const _readRoles = <String>{'admin', 'doctor', 'nurse'};

  bool _current(int revision) =>
      guard.canAccessProtectedRoutes && guard.revision == revision;

  Future<AuthTokenResponse> _session({required String permission}) async {
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
          !_readRoles.contains(role) ||
          (permission == 'doctor' && role != 'doctor') ||
          (permission == 'nurse' && role != 'nurse')) {
        throw const DietNursingSessionException();
      }
      return session;
    } on DietNursingSessionException {
      rethrow;
    } on Object {
      throw const DietNursingSessionException();
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
    if (!_current(revision)) {
      throw const DietNursingSessionException();
    }
    final session = await _session(permission: 'read');
    if (!_current(revision)) {
      throw const DietNursingSessionException();
    }
    return session;
  }

  Future<T> _execute<T>({
    required String permission,
    required Future<T> Function(String token, int clinicId) request,
  }) async {
    final revision = guard.revision;
    if (!_current(revision)) {
      throw const DietNursingSessionException();
    }
    final before = await _session(permission: permission);
    if (!_current(revision)) {
      throw const DietNursingSessionException();
    }
    final result = await request(before.accessToken, before.clinicContextId!);
    if (!_current(revision)) {
      throw const DietNursingSessionException();
    }
    final after = await _session(permission: permission);
    if (!_current(revision) || !_same(before, after)) {
      throw const DietNursingSessionException();
    }
    return result;
  }

  static void _ids(int patientId, int admissionId, {int? orderId}) {
    if (patientId <= 0 ||
        admissionId <= 0 ||
        patientId > 2147483647 ||
        admissionId > 2147483647 ||
        (orderId != null && (orderId <= 0 || orderId > 2147483647))) {
      throw ArgumentError('Invalid patient, admission or order ID.');
    }
  }

  static void _kind(String kind) {
    if (!DietNursingOrder.kinds.contains(kind)) {
      throw ArgumentError.value(kind, 'kind');
    }
  }

  static String _required(String value, String field, int max) {
    if (value.trim().isEmpty || value.trim() != value || value.length > max) {
      throw ArgumentError.value(value, field, 'Invalid clinical order input.');
    }
    return value;
  }

  static DietNursingOrder _parseOrder(
    Map<String, dynamic> json, {
    required String kind,
    required int patientId,
    required int admissionId,
    required int clinicId,
    int? orderId,
    bool envelope = false,
  }) {
    final item = envelope ? json['order'] : json;
    if (item is! Map<String, dynamic>) {
      throw const FormatException(
        'Clinical order response is missing the order.',
      );
    }
    return DietNursingOrder.fromJson(
      item,
      kind: kind,
      expectedClinicId: clinicId,
      expectedPatientId: patientId,
      expectedAdmissionId: admissionId,
      expectedId: orderId,
    );
  }

  Future<DietNursingOrderList> list({
    required String kind,
    required int patientId,
    required int admissionId,
  }) {
    _kind(kind);
    _ids(patientId, admissionId);
    return _execute(
      permission: 'read',
      request: (token, clinic) async {
        final json = await _api.getJson(
          'api/v1/clinical-orders/$kind?admission_id=$admissionId',
          bearerToken: token,
        );
        return DietNursingOrderList.fromJson(
          json,
          kind: kind,
          expectedClinicId: clinic,
          expectedPatientId: patientId,
          expectedAdmissionId: admissionId,
        );
      },
    );
  }

  Future<DietNursingOrder> get({
    required String kind,
    required int patientId,
    required int admissionId,
    required int orderId,
  }) {
    _kind(kind);
    _ids(patientId, admissionId, orderId: orderId);
    return _execute(
      permission: 'read',
      request: (token, clinic) async {
        final json = await _api.getJson(
          'api/v1/clinical-orders/$kind/$orderId',
          bearerToken: token,
        );
        return _parseOrder(
          json,
          kind: kind,
          patientId: patientId,
          admissionId: admissionId,
          clinicId: clinic,
          orderId: orderId,
        );
      },
    );
  }

  Future<DietNursingOrder> createDiet({
    required int patientId,
    required int admissionId,
    required String dietType,
    String? customDescription,
    String? feedingRoute = 'oral',
    String? instructions,
  }) {
    _ids(patientId, admissionId);
    if (!DietNursingOrder.dietTypes.contains(dietType)) {
      throw ArgumentError.value(dietType, 'dietType');
    }
    if (dietType == 'custom') {
      _required(customDescription ?? '', 'customDescription', 1000);
    }
    if (dietType == 'npo') {
      if (feedingRoute != null) {
        throw ArgumentError.value(
          feedingRoute,
          'feedingRoute',
          'NPO orders must not specify a feeding route.',
        );
      }
    } else if (!DietNursingOrder.feedingRoutes.contains(feedingRoute)) {
      throw ArgumentError.value(feedingRoute, 'feedingRoute');
    }
    if (instructions != null) {
      _required(instructions, 'instructions', 10000);
    }
    final body = <String, dynamic>{
      'admission_id': admissionId,
      'diet_type': dietType,
      // Explicit null is mandatory for NPO: the server default is "oral".
      'feeding_route': feedingRoute,
      if (dietType == 'custom') 'custom_description': customDescription,
      'instructions': ?instructions,
    };
    return _execute(
      permission: 'doctor',
      request: (token, clinic) async {
        final json = await _api.postJson(
          'api/v1/clinical-orders/diets',
          body: body,
          bearerToken: token,
        );
        final order = _parseOrder(
          json,
          kind: 'diets',
          patientId: patientId,
          admissionId: admissionId,
          clinicId: clinic,
          envelope: true,
        );
        if (!order.isOpen || order.category != dietType) {
          throw const FormatException('Diet order creation was not confirmed.');
        }
        return order;
      },
    );
  }

  Future<DietNursingOrder> createNursing({
    required int patientId,
    required int admissionId,
    required String category,
    required String orderText,
    required String frequency,
    String? customFrequency,
    String? instructions,
    int? assignedToStaffId,
  }) {
    _ids(patientId, admissionId);
    if (!DietNursingOrder.nursingCategories.contains(category) ||
        !DietNursingOrder.frequencies.contains(frequency)) {
      throw ArgumentError('Invalid nursing order category or frequency.');
    }
    _required(orderText, 'orderText', 10000);
    if ((frequency == 'custom') != (customFrequency != null)) {
      throw ArgumentError('Custom frequency must match frequency type.');
    }
    if (customFrequency != null) {
      _required(customFrequency, 'customFrequency', 255);
    }
    if (instructions != null) {
      _required(instructions, 'instructions', 10000);
    }
    if (assignedToStaffId != null &&
        (assignedToStaffId <= 0 || assignedToStaffId > 2147483647)) {
      throw ArgumentError.value(assignedToStaffId, 'assignedToStaffId');
    }
    final body = <String, dynamic>{
      'admission_id': admissionId,
      'category': category,
      'order_text': orderText,
      'frequency': frequency,
      'custom_frequency': ?customFrequency,
      'instructions': ?instructions,
      'assigned_to_staff_id': ?assignedToStaffId,
    };
    return _execute(
      permission: 'doctor',
      request: (token, clinic) async {
        final json = await _api.postJson(
          'api/v1/clinical-orders/nursing-orders',
          body: body,
          bearerToken: token,
        );
        final order = _parseOrder(
          json,
          kind: 'nursing-orders',
          patientId: patientId,
          admissionId: admissionId,
          clinicId: clinic,
          envelope: true,
        );
        if (!order.isOpen ||
            order.category != category ||
            order.frequency != frequency) {
          throw const FormatException(
            'Nursing order creation was not confirmed.',
          );
        }
        return order;
      },
    );
  }

  Future<DietNursingOrder> act({
    required String kind,
    required int patientId,
    required int admissionId,
    required DietNursingOrder order,
    required String action,
    required String note,
  }) {
    _kind(kind);
    _ids(patientId, admissionId, orderId: order.id);
    if (order.kind != kind ||
        order.patientId != patientId ||
        order.admissionId != admissionId ||
        !order.isOpen ||
        !const <String>{'complete', 'cancel'}.contains(action) ||
        (action == 'complete' && !order.canDoctorComplete)) {
      throw ArgumentError('Invalid clinical order transition.');
    }
    _required(note, 'note', 10000);
    final body = <String, dynamic>{
      action == 'cancel' ? 'reason' : 'completion_note': note,
    };
    return _execute(
      permission: 'doctor',
      request: (token, clinic) async {
        if (order.clinicId != clinic) {
          throw const FormatException('Order clinic context mismatch.');
        }
        final json = await _api.postJson(
          'api/v1/clinical-orders/$kind/${order.id}/$action',
          body: body,
          bearerToken: token,
        );
        final result = _parseOrder(
          json,
          kind: kind,
          patientId: patientId,
          admissionId: admissionId,
          clinicId: clinic,
          orderId: order.id,
          envelope: true,
        );
        if (result.status != (action == 'cancel' ? 'cancelled' : 'completed') ||
            result.version <= order.version) {
          throw const FormatException(
            'Clinical order transition not confirmed.',
          );
        }
        return result;
      },
    );
  }

  Future<NursingOrderExecutionList> executions({
    required int patientId,
    required int admissionId,
    required int orderId,
  }) {
    _ids(patientId, admissionId, orderId: orderId);
    return _execute(
      permission: 'read',
      request: (token, clinic) async {
        final json = await _api.getJson(
          'api/v1/clinical-orders/nursing-orders/$orderId/executions',
          bearerToken: token,
        );
        return NursingOrderExecutionList.fromJson(
          json,
          expectedOrderId: orderId,
          expectedClinicId: clinic,
          expectedPatientId: patientId,
          expectedAdmissionId: admissionId,
        );
      },
    );
  }

  Future<DietNursingOrder> executeNursing({
    required int patientId,
    required int admissionId,
    required DietNursingOrder order,
    required String resultNote,
  }) {
    _ids(patientId, admissionId, orderId: order.id);
    if (order.kind != 'nursing-orders' ||
        order.patientId != patientId ||
        order.admissionId != admissionId ||
        !order.canExecute) {
      throw ArgumentError('This nursing order cannot be executed.');
    }
    _required(resultNote, 'resultNote', 10000);
    return _execute(
      permission: 'nurse',
      request: (token, clinic) async {
        if (order.clinicId != clinic) {
          throw const FormatException('Nursing order clinic mismatch.');
        }
        final json = await _api.postJson(
          'api/v1/clinical-orders/nursing-orders/${order.id}/execute',
          body: <String, dynamic>{'result_note': resultNote},
          bearerToken: token,
        );
        final execution = json['execution'];
        if (execution is! Map<String, dynamic>) {
          throw const FormatException(
            'Missing nursing execution confirmation.',
          );
        }
        NursingOrderExecution.fromJson(
          execution,
          expectedOrderId: order.id,
          expectedClinicId: clinic,
          expectedPatientId: patientId,
          expectedAdmissionId: admissionId,
        );
        final result = _parseOrder(
          json,
          kind: 'nursing-orders',
          patientId: patientId,
          admissionId: admissionId,
          clinicId: clinic,
          orderId: order.id,
          envelope: true,
        );
        if (result.version <= order.version ||
            (order.frequency == 'once' && result.status != 'completed') ||
            (order.frequency != 'once' &&
                result.status != 'active' &&
                result.status != 'in_progress')) {
          throw const FormatException(
            'Nursing execution transition not confirmed.',
          );
        }
        return result;
      },
    );
  }
}
