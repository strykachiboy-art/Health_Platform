import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/features/emergency_access/data/models/emergency_access_grant.dart';

Map<String, dynamic> _json({
  String status = 'active',
  Object? expiresAt = '2026-11-03T12:45:00+00:00',
}) {
  return <String, dynamic>{
    'id': 17,
    'clinic_id': 3,
    'patient_id': 7,
    'requester_user_id': 42,
    'requester_role': 'DOCTOR',
    'reason': 'Immediate treatment required',
    'purpose': 'Emergency clinical review',
    'scope': <String>['patient:read', 'nursing_note:read'],
    'status': status,
    'requested_at': '2026-11-03T12:00:00+00:00',
    'granted_at': '2026-11-03T12:05:00+00:00',
    'expires_at': expiresAt,
    'revoked_at': null,
    'reviewed_at': '2026-11-03T12:05:00+00:00',
    'reviewed_by_user_id': 99,
    'review_notes': 'Approved for emergency care',
  };
}

void main() {
  group('EmergencyAccessGrant', () {
    test('parses the backend response contract', () {
      final grant = EmergencyAccessGrant.fromJson(_json());

      expect(grant.id, 17);
      expect(grant.clinicId, 3);
      expect(grant.patientId, 7);
      expect(grant.requesterUserId, 42);
      expect(grant.requesterRole, 'DOCTOR');
      expect(grant.status, EmergencyAccessStatus.active);
      expect(grant.scope, ['patient:read', 'nursing_note:read']);
      expect(grant.requestedAt.isUtc, isTrue);
      expect(grant.expiresAt?.isUtc, isTrue);
      expect(grant.reviewedByUserId, 99);
    });

    test('supports every backend emergency access status', () {
      const cases = <String, EmergencyAccessStatus>{
        'requested': EmergencyAccessStatus.requested,
        'active': EmergencyAccessStatus.active,
        'denied': EmergencyAccessStatus.denied,
        'revoked': EmergencyAccessStatus.revoked,
        'expired': EmergencyAccessStatus.expired,
      };

      for (final entry in cases.entries) {
        expect(
          EmergencyAccessGrant.fromJson(_json(status: entry.key)).status,
          entry.value,
        );
      }
    });

    test('rejects an unknown status', () {
      expect(
        () => EmergencyAccessGrant.fromJson(_json(status: 'approved')),
        throwsFormatException,
      );
    });

    test('accepts nullable lifecycle timestamps', () {
      final json = _json(expiresAt: null)
        ..['granted_at'] = null
        ..['revoked_at'] = null
        ..['reviewed_at'] = null
        ..['reviewed_by_user_id'] = null
        ..['review_notes'] = null;

      final grant = EmergencyAccessGrant.fromJson(json);

      expect(grant.grantedAt, isNull);
      expect(grant.expiresAt, isNull);
      expect(grant.revokedAt, isNull);
      expect(grant.reviewedAt, isNull);
      expect(grant.reviewedByUserId, isNull);
      expect(grant.reviewNotes, isNull);
    });

    test('rejects malformed required identifiers and timestamps', () {
      final invalidId = _json()..['patient_id'] = 0;
      expect(
        () => EmergencyAccessGrant.fromJson(invalidId),
        throwsFormatException,
      );

      final invalidDate = _json()..['requested_at'] = 'not-a-date';
      expect(
        () => EmergencyAccessGrant.fromJson(invalidDate),
        throwsFormatException,
      );
    });
  });
}
