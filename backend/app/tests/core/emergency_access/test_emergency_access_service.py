from __future__ import annotations

import pytest

from app.core.emergency_access.models.emergency_access_model import (
    EmergencyAccessGrant,
)
from app.core.enums.audit_enums import AuditAction
from app.core.enums.emergency_access_enums import (
    EmergencyAccessStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)

from app.core.emergency_access.services import (
    emergency_access_service,
)


DEFAULT_SCOPE = [
    "patient:read",
]


class TestValidatePositiveId:
    @pytest.mark.parametrize(
        "value",
        [
            0,
            -1,
            -100,
            None,
            True,
            False,
            "1",
        ],
    )
    def test_rejects_invalid_id(
        self,
        value,
    ):
        with pytest.raises(
            ValidationError,
            match="must be a positive integer",
        ):
            emergency_access_service._validate_positive_id(
                value,
                "Test ID",
            )

    def test_accepts_positive_integer(self):
        assert (
            emergency_access_service._validate_positive_id(
                10,
                "Test ID",
            )
            == 10
        )


class TestNormalizeRequiredString:
    def test_strips_whitespace(self):
        result = (
            emergency_access_service._normalize_required_string(
                "  emergency care  ",
                "Reason",
                100,
            )
        )

        assert result == "emergency care"

    def test_rejects_blank_string(self):
        with pytest.raises(
            ValidationError,
            match="Reason is required",
        ):
            emergency_access_service._normalize_required_string(
                "   ",
                "Reason",
                100,
            )

    def test_rejects_non_string(self):
        with pytest.raises(
            ValidationError,
            match="Reason must be a string",
        ):
            emergency_access_service._normalize_required_string(
                123,
                "Reason",
                100,
            )

    def test_rejects_too_long_value(self):
        with pytest.raises(
            ValidationError,
            match="Reason cannot exceed 10 characters",
        ):
            emergency_access_service._normalize_required_string(
                "x" * 11,
                "Reason",
                10,
            )


class TestNormalizeScope:
    def test_normalizes_scope(self):
        result = emergency_access_service._normalize_scope(
            [
                " Patient:Read ",
                "patient:read",
                "consultation:123:write",
            ]
        )

        assert result == [
            "patient:read",
            "consultation:123:write",
        ]

    def test_rejects_empty_scope(self):
        with pytest.raises(
            ValidationError,
            match="At least one emergency access scope is required",
        ):
            emergency_access_service._normalize_scope([])

    def test_rejects_too_many_scope_items(self):
        with pytest.raises(
            ValidationError,
            match="Scope cannot contain more than 50 items",
        ):
            emergency_access_service._normalize_scope(
                [
                    f"patient:{index}"
                    for index in range(51)
                ]
            )

    @pytest.mark.parametrize(
        "scope",
        [
            ["*"],
            ["*:*"],
            ["*:read"],
            ["patient"],
            ["patient:read:123:extra"],
            ["patient:0:read"],
            ["patient:-1:read"],
            ["patient:abc:read"],
            ["Patient Type:read"],
            ["patient:READ!"],
        ],
    )
    def test_rejects_invalid_scope(
        self,
        scope,
    ):
        with pytest.raises(
            ValidationError,
        ):
            emergency_access_service._normalize_scope(
                scope
            )

    def test_accepts_resource_action_scope(self):
        result = emergency_access_service._normalize_scope(
            ["patient:read"]
        )

        assert result == [
            "patient:read",
        ]

    def test_accepts_resource_id_action_scope(self):
        result = emergency_access_service._normalize_scope(
            ["consultation:123:read"]
        )

        assert result == [
            "consultation:123:read",
        ]

    def test_accepts_wildcard_action(self):
        result = emergency_access_service._normalize_scope(
            ["consultation:*"]
        )

        assert result == [
            "consultation:*",
        ]

    def test_accepts_exact_resource_wildcard_action(self):
        result = emergency_access_service._normalize_scope(
            ["consultation:123:*"]
        )

        assert result == [
            "consultation:123:*",
        ]


class TestRequestEmergencyAccess:
    def test_creates_request(
        self,
        clinic,
        emergency_patient,
        emergency_staff,
        emergency_audit_mock,
    ):
        grant = (
            emergency_access_service.request_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                reason=(
                    "Immediate emergency treatment required."
                ),
                purpose="Emergency clinical care.",
                scope=[
                    "patient:read",
                    "consultation:123:read",
                ],
            )
        )

        assert grant.id is not None
        assert grant.clinic_id == clinic.id
        assert grant.patient_id == emergency_patient.id
        assert (
            grant.requester_user_id
            == emergency_staff.user.id
        )
        assert grant.requester_role == Role.DOCTOR.value
        assert (
            grant.status
            == EmergencyAccessStatus.REQUESTED
        )
        assert grant.reason == (
            "Immediate emergency treatment required."
        )
        assert grant.purpose == "Emergency clinical care."
        assert grant.scope == [
            "patient:read",
            "consultation:123:read",
        ]
        assert grant.requested_at is not None

        emergency_audit_mock.assert_called_once()

        call = emergency_audit_mock.call_args.kwargs

        assert call["action"] == AuditAction.CREATE
        assert (
            call["entity_type"]
            == "EmergencyAccessGrant"
        )
        assert call["entity_id"] == grant.id
        assert (
            call["user_id"]
            == emergency_staff.user.id
        )

    @pytest.mark.parametrize(
        "role",
        [
            Role.ADMIN,
            Role.PATIENT,
            Role.RECEPTIONIST,
            Role.ACCOUNTANT,
            Role.OTHER,
            Role.DRIVER,
            Role.AMBULANCE_DISPATCHER,
            Role.AMBULANCE_COORDINATOR,
        ],
    )
    def test_rejects_ineligible_requester_role(
        self,
        clinic,
        emergency_patient,
        make_staff,
        role,
    ):
        staff = make_staff(
            clinic,
            role=role,
        )

        with pytest.raises(
            ValidationError,
            match="not eligible for emergency clinical access",
        ):
            emergency_access_service.request_emergency_access(
                actor_id=staff.user.id,
                patient_id=emergency_patient.id,
                reason="Emergency treatment.",
                purpose="Emergency care.",
                scope=DEFAULT_SCOPE,
            )

    def test_rejects_unlinked_staff_user(
        self,
        clinic,
        emergency_patient,
        make_user,
    ):
        actor = make_user(
            clinic,
            role=Role.DOCTOR,
            email="unlinked-doctor@test.com",
        )

        with pytest.raises(
            ValidationError,
            match="not linked to a staff record",
        ):
            emergency_access_service.request_emergency_access(
                actor_id=actor.id,
                patient_id=emergency_patient.id,
                reason="Emergency treatment.",
                purpose="Emergency care.",
                scope=DEFAULT_SCOPE,
            )

    def test_rejects_suspended_staff(
        self,
        clinic,
        emergency_patient,
        make_staff,
    ):
        staff = make_staff(
            clinic,
            role=Role.DOCTOR,
            status=StaffStatus.SUSPENDED,
        )

        with pytest.raises(
            ValidationError,
            match="Staff record is not active",
        ):
            emergency_access_service.request_emergency_access(
                actor_id=staff.user.id,
                patient_id=emergency_patient.id,
                reason="Emergency treatment.",
                purpose="Emergency care.",
                scope=DEFAULT_SCOPE,
            )

    def test_rejects_inactive_user(
        self,
        clinic,
        emergency_patient,
        make_staff,
    ):
        staff = make_staff(
            clinic,
            role=Role.DOCTOR,
            user_is_active=False,
        )

        with pytest.raises(
            ValidationError,
            match="User .* is inactive",
        ):
            emergency_access_service.request_emergency_access(
                actor_id=staff.user.id,
                patient_id=emergency_patient.id,
                reason="Emergency treatment.",
                purpose="Emergency care.",
                scope=DEFAULT_SCOPE,
            )

    def test_rejects_patient_from_other_clinic(
        self,
        emergency_staff,
        emergency_other_patient,
    ):
        with pytest.raises(
            NotFoundError,
            match=(
                f"Patient {emergency_other_patient.id} not found"
            ),
        ):
            emergency_access_service.request_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_other_patient.id,
                reason="Emergency treatment.",
                purpose="Emergency care.",
                scope=DEFAULT_SCOPE,
            )

    def test_rejects_suspended_clinic(
        self,
        suspended_clinic,
        make_staff,
        make_patient,
    ):
        staff = make_staff(
            suspended_clinic,
            role=Role.DOCTOR,
        )

        patient = make_patient(
            suspended_clinic,
        )

        with pytest.raises(
            ValidationError,
            match="Clinic .* is not active",
        ):
            emergency_access_service.request_emergency_access(
                actor_id=staff.user.id,
                patient_id=patient.id,
                reason="Emergency treatment.",
                purpose="Emergency care.",
                scope=DEFAULT_SCOPE,
            )

    @pytest.mark.parametrize(
        "reason",
        [
            "",
            "   ",
            None,
            123,
            "x" * 501,
        ],
    )
    def test_rejects_invalid_reason(
        self,
        emergency_patient,
        emergency_staff,
        reason,
    ):
        with pytest.raises(
            ValidationError,
        ):
            emergency_access_service.request_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                reason=reason,
                purpose="Emergency care.",
                scope=DEFAULT_SCOPE,
            )

    @pytest.mark.parametrize(
        "purpose",
        [
            "",
            "   ",
            None,
            123,
            "x" * 256,
        ],
    )
    def test_rejects_invalid_purpose(
        self,
        emergency_patient,
        emergency_staff,
        purpose,
    ):
        with pytest.raises(
            ValidationError,
        ):
            emergency_access_service.request_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                reason="Emergency treatment.",
                purpose=purpose,
                scope=DEFAULT_SCOPE,
            )

    def test_rejects_existing_requested_access(
        self,
        emergency_requested_grant,
        emergency_patient,
        emergency_staff,
    ):
        with pytest.raises(
            ConflictError,
            match="already active",
        ):
            emergency_access_service.request_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                reason="Second request.",
                purpose="Emergency care.",
                scope=DEFAULT_SCOPE,
            )

    def test_rejects_existing_active_access(
        self,
        emergency_active_grant,
        emergency_patient,
        emergency_staff,
    ):
        with pytest.raises(
            ConflictError,
            match="already active",
        ):
            emergency_access_service.request_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                reason="Second request.",
                purpose="Emergency care.",
                scope=DEFAULT_SCOPE,
            )

    def test_expired_active_access_is_marked_expired_and_allows_new_request(
        self,
        emergency_expired_grant,
        emergency_patient,
        emergency_staff,
        emergency_audit_mock,
    ):
        grant = (
            emergency_access_service.request_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                reason="New emergency request.",
                purpose="Emergency care.",
                scope=DEFAULT_SCOPE,
            )
        )

        assert (
            emergency_expired_grant.status
            == EmergencyAccessStatus.EXPIRED
        )
        assert (
            grant.status
            == EmergencyAccessStatus.REQUESTED
        )
        assert grant.id != emergency_expired_grant.id
        assert emergency_audit_mock.call_count == 2


class TestGrantEmergencyAccess:
    def test_grants_requested_access(
        self,
        emergency_requested_grant,
        emergency_reviewer,
        emergency_audit_mock,
    ):
        grant = (
            emergency_access_service.grant_emergency_access(
                emergency_access_id=(
                    emergency_requested_grant.id
                ),
                reviewer_id=emergency_reviewer.id,
                duration_minutes=30,
                review_notes=(
                    "Approved for emergency treatment."
                ),
            )
        )

        assert (
            grant.status
            == EmergencyAccessStatus.ACTIVE
        )
        assert grant.granted_at is not None
        assert grant.reviewed_at is not None
        assert (
            grant.reviewed_by_user_id
            == emergency_reviewer.id
        )
        assert (
            grant.review_notes
            == "Approved for emergency treatment."
        )
        assert grant.expires_at is not None

        duration = (
            grant.expires_at - grant.granted_at
        ).total_seconds()

        assert 29 * 60 <= duration <= 31 * 60

        call = emergency_audit_mock.call_args.kwargs

        assert (
            call["action"]
            == AuditAction.STATUS_CHANGE
        )
        assert (
            call["entity_id"]
            == emergency_requested_grant.id
        )
        assert (
            call["user_id"]
            == emergency_reviewer.id
        )
        assert (
            call["new_value"]["new_status"]
            == EmergencyAccessStatus.ACTIVE.value
        )

    def test_reviewer_cannot_approve_own_request(
        self,
        emergency_requested_grant,
        emergency_staff,
    ):
        with pytest.raises(
            ValidationError,
            match="cannot approve or deny their own request",
        ):
            emergency_access_service.grant_emergency_access(
                emergency_access_id=(
                    emergency_requested_grant.id
                ),
                reviewer_id=emergency_staff.user.id,
                duration_minutes=30,
            )

    @pytest.mark.parametrize(
        "duration_minutes",
        [
            0,
            -1,
            61,
        ],
    )
    def test_rejects_invalid_duration(
        self,
        emergency_requested_grant,
        emergency_reviewer,
        duration_minutes,
    ):
        with pytest.raises(
            ValidationError,
        ):
            emergency_access_service.grant_emergency_access(
                emergency_access_id=(
                    emergency_requested_grant.id
                ),
                reviewer_id=emergency_reviewer.id,
                duration_minutes=duration_minutes,
            )

    @pytest.mark.parametrize(
        "duration_minutes",
        [
            True,
            False,
            "30",
            30.5,
        ],
    )
    def test_rejects_non_integer_duration(
        self,
        emergency_requested_grant,
        emergency_reviewer,
        duration_minutes,
    ):
        with pytest.raises(
            ValidationError,
            match="duration must be an integer",
        ):
            emergency_access_service.grant_emergency_access(
                emergency_access_id=(
                    emergency_requested_grant.id
                ),
                reviewer_id=emergency_reviewer.id,
                duration_minutes=duration_minutes,
            )

    def test_rejects_granting_non_requested_access(
        self,
        emergency_active_grant,
        emergency_reviewer,
    ):
        with pytest.raises(
            ConflictError,
            match=(
                "cannot be granted from status 'active'"
            ),
        ):
            emergency_access_service.grant_emergency_access(
                emergency_access_id=(
                    emergency_active_grant.id
                ),
                reviewer_id=emergency_reviewer.id,
                duration_minutes=30,
            )

    def test_reviewer_must_belong_to_same_clinic(
        self,
        emergency_requested_grant,
        emergency_other_clinic,
        make_user,
    ):
        reviewer = make_user(
            emergency_other_clinic,
            role=Role.ADMIN,
            email="foreign-reviewer@test.com",
        )

        with pytest.raises(
            NotFoundError,
            match="Emergency access request not found",
        ):
            emergency_access_service.grant_emergency_access(
                emergency_access_id=(
                    emergency_requested_grant.id
                ),
                reviewer_id=reviewer.id,
                duration_minutes=30,
            )

    def test_super_admin_can_review(
        self,
        emergency_requested_grant,
        emergency_super_admin,
    ):
        grant = (
            emergency_access_service.grant_emergency_access(
                emergency_access_id=(
                    emergency_requested_grant.id
                ),
                reviewer_id=emergency_super_admin.id,
                duration_minutes=15,
            )
        )

        assert (
            grant.status
            == EmergencyAccessStatus.ACTIVE
        )
        assert (
            grant.reviewed_by_user_id
            == emergency_super_admin.id
        )

    def test_rejects_inactive_clinic(
        self,
        suspended_clinic,
        make_staff,
        make_patient,
        make_user,
        db_session,
    ):
        requester = make_staff(
            suspended_clinic,
            role=Role.DOCTOR,
        )

        patient = make_patient(
            suspended_clinic,
        )

        reviewer = make_user(
            suspended_clinic,
            role=Role.ADMIN,
            email="suspended-reviewer@test.com",
        )

        grant = EmergencyAccessGrant(
            clinic_id=suspended_clinic.id,
            patient_id=patient.id,
            requester_user_id=requester.user.id,
            requester_role=Role.DOCTOR.value,
            reason="Emergency treatment.",
            purpose="Emergency care.",
            scope=DEFAULT_SCOPE,
            status=EmergencyAccessStatus.REQUESTED,
        )

        db_session.add(grant)
        db_session.flush()

        with pytest.raises(
            ValidationError,
            match="Clinic .* is not active",
        ):
            emergency_access_service.grant_emergency_access(
                emergency_access_id=grant.id,
                reviewer_id=reviewer.id,
                duration_minutes=30,
            )


class TestDenyEmergencyAccess:
    def test_denies_requested_access(
        self,
        emergency_requested_grant,
        emergency_reviewer,
        emergency_audit_mock,
    ):
        grant = (
            emergency_access_service.deny_emergency_access(
                emergency_access_id=(
                    emergency_requested_grant.id
                ),
                reviewer_id=emergency_reviewer.id,
                review_notes=(
                    "Insufficient justification."
                ),
            )
        )

        assert (
            grant.status
            == EmergencyAccessStatus.DENIED
        )
        assert grant.reviewed_at is not None
        assert (
            grant.reviewed_by_user_id
            == emergency_reviewer.id
        )
        assert grant.review_notes == (
            "Insufficient justification."
        )

        call = emergency_audit_mock.call_args.kwargs

        assert (
            call["action"]
            == AuditAction.STATUS_CHANGE
        )
        assert (
            call["new_value"]["new_status"]
            == EmergencyAccessStatus.DENIED.value
        )

    def test_requires_review_notes(
        self,
        emergency_requested_grant,
        emergency_reviewer,
    ):
        with pytest.raises(
            ValidationError,
            match="Review notes is required",
        ):
            emergency_access_service.deny_emergency_access(
                emergency_access_id=(
                    emergency_requested_grant.id
                ),
                reviewer_id=emergency_reviewer.id,
                review_notes="   ",
            )

    def test_rejects_denial_of_non_requested_access(
        self,
        emergency_active_grant,
        emergency_reviewer,
    ):
        with pytest.raises(
            ConflictError,
            match="cannot be denied from status 'active'",
        ):
            emergency_access_service.deny_emergency_access(
                emergency_access_id=(
                    emergency_active_grant.id
                ),
                reviewer_id=emergency_reviewer.id,
                review_notes=(
                    "Cannot deny active access."
                ),
            )


class TestRevokeEmergencyAccess:
    def test_requester_can_revoke_active_access(
        self,
        emergency_active_grant,
        emergency_staff,
        emergency_audit_mock,
    ):
        grant = (
            emergency_access_service.revoke_emergency_access(
                emergency_access_id=(
                    emergency_active_grant.id
                ),
                actor_id=emergency_staff.user.id,
                reason="Emergency treatment completed.",
            )
        )

        assert (
            grant.status
            == EmergencyAccessStatus.REVOKED
        )
        assert grant.revoked_at is not None

        call = emergency_audit_mock.call_args.kwargs

        assert (
            call["action"]
            == AuditAction.STATUS_CHANGE
        )
        assert (
            call["new_value"]["new_status"]
            == EmergencyAccessStatus.REVOKED.value
        )
        assert (
            call["new_value"]["actor_user_id"]
            == emergency_staff.user.id
        )
        assert (
            call["new_value"]["reason"]
            == "Emergency treatment completed."
        )

    def test_super_admin_can_revoke(
        self,
        emergency_active_grant,
        emergency_super_admin,
    ):
        grant = (
            emergency_access_service.revoke_emergency_access(
                emergency_access_id=(
                    emergency_active_grant.id
                ),
                actor_id=emergency_super_admin.id,
                reason="Emergency session closed.",
            )
        )

        assert (
            grant.status
            == EmergencyAccessStatus.REVOKED
        )

    def test_same_clinic_reviewer_can_revoke(
        self,
        emergency_active_grant,
        emergency_reviewer,
    ):
        grant = (
            emergency_access_service.revoke_emergency_access(
                emergency_access_id=(
                    emergency_active_grant.id
                ),
                actor_id=emergency_reviewer.id,
                reason="Reviewer terminated access.",
            )
        )

        assert (
            grant.status
            == EmergencyAccessStatus.REVOKED
        )

    def test_foreign_actor_cannot_revoke(
        self,
        emergency_active_grant,
        emergency_other_staff,
    ):
        with pytest.raises(
            NotFoundError,
            match="Emergency access request not found",
        ):
            emergency_access_service.revoke_emergency_access(
                emergency_access_id=(
                    emergency_active_grant.id
                ),
                actor_id=(
                    emergency_other_staff.user.id
                ),
                reason="Unauthorized attempt.",
            )

    def test_expired_access_becomes_expired_on_revoke_attempt(
        self,
        emergency_expired_grant,
        emergency_staff,
        emergency_audit_mock,
    ):
        grant = (
            emergency_access_service.revoke_emergency_access(
                emergency_access_id=(
                    emergency_expired_grant.id
                ),
                actor_id=emergency_staff.user.id,
                reason="Attempted revoke after expiry.",
            )
        )

        assert (
            grant.status
            == EmergencyAccessStatus.EXPIRED
        )
        assert grant.revoked_at is None

        call = emergency_audit_mock.call_args.kwargs

        assert (
            call["new_value"]["new_status"]
            == EmergencyAccessStatus.EXPIRED.value
        )

    def test_already_expired_is_idempotent(
        self,
        emergency_expired_grant,
        emergency_staff,
        emergency_audit_mock,
    ):
        emergency_expired_grant.status = (
            EmergencyAccessStatus.EXPIRED
        )

        result = (
            emergency_access_service.revoke_emergency_access(
                emergency_access_id=(
                    emergency_expired_grant.id
                ),
                actor_id=emergency_staff.user.id,
                reason="Already expired.",
            )
        )

        assert (
            result.status
            == EmergencyAccessStatus.EXPIRED
        )
        emergency_audit_mock.assert_not_called()

    def test_requires_valid_reason(
        self,
        emergency_active_grant,
        emergency_staff,
    ):
        with pytest.raises(
            ValidationError,
            match="Revoke reason is required",
        ):
            emergency_access_service.revoke_emergency_access(
                emergency_access_id=(
                    emergency_active_grant.id
                ),
                actor_id=emergency_staff.user.id,
                reason="   ",
            )


class TestExpireEmergencyAccess:
    def test_expires_expired_active_grant(
        self,
        emergency_expired_grant,
        emergency_audit_mock,
    ):
        grant = (
            emergency_access_service.expire_emergency_access(
                emergency_access_id=(
                    emergency_expired_grant.id
                )
            )
        )

        assert (
            grant.status
            == EmergencyAccessStatus.EXPIRED
        )

        call = emergency_audit_mock.call_args.kwargs

        assert (
            call["action"]
            == AuditAction.STATUS_CHANGE
        )
        assert (
            call["new_value"]["new_status"]
            == EmergencyAccessStatus.EXPIRED.value
        )

    def test_expire_is_idempotent(
        self,
        emergency_expired_grant,
        emergency_audit_mock,
    ):
        emergency_expired_grant.status = (
            EmergencyAccessStatus.EXPIRED
        )

        result = (
            emergency_access_service.expire_emergency_access(
                emergency_access_id=(
                    emergency_expired_grant.id
                )
            )
        )

        assert (
            result.status
            == EmergencyAccessStatus.EXPIRED
        )
        emergency_audit_mock.assert_not_called()

    def test_rejects_future_expiry(
        self,
        emergency_active_grant,
    ):
        with pytest.raises(
            ConflictError,
            match="has not expired yet",
        ):
            emergency_access_service.expire_emergency_access(
                emergency_access_id=(
                    emergency_active_grant.id
                )
            )

    def test_rejects_active_grant_without_expiry(
        self,
        emergency_active_grant,
        db_session,
    ):
        emergency_active_grant.expires_at = None
        db_session.flush()

        with pytest.raises(
            ValidationError,
            match="missing an expiry timestamp",
        ):
            emergency_access_service.expire_emergency_access(
                emergency_access_id=(
                    emergency_active_grant.id
                )
            )

    def test_rejects_non_active_grant(
        self,
        emergency_requested_grant,
    ):
        with pytest.raises(
            ConflictError,
            match="cannot expire from status 'requested'",
        ):
            emergency_access_service.expire_emergency_access(
                emergency_access_id=(
                    emergency_requested_grant.id
                )
            )


class TestAssertEmergencyAccess:
    def test_allows_exact_scope(
        self,
        emergency_active_grant,
        emergency_staff,
        emergency_patient,
        emergency_audit_mock,
    ):
        emergency_active_grant.scope = [
            "consultation:123:read",
        ]

        result = (
            emergency_access_service.assert_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                resource_type="consultation",
                resource_id=123,
                action="read",
            )
        )

        assert result is True

        emergency_audit_mock.assert_called_once()

        call = emergency_audit_mock.call_args.kwargs

        assert call["action"] == AuditAction.VIEW
        assert (
            call["new_value"]["resource_type"]
            == "consultation"
        )
        assert (
            call["new_value"]["resource_id"]
            == 123
        )

    def test_allows_resource_wildcard_action(
        self,
        emergency_active_grant,
        emergency_staff,
        emergency_patient,
    ):
        emergency_active_grant.scope = [
            "consultation:123:*",
        ]

        assert (
            emergency_access_service.assert_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                resource_type="consultation",
                resource_id=123,
                action="write",
            )
            is True
        )

    def test_allows_resource_type_action(
        self,
        emergency_active_grant,
        emergency_staff,
        emergency_patient,
    ):
        emergency_active_grant.scope = [
            "consultation:read",
        ]

        assert (
            emergency_access_service.assert_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                resource_type="consultation",
                resource_id=456,
                action="read",
            )
            is True
        )

    def test_denies_missing_grant(
        self,
        emergency_staff,
        emergency_patient,
    ):
        assert (
            emergency_access_service.assert_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                resource_type="patient",
                resource_id=emergency_patient.id,
                action="read",
            )
            is False
        )

    def test_denies_expired_grant(
        self,
        emergency_expired_grant,
        emergency_staff,
        emergency_patient,
    ):
        assert (
            emergency_access_service.assert_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                resource_type="patient",
                resource_id=emergency_patient.id,
                action="read",
            )
            is False
        )

    def test_denies_scope_mismatch(
        self,
        emergency_active_grant,
        emergency_staff,
        emergency_patient,
    ):
        emergency_active_grant.scope = [
            "patient:read",
        ]

        assert (
            emergency_access_service.assert_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                resource_type="patient",
                resource_id=emergency_patient.id,
                action="write",
            )
            is False
        )

    def test_denies_patient_resource_for_different_patient_id(
        self,
        emergency_active_grant,
        emergency_staff,
        emergency_patient,
    ):
        emergency_active_grant.scope = [
            "patient:read",
        ]

        assert (
            emergency_access_service.assert_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                resource_type="patient",
                resource_id=999999,
                action="read",
            )
            is False
        )

    def test_denies_foreign_patient(
        self,
        emergency_active_grant,
        emergency_staff,
        emergency_other_patient,
    ):
        emergency_active_grant.scope = [
            "patient:read",
        ]

        assert (
            emergency_access_service.assert_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_other_patient.id,
                resource_type="patient",
                resource_id=emergency_other_patient.id,
                action="read",
            )
            is False
        )

    def test_denies_access_after_staff_is_suspended(
        self,
        emergency_active_grant,
        emergency_staff,
        emergency_patient,
        db_session,
    ):
        emergency_staff.status = StaffStatus.SUSPENDED
        db_session.flush()

        with pytest.raises(
            ValidationError,
            match="Staff record is not active",
        ):
            emergency_access_service.assert_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                resource_type="patient",
                resource_id=emergency_patient.id,
                action="read",
            )

    def test_denies_access_after_clinic_is_suspended(
        self,
        emergency_active_grant,
        emergency_staff,
        emergency_patient,
        clinic,
        db_session,
    ):
        from app.core.enums.clinic_enums import ClinicStatus

        clinic.status = ClinicStatus.SUSPENDED
        db_session.flush()

        with pytest.raises(
            ValidationError,
            match="Clinic .* is not active",
        ):
            emergency_access_service.assert_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                resource_type="patient",
                resource_id=emergency_patient.id,
                action="read",
            )

    def test_denies_ineligible_role(
        self,
        emergency_active_grant,
        emergency_patient,
        emergency_staff,
    ):
        emergency_staff.user.role = Role.ADMIN

        assert (
            emergency_access_service.assert_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                resource_type="patient",
                resource_id=emergency_patient.id,
                action="read",
            )
            is False
        )

    @pytest.mark.parametrize(
        "field,value",
        [
            ("actor_id", 0),
            ("patient_id", 0),
            ("resource_id", 0),
        ],
    )
    def test_rejects_invalid_ids(
        self,
        emergency_active_grant,
        emergency_staff,
        emergency_patient,
        field,
        value,
    ):
        kwargs = {
            "actor_id": emergency_staff.user.id,
            "patient_id": emergency_patient.id,
            "resource_type": "patient",
            "resource_id": emergency_patient.id,
            "action": "read",
        }

        kwargs[field] = value

        with pytest.raises(
            ValidationError,
        ):
            emergency_access_service.assert_emergency_access(
                **kwargs
            )

    @pytest.mark.parametrize(
        "resource_type",
        [
            "",
            " ",
            "bad type",
            "resource!",
        ],
    )
    def test_rejects_invalid_resource_type(
        self,
        emergency_staff,
        emergency_patient,
        resource_type,
    ):
        with pytest.raises(
            ValidationError,
        ):
            emergency_access_service.assert_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                resource_type=resource_type,
                resource_id=1,
                action="read",
            )

    @pytest.mark.parametrize(
        "action",
        [
            "",
            " ",
            "bad action",
            "read!",
        ],
    )
    def test_rejects_invalid_action(
        self,
        emergency_staff,
        emergency_patient,
        action,
    ):
        with pytest.raises(
            ValidationError,
        ):
            emergency_access_service.assert_emergency_access(
                actor_id=emergency_staff.user.id,
                patient_id=emergency_patient.id,
                resource_type="patient",
                resource_id=1,
                action=action,
            )