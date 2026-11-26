import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/pending_work/data/offline_contract_policy.dart';

void main() {
  test('RUN2 only the three exact operation types admit stable-key replay', () {
    const allowed = {'appointment.create', 'feedback.product.preset', 'chat.message.preset'};
    expect(OfflineContractRegistry.policies.entries.where((e) => e.value.canAutoReplay).map((e) => e.key).toSet(), allowed);
    for (final type in allowed) {
      expect(OfflineContractRegistry.mayAutoReplay(type), isTrue);
      expect(OfflineContractRegistry.classify(type), OfflineContractClass.provedSafeQueued);
    }
    for (final entry in OfflineContractRegistry.policies.entries) {
      if (allowed.contains(entry.key)) {
        continue;
      }
      expect(OfflineContractRegistry.mayAutoReplay(entry.key), isFalse);
      expect(entry.value.canAutoReplay, isFalse);
    }
    expect(OfflineContractRegistry.mayAutoReplay('invented.endpoint'), isFalse);
    expect(
      OfflineContractRegistry.classify('invented.endpoint'),
      OfflineContractClass.neverAutoReplay,
    );
  });

  test('P25 never queues medication, payments, emergency and feedback', () {
    for (final name in <String>[
      'medication.administer',
      'medication.reconcile',
      'financial.payment',
      'emergency-access.decision',
      'feedback.create',
      'chat.message',
    ]) {
      expect(OfflineContractRegistry.mayAutoReplay(name), isFalse);
    }
    expect(
      OfflineContractRegistry.classify('appointment.create'),
      OfflineContractClass.provedSafeQueued,
    );
    expect(
      OfflineContractRegistry.classify('feedback.read'),
      OfflineContractClass.readOnly,
    );
  });
}
