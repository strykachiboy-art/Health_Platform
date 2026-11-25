import 'dart:math';

import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

import 'blood_bank_contract.dart';

class BbUnavailable implements Exception {
  const BbUnavailable();
}

class BbUnknownOutcome implements Exception {
  const BbUnknownOutcome();
}

/// Router-lifetime, memory-only transport. No retry, queue, cache, token store,
/// or automatic disposition; an uncertain mutation locks this clinic/user scope.
class BloodBankSource {
  BloodBankSource({
    required this.api,
    required this.loadSession,
    required this.guard,
    this.admissions,
  });
  final ApiClient api;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard guard;
  final PatientAdmissionRemoteDataSource? admissions;
  final Set<String> _uncertain = {};
  final Set<String> _inFlight = {};
  final Set<String> _consumedScans = {};

  Future<AuthTokenResponse> _auth(int revision) async {
    if (!guard.canAccessProtectedRoutes || guard.revision != revision) {
      throw const BbUnavailable();
    }
    final state = await loadSession();
    final a = state.session;
    if (!guard.canAccessProtectedRoutes ||
        guard.revision != revision ||
        state.disposition != AuthSessionDisposition.ready ||
        a == null ||
        a.userId <= 0 ||
        (a.clinicContextId ?? 0) <= 0 ||
        a.tenantId == null ||
        a.tenantId!.isEmpty ||
        a.accessToken.trim().isEmpty ||
        a.accessToken.trim() != a.accessToken ||
        !bbRoles.contains(a.role.trim().toLowerCase())) {
      throw const BbUnavailable();
    }
    return a;
  }

  Future<BloodBankSession> open() async {
    final rev = guard.revision;
    final session = BloodBankSession._(this, await _auth(rev), rev);
    if (session.role != 'patient') await session.read(BbAction.offline);
    return session;
  }
}

class BloodBankSession {
  BloodBankSession._(this.source, this._auth, this.revision);
  final BloodBankSource source;
  final AuthTokenResponse _auth;
  final int revision;
  String get role => _auth.role.trim().toLowerCase();
  int get clinic => _auth.clinicContextId!;
  String get _key => '${_auth.tenantId}/$clinic/${_auth.userId}';
  bool get active =>
      source.guard.canAccessProtectedRoutes &&
      source.guard.revision == revision;
  bool get writeBlocked =>
      source._uncertain.contains(_key) || source._inFlight.contains(_key);
  bool allows(BbAction action) => active && action.roles.contains(role);
  Future<void> check() async {
    final a = await source._auth(revision);
    if (a.userId != _auth.userId ||
        a.tenantId != _auth.tenantId ||
        a.clinicContextId != clinic ||
        a.role != _auth.role ||
        a.accessToken != _auth.accessToken ||
        a.refreshToken != _auth.refreshToken) {
      throw const BbUnavailable();
    }
  }

  Future<BbJson> read(
    BbAction action, {
    int? id,
    int? admission,
    int? after,
    int? patient,
  }) async {
    if (action.write || !allows(action)) throw const BbUnavailable();
    if (action == BbAction.order && admission != null) {
      bbId(admission);
    }
    final path = action.path(
      id: id,
      admission: action == BbAction.order ? null : admission,
      after: after,
    );
    await check();
    final raw = await source.api.getJson(path, bearerToken: _auth.accessToken);
    await check();
    return bbValidate(
      action,
      raw,
      clinic,
      id: id,
      admission: admission,
      patient: patient,
    );
  }

  Future<T> _write<T>(
    BbAction action,
    BbJson body,
    T Function(BbJson) parse, {
    int? id,
    int? secondary,
  }) async {
    if (!action.write || !allows(action)) throw const BbUnavailable();
    await read(BbAction.offline);
    final path = action.path(id: id, secondary: secondary);
    await check();
    if (source._uncertain.contains(_key) || !source._inFlight.add(_key)) {
      throw const BbUnknownOutcome();
    }
    try {
      final raw = await source.api.postJson(
        path,
        body: body,
        bearerToken: _auth.accessToken,
      );
      await check();
      return parse(raw);
    } on Object {
      source._uncertain.add(_key);
      throw const BbUnknownOutcome();
    } finally {
      source._inFlight.remove(_key);
    }
  }

  Future<BbJson> submit(
    BbAction action,
    BbJson body, {
    int? id,
    int? secondary,
    int? patient,
    int? admission,
  }) {
    if (action.dedicated ||
        action == BbAction.issue ||
        action == BbAction.reserve) {
      throw const BbUnavailable();
    }
    return _write(
      action,
      body,
      (raw) => bbValidate(
        action,
        raw,
        clinic,
        id: id,
        patient: patient,
        admission: admission,
      ),
      id: id,
      secondary: secondary,
    );
  }

  Future<int> currentAdmission(int patient) async {
    bbId(patient);
    await check();
    final admissions = source.admissions;
    if (admissions == null ||
        !identical(admissions.sessionNavigationGuard, source.guard)) {
      throw const BbUnavailable();
    }
    final row = await admissions.fetchCurrentAdmission(patientId: patient);
    await check();
    if (row == null || row.patientId != patient || !row.isActive) {
      throw const BbUnavailable();
    }
    return row.id;
  }

  Future<BbJson> reserve({
    required BbJson order,
    required List<BbJson> units,
  }) async {
    final id = bbId(order['id']);
    bbOrderJson(order, clinic, id: id);
    if (units.isEmpty ||
        units.length >
            bbCount(order['units_requested']) - bbCount(order['units_issued'])) {
      throw const FormatException('Select actual outstanding components.');
    }
    final fresh = await read(BbAction.order, id: id);
    if (fresh['version'] != order['version']) {
      throw const FormatException('Order changed. Reload.');
    }
    final seen = <int>{};
    for (final item in units) {
      if (!seen.add(bbId(item['blood_unit_id']))) {
        throw const FormatException('Duplicate component.');
      }
    }
    for (final item in units) {
      final uid = bbId(item['blood_unit_id']);
      final u = BbUnit.fromJson(await read(BbAction.unit, id: uid), clinic);
      if (u.version != item['expected_version'] ||
          u.status != 'available' ||
          !u.currentlyUsable ||
          u.product != order['product_type']) {
        throw const FormatException(
          'Component changed or is not available. Reload.',
        );
      }
    }
    return _write(
      BbAction.reserve,
      {'expected_order_version': bbId(order['version']), 'units': units},
      (raw) {
        final checked = bbValidate(BbAction.reserve, raw, clinic);
        final rows = bbRows(checked['items']);
        if (rows.length != seen.length ||
            rows.any(
              (r) =>
                  !seen.contains(r['id']) ||
                  r['status'] != 'reserved' ||
                  r['reserved_for_order_id'] != id,
            )) {
          bbInvalid();
        }
        return checked;
      },
      id: id,
    );
  }

  Future<List<BbUnit>> reserved(BbJson order) async {
    final id = bbId(order['id']);
    bbOrderJson(order, clinic, id: id);
    final rows = bbRows((await read(BbAction.inventory))['items'])
        .map((r) => BbUnit.fromJson(r, clinic))
        .where((u) => u.status == 'reserved' && u.reservedOrder == id)
        .toList();
    return List.unmodifiable(rows);
  }

  Future<BbJson> issue({
    required BbJson order,
    required List<BbUnit> displayed,
    required String note,
  }) async {
    final id = bbId(order['id']);
    final latest = await read(BbAction.order, id: id);
    if (latest['version'] != order['version'] ||
        latest['inventory_required'] != true) {
      throw const FormatException(
        'Order changed or has no physical inventory contract.',
      );
    }
    final current = await reserved(latest);
    if (current.isEmpty ||
        current.length != displayed.length ||
        current.length >
            bbCount(order['units_requested']) -
                bbCount(order['units_issued']) ||
        current.any(
          (u) =>
              !u.currentlyUsable ||
              !displayed.any(
                (d) =>
                    d.id == u.id &&
                    d.version == u.version &&
                    d.barcode == u.barcode &&
                    d.group == u.group,
              ),
        )) {
      throw const FormatException(
        'Reservation changed. Reload exact component set.',
      );
    }
    return _write(
      BbAction.issue,
      {
        'units_issued': current.length,
        'issued_units': [
          for (final u in current)
            {'unit_identifier': u.barcode, 'product_blood_type': u.group},
        ],
        'fulfillment_note': bbText(note, max: 10000),
      },
      (raw) {
        final result = bbOrderJson(
          raw,
          clinic,
          id: id,
          patient: bbId(order['patient_id']),
          admission: bbId(order['admission_id']),
        );
        if (bbCount(result['units_issued']) !=
                bbCount(order['units_issued']) + current.length ||
            bbId(result['version']) <= bbId(order['version']) ||
            result['inventory_required'] != true) {
          bbInvalid();
        }
        final issued = bbRows(result['issued_units'], max: 100);
        for (final u in current) {
          if (!issued.any(
            (r) =>
                r['blood_unit_id'] == u.id &&
                r['unit_identifier'] == u.barcode &&
                r['product_blood_type'] == u.group,
          )) {
            bbInvalid();
          }
        }
        return result;
      },
      id: id,
    );
  }

  Future<BbVerifiedScan?> verify({
    required BbJson order,
    required String patientBarcode,
    required String unitBarcode,
    required DateTime capturedAt,
  }) async {
    final oid = bbId(order['id']);
    final pid = bbId(order['patient_id']);
    final aid = bbId(order['admission_id']);
    bbOrderJson(order, clinic, id: oid);
    if (order['inventory_required'] != true ||
        !bbRows(order['issued_units'], max: 100).any(
          (r) =>
              r['blood_unit_id'] != null && r['unit_identifier'] == unitBarcode,
        )) {
      throw const FormatException('No matching physical issuance evidence.');
    }
    final latest = await read(
      BbAction.order,
      id: oid,
      patient: pid,
      admission: aid,
    );
    if (latest['version'] != order['version']) {
      throw const FormatException('Order changed. Reload.');
    }
    bbText(patientBarcode, max: 128);
    bbText(unitBarcode, max: 128);
    if (patientBarcode.trim() != patientBarcode ||
        unitBarcode.trim() != unitBarcode) {
      throw const FormatException('Invalid barcode.');
    }
    final age = DateTime.now().toUtc().difference(capturedAt.toUtc());
    if (age.isNegative || age > const Duration(minutes: 5)) {
      throw const FormatException('Scan expired. Capture again.');
    }
    final random = Random.secure();
    final nonce = List.generate(
      24,
      (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'),
    ).join();
    final op = 'bb02-$nonce';
    return _write(
      BbAction.verifyBarcode,
      {
        'blood_product_order_id': oid,
        'patient_barcode': patientBarcode,
        'unit_barcode': unitBarcode,
        'scan_timestamp': capturedAt.toUtc().toIso8601String(),
        'scan_device': 'flutter_camera',
        'operation_identity': op,
      },
      (raw) {
        if (bbId(raw['clinic_id']) != clinic ||
            raw['scan_device'] != 'flutter_camera' ||
            raw['operation_identity'] != op ||
            raw['requested_order_id'] != oid ||
            raw['patient_barcode'] != patientBarcode ||
            raw['unit_barcode'] != unitBarcode ||
            raw['verification_policy_version'] != 2) {
          bbInvalid();
        }
        final event = bbId(raw['id']);
        if (raw['verification_result'] == 'not_barcode_verified') {
          bbText(raw['failure_code']);
          return null;
        }
        if (raw['verification_result'] != 'barcode_verified' ||
            raw['failure_code'] != null ||
            raw['failure_reason'] != null ||
            [
              'patient_match',
              'unit_match',
              'blood_type_match',
              'expiry_match',
              'order_status_match',
            ].any((k) => raw[k] != true) ||
            raw['patient_id'] != pid ||
            raw['scanned_patient_id'] != pid ||
            raw['admission_id'] != aid ||
            raw['blood_product_order_id'] != oid ||
            raw['source_order_version'] != order['version']) {
          bbInvalid();
        }
        final unit = bbId(raw['blood_unit_id']);
        if (raw['scanned_unit_id'] != unit) bbInvalid();
        final issue = bbId(raw['issued_unit_evidence_id']);
        if (!bbRows(order['issued_units'], max: 100).any(
          (r) =>
              r['id'] == issue &&
              r['blood_unit_id'] == unit &&
              r['unit_identifier'] == unitBarcode,
        )) {
          bbInvalid();
        }
        bbId(raw['source_unit_version']);
        bbId(raw['actor_staff_id']);
        bbText(raw['policy_reference']);
        final scan = bbTime(raw['scan_timestamp']);
        if (scan != capturedAt.toUtc()) bbInvalid();
        return BbVerifiedScan._(this, event, oid, pid, aid, unit, scan);
      },
    );
  }

  Future<BbJson> start(
    BbVerifiedScan scan,
    BbJson observations, {
    String? notes,
  }) async {
    if (!identical(scan.session, this) || !scan.fresh || observations.isEmpty) {
      throw const FormatException(
        'New live verification and observations required.',
      );
    }
    final key = '$_key/${scan.eventId}';
    if (!source._consumedScans.add(key)) throw const BbUnavailable();
    return _write(
      BbAction.start,
      {
        'blood_product_order_id': scan.orderId,
        'blood_unit_id': scan.unitId,
        'barcode_verification_event_id': scan.eventId,
        'pre_vitals': observations,
        if (notes != null && notes.isNotEmpty) 'notes': notes,
      },
      (raw) {
        final row = BbTransfusion.fromJson(
          raw,
          clinic,
          expectedPatient: scan.patientId,
          expectedAdmission: scan.admissionId,
          expectedOrder: scan.orderId,
          expectedUnit: scan.unitId,
        );
        if (row.status != 'in_progress' ||
            raw['barcode_verification_event_id'] != scan.eventId) {
          bbInvalid();
        }
        return raw;
      },
    );
  }
}

class BbVerifiedScan {
  const BbVerifiedScan._(
    this.session,
    this.eventId,
    this.orderId,
    this.patientId,
    this.admissionId,
    this.unitId,
    this.capturedAt,
  );
  final BloodBankSession session;
  final int eventId, orderId, patientId, admissionId, unitId;
  final DateTime capturedAt;
  bool get fresh {
    final age = DateTime.now().toUtc().difference(capturedAt);
    return session.active &&
        !age.isNegative &&
        age <= const Duration(minutes: 5);
  }
}
