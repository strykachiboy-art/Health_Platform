from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardContextSchema,
    DashboardPeriodSchema,
    DashboardMetricSchema,
)


class PatientDashboardOverviewSchema(BaseModel):
    upcoming_appointments: int = Field(
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

    outstanding_balance: Decimal = Field(
        ...,
        ge=0,
    )

    unread_notifications: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class PatientDashboardSchema(BaseModel):
    context: DashboardContextSchema

    period: DashboardPeriodSchema

    overview: PatientDashboardOverviewSchema

    metrics: list[
        DashboardMetricSchema
    ] = Field(
        default_factory=list,
    )

    model_config = ConfigDict(
        extra="forbid",
    )