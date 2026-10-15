// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'app_database.dart';

// ignore_for_file: type=lint
class $AppMetadataTable extends AppMetadata
    with TableInfo<$AppMetadataTable, AppMetadataData> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $AppMetadataTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _keyMeta = const VerificationMeta('key');
  @override
  late final GeneratedColumn<String> key = GeneratedColumn<String>(
    'key',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _valueMeta = const VerificationMeta('value');
  @override
  late final GeneratedColumn<String> value = GeneratedColumn<String>(
    'value',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> updatedAt = GeneratedColumn<DateTime>(
    'updated_at',
    aliasedName,
    true,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: false,
  );
  @override
  List<GeneratedColumn> get $columns => [key, value, updatedAt];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'app_metadata';
  @override
  VerificationContext validateIntegrity(
    Insertable<AppMetadataData> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('key')) {
      context.handle(
        _keyMeta,
        key.isAcceptableOrUnknown(data['key']!, _keyMeta),
      );
    } else if (isInserting) {
      context.missing(_keyMeta);
    }
    if (data.containsKey('value')) {
      context.handle(
        _valueMeta,
        value.isAcceptableOrUnknown(data['value']!, _valueMeta),
      );
    } else if (isInserting) {
      context.missing(_valueMeta);
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {key};
  @override
  AppMetadataData map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return AppMetadataData(
      key: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}key'],
      )!,
      value: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}value'],
      )!,
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}updated_at'],
      ),
    );
  }

  @override
  $AppMetadataTable createAlias(String alias) {
    return $AppMetadataTable(attachedDatabase, alias);
  }
}

class AppMetadataData extends DataClass implements Insertable<AppMetadataData> {
  final String key;
  final String value;
  final DateTime? updatedAt;
  const AppMetadataData({
    required this.key,
    required this.value,
    this.updatedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['key'] = Variable<String>(key);
    map['value'] = Variable<String>(value);
    if (!nullToAbsent || updatedAt != null) {
      map['updated_at'] = Variable<DateTime>(updatedAt);
    }
    return map;
  }

  AppMetadataCompanion toCompanion(bool nullToAbsent) {
    return AppMetadataCompanion(
      key: Value(key),
      value: Value(value),
      updatedAt: updatedAt == null && nullToAbsent
          ? const Value.absent()
          : Value(updatedAt),
    );
  }

  factory AppMetadataData.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return AppMetadataData(
      key: serializer.fromJson<String>(json['key']),
      value: serializer.fromJson<String>(json['value']),
      updatedAt: serializer.fromJson<DateTime?>(json['updatedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'key': serializer.toJson<String>(key),
      'value': serializer.toJson<String>(value),
      'updatedAt': serializer.toJson<DateTime?>(updatedAt),
    };
  }

  AppMetadataData copyWith({
    String? key,
    String? value,
    Value<DateTime?> updatedAt = const Value.absent(),
  }) => AppMetadataData(
    key: key ?? this.key,
    value: value ?? this.value,
    updatedAt: updatedAt.present ? updatedAt.value : this.updatedAt,
  );
  AppMetadataData copyWithCompanion(AppMetadataCompanion data) {
    return AppMetadataData(
      key: data.key.present ? data.key.value : this.key,
      value: data.value.present ? data.value.value : this.value,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('AppMetadataData(')
          ..write('key: $key, ')
          ..write('value: $value, ')
          ..write('updatedAt: $updatedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(key, value, updatedAt);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is AppMetadataData &&
          other.key == this.key &&
          other.value == this.value &&
          other.updatedAt == this.updatedAt);
}

class AppMetadataCompanion extends UpdateCompanion<AppMetadataData> {
  final Value<String> key;
  final Value<String> value;
  final Value<DateTime?> updatedAt;
  final Value<int> rowid;
  const AppMetadataCompanion({
    this.key = const Value.absent(),
    this.value = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  AppMetadataCompanion.insert({
    required String key,
    required String value,
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  }) : key = Value(key),
       value = Value(value);
  static Insertable<AppMetadataData> custom({
    Expression<String>? key,
    Expression<String>? value,
    Expression<DateTime>? updatedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (key != null) 'key': key,
      if (value != null) 'value': value,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  AppMetadataCompanion copyWith({
    Value<String>? key,
    Value<String>? value,
    Value<DateTime?>? updatedAt,
    Value<int>? rowid,
  }) {
    return AppMetadataCompanion(
      key: key ?? this.key,
      value: value ?? this.value,
      updatedAt: updatedAt ?? this.updatedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (key.present) {
      map['key'] = Variable<String>(key.value);
    }
    if (value.present) {
      map['value'] = Variable<String>(value.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<DateTime>(updatedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('AppMetadataCompanion(')
          ..write('key: $key, ')
          ..write('value: $value, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $OperationQueueEntryTable extends OperationQueueEntry
    with TableInfo<$OperationQueueEntryTable, OperationQueueEntryData> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $OperationQueueEntryTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _operationIdMeta = const VerificationMeta(
    'operationId',
  );
  @override
  late final GeneratedColumn<String> operationId = GeneratedColumn<String>(
    'operation_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _operationTypeMeta = const VerificationMeta(
    'operationType',
  );
  @override
  late final GeneratedColumn<String> operationType = GeneratedColumn<String>(
    'operation_type',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _idempotencyKeyMeta = const VerificationMeta(
    'idempotencyKey',
  );
  @override
  late final GeneratedColumn<String> idempotencyKey = GeneratedColumn<String>(
    'idempotency_key',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
    defaultConstraints: GeneratedColumn.constraintIsAlways('UNIQUE'),
  );
  static const VerificationMeta _ownerUserIdMeta = const VerificationMeta(
    'ownerUserId',
  );
  @override
  late final GeneratedColumn<String> ownerUserId = GeneratedColumn<String>(
    'owner_user_id',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _clinicContextIdMeta = const VerificationMeta(
    'clinicContextId',
  );
  @override
  late final GeneratedColumn<String> clinicContextId = GeneratedColumn<String>(
    'clinic_context_id',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _lifecycleStateMeta = const VerificationMeta(
    'lifecycleState',
  );
  @override
  late final GeneratedColumn<String> lifecycleState = GeneratedColumn<String>(
    'lifecycle_state',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
    defaultValue: const Constant('queued'),
  );
  static const VerificationMeta _attemptsCompletedMeta = const VerificationMeta(
    'attemptsCompleted',
  );
  @override
  late final GeneratedColumn<int> attemptsCompleted = GeneratedColumn<int>(
    'attempts_completed',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: false,
    defaultValue: const Constant(0),
  );
  static const VerificationMeta _createdAtMeta = const VerificationMeta(
    'createdAt',
  );
  @override
  late final GeneratedColumn<DateTime> createdAt = GeneratedColumn<DateTime>(
    'created_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> updatedAt = GeneratedColumn<DateTime>(
    'updated_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _nextAttemptAtMeta = const VerificationMeta(
    'nextAttemptAt',
  );
  @override
  late final GeneratedColumn<DateTime> nextAttemptAt =
      GeneratedColumn<DateTime>(
        'next_attempt_at',
        aliasedName,
        true,
        type: DriftSqlType.dateTime,
        requiredDuringInsert: false,
      );
  static const VerificationMeta _lastFailureCategoryMeta =
      const VerificationMeta('lastFailureCategory');
  @override
  late final GeneratedColumn<String> lastFailureCategory =
      GeneratedColumn<String>(
        'last_failure_category',
        aliasedName,
        true,
        type: DriftSqlType.string,
        requiredDuringInsert: false,
      );
  @override
  List<GeneratedColumn> get $columns => [
    operationId,
    operationType,
    idempotencyKey,
    ownerUserId,
    clinicContextId,
    lifecycleState,
    attemptsCompleted,
    createdAt,
    updatedAt,
    nextAttemptAt,
    lastFailureCategory,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'operation_queue_entry';
  @override
  VerificationContext validateIntegrity(
    Insertable<OperationQueueEntryData> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('operation_id')) {
      context.handle(
        _operationIdMeta,
        operationId.isAcceptableOrUnknown(
          data['operation_id']!,
          _operationIdMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_operationIdMeta);
    }
    if (data.containsKey('operation_type')) {
      context.handle(
        _operationTypeMeta,
        operationType.isAcceptableOrUnknown(
          data['operation_type']!,
          _operationTypeMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_operationTypeMeta);
    }
    if (data.containsKey('idempotency_key')) {
      context.handle(
        _idempotencyKeyMeta,
        idempotencyKey.isAcceptableOrUnknown(
          data['idempotency_key']!,
          _idempotencyKeyMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_idempotencyKeyMeta);
    }
    if (data.containsKey('owner_user_id')) {
      context.handle(
        _ownerUserIdMeta,
        ownerUserId.isAcceptableOrUnknown(
          data['owner_user_id']!,
          _ownerUserIdMeta,
        ),
      );
    }
    if (data.containsKey('clinic_context_id')) {
      context.handle(
        _clinicContextIdMeta,
        clinicContextId.isAcceptableOrUnknown(
          data['clinic_context_id']!,
          _clinicContextIdMeta,
        ),
      );
    }
    if (data.containsKey('lifecycle_state')) {
      context.handle(
        _lifecycleStateMeta,
        lifecycleState.isAcceptableOrUnknown(
          data['lifecycle_state']!,
          _lifecycleStateMeta,
        ),
      );
    }
    if (data.containsKey('attempts_completed')) {
      context.handle(
        _attemptsCompletedMeta,
        attemptsCompleted.isAcceptableOrUnknown(
          data['attempts_completed']!,
          _attemptsCompletedMeta,
        ),
      );
    }
    if (data.containsKey('created_at')) {
      context.handle(
        _createdAtMeta,
        createdAt.isAcceptableOrUnknown(data['created_at']!, _createdAtMeta),
      );
    } else if (isInserting) {
      context.missing(_createdAtMeta);
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    } else if (isInserting) {
      context.missing(_updatedAtMeta);
    }
    if (data.containsKey('next_attempt_at')) {
      context.handle(
        _nextAttemptAtMeta,
        nextAttemptAt.isAcceptableOrUnknown(
          data['next_attempt_at']!,
          _nextAttemptAtMeta,
        ),
      );
    }
    if (data.containsKey('last_failure_category')) {
      context.handle(
        _lastFailureCategoryMeta,
        lastFailureCategory.isAcceptableOrUnknown(
          data['last_failure_category']!,
          _lastFailureCategoryMeta,
        ),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {operationId};
  @override
  OperationQueueEntryData map(
    Map<String, dynamic> data, {
    String? tablePrefix,
  }) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return OperationQueueEntryData(
      operationId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}operation_id'],
      )!,
      operationType: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}operation_type'],
      )!,
      idempotencyKey: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}idempotency_key'],
      )!,
      ownerUserId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}owner_user_id'],
      ),
      clinicContextId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}clinic_context_id'],
      ),
      lifecycleState: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}lifecycle_state'],
      )!,
      attemptsCompleted: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}attempts_completed'],
      )!,
      createdAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}created_at'],
      )!,
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}updated_at'],
      )!,
      nextAttemptAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}next_attempt_at'],
      ),
      lastFailureCategory: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}last_failure_category'],
      ),
    );
  }

  @override
  $OperationQueueEntryTable createAlias(String alias) {
    return $OperationQueueEntryTable(attachedDatabase, alias);
  }
}

class OperationQueueEntryData extends DataClass
    implements Insertable<OperationQueueEntryData> {
  final String operationId;
  final String operationType;
  final String idempotencyKey;

  /// Owner at queue creation; delivery must validate against the active session.
  /// Nullable only for metadata-only entries, which must not be replayed.
  final String? ownerUserId;
  final String? clinicContextId;
  final String lifecycleState;
  final int attemptsCompleted;
  final DateTime createdAt;
  final DateTime updatedAt;
  final DateTime? nextAttemptAt;

  /// A classified failure category only, never a raw server-error message.
  final String? lastFailureCategory;
  const OperationQueueEntryData({
    required this.operationId,
    required this.operationType,
    required this.idempotencyKey,
    this.ownerUserId,
    this.clinicContextId,
    required this.lifecycleState,
    required this.attemptsCompleted,
    required this.createdAt,
    required this.updatedAt,
    this.nextAttemptAt,
    this.lastFailureCategory,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['operation_id'] = Variable<String>(operationId);
    map['operation_type'] = Variable<String>(operationType);
    map['idempotency_key'] = Variable<String>(idempotencyKey);
    if (!nullToAbsent || ownerUserId != null) {
      map['owner_user_id'] = Variable<String>(ownerUserId);
    }
    if (!nullToAbsent || clinicContextId != null) {
      map['clinic_context_id'] = Variable<String>(clinicContextId);
    }
    map['lifecycle_state'] = Variable<String>(lifecycleState);
    map['attempts_completed'] = Variable<int>(attemptsCompleted);
    map['created_at'] = Variable<DateTime>(createdAt);
    map['updated_at'] = Variable<DateTime>(updatedAt);
    if (!nullToAbsent || nextAttemptAt != null) {
      map['next_attempt_at'] = Variable<DateTime>(nextAttemptAt);
    }
    if (!nullToAbsent || lastFailureCategory != null) {
      map['last_failure_category'] = Variable<String>(lastFailureCategory);
    }
    return map;
  }

  OperationQueueEntryCompanion toCompanion(bool nullToAbsent) {
    return OperationQueueEntryCompanion(
      operationId: Value(operationId),
      operationType: Value(operationType),
      idempotencyKey: Value(idempotencyKey),
      ownerUserId: ownerUserId == null && nullToAbsent
          ? const Value.absent()
          : Value(ownerUserId),
      clinicContextId: clinicContextId == null && nullToAbsent
          ? const Value.absent()
          : Value(clinicContextId),
      lifecycleState: Value(lifecycleState),
      attemptsCompleted: Value(attemptsCompleted),
      createdAt: Value(createdAt),
      updatedAt: Value(updatedAt),
      nextAttemptAt: nextAttemptAt == null && nullToAbsent
          ? const Value.absent()
          : Value(nextAttemptAt),
      lastFailureCategory: lastFailureCategory == null && nullToAbsent
          ? const Value.absent()
          : Value(lastFailureCategory),
    );
  }

  factory OperationQueueEntryData.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return OperationQueueEntryData(
      operationId: serializer.fromJson<String>(json['operationId']),
      operationType: serializer.fromJson<String>(json['operationType']),
      idempotencyKey: serializer.fromJson<String>(json['idempotencyKey']),
      ownerUserId: serializer.fromJson<String?>(json['ownerUserId']),
      clinicContextId: serializer.fromJson<String?>(json['clinicContextId']),
      lifecycleState: serializer.fromJson<String>(json['lifecycleState']),
      attemptsCompleted: serializer.fromJson<int>(json['attemptsCompleted']),
      createdAt: serializer.fromJson<DateTime>(json['createdAt']),
      updatedAt: serializer.fromJson<DateTime>(json['updatedAt']),
      nextAttemptAt: serializer.fromJson<DateTime?>(json['nextAttemptAt']),
      lastFailureCategory: serializer.fromJson<String?>(
        json['lastFailureCategory'],
      ),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'operationId': serializer.toJson<String>(operationId),
      'operationType': serializer.toJson<String>(operationType),
      'idempotencyKey': serializer.toJson<String>(idempotencyKey),
      'ownerUserId': serializer.toJson<String?>(ownerUserId),
      'clinicContextId': serializer.toJson<String?>(clinicContextId),
      'lifecycleState': serializer.toJson<String>(lifecycleState),
      'attemptsCompleted': serializer.toJson<int>(attemptsCompleted),
      'createdAt': serializer.toJson<DateTime>(createdAt),
      'updatedAt': serializer.toJson<DateTime>(updatedAt),
      'nextAttemptAt': serializer.toJson<DateTime?>(nextAttemptAt),
      'lastFailureCategory': serializer.toJson<String?>(lastFailureCategory),
    };
  }

  OperationQueueEntryData copyWith({
    String? operationId,
    String? operationType,
    String? idempotencyKey,
    Value<String?> ownerUserId = const Value.absent(),
    Value<String?> clinicContextId = const Value.absent(),
    String? lifecycleState,
    int? attemptsCompleted,
    DateTime? createdAt,
    DateTime? updatedAt,
    Value<DateTime?> nextAttemptAt = const Value.absent(),
    Value<String?> lastFailureCategory = const Value.absent(),
  }) => OperationQueueEntryData(
    operationId: operationId ?? this.operationId,
    operationType: operationType ?? this.operationType,
    idempotencyKey: idempotencyKey ?? this.idempotencyKey,
    ownerUserId: ownerUserId.present ? ownerUserId.value : this.ownerUserId,
    clinicContextId: clinicContextId.present
        ? clinicContextId.value
        : this.clinicContextId,
    lifecycleState: lifecycleState ?? this.lifecycleState,
    attemptsCompleted: attemptsCompleted ?? this.attemptsCompleted,
    createdAt: createdAt ?? this.createdAt,
    updatedAt: updatedAt ?? this.updatedAt,
    nextAttemptAt: nextAttemptAt.present
        ? nextAttemptAt.value
        : this.nextAttemptAt,
    lastFailureCategory: lastFailureCategory.present
        ? lastFailureCategory.value
        : this.lastFailureCategory,
  );
  OperationQueueEntryData copyWithCompanion(OperationQueueEntryCompanion data) {
    return OperationQueueEntryData(
      operationId: data.operationId.present
          ? data.operationId.value
          : this.operationId,
      operationType: data.operationType.present
          ? data.operationType.value
          : this.operationType,
      idempotencyKey: data.idempotencyKey.present
          ? data.idempotencyKey.value
          : this.idempotencyKey,
      ownerUserId: data.ownerUserId.present
          ? data.ownerUserId.value
          : this.ownerUserId,
      clinicContextId: data.clinicContextId.present
          ? data.clinicContextId.value
          : this.clinicContextId,
      lifecycleState: data.lifecycleState.present
          ? data.lifecycleState.value
          : this.lifecycleState,
      attemptsCompleted: data.attemptsCompleted.present
          ? data.attemptsCompleted.value
          : this.attemptsCompleted,
      createdAt: data.createdAt.present ? data.createdAt.value : this.createdAt,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
      nextAttemptAt: data.nextAttemptAt.present
          ? data.nextAttemptAt.value
          : this.nextAttemptAt,
      lastFailureCategory: data.lastFailureCategory.present
          ? data.lastFailureCategory.value
          : this.lastFailureCategory,
    );
  }

  @override
  String toString() {
    return (StringBuffer('OperationQueueEntryData(')
          ..write('operationId: $operationId, ')
          ..write('operationType: $operationType, ')
          ..write('idempotencyKey: $idempotencyKey, ')
          ..write('ownerUserId: $ownerUserId, ')
          ..write('clinicContextId: $clinicContextId, ')
          ..write('lifecycleState: $lifecycleState, ')
          ..write('attemptsCompleted: $attemptsCompleted, ')
          ..write('createdAt: $createdAt, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('nextAttemptAt: $nextAttemptAt, ')
          ..write('lastFailureCategory: $lastFailureCategory')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    operationId,
    operationType,
    idempotencyKey,
    ownerUserId,
    clinicContextId,
    lifecycleState,
    attemptsCompleted,
    createdAt,
    updatedAt,
    nextAttemptAt,
    lastFailureCategory,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is OperationQueueEntryData &&
          other.operationId == this.operationId &&
          other.operationType == this.operationType &&
          other.idempotencyKey == this.idempotencyKey &&
          other.ownerUserId == this.ownerUserId &&
          other.clinicContextId == this.clinicContextId &&
          other.lifecycleState == this.lifecycleState &&
          other.attemptsCompleted == this.attemptsCompleted &&
          other.createdAt == this.createdAt &&
          other.updatedAt == this.updatedAt &&
          other.nextAttemptAt == this.nextAttemptAt &&
          other.lastFailureCategory == this.lastFailureCategory);
}

class OperationQueueEntryCompanion
    extends UpdateCompanion<OperationQueueEntryData> {
  final Value<String> operationId;
  final Value<String> operationType;
  final Value<String> idempotencyKey;
  final Value<String?> ownerUserId;
  final Value<String?> clinicContextId;
  final Value<String> lifecycleState;
  final Value<int> attemptsCompleted;
  final Value<DateTime> createdAt;
  final Value<DateTime> updatedAt;
  final Value<DateTime?> nextAttemptAt;
  final Value<String?> lastFailureCategory;
  final Value<int> rowid;
  const OperationQueueEntryCompanion({
    this.operationId = const Value.absent(),
    this.operationType = const Value.absent(),
    this.idempotencyKey = const Value.absent(),
    this.ownerUserId = const Value.absent(),
    this.clinicContextId = const Value.absent(),
    this.lifecycleState = const Value.absent(),
    this.attemptsCompleted = const Value.absent(),
    this.createdAt = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.nextAttemptAt = const Value.absent(),
    this.lastFailureCategory = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  OperationQueueEntryCompanion.insert({
    required String operationId,
    required String operationType,
    required String idempotencyKey,
    this.ownerUserId = const Value.absent(),
    this.clinicContextId = const Value.absent(),
    this.lifecycleState = const Value.absent(),
    this.attemptsCompleted = const Value.absent(),
    required DateTime createdAt,
    required DateTime updatedAt,
    this.nextAttemptAt = const Value.absent(),
    this.lastFailureCategory = const Value.absent(),
    this.rowid = const Value.absent(),
  }) : operationId = Value(operationId),
       operationType = Value(operationType),
       idempotencyKey = Value(idempotencyKey),
       createdAt = Value(createdAt),
       updatedAt = Value(updatedAt);
  static Insertable<OperationQueueEntryData> custom({
    Expression<String>? operationId,
    Expression<String>? operationType,
    Expression<String>? idempotencyKey,
    Expression<String>? ownerUserId,
    Expression<String>? clinicContextId,
    Expression<String>? lifecycleState,
    Expression<int>? attemptsCompleted,
    Expression<DateTime>? createdAt,
    Expression<DateTime>? updatedAt,
    Expression<DateTime>? nextAttemptAt,
    Expression<String>? lastFailureCategory,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (operationId != null) 'operation_id': operationId,
      if (operationType != null) 'operation_type': operationType,
      if (idempotencyKey != null) 'idempotency_key': idempotencyKey,
      if (ownerUserId != null) 'owner_user_id': ownerUserId,
      if (clinicContextId != null) 'clinic_context_id': clinicContextId,
      if (lifecycleState != null) 'lifecycle_state': lifecycleState,
      if (attemptsCompleted != null) 'attempts_completed': attemptsCompleted,
      if (createdAt != null) 'created_at': createdAt,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (nextAttemptAt != null) 'next_attempt_at': nextAttemptAt,
      if (lastFailureCategory != null)
        'last_failure_category': lastFailureCategory,
      if (rowid != null) 'rowid': rowid,
    });
  }

  OperationQueueEntryCompanion copyWith({
    Value<String>? operationId,
    Value<String>? operationType,
    Value<String>? idempotencyKey,
    Value<String?>? ownerUserId,
    Value<String?>? clinicContextId,
    Value<String>? lifecycleState,
    Value<int>? attemptsCompleted,
    Value<DateTime>? createdAt,
    Value<DateTime>? updatedAt,
    Value<DateTime?>? nextAttemptAt,
    Value<String?>? lastFailureCategory,
    Value<int>? rowid,
  }) {
    return OperationQueueEntryCompanion(
      operationId: operationId ?? this.operationId,
      operationType: operationType ?? this.operationType,
      idempotencyKey: idempotencyKey ?? this.idempotencyKey,
      ownerUserId: ownerUserId ?? this.ownerUserId,
      clinicContextId: clinicContextId ?? this.clinicContextId,
      lifecycleState: lifecycleState ?? this.lifecycleState,
      attemptsCompleted: attemptsCompleted ?? this.attemptsCompleted,
      createdAt: createdAt ?? this.createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
      nextAttemptAt: nextAttemptAt ?? this.nextAttemptAt,
      lastFailureCategory: lastFailureCategory ?? this.lastFailureCategory,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (operationId.present) {
      map['operation_id'] = Variable<String>(operationId.value);
    }
    if (operationType.present) {
      map['operation_type'] = Variable<String>(operationType.value);
    }
    if (idempotencyKey.present) {
      map['idempotency_key'] = Variable<String>(idempotencyKey.value);
    }
    if (ownerUserId.present) {
      map['owner_user_id'] = Variable<String>(ownerUserId.value);
    }
    if (clinicContextId.present) {
      map['clinic_context_id'] = Variable<String>(clinicContextId.value);
    }
    if (lifecycleState.present) {
      map['lifecycle_state'] = Variable<String>(lifecycleState.value);
    }
    if (attemptsCompleted.present) {
      map['attempts_completed'] = Variable<int>(attemptsCompleted.value);
    }
    if (createdAt.present) {
      map['created_at'] = Variable<DateTime>(createdAt.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<DateTime>(updatedAt.value);
    }
    if (nextAttemptAt.present) {
      map['next_attempt_at'] = Variable<DateTime>(nextAttemptAt.value);
    }
    if (lastFailureCategory.present) {
      map['last_failure_category'] = Variable<String>(
        lastFailureCategory.value,
      );
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('OperationQueueEntryCompanion(')
          ..write('operationId: $operationId, ')
          ..write('operationType: $operationType, ')
          ..write('idempotencyKey: $idempotencyKey, ')
          ..write('ownerUserId: $ownerUserId, ')
          ..write('clinicContextId: $clinicContextId, ')
          ..write('lifecycleState: $lifecycleState, ')
          ..write('attemptsCompleted: $attemptsCompleted, ')
          ..write('createdAt: $createdAt, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('nextAttemptAt: $nextAttemptAt, ')
          ..write('lastFailureCategory: $lastFailureCategory, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $OperationQueuePayloadTable extends OperationQueuePayload
    with TableInfo<$OperationQueuePayloadTable, OperationQueuePayloadData> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $OperationQueuePayloadTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _operationIdMeta = const VerificationMeta(
    'operationId',
  );
  @override
  late final GeneratedColumn<String> operationId = GeneratedColumn<String>(
    'operation_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'REFERENCES operation_queue_entry (operation_id) ON DELETE CASCADE',
    ),
  );
  static const VerificationMeta _ciphertextMeta = const VerificationMeta(
    'ciphertext',
  );
  @override
  late final GeneratedColumn<Uint8List> ciphertext = GeneratedColumn<Uint8List>(
    'ciphertext',
    aliasedName,
    false,
    type: DriftSqlType.blob,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _nonceMeta = const VerificationMeta('nonce');
  @override
  late final GeneratedColumn<Uint8List> nonce = GeneratedColumn<Uint8List>(
    'nonce',
    aliasedName,
    false,
    type: DriftSqlType.blob,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _macMeta = const VerificationMeta('mac');
  @override
  late final GeneratedColumn<Uint8List> mac = GeneratedColumn<Uint8List>(
    'mac',
    aliasedName,
    false,
    type: DriftSqlType.blob,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _encryptionSchemeMeta = const VerificationMeta(
    'encryptionScheme',
  );
  @override
  late final GeneratedColumn<String> encryptionScheme = GeneratedColumn<String>(
    'encryption_scheme',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _encryptionKeyVersionMeta =
      const VerificationMeta('encryptionKeyVersion');
  @override
  late final GeneratedColumn<int> encryptionKeyVersion = GeneratedColumn<int>(
    'encryption_key_version',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [
    operationId,
    ciphertext,
    nonce,
    mac,
    encryptionScheme,
    encryptionKeyVersion,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'operation_queue_payload';
  @override
  VerificationContext validateIntegrity(
    Insertable<OperationQueuePayloadData> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('operation_id')) {
      context.handle(
        _operationIdMeta,
        operationId.isAcceptableOrUnknown(
          data['operation_id']!,
          _operationIdMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_operationIdMeta);
    }
    if (data.containsKey('ciphertext')) {
      context.handle(
        _ciphertextMeta,
        ciphertext.isAcceptableOrUnknown(data['ciphertext']!, _ciphertextMeta),
      );
    } else if (isInserting) {
      context.missing(_ciphertextMeta);
    }
    if (data.containsKey('nonce')) {
      context.handle(
        _nonceMeta,
        nonce.isAcceptableOrUnknown(data['nonce']!, _nonceMeta),
      );
    } else if (isInserting) {
      context.missing(_nonceMeta);
    }
    if (data.containsKey('mac')) {
      context.handle(
        _macMeta,
        mac.isAcceptableOrUnknown(data['mac']!, _macMeta),
      );
    } else if (isInserting) {
      context.missing(_macMeta);
    }
    if (data.containsKey('encryption_scheme')) {
      context.handle(
        _encryptionSchemeMeta,
        encryptionScheme.isAcceptableOrUnknown(
          data['encryption_scheme']!,
          _encryptionSchemeMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_encryptionSchemeMeta);
    }
    if (data.containsKey('encryption_key_version')) {
      context.handle(
        _encryptionKeyVersionMeta,
        encryptionKeyVersion.isAcceptableOrUnknown(
          data['encryption_key_version']!,
          _encryptionKeyVersionMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_encryptionKeyVersionMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {operationId};
  @override
  OperationQueuePayloadData map(
    Map<String, dynamic> data, {
    String? tablePrefix,
  }) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return OperationQueuePayloadData(
      operationId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}operation_id'],
      )!,
      ciphertext: attachedDatabase.typeMapping.read(
        DriftSqlType.blob,
        data['${effectivePrefix}ciphertext'],
      )!,
      nonce: attachedDatabase.typeMapping.read(
        DriftSqlType.blob,
        data['${effectivePrefix}nonce'],
      )!,
      mac: attachedDatabase.typeMapping.read(
        DriftSqlType.blob,
        data['${effectivePrefix}mac'],
      )!,
      encryptionScheme: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}encryption_scheme'],
      )!,
      encryptionKeyVersion: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}encryption_key_version'],
      )!,
    );
  }

  @override
  $OperationQueuePayloadTable createAlias(String alias) {
    return $OperationQueuePayloadTable(attachedDatabase, alias);
  }
}

class OperationQueuePayloadData extends DataClass
    implements Insertable<OperationQueuePayloadData> {
  final String operationId;
  final Uint8List ciphertext;
  final Uint8List nonce;
  final Uint8List mac;
  final String encryptionScheme;
  final int encryptionKeyVersion;
  const OperationQueuePayloadData({
    required this.operationId,
    required this.ciphertext,
    required this.nonce,
    required this.mac,
    required this.encryptionScheme,
    required this.encryptionKeyVersion,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['operation_id'] = Variable<String>(operationId);
    map['ciphertext'] = Variable<Uint8List>(ciphertext);
    map['nonce'] = Variable<Uint8List>(nonce);
    map['mac'] = Variable<Uint8List>(mac);
    map['encryption_scheme'] = Variable<String>(encryptionScheme);
    map['encryption_key_version'] = Variable<int>(encryptionKeyVersion);
    return map;
  }

  OperationQueuePayloadCompanion toCompanion(bool nullToAbsent) {
    return OperationQueuePayloadCompanion(
      operationId: Value(operationId),
      ciphertext: Value(ciphertext),
      nonce: Value(nonce),
      mac: Value(mac),
      encryptionScheme: Value(encryptionScheme),
      encryptionKeyVersion: Value(encryptionKeyVersion),
    );
  }

  factory OperationQueuePayloadData.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return OperationQueuePayloadData(
      operationId: serializer.fromJson<String>(json['operationId']),
      ciphertext: serializer.fromJson<Uint8List>(json['ciphertext']),
      nonce: serializer.fromJson<Uint8List>(json['nonce']),
      mac: serializer.fromJson<Uint8List>(json['mac']),
      encryptionScheme: serializer.fromJson<String>(json['encryptionScheme']),
      encryptionKeyVersion: serializer.fromJson<int>(
        json['encryptionKeyVersion'],
      ),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'operationId': serializer.toJson<String>(operationId),
      'ciphertext': serializer.toJson<Uint8List>(ciphertext),
      'nonce': serializer.toJson<Uint8List>(nonce),
      'mac': serializer.toJson<Uint8List>(mac),
      'encryptionScheme': serializer.toJson<String>(encryptionScheme),
      'encryptionKeyVersion': serializer.toJson<int>(encryptionKeyVersion),
    };
  }

  OperationQueuePayloadData copyWith({
    String? operationId,
    Uint8List? ciphertext,
    Uint8List? nonce,
    Uint8List? mac,
    String? encryptionScheme,
    int? encryptionKeyVersion,
  }) => OperationQueuePayloadData(
    operationId: operationId ?? this.operationId,
    ciphertext: ciphertext ?? this.ciphertext,
    nonce: nonce ?? this.nonce,
    mac: mac ?? this.mac,
    encryptionScheme: encryptionScheme ?? this.encryptionScheme,
    encryptionKeyVersion: encryptionKeyVersion ?? this.encryptionKeyVersion,
  );
  OperationQueuePayloadData copyWithCompanion(
    OperationQueuePayloadCompanion data,
  ) {
    return OperationQueuePayloadData(
      operationId: data.operationId.present
          ? data.operationId.value
          : this.operationId,
      ciphertext: data.ciphertext.present
          ? data.ciphertext.value
          : this.ciphertext,
      nonce: data.nonce.present ? data.nonce.value : this.nonce,
      mac: data.mac.present ? data.mac.value : this.mac,
      encryptionScheme: data.encryptionScheme.present
          ? data.encryptionScheme.value
          : this.encryptionScheme,
      encryptionKeyVersion: data.encryptionKeyVersion.present
          ? data.encryptionKeyVersion.value
          : this.encryptionKeyVersion,
    );
  }

  @override
  String toString() {
    return (StringBuffer('OperationQueuePayloadData(')
          ..write('operationId: $operationId, ')
          ..write('ciphertext: $ciphertext, ')
          ..write('nonce: $nonce, ')
          ..write('mac: $mac, ')
          ..write('encryptionScheme: $encryptionScheme, ')
          ..write('encryptionKeyVersion: $encryptionKeyVersion')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    operationId,
    $driftBlobEquality.hash(ciphertext),
    $driftBlobEquality.hash(nonce),
    $driftBlobEquality.hash(mac),
    encryptionScheme,
    encryptionKeyVersion,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is OperationQueuePayloadData &&
          other.operationId == this.operationId &&
          $driftBlobEquality.equals(other.ciphertext, this.ciphertext) &&
          $driftBlobEquality.equals(other.nonce, this.nonce) &&
          $driftBlobEquality.equals(other.mac, this.mac) &&
          other.encryptionScheme == this.encryptionScheme &&
          other.encryptionKeyVersion == this.encryptionKeyVersion);
}

class OperationQueuePayloadCompanion
    extends UpdateCompanion<OperationQueuePayloadData> {
  final Value<String> operationId;
  final Value<Uint8List> ciphertext;
  final Value<Uint8List> nonce;
  final Value<Uint8List> mac;
  final Value<String> encryptionScheme;
  final Value<int> encryptionKeyVersion;
  final Value<int> rowid;
  const OperationQueuePayloadCompanion({
    this.operationId = const Value.absent(),
    this.ciphertext = const Value.absent(),
    this.nonce = const Value.absent(),
    this.mac = const Value.absent(),
    this.encryptionScheme = const Value.absent(),
    this.encryptionKeyVersion = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  OperationQueuePayloadCompanion.insert({
    required String operationId,
    required Uint8List ciphertext,
    required Uint8List nonce,
    required Uint8List mac,
    required String encryptionScheme,
    required int encryptionKeyVersion,
    this.rowid = const Value.absent(),
  }) : operationId = Value(operationId),
       ciphertext = Value(ciphertext),
       nonce = Value(nonce),
       mac = Value(mac),
       encryptionScheme = Value(encryptionScheme),
       encryptionKeyVersion = Value(encryptionKeyVersion);
  static Insertable<OperationQueuePayloadData> custom({
    Expression<String>? operationId,
    Expression<Uint8List>? ciphertext,
    Expression<Uint8List>? nonce,
    Expression<Uint8List>? mac,
    Expression<String>? encryptionScheme,
    Expression<int>? encryptionKeyVersion,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (operationId != null) 'operation_id': operationId,
      if (ciphertext != null) 'ciphertext': ciphertext,
      if (nonce != null) 'nonce': nonce,
      if (mac != null) 'mac': mac,
      if (encryptionScheme != null) 'encryption_scheme': encryptionScheme,
      if (encryptionKeyVersion != null)
        'encryption_key_version': encryptionKeyVersion,
      if (rowid != null) 'rowid': rowid,
    });
  }

  OperationQueuePayloadCompanion copyWith({
    Value<String>? operationId,
    Value<Uint8List>? ciphertext,
    Value<Uint8List>? nonce,
    Value<Uint8List>? mac,
    Value<String>? encryptionScheme,
    Value<int>? encryptionKeyVersion,
    Value<int>? rowid,
  }) {
    return OperationQueuePayloadCompanion(
      operationId: operationId ?? this.operationId,
      ciphertext: ciphertext ?? this.ciphertext,
      nonce: nonce ?? this.nonce,
      mac: mac ?? this.mac,
      encryptionScheme: encryptionScheme ?? this.encryptionScheme,
      encryptionKeyVersion: encryptionKeyVersion ?? this.encryptionKeyVersion,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (operationId.present) {
      map['operation_id'] = Variable<String>(operationId.value);
    }
    if (ciphertext.present) {
      map['ciphertext'] = Variable<Uint8List>(ciphertext.value);
    }
    if (nonce.present) {
      map['nonce'] = Variable<Uint8List>(nonce.value);
    }
    if (mac.present) {
      map['mac'] = Variable<Uint8List>(mac.value);
    }
    if (encryptionScheme.present) {
      map['encryption_scheme'] = Variable<String>(encryptionScheme.value);
    }
    if (encryptionKeyVersion.present) {
      map['encryption_key_version'] = Variable<int>(encryptionKeyVersion.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('OperationQueuePayloadCompanion(')
          ..write('operationId: $operationId, ')
          ..write('ciphertext: $ciphertext, ')
          ..write('nonce: $nonce, ')
          ..write('mac: $mac, ')
          ..write('encryptionScheme: $encryptionScheme, ')
          ..write('encryptionKeyVersion: $encryptionKeyVersion, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

abstract class _$AppDatabase extends GeneratedDatabase {
  _$AppDatabase(QueryExecutor e) : super(e);
  $AppDatabaseManager get managers => $AppDatabaseManager(this);
  late final $AppMetadataTable appMetadata = $AppMetadataTable(this);
  late final $OperationQueueEntryTable operationQueueEntry =
      $OperationQueueEntryTable(this);
  late final $OperationQueuePayloadTable operationQueuePayload =
      $OperationQueuePayloadTable(this);
  @override
  Iterable<TableInfo<Table, Object?>> get allTables =>
      allSchemaEntities.whereType<TableInfo<Table, Object?>>();
  @override
  List<DatabaseSchemaEntity> get allSchemaEntities => [
    appMetadata,
    operationQueueEntry,
    operationQueuePayload,
  ];
  @override
  StreamQueryUpdateRules get streamUpdateRules => const StreamQueryUpdateRules([
    WritePropagation(
      on: TableUpdateQuery.onTableName(
        'operation_queue_entry',
        limitUpdateKind: UpdateKind.delete,
      ),
      result: [TableUpdate('operation_queue_payload', kind: UpdateKind.delete)],
    ),
  ]);
}

typedef $$AppMetadataTableCreateCompanionBuilder =
    AppMetadataCompanion Function({
      required String key,
      required String value,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });
typedef $$AppMetadataTableUpdateCompanionBuilder =
    AppMetadataCompanion Function({
      Value<String> key,
      Value<String> value,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });

class $$AppMetadataTableFilterComposer
    extends Composer<_$AppDatabase, $AppMetadataTable> {
  $$AppMetadataTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get key => $composableBuilder(
    column: $table.key,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get value => $composableBuilder(
    column: $table.value,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$AppMetadataTableOrderingComposer
    extends Composer<_$AppDatabase, $AppMetadataTable> {
  $$AppMetadataTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get key => $composableBuilder(
    column: $table.key,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get value => $composableBuilder(
    column: $table.value,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$AppMetadataTableAnnotationComposer
    extends Composer<_$AppDatabase, $AppMetadataTable> {
  $$AppMetadataTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get key =>
      $composableBuilder(column: $table.key, builder: (column) => column);

  GeneratedColumn<String> get value =>
      $composableBuilder(column: $table.value, builder: (column) => column);

  GeneratedColumn<DateTime> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);
}

class $$AppMetadataTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $AppMetadataTable,
          AppMetadataData,
          $$AppMetadataTableFilterComposer,
          $$AppMetadataTableOrderingComposer,
          $$AppMetadataTableAnnotationComposer,
          $$AppMetadataTableCreateCompanionBuilder,
          $$AppMetadataTableUpdateCompanionBuilder,
          (
            AppMetadataData,
            BaseReferences<_$AppDatabase, $AppMetadataTable, AppMetadataData>,
          ),
          AppMetadataData,
          PrefetchHooks Function()
        > {
  $$AppMetadataTableTableManager(_$AppDatabase db, $AppMetadataTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$AppMetadataTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$AppMetadataTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$AppMetadataTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> key = const Value.absent(),
                Value<String> value = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => AppMetadataCompanion(
                key: key,
                value: value,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String key,
                required String value,
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => AppMetadataCompanion.insert(
                key: key,
                value: value,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map(
                (e) => (
                  e.readTable<$AppMetadataTable, AppMetadataData>(table),
                  BaseReferences<
                    _$AppDatabase,
                    $AppMetadataTable,
                    AppMetadataData
                  >(db, table, e),
                ),
              )
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$AppMetadataTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $AppMetadataTable,
      AppMetadataData,
      $$AppMetadataTableFilterComposer,
      $$AppMetadataTableOrderingComposer,
      $$AppMetadataTableAnnotationComposer,
      $$AppMetadataTableCreateCompanionBuilder,
      $$AppMetadataTableUpdateCompanionBuilder,
      (
        AppMetadataData,
        BaseReferences<_$AppDatabase, $AppMetadataTable, AppMetadataData>,
      ),
      AppMetadataData,
      PrefetchHooks Function()
    >;
typedef $$OperationQueueEntryTableCreateCompanionBuilder =
    OperationQueueEntryCompanion Function({
      required String operationId,
      required String operationType,
      required String idempotencyKey,
      Value<String?> ownerUserId,
      Value<String?> clinicContextId,
      Value<String> lifecycleState,
      Value<int> attemptsCompleted,
      required DateTime createdAt,
      required DateTime updatedAt,
      Value<DateTime?> nextAttemptAt,
      Value<String?> lastFailureCategory,
      Value<int> rowid,
    });
typedef $$OperationQueueEntryTableUpdateCompanionBuilder =
    OperationQueueEntryCompanion Function({
      Value<String> operationId,
      Value<String> operationType,
      Value<String> idempotencyKey,
      Value<String?> ownerUserId,
      Value<String?> clinicContextId,
      Value<String> lifecycleState,
      Value<int> attemptsCompleted,
      Value<DateTime> createdAt,
      Value<DateTime> updatedAt,
      Value<DateTime?> nextAttemptAt,
      Value<String?> lastFailureCategory,
      Value<int> rowid,
    });

final class $$OperationQueueEntryTableReferences
    extends
        BaseReferences<
          _$AppDatabase,
          $OperationQueueEntryTable,
          OperationQueueEntryData
        > {
  $$OperationQueueEntryTableReferences(
    super.$_db,
    super.$_table,
    super.$_typedResult,
  );

  static MultiTypedResultKey<
    $OperationQueuePayloadTable,
    List<OperationQueuePayloadData>
  >
  _operationQueuePayloadRefsTable(_$AppDatabase db) =>
      MultiTypedResultKey.fromTable(
        db.operationQueuePayload,
        aliasName: 'operation_queue_entry__operation_id__operation_queue_payload__operation_id',
      );

  $$OperationQueuePayloadTableProcessedTableManager
  get operationQueuePayloadRefs {
    final manager =
        $$OperationQueuePayloadTableTableManager(
          $_db,
          $_db.operationQueuePayload,
        ).filter(
          (f) => f.operationId.operationId.sqlEquals(
            $_itemColumn<String>('operation_id')!,
          ),
        );

    final cache = $_typedResult.readTableOrNull(
      _operationQueuePayloadRefsTable($_db),
    );
    return ProcessedTableManager(
      manager.$state.copyWith(prefetchedData: cache),
    );
  }
}

class $$OperationQueueEntryTableFilterComposer
    extends Composer<_$AppDatabase, $OperationQueueEntryTable> {
  $$OperationQueueEntryTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get operationId => $composableBuilder(
    column: $table.operationId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get operationType => $composableBuilder(
    column: $table.operationType,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get idempotencyKey => $composableBuilder(
    column: $table.idempotencyKey,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get ownerUserId => $composableBuilder(
    column: $table.ownerUserId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get clinicContextId => $composableBuilder(
    column: $table.clinicContextId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get lifecycleState => $composableBuilder(
    column: $table.lifecycleState,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get attemptsCompleted => $composableBuilder(
    column: $table.attemptsCompleted,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get nextAttemptAt => $composableBuilder(
    column: $table.nextAttemptAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get lastFailureCategory => $composableBuilder(
    column: $table.lastFailureCategory,
    builder: (column) => ColumnFilters(column),
  );

  Expression<bool> operationQueuePayloadRefs(
    Expression<bool> Function($$OperationQueuePayloadTableFilterComposer f) f,
  ) {
    final $$OperationQueuePayloadTableFilterComposer composer =
        $composerBuilder(
          composer: this,
          getCurrentColumn: (t) => t.operationId,
          referencedTable: $db.operationQueuePayload,
          getReferencedColumn: (t) => t.operationId,
          builder:
              (
                joinBuilder, {
                $addJoinBuilderToRootComposer,
                $removeJoinBuilderFromRootComposer,
              }) => $$OperationQueuePayloadTableFilterComposer(
                $db: $db,
                $table: $db.operationQueuePayload,
                $addJoinBuilderToRootComposer: $addJoinBuilderToRootComposer,
                joinBuilder: joinBuilder,
                $removeJoinBuilderFromRootComposer:
                    $removeJoinBuilderFromRootComposer,
              ),
        );
    return f(composer);
  }
}

class $$OperationQueueEntryTableOrderingComposer
    extends Composer<_$AppDatabase, $OperationQueueEntryTable> {
  $$OperationQueueEntryTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get operationId => $composableBuilder(
    column: $table.operationId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get operationType => $composableBuilder(
    column: $table.operationType,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get idempotencyKey => $composableBuilder(
    column: $table.idempotencyKey,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get ownerUserId => $composableBuilder(
    column: $table.ownerUserId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get clinicContextId => $composableBuilder(
    column: $table.clinicContextId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get lifecycleState => $composableBuilder(
    column: $table.lifecycleState,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get attemptsCompleted => $composableBuilder(
    column: $table.attemptsCompleted,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get nextAttemptAt => $composableBuilder(
    column: $table.nextAttemptAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get lastFailureCategory => $composableBuilder(
    column: $table.lastFailureCategory,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$OperationQueueEntryTableAnnotationComposer
    extends Composer<_$AppDatabase, $OperationQueueEntryTable> {
  $$OperationQueueEntryTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get operationId => $composableBuilder(
    column: $table.operationId,
    builder: (column) => column,
  );

  GeneratedColumn<String> get operationType => $composableBuilder(
    column: $table.operationType,
    builder: (column) => column,
  );

  GeneratedColumn<String> get idempotencyKey => $composableBuilder(
    column: $table.idempotencyKey,
    builder: (column) => column,
  );

  GeneratedColumn<String> get ownerUserId => $composableBuilder(
    column: $table.ownerUserId,
    builder: (column) => column,
  );

  GeneratedColumn<String> get clinicContextId => $composableBuilder(
    column: $table.clinicContextId,
    builder: (column) => column,
  );

  GeneratedColumn<String> get lifecycleState => $composableBuilder(
    column: $table.lifecycleState,
    builder: (column) => column,
  );

  GeneratedColumn<int> get attemptsCompleted => $composableBuilder(
    column: $table.attemptsCompleted,
    builder: (column) => column,
  );

  GeneratedColumn<DateTime> get createdAt =>
      $composableBuilder(column: $table.createdAt, builder: (column) => column);

  GeneratedColumn<DateTime> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);

  GeneratedColumn<DateTime> get nextAttemptAt => $composableBuilder(
    column: $table.nextAttemptAt,
    builder: (column) => column,
  );

  GeneratedColumn<String> get lastFailureCategory => $composableBuilder(
    column: $table.lastFailureCategory,
    builder: (column) => column,
  );

  Expression<T> operationQueuePayloadRefs<T extends Object>(
    Expression<T> Function($$OperationQueuePayloadTableAnnotationComposer a) f,
  ) {
    final $$OperationQueuePayloadTableAnnotationComposer composer =
        $composerBuilder(
          composer: this,
          getCurrentColumn: (t) => t.operationId,
          referencedTable: $db.operationQueuePayload,
          getReferencedColumn: (t) => t.operationId,
          builder:
              (
                joinBuilder, {
                $addJoinBuilderToRootComposer,
                $removeJoinBuilderFromRootComposer,
              }) => $$OperationQueuePayloadTableAnnotationComposer(
                $db: $db,
                $table: $db.operationQueuePayload,
                $addJoinBuilderToRootComposer: $addJoinBuilderToRootComposer,
                joinBuilder: joinBuilder,
                $removeJoinBuilderFromRootComposer:
                    $removeJoinBuilderFromRootComposer,
              ),
        );
    return f(composer);
  }
}

class $$OperationQueueEntryTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $OperationQueueEntryTable,
          OperationQueueEntryData,
          $$OperationQueueEntryTableFilterComposer,
          $$OperationQueueEntryTableOrderingComposer,
          $$OperationQueueEntryTableAnnotationComposer,
          $$OperationQueueEntryTableCreateCompanionBuilder,
          $$OperationQueueEntryTableUpdateCompanionBuilder,
          (OperationQueueEntryData, $$OperationQueueEntryTableReferences),
          OperationQueueEntryData,
          PrefetchHooks Function({bool operationQueuePayloadRefs})
        > {
  $$OperationQueueEntryTableTableManager(
    _$AppDatabase db,
    $OperationQueueEntryTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$OperationQueueEntryTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$OperationQueueEntryTableOrderingComposer(
                $db: db,
                $table: table,
              ),
          createComputedFieldComposer: () =>
              $$OperationQueueEntryTableAnnotationComposer(
                $db: db,
                $table: table,
              ),
          updateCompanionCallback:
              ({
                Value<String> operationId = const Value.absent(),
                Value<String> operationType = const Value.absent(),
                Value<String> idempotencyKey = const Value.absent(),
                Value<String?> ownerUserId = const Value.absent(),
                Value<String?> clinicContextId = const Value.absent(),
                Value<String> lifecycleState = const Value.absent(),
                Value<int> attemptsCompleted = const Value.absent(),
                Value<DateTime> createdAt = const Value.absent(),
                Value<DateTime> updatedAt = const Value.absent(),
                Value<DateTime?> nextAttemptAt = const Value.absent(),
                Value<String?> lastFailureCategory = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => OperationQueueEntryCompanion(
                operationId: operationId,
                operationType: operationType,
                idempotencyKey: idempotencyKey,
                ownerUserId: ownerUserId,
                clinicContextId: clinicContextId,
                lifecycleState: lifecycleState,
                attemptsCompleted: attemptsCompleted,
                createdAt: createdAt,
                updatedAt: updatedAt,
                nextAttemptAt: nextAttemptAt,
                lastFailureCategory: lastFailureCategory,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String operationId,
                required String operationType,
                required String idempotencyKey,
                Value<String?> ownerUserId = const Value.absent(),
                Value<String?> clinicContextId = const Value.absent(),
                Value<String> lifecycleState = const Value.absent(),
                Value<int> attemptsCompleted = const Value.absent(),
                required DateTime createdAt,
                required DateTime updatedAt,
                Value<DateTime?> nextAttemptAt = const Value.absent(),
                Value<String?> lastFailureCategory = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => OperationQueueEntryCompanion.insert(
                operationId: operationId,
                operationType: operationType,
                idempotencyKey: idempotencyKey,
                ownerUserId: ownerUserId,
                clinicContextId: clinicContextId,
                lifecycleState: lifecycleState,
                attemptsCompleted: attemptsCompleted,
                createdAt: createdAt,
                updatedAt: updatedAt,
                nextAttemptAt: nextAttemptAt,
                lastFailureCategory: lastFailureCategory,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map(
                (e) => (
                  e.readTable<
                    $OperationQueueEntryTable,
                    OperationQueueEntryData
                  >(table),
                  $$OperationQueueEntryTableReferences(db, table, e),
                ),
              )
              .toList(),
          prefetchHooksCallback: ({operationQueuePayloadRefs = false}) {
            return PrefetchHooks(
              db: db,
              explicitlyWatchedTables: [
                if (operationQueuePayloadRefs) db.operationQueuePayload,
              ],
              addJoins: null,
              getPrefetchedDataCallback: (items) async {
                return [
                  if (operationQueuePayloadRefs)
                    await $_getPrefetchedData<
                      OperationQueueEntryData,
                      $OperationQueueEntryTable,
                      OperationQueuePayloadData
                    >(
                      currentTable: table,
                      referencedTable: $$OperationQueueEntryTableReferences
                          ._operationQueuePayloadRefsTable(db),
                      managerFromTypedResult: (p0) =>
                          $$OperationQueueEntryTableReferences(
                            db,
                            table,
                            p0,
                          ).operationQueuePayloadRefs,
                      referencedItemsForCurrentItem: (item, referencedItems) =>
                          referencedItems.where(
                            (e) => e.operationId == item.operationId,
                          ),
                      typedResults: items,
                    ),
                ];
              },
            );
          },
        ),
      );
}

typedef $$OperationQueueEntryTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $OperationQueueEntryTable,
      OperationQueueEntryData,
      $$OperationQueueEntryTableFilterComposer,
      $$OperationQueueEntryTableOrderingComposer,
      $$OperationQueueEntryTableAnnotationComposer,
      $$OperationQueueEntryTableCreateCompanionBuilder,
      $$OperationQueueEntryTableUpdateCompanionBuilder,
      (OperationQueueEntryData, $$OperationQueueEntryTableReferences),
      OperationQueueEntryData,
      PrefetchHooks Function({bool operationQueuePayloadRefs})
    >;
typedef $$OperationQueuePayloadTableCreateCompanionBuilder =
    OperationQueuePayloadCompanion Function({
      required String operationId,
      required Uint8List ciphertext,
      required Uint8List nonce,
      required Uint8List mac,
      required String encryptionScheme,
      required int encryptionKeyVersion,
      Value<int> rowid,
    });
typedef $$OperationQueuePayloadTableUpdateCompanionBuilder =
    OperationQueuePayloadCompanion Function({
      Value<String> operationId,
      Value<Uint8List> ciphertext,
      Value<Uint8List> nonce,
      Value<Uint8List> mac,
      Value<String> encryptionScheme,
      Value<int> encryptionKeyVersion,
      Value<int> rowid,
    });

final class $$OperationQueuePayloadTableReferences
    extends
        BaseReferences<
          _$AppDatabase,
          $OperationQueuePayloadTable,
          OperationQueuePayloadData
        > {
  $$OperationQueuePayloadTableReferences(
    super.$_db,
    super.$_table,
    super.$_typedResult,
  );

  static $OperationQueueEntryTable _operationIdTable(_$AppDatabase db) =>
      db.operationQueueEntry.createAlias(
        'operation_queue_payload__operation_id__operation_queue_entry__operation_id',
      );

  $$OperationQueueEntryTableProcessedTableManager get operationId {
    final $_column = $_itemColumn<String>('operation_id')!;

    final manager = $$OperationQueueEntryTableTableManager(
      $_db,
      $_db.operationQueueEntry,
    ).filter((f) => f.operationId.sqlEquals($_column));
    final item = $_typedResult.readTableOrNull(_operationIdTable($_db));
    if (item == null) return manager;
    return ProcessedTableManager(
      manager.$state.copyWith(prefetchedData: [item]),
    );
  }
}

class $$OperationQueuePayloadTableFilterComposer
    extends Composer<_$AppDatabase, $OperationQueuePayloadTable> {
  $$OperationQueuePayloadTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<Uint8List> get ciphertext => $composableBuilder(
    column: $table.ciphertext,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<Uint8List> get nonce => $composableBuilder(
    column: $table.nonce,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<Uint8List> get mac => $composableBuilder(
    column: $table.mac,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get encryptionScheme => $composableBuilder(
    column: $table.encryptionScheme,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get encryptionKeyVersion => $composableBuilder(
    column: $table.encryptionKeyVersion,
    builder: (column) => ColumnFilters(column),
  );

  $$OperationQueueEntryTableFilterComposer get operationId {
    final $$OperationQueueEntryTableFilterComposer composer = $composerBuilder(
      composer: this,
      getCurrentColumn: (t) => t.operationId,
      referencedTable: $db.operationQueueEntry,
      getReferencedColumn: (t) => t.operationId,
      builder:
          (
            joinBuilder, {
            $addJoinBuilderToRootComposer,
            $removeJoinBuilderFromRootComposer,
          }) => $$OperationQueueEntryTableFilterComposer(
            $db: $db,
            $table: $db.operationQueueEntry,
            $addJoinBuilderToRootComposer: $addJoinBuilderToRootComposer,
            joinBuilder: joinBuilder,
            $removeJoinBuilderFromRootComposer:
                $removeJoinBuilderFromRootComposer,
          ),
    );
    return composer;
  }
}

class $$OperationQueuePayloadTableOrderingComposer
    extends Composer<_$AppDatabase, $OperationQueuePayloadTable> {
  $$OperationQueuePayloadTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<Uint8List> get ciphertext => $composableBuilder(
    column: $table.ciphertext,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<Uint8List> get nonce => $composableBuilder(
    column: $table.nonce,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<Uint8List> get mac => $composableBuilder(
    column: $table.mac,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get encryptionScheme => $composableBuilder(
    column: $table.encryptionScheme,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get encryptionKeyVersion => $composableBuilder(
    column: $table.encryptionKeyVersion,
    builder: (column) => ColumnOrderings(column),
  );

  $$OperationQueueEntryTableOrderingComposer get operationId {
    final $$OperationQueueEntryTableOrderingComposer composer =
        $composerBuilder(
          composer: this,
          getCurrentColumn: (t) => t.operationId,
          referencedTable: $db.operationQueueEntry,
          getReferencedColumn: (t) => t.operationId,
          builder:
              (
                joinBuilder, {
                $addJoinBuilderToRootComposer,
                $removeJoinBuilderFromRootComposer,
              }) => $$OperationQueueEntryTableOrderingComposer(
                $db: $db,
                $table: $db.operationQueueEntry,
                $addJoinBuilderToRootComposer: $addJoinBuilderToRootComposer,
                joinBuilder: joinBuilder,
                $removeJoinBuilderFromRootComposer:
                    $removeJoinBuilderFromRootComposer,
              ),
        );
    return composer;
  }
}

class $$OperationQueuePayloadTableAnnotationComposer
    extends Composer<_$AppDatabase, $OperationQueuePayloadTable> {
  $$OperationQueuePayloadTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<Uint8List> get ciphertext => $composableBuilder(
    column: $table.ciphertext,
    builder: (column) => column,
  );

  GeneratedColumn<Uint8List> get nonce =>
      $composableBuilder(column: $table.nonce, builder: (column) => column);

  GeneratedColumn<Uint8List> get mac =>
      $composableBuilder(column: $table.mac, builder: (column) => column);

  GeneratedColumn<String> get encryptionScheme => $composableBuilder(
    column: $table.encryptionScheme,
    builder: (column) => column,
  );

  GeneratedColumn<int> get encryptionKeyVersion => $composableBuilder(
    column: $table.encryptionKeyVersion,
    builder: (column) => column,
  );

  $$OperationQueueEntryTableAnnotationComposer get operationId {
    final $$OperationQueueEntryTableAnnotationComposer composer =
        $composerBuilder(
          composer: this,
          getCurrentColumn: (t) => t.operationId,
          referencedTable: $db.operationQueueEntry,
          getReferencedColumn: (t) => t.operationId,
          builder:
              (
                joinBuilder, {
                $addJoinBuilderToRootComposer,
                $removeJoinBuilderFromRootComposer,
              }) => $$OperationQueueEntryTableAnnotationComposer(
                $db: $db,
                $table: $db.operationQueueEntry,
                $addJoinBuilderToRootComposer: $addJoinBuilderToRootComposer,
                joinBuilder: joinBuilder,
                $removeJoinBuilderFromRootComposer:
                    $removeJoinBuilderFromRootComposer,
              ),
        );
    return composer;
  }
}

class $$OperationQueuePayloadTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $OperationQueuePayloadTable,
          OperationQueuePayloadData,
          $$OperationQueuePayloadTableFilterComposer,
          $$OperationQueuePayloadTableOrderingComposer,
          $$OperationQueuePayloadTableAnnotationComposer,
          $$OperationQueuePayloadTableCreateCompanionBuilder,
          $$OperationQueuePayloadTableUpdateCompanionBuilder,
          (OperationQueuePayloadData, $$OperationQueuePayloadTableReferences),
          OperationQueuePayloadData,
          PrefetchHooks Function({bool operationId})
        > {
  $$OperationQueuePayloadTableTableManager(
    _$AppDatabase db,
    $OperationQueuePayloadTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$OperationQueuePayloadTableFilterComposer(
                $db: db,
                $table: table,
              ),
          createOrderingComposer: () =>
              $$OperationQueuePayloadTableOrderingComposer(
                $db: db,
                $table: table,
              ),
          createComputedFieldComposer: () =>
              $$OperationQueuePayloadTableAnnotationComposer(
                $db: db,
                $table: table,
              ),
          updateCompanionCallback:
              ({
                Value<String> operationId = const Value.absent(),
                Value<Uint8List> ciphertext = const Value.absent(),
                Value<Uint8List> nonce = const Value.absent(),
                Value<Uint8List> mac = const Value.absent(),
                Value<String> encryptionScheme = const Value.absent(),
                Value<int> encryptionKeyVersion = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => OperationQueuePayloadCompanion(
                operationId: operationId,
                ciphertext: ciphertext,
                nonce: nonce,
                mac: mac,
                encryptionScheme: encryptionScheme,
                encryptionKeyVersion: encryptionKeyVersion,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String operationId,
                required Uint8List ciphertext,
                required Uint8List nonce,
                required Uint8List mac,
                required String encryptionScheme,
                required int encryptionKeyVersion,
                Value<int> rowid = const Value.absent(),
              }) => OperationQueuePayloadCompanion.insert(
                operationId: operationId,
                ciphertext: ciphertext,
                nonce: nonce,
                mac: mac,
                encryptionScheme: encryptionScheme,
                encryptionKeyVersion: encryptionKeyVersion,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map(
                (e) => (
                  e.readTable<
                    $OperationQueuePayloadTable,
                    OperationQueuePayloadData
                  >(table),
                  $$OperationQueuePayloadTableReferences(db, table, e),
                ),
              )
              .toList(),
          prefetchHooksCallback: ({operationId = false}) {
            return PrefetchHooks(
              db: db,
              explicitlyWatchedTables: [],
              addJoins:
                  <
                    T extends TableManagerState<
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic
                    >
                  >(state) {
                    if (operationId) {
                      state = state.withJoin(
                        currentTable: table,
                        currentColumn: table.operationId,
                        referencedTable: $$OperationQueuePayloadTableReferences
                            ._operationIdTable(db),
                        referencedColumn: $$OperationQueuePayloadTableReferences
                            ._operationIdTable(db)
                            .operationId,
                      ) as T;
                    }

                    return state;
                  },
              getPrefetchedDataCallback: (items) async {
                return [];
              },
            );
          },
        ),
      );
}

typedef $$OperationQueuePayloadTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $OperationQueuePayloadTable,
      OperationQueuePayloadData,
      $$OperationQueuePayloadTableFilterComposer,
      $$OperationQueuePayloadTableOrderingComposer,
      $$OperationQueuePayloadTableAnnotationComposer,
      $$OperationQueuePayloadTableCreateCompanionBuilder,
      $$OperationQueuePayloadTableUpdateCompanionBuilder,
      (OperationQueuePayloadData, $$OperationQueuePayloadTableReferences),
      OperationQueuePayloadData,
      PrefetchHooks Function({bool operationId})
    >;

class $AppDatabaseManager {
  final _$AppDatabase _db;
  $AppDatabaseManager(this._db);
  $$AppMetadataTableTableManager get appMetadata =>
      $$AppMetadataTableTableManager(_db, _db.appMetadata);
  $$OperationQueueEntryTableTableManager get operationQueueEntry =>
      $$OperationQueueEntryTableTableManager(_db, _db.operationQueueEntry);
  $$OperationQueuePayloadTableTableManager get operationQueuePayload =>
      $$OperationQueuePayloadTableTableManager(_db, _db.operationQueuePayload);
}
