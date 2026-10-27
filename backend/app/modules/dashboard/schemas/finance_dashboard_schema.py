from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardActivitySchema,
    DashboardAlertSchema,
    DashboardChatSummarySchema,
    DashboardContextSchema,
    DashboardPeriodSchema,
    DashboardMetricSchema,
)


class FinanceDashboardOverviewSchema(BaseModel):
    outstanding_invoice_count: int = Field(
        ...,
        ge=0,
    )

    outstanding_invoice_amount: Decimal = Field(
        ...,
        ge=0,
    )

    overdue_invoice_count: int = Field(
        ...,
        ge=0,
    )

    overdue_invoice_amount: Decimal = Field(
        ...,
        ge=0,
    )

    successful_payments_today: int = Field(
        ...,
        ge=0,
    )

    successful_payments_today_amount: Decimal = Field(
        ...,
        ge=0,
    )

    pending_payment_count: int = Field(
        ...,
        ge=0,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class FinanceAICostSchema(BaseModel):
    total_ai_requests: int = Field(
        ...,
        ge=0,
    )

    total_credits_used: int = Field(
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


class FinanceDashboardSchema(BaseModel):
    context: DashboardContextSchema

    period: DashboardPeriodSchema

    overview: FinanceDashboardOverviewSchema

    ai: FinanceAICostSchema

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