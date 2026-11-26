part of 'run2_workspace.dart';

class _ModuleTarget {
  const _ModuleTarget(this.selector, this.identity);
  final int selector;
  final Map<String, dynamic> identity;
}

class Run2ModuleItem {
  const Run2ModuleItem({
    required this.id,
    required this.type,
    required this.kind,
    required this.label,
    required this.capturedAt,
    required this.visible,
    this.selector,
    this.targetId,
    this.targetPatientId,
    this.rows = const [],
    this.fields = const {},
  });
  final String id, type, kind, label;
  final DateTime capturedAt;
  final bool visible;
  final int? selector, targetId, targetPatientId;
  final List<Map<String, dynamic>> rows;
  final Map<String, String> fields;
  bool get isDraft => type == Run2ModuleCatalog.draftType;
}

/// Module-specific working documents and historical reads use the same
/// encrypted Drift store, scope and short online-established lease. There is
/// no POST/PATCH path. A draft is not a validated server request or signature.
extension Run2ModuleWorkspace on Run2Workspace {
  Run2ModuleTransport get _modules {
    final value = moduleTransport;
    if (value == null) {
      throw const Run2WorkspaceUnavailable();
    }
    return value;
  }

  Future<String> moduleRole() async => (await _authorized()).role;

  ModuleReadSpec _readSpec(_Stamp stamp, String key) {
    final spec = Run2ModuleCatalog.reads[key];
    if (spec == null ||
        !spec.roles.contains(stamp.role) ||
        (spec.managed && !managedRecords)) {
      throw const Run2WorkspaceUnavailable();
    }
    return spec;
  }

  ModuleDraftSpec _draftSpec(_Stamp stamp, String key) {
    final spec = Run2ModuleCatalog.drafts[key];
    if (!managedRecords || spec == null || !spec.roles.contains(stamp.role)) {
      throw const Run2WorkspaceUnavailable();
    }
    return spec;
  }

  Future<void> _freshScope(_Stamp stamp) async {
    await _checkLease(stamp);
    try {
      await transport.verifyScope(stamp.session);
      await _checkLease(stamp);
    } on Object {
      clearLease();
      rethrow;
    }
  }

  Future<String> _writeLocal(
    _Stamp stamp,
    String type,
    Map<String, dynamic> values,
  ) async {
    if (!Run2ModuleCatalog.localTypes.contains(type)) {
      throw const Run2WorkspaceUnavailable();
    }
    await store.purgeRun2ExpiredModuleRecords(now: _now);
    if ((await _rows(stamp, type)).length >= 30) {
      throw StateError('Module work capacity reached.');
    }
    final now = _now;
    final identity = Run2Policy.identity(type);
    final payload = <String, dynamic>{
      'role': stamp.role,
      'captured_at': now.toIso8601String(),
      ...values,
    };
    await _checkLease(stamp);
    final result = await store.enqueueEncrypted(
      identity,
      context: stamp.context,
      payload: payload,
      now: now,
    );
    if (result != OperationQueueEnqueueResult.inserted) {
      throw const Run2WorkspaceUnavailable();
    }
    await _checkLease(stamp);
    _visibleModuleItems.add(identity.operationId);
    return identity.operationId;
  }

  Future<String> captureModuleSnapshot(String key, int selector) =>
      _edit(() async {
        final stamp = await _authorized();
        final spec = _readSpec(stamp, key);
        await _freshScope(stamp);
        final rows = await _modules.capture(stamp.session, spec, selector);
        await _checkLease(stamp);
        return _writeLocal(stamp, Run2ModuleCatalog.snapshotType, {
          'kind': key,
          'selector': selector,
          'rows': rows,
        });
      });

  /// Establishes a particular live target before offline authoring. The grant
  /// is memory-only and disappears with the lease, including at restart.
  Future<int> prepareModuleDraft(String key, int selector) => _edit(() async {
    final stamp = await _authorized();
    final spec = _draftSpec(stamp, key);
    _moduleTargets.remove('$key:$selector');
    if (spec.anchor.isEmpty) {
      throw const Run2WorkspaceUnavailable();
    }
    final anchor = Run2ModuleCatalog.anchor(spec.anchor)!;
    await _freshScope(stamp);
    final target = await _modules.verifyTarget(stamp.session, anchor, selector);
    await _checkLease(stamp);
    _moduleTargets['$key:$selector'] = _ModuleTarget(selector, target);
    return target['record_id'] as int;
  });

  static void _checkLabItem(
    ModuleDraftSpec spec,
    Map<String, String> fields,
    Map<String, dynamic> target,
  ) {
    if (spec.key == 'lab_result') {
      final ids = target['item_ids'];
      if (ids is! List ||
          !ids.contains(int.tryParse(fields['order_item_id'] ?? ''))) {
        throw const FormatException('Lab item is not in the verified order.');
      }
    }
  }

  /// Metadata only for human identity confirmation; never a clinical grant.
  Future<int?> verifiedDraftPatient(String key, int selector) async {
    final stamp = await _authorized();
    final spec = _draftSpec(stamp, key);
    final target = _moduleTargets['$key:$selector'];
    if (target == null) {
      throw const Run2WorkspaceUnavailable();
    }
    final value = spec.anchor == 'patient'
        ? selector
        : target.identity['patient_id'];
    return value is int ? value : null;
  }

  Future<String> saveModuleDraft(
    String key,
    int selector,
    Map<String, String> values,
  ) => _edit(() async {
    final stamp = await _authorized();
    final spec = _draftSpec(stamp, key);
    final fields = Run2ModuleCatalog.validateDraft(spec, values);
    final grant = _moduleTargets['$key:$selector'];
    int targetId = 0;
    int? targetPatientId;
    if (spec.anchor.isEmpty) {
      if (selector != 0) {
        throw const Run2WorkspaceUnavailable();
      }
    } else {
      if (grant == null || grant.selector != selector) {
        throw const Run2WorkspaceUnavailable();
      }
      targetId = grant.identity['record_id'] as int;
      final patientId = grant.identity['patient_id'];
      targetPatientId = spec.anchor == 'patient'
          ? selector
          : (patientId is int && patientId > 0 ? patientId : null);
      _checkLabItem(spec, fields, grant.identity);
    }
    // Only the user's own typed draft and selected record identity are stored.
    // No raw readback, signing status, credential or server authorization grant.
    return _writeLocal(stamp, Run2ModuleCatalog.draftType, {
      'kind': key,
      'selector': selector,
      'target_id': targetId,
      'target_patient_id': targetPatientId,
      'fields': fields,
    });
  });

  Future<Run2ModuleItem> _decodeModule(
    _Stamp stamp,
    OperationQueueEntryData row, {
    required bool reveal,
  }) async {
    final p = await store.loadPayload(
      operationId: row.operationId,
      context: stamp.context,
    );
    if (row.lifecycleState != OperationLifecycleState.queued.name ||
        row.attemptsCompleted != 0 ||
        p['role'] != stamp.role ||
        p['kind'] is! String ||
        p['selector'] is! int) {
      throw const FormatException('Invalid local module record.');
    }
    final captured = Run2Policy.captured(p);
    final age = _now.difference(captured);
    if (age.isNegative || age >= Run2ModuleCatalog.retention) {
      throw const Run2WorkspaceUnavailable();
    }
    final key = p['kind'] as String;
    final selector = p['selector'] as int;
    if (row.operationType == Run2ModuleCatalog.snapshotType) {
      final spec = _readSpec(stamp, key);
      if (p.length != 5 || p['rows'] is! List) {
        throw const FormatException('Invalid saved snapshot.');
      }
      final rows = (p['rows'] as List)
          .map(Run2ModuleTransport.object)
          .toList(growable: false);
      Run2ModuleTransport.validateProjection(spec, rows, selector);
      if (key == 'notifications' &&
          rows.any((r) => r['user_id'] != stamp.session.userId)) {
        throw const FormatException('Stored notification owner mismatch.');
      }
      return Run2ModuleItem(
        id: row.operationId,
        type: row.operationType,
        kind: key,
        label: spec.label,
        capturedAt: captured,
        visible: reveal,
        selector: reveal ? selector : null,
        rows: reveal
            ? List.unmodifiable(
                rows.map((r) => Map<String, dynamic>.unmodifiable(r)),
              )
            : const [],
      );
    }
    final spec = _draftSpec(stamp, key);
    if (row.operationType != Run2ModuleCatalog.draftType ||
        p.length != 7 ||
        p['target_id'] is! int) {
      throw const FormatException('Invalid saved working draft.');
    }
    final map = Run2ModuleTransport.object(p['fields']);
    if (map.values.any((v) => v is! String)) {
      throw const FormatException('Invalid saved draft fields.');
    }
    final fields = Run2ModuleCatalog.validateDraft(
      spec,
      Map<String, String>.from(map),
    );
    final targetId = p['target_id'] as int;
    final patient = p['target_patient_id'];
    if (patient != null && (patient is! int || patient <= 0)) {
      throw const FormatException('Invalid saved patient lineage.');
    }
    if (spec.anchor.isEmpty
        ? (selector != 0 || targetId != 0)
        : (selector <= 0 || targetId <= 0)) {
      throw const FormatException('Invalid saved target identity.');
    }
    return Run2ModuleItem(
      id: row.operationId,
      type: row.operationType,
      kind: key,
      label: spec.label,
      capturedAt: captured,
      visible: reveal,
      selector: reveal ? selector : null,
      targetId: reveal ? targetId : null,
      targetPatientId: reveal ? (patient as int?) : null,
      fields: reveal ? fields : const {},
    );
  }

  Future<List<Run2ModuleItem>> moduleItems() async {
    final stamp = await _authorized();
    await store.purgeRun2ExpiredModuleRecords(now: _now);
    final result = <Run2ModuleItem>[];
    for (final type in Run2ModuleCatalog.localTypes) {
      for (final row in await _rows(stamp, type)) {
        await _checkLease(stamp);
        // Policy-disabled or old records are hidden, not reclassified or sent.
        try {
          final item = await _decodeModule(
            stamp,
            row,
            reveal: _visibleModuleItems.contains(row.operationId),
          );
          result.add(item);
        } on Run2WorkspaceUnavailable {
          // Expired, future-clock or policy-disabled records are not displayed.
        }
      }
    }
    await _checkLease(stamp);
    result.sort((a, b) => b.capturedAt.compareTo(a.capturedAt));
    return List.unmodifiable(result);
  }

  static bool _sameLineage(
    ModuleReadSpec spec,
    Map<String, dynamic> saved,
    Map<String, dynamic> live,
  ) {
    if (spec.identity.isNotEmpty &&
        saved[spec.identity] != live[spec.identity]) {
      return false;
    }
    return [
      'patient_id',
      'admission_id',
      'staff_id',
      'user_id',
    ].every((key) => !saved.containsKey(key) || saved[key] == live[key]);
  }

  /// Old records after a new online lease remain sealed in the UI until a
  /// resource-specific live read is authorized again. A changed patient or
  /// admission identity denies disclosure. A draft is never automatically sent.
  Future<void> reopenModuleItemOnline(String id) => _edit(() async {
    final stamp = await _authorized();
    final row = await store.findByOperationId(id);
    if (row == null ||
        !Run2ModuleCatalog.localTypes.contains(row.operationType) ||
        row.ownerUserId != stamp.context.userId ||
        row.clinicContextId != stamp.context.clinicContextId) {
      throw const Run2WorkspaceUnavailable();
    }
    _visibleModuleItems.remove(id);
    final item = await _decodeModule(stamp, row, reveal: true);
    await _freshScope(stamp);
    if (!item.isDraft) {
      final spec = _readSpec(stamp, item.kind);
      final current = await _modules.capture(
        stamp.session,
        spec,
        item.selector!,
      );
      if (item.rows.any(
        (saved) => !current.any((live) => _sameLineage(spec, saved, live)),
      )) {
        throw const Run2WorkspaceUnavailable();
      }
    } else {
      final spec = _draftSpec(stamp, item.kind);
      if (spec.anchor.isNotEmpty) {
        final current = await _modules.verifyTarget(
          stamp.session,
          Run2ModuleCatalog.anchor(spec.anchor)!,
          item.selector!,
        );
        final patientId = spec.anchor == 'patient'
            ? item.selector
            : current['patient_id'];
        if (current['record_id'] != item.targetId ||
            patientId != item.targetPatientId) {
          throw const Run2WorkspaceUnavailable();
        }
        _checkLabItem(spec, item.fields, current);
        _moduleTargets['${item.kind}:${item.selector}'] = _ModuleTarget(
          item.selector!,
          current,
        );
      }
    }
    await _checkLease(stamp);
    _visibleModuleItems.add(id);
  });

  Future<void> deleteModuleItem(String id, String type) => _edit(() async {
    final stamp = await _authorized();
    if (!Run2ModuleCatalog.localTypes.contains(type)) {
      throw const Run2WorkspaceUnavailable();
    }
    await store.removeRun2LocalOnly(
      operationId: id,
      context: stamp.context,
      type: type,
    );
    await _checkLease(stamp);
    _visibleModuleItems.remove(id);
  });
}
