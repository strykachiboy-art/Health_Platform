import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/pending_work/data/offline_contract_policy.dart';

void main() {
  test('P25 only appointment.create is admitted to stable-key replay', () {
    expect(OfflineContractRegistry.mayAutoReplay('appointment.create'), isTrue);
    for (final entry in OfflineContractRegistry.policies.entries) {
      if (entry.key == 'appointment.create') {
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
