"""CT1C nursing API input contracts for explicit primary assignments.

These schemas do not infer clinician ownership from consultation or task records.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt


AssignmentKind = Literal["primary_doctor", "primary_nurse"]


class _StrictCareTeamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PrimaryClinicianAssignmentRequest(_StrictCareTeamRequest):
    assignment_kind: AssignmentKind
    staff_id: StrictInt = Field(..., gt=0)
    assignment_reason: str = Field(..., min_length=1, max_length=500)
    expected_current_assignment_id: StrictInt | None = Field(default=None, gt=0)


class PrimaryClinicianEndRequest(_StrictCareTeamRequest):
    assignment_kind: AssignmentKind
    expected_current_assignment_id: StrictInt = Field(..., gt=0)
    end_reason: str = Field(..., min_length=1, max_length=500)
