from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums.ai_enums import (
    AIFeature,
    AIApprovalStatus,
    AIRiskLevel,
)

from app.modules.dashboard.schemas.department_dashboard_schema import (
    ClinicalDepartmentSchema,
)

from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardActivitySchema,
    DashboardAlertSchema,
    DashboardChatSummarySchema,
    DashboardContextSchema,
    DashboardPeriodSchema,
    DashboardMetricSchema,
)


class ClinicalDashboardOverviewSchema(BaseModel):
    appointments_today: int = Field(
        ...,
        ge=0,
    )

    confirmed_appointments_today: int = Field(
        ...,
        ge=0,
    )

    pending_consultations: int = Field(
        ...,
        ge=0,
    )

    pending_lab_orders: int = Field(
        ...,
        ge=0,
    )

    active_prescriptions: int = Field(
        ...,
        ge=0,
    )

    active_admissions: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class ClinicalAIOverviewSchema(BaseModel):
    ai_requests: int = Field(
        ...,
        ge=0,
    )

    pending_reviews: int = Field(
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

    approved_results: int = Field(
        ...,
        ge=0,
    )

    rejected_results: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class ClinicalAIFeatureUsageSchema(BaseModel):
    feature: AIFeature

    request_count: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class ClinicalAIRiskSummarySchema(BaseModel):
    risk_level: AIRiskLevel

    count: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class ClinicalAIReviewSummarySchema(BaseModel):
    approval_status: AIApprovalStatus

    count: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class ClinicalAIDashboardSchema(BaseModel):
    overview: ClinicalAIOverviewSchema

    feature_usage: list[
        ClinicalAIFeatureUsageSchema
    ] = Field(
        default_factory=list,
    )

    risk_summary: list[
        ClinicalAIRiskSummarySchema
    ] = Field(
        default_factory=list,
    )

    review_summary: list[
        ClinicalAIReviewSummarySchema
    ] = Field(
        default_factory=list,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class ClinicalDashboardSchema(BaseModel):
    context: DashboardContextSchema

    period: DashboardPeriodSchema

    overview: ClinicalDashboardOverviewSchema

    my_departments: list[
        ClinicalDepartmentSchema
    ] = Field(
        default_factory=list,
    )

    ai: ClinicalAIDashboardSchema

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