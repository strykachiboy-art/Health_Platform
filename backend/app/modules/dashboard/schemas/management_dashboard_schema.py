from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums.ai_enums import (
    AIFeature,
    AIApprovalStatus,
    AIRiskLevel,
)

from app.modules.dashboard.schemas.department_dashboard_schema import (
    ManagementDepartmentDashboardSchema,
)

from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardActivitySchema,
    DashboardAlertSchema,
    DashboardChatSummarySchema,
    DashboardContextSchema,
    DashboardPeriodSchema,
    DashboardMetricSchema,
)


class ManagementHospitalAdmissionsSchema(BaseModel):
    active_admissions: int = Field(..., ge=0)
    total_beds: int = Field(..., ge=0)
    occupied_beds: int = Field(..., ge=0)
    available_beds: int = Field(..., ge=0)

    model_config = ConfigDict(
        extra="forbid",
    )


class ManagementNursingOperationsSchema(BaseModel):
    active_care_plans: int = Field(..., ge=0)
    pending_tasks: int = Field(..., ge=0)
    in_progress_tasks: int = Field(..., ge=0)
    urgent_open_tasks: int = Field(..., ge=0)
    overdue_open_tasks: int = Field(..., ge=0)
    unsigned_assessments: int = Field(..., ge=0)
    ready_handoffs: int = Field(..., ge=0)

    model_config = ConfigDict(
        extra="forbid",
    )


class ManagementEMAROperationsSchema(BaseModel):
    scheduled: int = Field(..., ge=0)
    due: int = Field(..., ge=0)
    administered: int = Field(..., ge=0)
    held: int = Field(..., ge=0)
    refused: int = Field(..., ge=0)
    missed: int = Field(..., ge=0)

    model_config = ConfigDict(
        extra="forbid",
    )


class ManagementMedicationReconciliationSchema(BaseModel):
    draft: int = Field(..., ge=0)
    final: int = Field(..., ge=0)
    amended: int = Field(..., ge=0)
    draft_discharge: int = Field(..., ge=0)
    finalized_discharge: int = Field(..., ge=0)

    model_config = ConfigDict(
        extra="forbid",
    )


class ManagementHospitalOperationsSchema(BaseModel):
    admissions: ManagementHospitalAdmissionsSchema
    nursing: ManagementNursingOperationsSchema
    emar: ManagementEMAROperationsSchema
    medication_reconciliation: (
        ManagementMedicationReconciliationSchema
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class ManagementDashboardOverviewSchema(BaseModel):
    total_patients: int = Field(
        ...,
        ge=0,
    )

    active_patients: int = Field(
        ...,
        ge=0,
    )

    total_staff: int = Field(
        ...,
        ge=0,
    )

    active_staff: int = Field(
        ...,
        ge=0,
    )

    appointments_today: int = Field(
        ...,
        ge=0,
    )

    missed_appointments_today: int = Field(
        ...,
        ge=0,
    )

    active_admissions: int = Field(
        ...,
        ge=0,
    )

    occupied_beds: int = Field(
        ...,
        ge=0,
    )

    pending_lab_orders: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class ManagementAIOverviewSchema(BaseModel):
    total_ai_requests: int = Field(
        ...,
        ge=0,
    )

    total_credits_used: int = Field(
        ...,
        ge=0,
    )

    total_tokens: int = Field(
        ...,
        ge=0,
    )

    estimated_cost: Decimal = Field(
        ...,
        ge=0,
    )

    pending_reviews: int = Field(
        ...,
        ge=0,
    )

    approved_reviews: int = Field(
        ...,
        ge=0,
    )

    rejected_reviews: int = Field(
        ...,
        ge=0,
    )

    high_risk_results: int = Field(
        ...,
        ge=0,
    )

    critical_risk_results: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class ManagementAIFeatureUsageSchema(BaseModel):
    feature: AIFeature

    request_count: int = Field(
        ...,
        ge=0,
    )

    credits_used: int = Field(
        ...,
        ge=0,
    )

    estimated_cost: Decimal = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class ManagementAIRiskSummarySchema(BaseModel):
    risk_level: AIRiskLevel

    count: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class ManagementAIApprovalSummarySchema(BaseModel):
    approval_status: AIApprovalStatus

    count: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class ManagementAIDashboardSchema(BaseModel):
    overview: ManagementAIOverviewSchema

    feature_usage: list[
        ManagementAIFeatureUsageSchema
    ] = Field(
        default_factory=list,
    )

    risk_summary: list[
        ManagementAIRiskSummarySchema
    ] = Field(
        default_factory=list,
    )

    approval_summary: list[
        ManagementAIApprovalSummarySchema
    ] = Field(
        default_factory=list,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class ManagementDashboardSchema(BaseModel):
    context: DashboardContextSchema

    period: DashboardPeriodSchema

    overview: ManagementDashboardOverviewSchema

    hospital: ManagementHospitalOperationsSchema

    departments: ManagementDepartmentDashboardSchema

    ai: ManagementAIDashboardSchema

    chat: DashboardChatSummarySchema

    metrics: list[
        DashboardMetricSchema
    ] = Field(
        default_factory=list,
    )

    alerts: list[
        DashboardAlertSchema
    ] = Field(
        default_factory=list,
    )

    recent_activity: list[
        DashboardActivitySchema
    ] = Field(
        default_factory=list,
    )

    model_config = ConfigDict(
        extra="forbid",
    )