from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardActivitySchema,
    DashboardAlertSchema,
    DashboardChatSummarySchema,
    DashboardContextSchema,
    DashboardPeriodSchema,
    DashboardMetricSchema,
)


class OperationsDashboardOverviewSchema(BaseModel):
    appointments_today: int = Field(
        ...,
        ge=0,
    )

    missed_appointments_today: int = Field(
        ...,
        ge=0,
    )

    scheduled_appointments_today: int = Field(
        ...,
        ge=0,
    )

    confirmed_appointments_today: int = Field(
        ...,
        ge=0,
    )

    active_ambulance_trips: int = Field(
        ...,
        ge=0,
    )

    pending_ambulance_requests: int = Field(
        ...,
        ge=0,
    )

    active_staff: int = Field(
        ...,
        ge=0,
    )

    active_departments: int = Field(
        ...,
        ge=0,
    )

    unassigned_active_staff: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class OperationsDashboardSchema(BaseModel):
    context: DashboardContextSchema

    period: DashboardPeriodSchema

    overview: OperationsDashboardOverviewSchema

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