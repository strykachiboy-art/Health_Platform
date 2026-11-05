"""CT1C routes attached to the EXISTING nursing blueprint.

Authorization lives at the route boundary; all writes delegate to the
CT1B2 transactional assignment service. No chat/handoff side effects.
"""

from __future__ import annotations

from flask import jsonify

from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError
from app.core.utils.decorators import active_staff_required, role_required
from app.extensions import db
from app.modules.nursing.schemas.care_team_route_schema import (
    PrimaryClinicianAssignmentRequest,
    PrimaryClinicianEndRequest,
)
from app.modules.nursing.services.care_team_assignment_service import (
    assign_primary_clinician,
    end_primary_clinician_assignment,
    get_primary_assignments,
    list_assignment_history,
)
from app.modules.nursing.services.care_team_context_service import (
    get_admission_care_context,
)
from app.modules.staff.models.staff_model import Staff


VIEW_ROLES = (Role.ADMIN, Role.DOCTOR, Role.NURSE)
WRITE_ROLES = (Role.ADMIN,)


def _assignment_response(assignment, *, clinic_id: int) -> dict | None:
    if assignment is None:
        return None
    staff = db.session.execute(
        db.select(Staff).where(
            Staff.id == assignment.staff_id,
            Staff.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()
    if staff is None:
        raise ConflictError("Assignment staff context is inconsistent")
    return {
        "assignment_id": assignment.id,
        "assignment_kind": assignment.assignment_kind,
        "staff_id": assignment.staff_id,
        "staff_user_id": staff.user_id,
        "staff_display_name": f"{staff.first_name} {staff.last_name}".strip(),
        "staff_active": bool(
            staff.status == StaffStatus.ACTIVE
            and staff.user is not None
            and staff.user.clinic_id == clinic_id
            and staff.user.is_active
        ),
        "assigned_by_user_id": assignment.assigned_by_user_id,
        "assignment_reason": assignment.assignment_reason,
        "assigned_at": assignment.assigned_at.isoformat(),
        "ended_at": assignment.ended_at.isoformat() if assignment.ended_at else None,
        "ended_by_user_id": assignment.ended_by_user_id,
        "end_reason": assignment.end_reason,
    }


def register_care_team_routes(
    nursing_bp,
    *,
    current_user,
    current_clinic_id,
    request_json,
):
    """Add CT1C endpoints to the canonical nursing Blueprint once."""

    @nursing_bp.get("/admissions/<int:admission_id>/care-team")
    @role_required(*VIEW_ROLES)
    @active_staff_required
    def get_admission_care_team_route(admission_id: int):
        clinic_id = current_clinic_id()
        # This call performs the existing admission/ward tenant check.
        context = get_admission_care_context(
            admission_id=admission_id,
            clinic_id=clinic_id,
        )
        primary = get_primary_assignments(
            admission_id=admission_id,
            clinic_id=clinic_id,
        )
        is_active_admission = (
            context["admission_status"] == AdmissionStatus.ADMITTED.value
        )
        doctor = (
            _assignment_response(primary["primary_doctor"], clinic_id=clinic_id)
            if is_active_admission else None
        )
        nurse = (
            _assignment_response(primary["primary_nurse"], clinic_id=clinic_id)
            if is_active_admission else None
        )
        context["responsibility"] = {
            "primary_doctor_staff_id": doctor["staff_id"] if doctor else None,
            "primary_nurse_staff_id": nurse["staff_id"] if nurse else None,
            "primary_doctor": doctor,
            "primary_nurse": nurse,
            "primary_doctor_confirmed": bool(doctor and doctor["staff_active"]),
            "primary_nurse_confirmed": bool(nurse and nurse["staff_active"]),
            "confirmed": bool(
                doctor and doctor["staff_active"]
                and nurse and nurse["staff_active"]
            ),
        }
        context["authoritative_assignment_available"] = True
        context["admission_active"] = is_active_admission
        return jsonify(context), 200

    @nursing_bp.get("/admissions/<int:admission_id>/care-team/assignment-history")
    @role_required(*VIEW_ROLES)
    @active_staff_required
    def get_admission_care_team_history_route(admission_id: int):
        clinic_id = current_clinic_id()
        assignments = list_assignment_history(
            admission_id=admission_id,
            clinic_id=clinic_id,
        )
        items = [
            _assignment_response(row, clinic_id=clinic_id)
            for row in assignments
        ]
        return jsonify({"items": items, "total": len(items)}), 200

    @nursing_bp.post("/admissions/<int:admission_id>/care-team/assignments")
    @role_required(*WRITE_ROLES)
    @active_staff_required
    def assign_primary_care_team_route(admission_id: int):
        data = PrimaryClinicianAssignmentRequest.model_validate(request_json())
        assigned = assign_primary_clinician(
            clinic_id=current_clinic_id(),
            admission_id=admission_id,
            actor_user_id=current_user().id,
            staff_id=data.staff_id,
            assignment_kind=data.assignment_kind,
            assignment_reason=data.assignment_reason,
            expected_current_assignment_id=data.expected_current_assignment_id,
        )
        return jsonify({
            "message": "Primary clinician assignment recorded",
            "assignment": _assignment_response(
                assigned, clinic_id=assigned.clinic_id,
            ),
        }), (200 if data.expected_current_assignment_id is not None else 201)

    @nursing_bp.post("/admissions/<int:admission_id>/care-team/end-assignment")
    @role_required(*WRITE_ROLES)
    @active_staff_required
    def end_primary_care_team_route(admission_id: int):
        data = PrimaryClinicianEndRequest.model_validate(request_json())
        ended = end_primary_clinician_assignment(
            clinic_id=current_clinic_id(),
            admission_id=admission_id,
            actor_user_id=current_user().id,
            assignment_kind=data.assignment_kind,
            expected_current_assignment_id=data.expected_current_assignment_id,
            end_reason=data.end_reason,
        )
        return jsonify({
            "message": "Primary clinician assignment ended",
            "assignment": _assignment_response(ended, clinic_id=ended.clinic_id),
        }), 200
