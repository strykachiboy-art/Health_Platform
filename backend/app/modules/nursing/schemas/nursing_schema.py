from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums.nursing_enums import (
    NursingCarePlanStatus,
    NursingCareTaskPriority,
    NursingCareTaskStatus,
    NursingHandoffStatus,
    NursingNoteStatus,
    NursingNoteType,
)


class _RequestModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class _ResponseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
    )


class NursingCarePlanCreateSchema(_RequestModel):
    title: str = Field(..., min_length=1, max_length=200)
    problem: str = Field(..., min_length=1, max_length=10000)
    goal: str = Field(..., min_length=1, max_length=10000)
    target_at: datetime | None = None


class NursingCarePlanResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int = Field(..., gt=0)
    admission_id: int = Field(..., gt=0)
    created_by_staff_id: int = Field(..., gt=0)
    title: str
    problem: str
    goal: str
    status: NursingCarePlanStatus
    started_at: datetime
    target_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    version: int = Field(..., ge=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NursingCarePlanListResponseSchema(_ResponseModel):
    items: list[NursingCarePlanResponseSchema] = Field(default_factory=list)
    total: int = Field(..., ge=0)


class NursingCareTaskCreateSchema(_RequestModel):
    description: str = Field(..., min_length=1, max_length=10000)
    instructions: str | None = Field(default=None, max_length=10000)
    priority: NursingCareTaskPriority = NursingCareTaskPriority.ROUTINE
    assigned_to_staff_id: int | None = Field(default=None, gt=0)
    due_at: datetime | None = None

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value):
        if value is None:
            return None
        value = value.strip()
        return value or None


class NursingCareTaskCancelSchema(_RequestModel):
    reason: str = Field(..., min_length=1, max_length=10000)


class NursingCareTaskResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    care_plan_id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int = Field(..., gt=0)
    admission_id: int = Field(..., gt=0)
    created_by_staff_id: int = Field(..., gt=0)
    assigned_to_staff_id: int | None = Field(default=None, gt=0)
    completed_by_staff_id: int | None = Field(default=None, gt=0)
    description: str
    instructions: str | None = None
    priority: NursingCareTaskPriority
    status: NursingCareTaskStatus
    due_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = None
    version: int = Field(..., ge=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NursingCareTaskListResponseSchema(_ResponseModel):
    items: list[NursingCareTaskResponseSchema] = Field(default_factory=list)
    total: int = Field(..., ge=0)


class NursingNoteCreateSchema(_RequestModel):
    note_text: str = Field(..., min_length=1, max_length=20000)
    note_type: NursingNoteType = NursingNoteType.PROGRESS
    observed_at: datetime | None = None


class NursingNoteUpdateSchema(_RequestModel):
    note_text: str = Field(..., min_length=1, max_length=20000)
    note_type: NursingNoteType | None = None
    observed_at: datetime | None = None


class NursingNoteResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int = Field(..., gt=0)
    admission_id: int = Field(..., gt=0)
    author_staff_id: int = Field(..., gt=0)
    note_type: NursingNoteType
    status: NursingNoteStatus
    note_text: str
    observed_at: datetime
    signed_at: datetime | None = None
    version: int = Field(..., ge=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NursingNoteListResponseSchema(_ResponseModel):
    items: list[NursingNoteResponseSchema] = Field(default_factory=list)
    total: int = Field(..., ge=0)


class NursingHandoffCreateSchema(_RequestModel):
    shift_start_at: datetime
    shift_end_at: datetime
    incoming_staff_id: int | None = Field(default=None, gt=0)
    situation: str | None = Field(default=None, max_length=10000)
    background: str | None = Field(default=None, max_length=10000)
    assessment: str | None = Field(default=None, max_length=10000)
    recommendation: str | None = Field(default=None, max_length=10000)
    safety_concerns: str | None = Field(default=None, max_length=10000)
    pending_actions: str | None = Field(default=None, max_length=10000)


class NursingHandoffUpdateSchema(_RequestModel):
    incoming_staff_id: int | None = Field(default=None, gt=0)
    situation: str | None = Field(default=None, max_length=10000)
    background: str | None = Field(default=None, max_length=10000)
    assessment: str | None = Field(default=None, max_length=10000)
    recommendation: str | None = Field(default=None, max_length=10000)
    safety_concerns: str | None = Field(default=None, max_length=10000)
    pending_actions: str | None = Field(default=None, max_length=10000)


class NursingHandoffResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int = Field(..., gt=0)
    admission_id: int = Field(..., gt=0)
    outgoing_staff_id: int = Field(..., gt=0)
    incoming_staff_id: int | None = Field(default=None, gt=0)
    acknowledged_by_staff_id: int | None = Field(default=None, gt=0)
    status: NursingHandoffStatus
    shift_start_at: datetime
    shift_end_at: datetime
    situation: str | None = None
    background: str | None = None
    assessment: str | None = None
    recommendation: str | None = None
    safety_concerns: str | None = None
    pending_actions: str | None = None
    ready_at: datetime | None = None
    acknowledged_at: datetime | None = None
    cancelled_at: datetime | None = None
    version: int = Field(..., ge=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NursingHandoffListResponseSchema(_ResponseModel):
    items: list[NursingHandoffResponseSchema] = Field(default_factory=list)
    total: int = Field(..., ge=0)
