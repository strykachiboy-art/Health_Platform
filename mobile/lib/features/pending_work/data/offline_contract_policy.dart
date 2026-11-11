/// P25: explicit offline contract classifications. This is a safety allowlist,
/// not a blanket permission to enqueue arbitrary authenticated POST requests.
enum OfflineContractClass {
  readOnly,
  onlineOnly,
  provedSafeQueued,
  reconcile,
  neverAutoReplay,
}

class OfflineContractPolicy {
  const OfflineContractPolicy({
    required this.operationType,
    required this.contractClass,
    required this.backendSupportsStableIdempotency,
    required this.canAutoReplay,
  });

  final String operationType;
  final OfflineContractClass contractClass;
  final bool backendSupportsStableIdempotency;
  final bool canAutoReplay;
}

/// The worker itself independently enforces the same single-operation allowlist.
/// No caller may interpret a contract matrix row as runtime authorization.
class OfflineContractRegistry {
  const OfflineContractRegistry._();

  static const Map<String, OfflineContractPolicy> policies = {
    'appointment.read': OfflineContractPolicy(
      operationType: 'appointment.read',
      contractClass: OfflineContractClass.readOnly,
      backendSupportsStableIdempotency: false,
      canAutoReplay: false,
    ),
    'chat.read': OfflineContractPolicy(
      operationType: 'chat.read',
      contractClass: OfflineContractClass.readOnly,
      backendSupportsStableIdempotency: false,
      canAutoReplay: false,
    ),
    'feedback.read': OfflineContractPolicy(
      operationType: 'feedback.read',
      contractClass: OfflineContractClass.readOnly,
      backendSupportsStableIdempotency: false,
      canAutoReplay: false,
    ),
    'appointment.create': OfflineContractPolicy(
      operationType: 'appointment.create',
      contractClass: OfflineContractClass.provedSafeQueued,
      backendSupportsStableIdempotency: true,
      canAutoReplay: true,
    ),
    'appointment.reschedule': OfflineContractPolicy(
      operationType: 'appointment.reschedule',
      contractClass: OfflineContractClass.neverAutoReplay,
      backendSupportsStableIdempotency: false,
      canAutoReplay: false,
    ),
    'appointment.status': OfflineContractPolicy(
      operationType: 'appointment.status',
      contractClass: OfflineContractClass.neverAutoReplay,
      backendSupportsStableIdempotency: false,
      canAutoReplay: false,
    ),
    'chat.message': OfflineContractPolicy(
      operationType: 'chat.message',
      contractClass: OfflineContractClass.onlineOnly,
      backendSupportsStableIdempotency: false,
      canAutoReplay: false,
    ),
    'chat.reaction': OfflineContractPolicy(
      operationType: 'chat.reaction',
      contractClass: OfflineContractClass.onlineOnly,
      backendSupportsStableIdempotency: false,
      canAutoReplay: false,
    ),
    'chat.mention': OfflineContractPolicy(
      operationType: 'chat.mention',
      contractClass: OfflineContractClass.onlineOnly,
      backendSupportsStableIdempotency: false,
      canAutoReplay: false,
    ),
    'feedback.create': OfflineContractPolicy(
      operationType: 'feedback.create',
      contractClass: OfflineContractClass.onlineOnly,
      backendSupportsStableIdempotency: false,
      canAutoReplay: false,
    ),
    'feedback.comment': OfflineContractPolicy(
      operationType: 'feedback.comment',
      contractClass: OfflineContractClass.onlineOnly,
      backendSupportsStableIdempotency: false,
      canAutoReplay: false,
    ),
    'clinical-alert.acknowledge': OfflineContractPolicy(
      operationType: 'clinical-alert.acknowledge',
      contractClass: OfflineContractClass.neverAutoReplay,
      backendSupportsStableIdempotency: false,
      canAutoReplay: false,
    ),
    'medication.administer': OfflineContractPolicy(
      operationType: 'medication.administer',
      contractClass: OfflineContractClass.neverAutoReplay,
      backendSupportsStableIdempotency: false,
      canAutoReplay: false,
    ),
    'medication.reconcile': OfflineContractPolicy(
      operationType: 'medication.reconcile',
      contractClass: OfflineContractClass.neverAutoReplay,
      backendSupportsStableIdempotency: false,
      canAutoReplay: false,
    ),
    'financial.payment': OfflineContractPolicy(
      operationType: 'financial.payment',
      contractClass: OfflineContractClass.neverAutoReplay,
      backendSupportsStableIdempotency: false,
      canAutoReplay: false,
    ),
    'emergency-access.decision': OfflineContractPolicy(
      operationType: 'emergency-access.decision',
      contractClass: OfflineContractClass.neverAutoReplay,
      backendSupportsStableIdempotency: false,
      canAutoReplay: false,
    ),
  };

  static bool mayAutoReplay(String operationType) =>
      operationType == 'appointment.create' &&
      policies[operationType]?.canAutoReplay == true &&
      policies[operationType]?.backendSupportsStableIdempotency == true;

  /// Unknown operations are never silently promoted to an offline queue.
  static OfflineContractClass classify(String operationType) =>
      policies[operationType]?.contractClass ??
      OfflineContractClass.neverAutoReplay;
}
