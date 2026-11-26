part of 'sa_workspace_remote.dart';

// Reuse the existing session, review and one-shot gate; no independent
// credentials, transport, cache or offline mutation channel.
extension _AdminOperationalRemote on SaWorkspaceRemote {
  void _adminTarget(SaSession s, SaRequest q) {
    if ((!adminMode && !doctorMode) ||
        !allows(s, q.operation) ||
        (q.operation.adminSpec!.selected && q.values['id'] != s.clinic)) {
      throw const SaUnavailable();
    }
    if (q.operation == SaOperation.adminTransferCreate &&
        q.values['destination_clinic_id'] == s.clinic) {
      throw const SaUnavailable();
    }
  }

  SaResult _adminParse(
    SaSession s,
    SaRequest q,
    Map<String, dynamic> raw,
    String shape,
    String scope,
    List<String> keys,
    Map<String, String> match, {
    bool select = false,
  }) {
    SaWorkspaceRemote._require(raw['error'] == null && raw['success'] != false);
    final explicit = const {
      'data',
      'dataPage',
      'rootPage',
      'integrationPage',
      'asset',
      'assignment',
      'history',
      'maintenance',
    }.contains(shape);
    if (explicit) {
      SaWorkspaceRemote._require(raw['success'] == true);
    }
    Map<String, dynamic>? pagination;
    final List<Map<String, dynamic>> incoming;
    switch (shape) {
      case 'dataPage':
        pagination = SaWorkspaceRemote._object(raw['data']);
        incoming = SaWorkspaceRemote._rows(pagination['items']);
      case 'rootPage':
      case 'rawPage':
        pagination = raw;
        incoming = SaWorkspaceRemote._rows(raw['items']);
      case 'integrationPage':
        pagination = SaWorkspaceRemote._object(raw['pagination']);
        incoming = SaWorkspaceRemote._rows(raw['data']);
      case 'clinicList':
        incoming = SaWorkspaceRemote._rows(raw['data']);
      case 'clinicObject':
      case 'data':
        incoming = [SaWorkspaceRemote._object(raw['data'])];
      case 'raw':
        incoming = [raw];
      case 'asset':
      case 'assignment':
      case 'history':
      case 'maintenance':
      case 'ward':
      case 'bed':
      case 'reservation':
        incoming = [SaWorkspaceRemote._object(raw[shape])];
      default:
        throw const FormatException(
          'Unexpected administrative response shape.',
        );
    }
    final ids = <int>{};
    final rows = <Map<String, dynamic>>[];
    for (final row in incoming) {
      final id = SaWorkspaceRemote._positive(row['id']);
      SaWorkspaceRemote._require(id <= 2147483647 && ids.add(id));
      if (scope == 'clinic') {
        SaWorkspaceRemote._require(
          row['clinic_id'] == s.clinic && s.clinic != null,
        );
      } else if (scope == 'transfer' || scope == 'transferSource') {
        final source = SaWorkspaceRemote._positive(row['source_clinic_id']);
        final destination = SaWorkspaceRemote._positive(
          row['destination_clinic_id'],
        );
        SaWorkspaceRemote._require(
          source != destination &&
              s.clinic != null &&
              (source == s.clinic ||
                  (scope == 'transfer' && destination == s.clinic)),
        );
        SaWorkspaceRemote._positive(row['item_id']);
        SaWorkspaceRemote._positive(row['quantity']);
        final requester = SaWorkspaceRemote._positive(row['requested_by_id']);
        if (const {
          'approved',
          'in_transit',
          'completed',
        }.contains(row['status'])) {
          final approver = SaWorkspaceRemote._positive(row['approved_by_id']);
          SaWorkspaceRemote._require(approver != requester);
        }
        SaWorkspaceRemote._require(
          const {
            'pending',
            'approved',
            'in_transit',
            'completed',
            'cancelled',
          }.contains(row['status']),
        );
      } else if (scope == 'supplier') {
        SaWorkspaceRemote._require(
          row.containsKey('clinic_id') &&
              (row['clinic_id'] == null || row['clinic_id'] == s.clinic),
        );
      } else {
        // These routes use verified server membership or parent scope; not
        // every serializer repeats clinic_id. Reject contradictory scope.
        SaWorkspaceRemote._require(
          !row.containsKey('clinic_id') || row['clinic_id'] == s.clinic,
        );
      }
      for (final e in match.entries) {
        if (select && e.key == 'id') {
          continue;
        }
        final wanted = q.values[e.value];
        if (wanted != null) {
          SaWorkspaceRemote._require(row[e.key] == wanted);
        }
      }
      final projected = SaWorkspaceRemote._project(row, keys.toSet());
      SaWorkspaceRemote._require(
        projected.values.every(
          (v) =>
              v == null ||
              v is bool ||
              v is num ||
              (v is String && v.length <= 12000),
        ),
      );
      if (!select || id == q.values['id']) {
        rows.add(projected);
      }
    }
    if (pagination != null) {
      final requested = select
          ? q.values['lookup_page'] as int
          : q.values['page'] as int?;
      final per = select ? 20 : q.values['per_page'];
      SaWorkspaceRemote._require(per == null || pagination['per_page'] == per);
      final checked = _page(
        pagination,
        select ? incoming : rows,
        requested: requested,
      );
      if (select) {
        SaWorkspaceRemote._require(rows.length == 1);
        return SaResult(rows);
      }
      return checked;
    }
    return SaResult(rows);
  }

  SaResult _adminReadResult(
    SaSession s,
    SaRequest q,
    Map<String, dynamic> raw,
  ) {
    _adminTarget(s, q);
    final spec = q.operation.adminSpec!;
    return _adminParse(
      s,
      q,
      raw,
      spec.shape,
      spec.scope,
      spec.keys,
      spec.match,
    );
  }

  Future<List<Map<String, dynamic>>> _adminPreflight(
    SaSession s,
    SaRequest q,
  ) async {
    _adminTarget(s, q);
    final spec = q.operation.adminSpec!;
    final rows = <Map<String, dynamic>>[];
    for (final a in spec.anchors) {
      if (a.skip.isNotEmpty && q.values[a.skip] == null) {
        continue;
      }
      final path = 'api/v1/${SaAdminSpec.render(a.path, q.values)}';
      final parsed = _adminParse(
        s,
        q,
        await _get(s, q.operation, path),
        a.shape,
        a.scope,
        a.keys,
        a.match,
        select: a.select,
      );
      SaWorkspaceRemote._require(parsed.rows.length == 1);
      final row = parsed.rows.single;
      for (final e in a.state.entries) {
        SaWorkspaceRemote._require(e.value.contains(row[e.key]));
      }
      if (q.operation == SaOperation.adminBedMaintenance &&
          a.shape == 'raw' &&
          a.keys.contains('bed_number')) {
        SaWorkspaceRemote._require(
          row['status'] ==
              (q.body['under_maintenance'] == true
                  ? 'available'
                  : 'maintenance'),
        );
      }
      if (q.operation == SaOperation.adminTransferCreate) {
        final available = row['quantity_on_hand'];
        SaWorkspaceRemote._require(
          available is int && available >= (q.values['quantity'] as int),
        );
      }
      rows.add(row);
    }
    return rows;
  }

  String _adminKey(SaSession s, SaRequest q) {
    final spec = q.operation.adminSpec!;
    // Session-only lock. No persisted input, request retry or auto replay.
    return 'admin-workspace|${s.actor.userId}|${s.actor.tenantId}|${s.clinic}|${q.operation.name}|${q.path}|${jsonEncode([for (final k in spec.createKey) q.values[k]])}';
  }

  SaResult _adminReceipt(
    SaSession s,
    SaRequest q,
    Map<String, dynamic> raw,
    List<Map<String, dynamic>> latest,
  ) {
    _adminTarget(s, q);
    final spec = q.operation.adminSpec!;
    if (spec.shape == 'familyDelete') {
      SaWorkspaceRemote._require(
        raw['success'] == true &&
            raw['message'] == 'Family member removed successfully',
      );
      return SaResult([
        {'id': q.values['id'], 'patient_id': q.values['patient_id']},
      ], note: 'Server confirmed family contact removal.');
    }
    final result = _adminParse(
      s,
      q,
      raw,
      spec.shape,
      spec.scope,
      spec.keys,
      spec.match,
    );
    final row = result.rows.single;
    for (final e in spec.expected.entries) {
      SaWorkspaceRemote._require(row[e.key] == e.value);
    }
    for (final key in spec.echo) {
      if (!q.body.containsKey(key)) {
        continue;
      }
      if (spec.decimal.contains(key)) {
        SaWorkspaceRemote._require(
          SaWorkspaceRemote._money(row[key]) ==
              SaWorkspaceRemote._money(q.body[key]),
        );
      } else {
        SaWorkspaceRemote._require(row[key] == q.body[key]);
      }
    }
    if (const {
      SaOperation.adminMaintenanceStart,
      SaOperation.adminMaintenanceComplete,
      SaOperation.adminMaintenanceCancel,
    }.contains(q.operation)) {
      SaWorkspaceRemote._require(row['asset_id'] == latest.single['asset_id']);
    }
    if (const {
      SaOperation.adminSettingsEnable,
      SaOperation.adminSettingsDisable,
      SaOperation.adminIntegrationEnable,
      SaOperation.adminIntegrationDisable,
    }.contains(q.operation)) {
      SaWorkspaceRemote._require(row['id'] == latest.single['id']);
    }
    if (q.operation == SaOperation.adminStockMovement) {
      final quantity = q.body['quantity'] as int;
      final type = q.body['movement_type'];
      final incoming =
          type == 'restock' || (type == 'adjustment' && quantity > 0);
      SaWorkspaceRemote._require(
        row['quantity'] == quantity.abs() &&
            row['batch_id'] == q.body['batch_id'] &&
            row['direction'] == (incoming ? 'in' : 'out'),
      );
    }
    if (const {
      SaOperation.adminTransferApprove,
      SaOperation.adminTransferComplete,
      SaOperation.adminTransferCancel,
    }.contains(q.operation)) {
      for (final key in const [
        'item_id',
        'batch_id',
        'source_clinic_id',
        'destination_clinic_id',
        'quantity',
        'requested_by_id',
      ]) {
        SaWorkspaceRemote._require(row[key] == latest.single[key]);
      }
    }
    if (q.operation == SaOperation.adminTransferComplete) {
      SaWorkspaceRemote._require(
        row['approved_by_id'] == latest.single['approved_by_id'],
      );
    }
    if (q.operation == SaOperation.adminTransferCreate) {
      SaWorkspaceRemote._require(row['batch_id'] == q.body['batch_id']);
    }
    if (q.operation == SaOperation.adminBedMaintenance) {
      SaWorkspaceRemote._require(
        row['status'] ==
            (q.body['under_maintenance'] == true ? 'maintenance' : 'available'),
      );
    }
    if (q.operation == SaOperation.adminTheatreCreate) {
      SaWorkspaceRemote._require(
        raw['documentation_only'] == true &&
            const [
              'operative_completion_authorized',
              'anesthesia_induction_authorized',
              'medication_administration_authorized',
              'nursing_transfer_authorized',
              'billing_clearance_authorized',
            ].every((k) => raw[k] == false),
      );
    }
    return result;
  }
}
