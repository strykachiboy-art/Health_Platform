from __future__ import annotations

from app.extensions import db

from app.core.auth.user.models.user_model import User
from app.core.enums.billing_enums import (
    InvoiceStatus,
    PaymentStatus,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError

from app.modules.ai.models.ai_model import AILog
from app.modules.billing.models.billing_model import (
    Invoice,
    Payment,
)

from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardAlertSchema,
    DashboardQuerySchema,
)

from app.modules.dashboard.schemas.finance_dashboard_schema import (
    FinanceAICostSchema,
    FinanceDashboardOverviewSchema,
    FinanceDashboardSchema,
)

from app.modules.dashboard.services.dashboard_widget_service import (
    build_chat_summary,
    build_dashboard_context,
    build_metric,
    build_recent_activity,
    decimal_scalar,
    period_bounds,
    resolve_dashboard_period,
)


def get_finance_dashboard(
    *,
    actor: User,
    query: DashboardQuerySchema | None = None,
) -> FinanceDashboardSchema:
    if actor.role is not Role.ACCOUNTANT:
        raise ValidationError(
            "Finance dashboard is not available "
            "to this user"
        )

    if not actor.is_active:
        raise ValidationError(
            "User account is inactive"
        )

    if actor.clinic_id is None or actor.clinic_id <= 0:
        raise ValidationError(
            "Accountant is not associated "
            "with a valid clinic"
        )

    clinic_id = actor.clinic_id

    period = resolve_dashboard_period(
        query,
    )

    start, end = period_bounds(
        period,
    )

    outstanding_invoice_count = (
        db.session.execute(
            db.select(
                db.func.count(
                    Invoice.id,
                )
            ).where(
                Invoice.clinic_id == clinic_id,
                Invoice.status.in_(
                    [
                        InvoiceStatus.ISSUED,
                        InvoiceStatus.PARTIALLY_PAID,
                        InvoiceStatus.OVERDUE,
                    ]
                ),
                Invoice.amount_paid
                < Invoice.total_amount,
            )
        ).scalar_one()
        or 0
    )

    outstanding_invoice_amount = decimal_scalar(
        db.select(
            db.func.coalesce(
                db.func.sum(
                    Invoice.total_amount
                    - Invoice.amount_paid,
                ),
                0,
            )
        ).where(
            Invoice.clinic_id == clinic_id,
            Invoice.status.in_(
                [
                    InvoiceStatus.ISSUED,
                    InvoiceStatus.PARTIALLY_PAID,
                    InvoiceStatus.OVERDUE,
                ]
            ),
            Invoice.amount_paid
            < Invoice.total_amount,
        )
    )

    overdue_invoice_count = (
        db.session.execute(
            db.select(
                db.func.count(
                    Invoice.id,
                )
            ).where(
                Invoice.clinic_id == clinic_id,
                Invoice.status
                == InvoiceStatus.OVERDUE,
            )
        ).scalar_one()
        or 0
    )

    overdue_invoice_amount = decimal_scalar(
        db.select(
            db.func.coalesce(
                db.func.sum(
                    Invoice.total_amount
                    - Invoice.amount_paid,
                ),
                0,
            )
        ).where(
            Invoice.clinic_id == clinic_id,
            Invoice.status
            == InvoiceStatus.OVERDUE,
        )
    )

    successful_payments_today = (
        db.session.execute(
            db.select(
                db.func.count(
                    Payment.id,
                )
            )
            .join(
                Invoice,
                Payment.invoice_id
                == Invoice.id,
            )
            .where(
                Invoice.clinic_id == clinic_id,
                Payment.status
                == PaymentStatus.SUCCESSFUL,
                Payment.created_at >= start,
                Payment.created_at < end,
            )
        ).scalar_one()
        or 0
    )

    successful_payments_today_amount = decimal_scalar(
        db.select(
            db.func.coalesce(
                db.func.sum(
                    Payment.amount,
                ),
                0,
            )
        )
        .join(
            Invoice,
            Payment.invoice_id
            == Invoice.id,
        )
        .where(
            Invoice.clinic_id == clinic_id,
            Payment.status
            == PaymentStatus.SUCCESSFUL,
            Payment.created_at >= start,
            Payment.created_at < end,
        )
    )

    pending_payment_count = (
        db.session.execute(
            db.select(
                db.func.count(
                    Payment.id,
                )
            )
            .join(
                Invoice,
                Payment.invoice_id
                == Invoice.id,
            )
            .where(
                Invoice.clinic_id == clinic_id,
                Payment.status
                == PaymentStatus.PENDING,
            )
        ).scalar_one()
        or 0
    )

    overview = FinanceDashboardOverviewSchema(
        outstanding_invoice_count=int(
            outstanding_invoice_count
        ),
        outstanding_invoice_amount=(
            outstanding_invoice_amount
        ),
        overdue_invoice_count=int(
            overdue_invoice_count
        ),
        overdue_invoice_amount=(
            overdue_invoice_amount
        ),
        successful_payments_today=int(
            successful_payments_today
        ),
        successful_payments_today_amount=(
            successful_payments_today_amount
        ),
        pending_payment_count=int(
            pending_payment_count
        ),
    )

    ai_requests = (
        db.session.execute(
            db.select(
                db.func.count(
                    AILog.id,
                )
            ).where(
                AILog.clinic_id == clinic_id,
                AILog.created_at >= start,
                AILog.created_at < end,
            )
        ).scalar_one()
        or 0
    )

    ai_credits = (
        db.session.execute(
            db.select(
                db.func.coalesce(
                    db.func.sum(
                        AILog.credits_used,
                    ),
                    0,
                )
            ).where(
                AILog.clinic_id == clinic_id,
                AILog.created_at >= start,
                AILog.created_at < end,
            )
        ).scalar_one()
        or 0
    )

    ai_cost = decimal_scalar(
        db.select(
            db.func.coalesce(
                db.func.sum(
                    AILog.estimated_cost,
                ),
                0,
            )
        ).where(
            AILog.clinic_id == clinic_id,
            AILog.created_at >= start,
            AILog.created_at < end,
        )
    )

    ai = FinanceAICostSchema(
        total_ai_requests=int(
            ai_requests
        ),
        total_credits_used=int(
            ai_credits
        ),
        estimated_cost=ai_cost,
    )

    chat = build_chat_summary(
        user_id=actor.id,
        clinic_id=clinic_id,
        period=period,
    )

    metrics = [
        build_metric(
            key="outstanding_balance",
            label="Outstanding balance",
            value=outstanding_invoice_amount,
            unit="currency",
        ),
        build_metric(
            key="overdue_balance",
            label="Overdue balance",
            value=overdue_invoice_amount,
            unit="currency",
        ),
        build_metric(
            key="payments_received",
            label="Successful payments",
            value=successful_payments_today_amount,
            unit="currency",
        ),
        build_metric(
            key="ai_cost",
            label="AI estimated cost",
            value=ai_cost,
            unit="currency",
        ),
        build_metric(
            key="unread_chat_messages",
            label="Unread chat messages",
            value=chat.unread_messages,
            unit="messages",
        ),
    ]

    alerts = []

    if overdue_invoice_count:
        alerts.append(
            DashboardAlertSchema(
                key="overdue_invoices",
                severity="warning",
                title="Overdue invoices",
                count=int(
                    overdue_invoice_count
                ),
                description=(
                    "Invoices currently marked as overdue."
                ),
            )
        )

    if pending_payment_count:
        alerts.append(
            DashboardAlertSchema(
                key="pending_payments",
                severity="warning",
                title="Pending payments",
                count=int(
                    pending_payment_count
                ),
                description=(
                    "Payments still awaiting processing."
                ),
            )
        )

    if chat.priority_messages:
        alerts.append(
            DashboardAlertSchema(
                key="priority_chat_messages",
                severity="critical",
                title="Priority chat messages",
                count=chat.priority_messages,
                description=(
                    "Urgent or STAT messages "
                    "in active conversations."
                ),
            )
        )

    return FinanceDashboardSchema(
        context=build_dashboard_context(
            role=actor.role,
            scope="clinic",
            clinic_id=clinic_id,
        ),
        period=period,
        overview=overview,
        ai=ai,
        chat=chat,
        metrics=metrics,
        alerts=alerts,
        recent_activity=build_recent_activity(
            clinic_id=clinic_id,
        ),
    )