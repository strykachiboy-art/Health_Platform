import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_context.dart';
import 'package:clinic_system_pro/features/emergency_access/data/models/emergency_access_grant.dart';

EmergencyAccessGrant _grant({
  int id = 17,
  int patientId = 7,
  String status = 'active',
  String? expiresAt = '2026-11-03T13:00:00+00:00',
}) {
  return EmergencyAccessGrant.fromJson(<String, dynamic>{
    'id': id,
    'clinic_id': 3,
    'patient_id': patientId,
    'requester_user_id': 42,
    'requester_role': 'DOCTOR',
    'reason': 'Emergency treatment',
    'purpose': 'Immediate clinical review',
    'scope': <String>['patient:read'],
    'status': status,
    'requested_at': '2026-11-03T12:00:00+00:00',
    'granted_at': status == 'active' ? '2026-11-03T12:05:00+00:00' : null,
    'expires_at': expiresAt,
    'revoked_at': status == 'revoked' ? '2026-11-03T12:20:00+00:00' : null,
    'reviewed_at': status == 'requested' ? null : '2026-11-03T12:05:00+00:00',
    'reviewed_by_user_id': status == 'requested' ? null : 99,
    'review_notes': status == 'requested' ? null : 'Reviewed',
  });
}

void main() {
  group('EmergencyAccessContext negative contract', () {
    test('requested denied revoked and expired grants cannot be activated', () {
      final context = EmergencyAccessContext(
        nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
      );
      addTearDown(context.dispose);

      for (final status in <String>[
        'requested',
        'denied',
        'revoked',
        'expired',
      ]) {
        expect(
          () => context.activate(
            _grant(
              status: status,
              expiresAt: status == 'requested'
                  ? null
                  : '2026-11-03T13:00:00+00:00',
            ),
          ),
          throwsArgumentError,
          reason: status,
        );
      }

      expect(context.activeEmergencyAccessId, isNull);
    });

    test('grant fails closed at the exact expiry instant', () {
      var now = DateTime.utc(2026, 11, 3, 12, 30);

      final context = EmergencyAccessContext(nowUtc: () => now);
      addTearDown(context.dispose);

      context.activate(_grant());

      expect(context.emergencyAccessIdForPatient(7), 17);

      now = DateTime.utc(2026, 11, 3, 13);

      expect(context.activeEmergencyAccessId, isNull);
      expect(context.emergencyAccessIdForPatient(7), isNull);
    });

    test('same grant cannot be used for a different patient', () {
      final context = EmergencyAccessContext(
        nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
      );
      addTearDown(context.dispose);

      context.activate(_grant(patientId: 7));

      expect(context.emergencyAccessIdForPatient(7), 17);
      expect(context.emergencyAccessIdForPatient(8), isNull);
    });

    test('reconciling an unrelated grant cannot replace active context', () {
      final context = EmergencyAccessContext(
        nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
      );
      addTearDown(context.dispose);

      context.activate(_grant());

      context.reconcile(_grant(id: 18, patientId: 8));

      expect(context.activeEmergencyAccessId, 17);
      expect(context.activePatientId, 7);
    });

    test(
      'clearIfGrantId ignores a different grant and clears the matching grant',
      () {
        final context = EmergencyAccessContext(
          nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
        );
        addTearDown(context.dispose);

        context.activate(_grant());

        context.clearIfGrantId(18);

        expect(context.activeEmergencyAccessId, 17);

        context.clearIfGrantId(17);

        expect(context.activeEmergencyAccessId, isNull);
      },
    );

    test('markSignedOut clears an active emergency context', () {
      final guard = SessionNavigationGuard(
        protectedRoutePrefixes: const <String>[
          '/patients',
          '/emergency-access',
        ],
      );

      guard.markSessionAvailable();

      final context = EmergencyAccessContext(
        sessionNavigationGuard: guard,
        nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
      );

      context.activate(_grant());

      expect(context.activeEmergencyAccessId, 17);

      guard.markSignedOut();

      expect(context.activeEmergencyAccessId, isNull);

      context.dispose();
      guard.dispose();
    });
  });
}
