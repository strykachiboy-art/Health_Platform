import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/imaging_procedure_order.dart';

class ImagingProcedureSessionException implements Exception {
  const ImagingProcedureSessionException();

  @override
  String toString() => 'Imaging/procedure session unavailable or changed.';
}

/// Online-only order actions: never queue or automatically retry a write.
/// Client-side checks complement, rather than replace, backend role/scope rules.
class ImagingProcedureRemoteDataSource {
  const ImagingProcedureRemoteDataSource({required this.unifiedSource});

  final ClinicalOrdersRemoteDataSource unifiedSource;
  ApiClient get _api => unifiedSource.apiClient;
  SessionNavigationGuard get guard => unifiedSource.sessionNavigationGuard;

  static const _readRoles = <String>{'admin', 'doctor', 'nurse'};

  bool _current(int revision) =>
      guard.canAccessProtectedRoutes && guard.revision == revision;

  Future<AuthTokenResponse> _session({required bool write}) async {
    try {
      final state = await unifiedSource.loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.isEmpty ||
          session.accessToken != session.accessToken.trim() ||
          !_readRoles.contains(session.role.trim().toLowerCase()) ||
          (write && session.role.trim().toLowerCase() != 'doctor')) {
        throw const ImagingProcedureSessionException();
      }
      return session;
    } on ImagingProcedureSessionException {
      rethrow;
    } on Object {
      throw const ImagingProcedureSessionException();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AuthTokenResponse> authorizedSession({bool write = false}) async {
    final revision = guard.revision;
    if (!_current(revision)) {
      throw const ImagingProcedureSessionException();
    }
    final session = await _session(write: write);
    if (!_current(revision)) {
      throw const ImagingProcedureSessionException();
    }
    return session;
  }

  Future<T> _execute<T>({
    required bool write,
    required Future<T> Function(String token, int clinicId) request,
  }) async {
    final revision = guard.revision;
    if (!_current(revision)) {
      throw const ImagingProcedureSessionException();
    }
    final before = await _session(write: write);
    if (!_current(revision)) {
      throw const ImagingProcedureSessionException();
    }
    final value = await request(before.accessToken, before.clinicContextId!);
    if (!_current(revision)) {
      throw const ImagingProcedureSessionException();
    }
    final after = await _session(write: write);
    if (!_current(revision) || !_same(before, after)) {
      throw const ImagingProcedureSessionException();
    }
    return value;
  }

  static void _ids(int patientId, int admissionId) {
    if (patientId <= 0 ||
        admissionId <= 0 ||
        patientId > 2147483647 ||
        admissionId > 2147483647) {
      throw ArgumentError('Patient and admission IDs must be positive.');
    }
  }

  static void _kind(String kind) {
    if (!ImagingProcedureOrder.kinds.contains(kind)) {
      throw ArgumentError.value(kind, 'kind');
    }
  }

  static String _required(String value, String field, int maxLength) {
    if (value.trim().isEmpty ||
        value.trim() != value ||
        value.length > maxLength) {
      throw ArgumentError.value(value, field, 'Invalid clinical order input.');
    }
    return value;
  }

  static Map<String, dynamic> _body(
    String kind, {
    required int admissionId,
    required String name,
    required String category,
    required String indication,
    String? bodySite,
    bool consentRequired = false,
  }) {
    _kind(kind);
    if (admissionId <= 0 || admissionId > 2147483647) {
      throw ArgumentError.value(admissionId, 'admissionId');
    }
    _required(name, 'name', 255);
    _required(indication, 'indication', 10000);
    final categories = kind == 'imaging'
        ? ImagingProcedureOrder.modalities
        : ImagingProcedureOrder.procedureTypes;
    if (!categories.contains(category)) {
      throw ArgumentError.value(category, 'category');
    }
    if (kind == 'imaging') {
      _required(bodySite ?? '', 'bodySite', 255);
      return <String, dynamic>{
        'admission_id': admissionId,
        'study_name': name,
        'modality': category,
        'body_site': bodySite,
        'indication': indication,
      };
    }
    return <String, dynamic>{
      'admission_id': admissionId,
      'procedure_name': name,
      'procedure_type': category,
      'indication': indication,
      'consent_required': consentRequired,
    };
  }

  static ImagingProcedureOrder _readOrder(
    Map<String, dynamic> response, {
    required String kind,
    required int patientId,
    required int admissionId,
    required int clinicId,
    int? orderId,
    bool envelope = false,
  }) {
    final data = envelope ? response['order'] : response;
    if (data is! Map<String, dynamic>) {
      throw const FormatException('Missing verified clinical order record.');
    }
    return ImagingProcedureOrder.fromJson(
      data,
      kind: kind,
      expectedClinicId: clinicId,
      expectedPatientId: patientId,
      expectedAdmissionId: admissionId,
      expectedId: orderId,
    );
  }

  Future<ImagingProcedureOrderList> list({
    required String kind,
    required int patientId,
    required int admissionId,
  }) {
    _kind(kind);
    _ids(patientId, admissionId);
    return _execute(
      write: false,
      request: (token, clinic) async {
        final result = await _api.getJson(
          'api/v1/clinical-orders/$kind?admission_id=$admissionId',
          bearerToken: token,
        );
        return ImagingProcedureOrderList.fromJson(
          result,
          kind: kind,
          expectedClinicId: clinic,
          expectedPatientId: patientId,
          expectedAdmissionId: admissionId,
        );
      },
    );
  }

  Future<ImagingProcedureOrder> get({
    required String kind,
    required int patientId,
    required int admissionId,
    required int orderId,
  }) {
    _kind(kind);
    _ids(patientId, admissionId);
    if (orderId <= 0) {
      throw ArgumentError.value(orderId, 'orderId');
    }
    return _execute(
      write: false,
      request: (token, clinic) async {
        final result = await _api.getJson(
          'api/v1/clinical-orders/$kind/$orderId',
          bearerToken: token,
        );
        return _readOrder(
          result,
          kind: kind,
          patientId: patientId,
          admissionId: admissionId,
          clinicId: clinic,
          orderId: orderId,
        );
      },
    );
  }

  Future<ImagingProcedureOrder> create({
    required String kind,
    required int patientId,
    required int admissionId,
    required String name,
    required String category,
    required String indication,
    String? bodySite,
    bool consentRequired = false,
  }) {
    _kind(kind);
    _ids(patientId, admissionId);
    final body = _body(
      kind,
      admissionId: admissionId,
      name: name,
      category: category,
      indication: indication,
      bodySite: bodySite,
      consentRequired: consentRequired,
    );
    return _execute(
      write: true,
      request: (token, clinic) async {
        final response = await _api.postJson(
          'api/v1/clinical-orders/$kind',
          body: body,
          bearerToken: token,
        );
        final record = _readOrder(
          response,
          kind: kind,
          patientId: patientId,
          admissionId: admissionId,
          clinicId: clinic,
          envelope: true,
        );
        if (!record.isOpen) {
          throw const FormatException(
            'New clinical order has terminal status.',
          );
        }
        return record;
      },
    );
  }

  Future<ImagingProcedureOrder> act({
    required String kind,
    required int patientId,
    required int admissionId,
    required int orderId,
    required String action,
    String? note,
  }) {
    _kind(kind);
    _ids(patientId, admissionId);
    if (orderId <= 0) {
      throw ArgumentError.value(orderId, 'orderId');
    }
    if (!const <String>{
          'cancel',
          'complete',
          'verify-consent',
        }.contains(action) ||
        (action == 'verify-consent' && kind != 'procedures')) {
      throw ArgumentError.value(action, 'action');
    }
    final Map<String, dynamic> body;
    if (action == 'verify-consent') {
      body = <String, dynamic>{};
    } else {
      final content = _required(
        note ?? '',
        'note',
        action == 'cancel' ? 10000 : 20000,
      );
      body = <String, dynamic>{
        (action == 'cancel'
                ? 'reason'
                : (kind == 'imaging' ? 'report_text' : 'procedure_note')):
            content,
      };
    }
    return _execute(
      write: true,
      request: (token, clinic) async {
        final response = await _api.postJson(
          'api/v1/clinical-orders/$kind/$orderId/$action',
          body: body,
          bearerToken: token,
        );
        final record = _readOrder(
          response,
          kind: kind,
          patientId: patientId,
          admissionId: admissionId,
          clinicId: clinic,
          orderId: orderId,
          envelope: true,
        );
        if ((action == 'cancel' && record.status != 'cancelled') ||
            (action == 'complete' && record.status != 'completed') ||
            (action == 'verify-consent' &&
                (!record.consentRequired ||
                    record.consentVerifiedAt == null))) {
          throw const FormatException('Clinical order action result mismatch.');
        }
        return record;
      },
    );
  }
}
