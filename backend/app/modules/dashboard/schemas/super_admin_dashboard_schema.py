from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums.ai_enums import (
    AIFeature,
    AIApprovalStatus,
    AIRiskLevel,
)

from app.modules.dashboard.schemas.department_dashboard_schema import (
    SuperAdminDepartmentDashboardSchema,
)

from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardActivitySchema,
    DashboardAlertSchema,
    DashboardContextSchema,
    DashboardPeriodSchema,
    DashboardMetricSchema,
)


class SuperAdminDashboardOverviewSchema(BaseModel):
    total_clinics: int = Field(
        ...,
        ge=0,
    )

    active_clinics: int = Field(
        ...,
        ge=0,
    )

    suspended_clinics: int = Field(
        ...,
        ge=0,
    )

    total_users: int = Field(
        ...,
        ge=0,
    )

    active_users: int = Field(
        ...,
        ge=0,
    )

    total_staff: int = Field(
        ...,
        ge=0,
    )

    total_patients: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class SuperAdminAccessControlOverviewSchema(BaseModel):
    total_users: int = Field(
        ...,
        ge=0,
    )

    active_users: int = Field(
        ...,
        ge=0,
    )

    inactive_users: int = Field(
        ...,
        ge=0,
    )

    total_admins: int = Field(
        ...,
        ge=0,
    )

    active_admins: int = Field(
        ...,
        ge=0,
    )

    total_super_admins: int = Field(
        ...,
        ge=0,
    )

    users_by_role: dict[str, int] = Field(
        default_factory=dict,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class SuperAdminAIOverviewSchema(BaseModel):
    total_ai_requests: int = Field(
        ...,
        ge=0,
    )

    total_credits_used: int = Field(
        ...,
        ge=0,
    )

    total_input_tokens: int = Field(
        ...,
        ge=0,
    )

    total_output_tokens: int = Field(
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

    low_risk_results: int = Field(
        ...,
        ge=0,
    )

    medium_risk_results: int = Field(
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


class SuperAdminAIFeatureUsageSchema(BaseModel):
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


class SuperAdminAIRiskSummarySchema(BaseModel):
    risk_level: AIRiskLevel

    count: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class SuperAdminAIApprovalSummarySchema(BaseModel):
    approval_status: AIApprovalStatus

    count: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class SuperAdminAIDashboardSchema(BaseModel):
    overview: SuperAdminAIOverviewSchema

    feature_usage: list[
        SuperAdminAIFeatureUsageSchema
    ] = Field(
        default_factory=list,
    )

    risk_summary: list[
        SuperAdminAIRiskSummarySchema
    ] = Field(
        default_factory=list,
    )

    approval_summary: list[
        SuperAdminAIApprovalSummarySchema
    ] = Field(
        default_factory=list,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class SuperAdminDashboardSchema(BaseModel):
    context: DashboardContextSchema

    period: DashboardPeriodSchema

    overview: SuperAdminDashboardOverviewSchema

    departments: SuperAdminDepartmentDashboardSchema

    access_control: SuperAdminAccessControlOverviewSchema

    ai: SuperAdminAIDashboardSchema

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