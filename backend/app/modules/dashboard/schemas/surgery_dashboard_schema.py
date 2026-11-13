"""CE1-S11V2: optional, role-redacted Surgery widget for existing dashboards.

Numbers represent stored documentation/booking state, never operative
completion, medication authority, PACU arrival, discharge or billing clearance.
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class SurgeryDashboardCasePreviewSchema(BaseModel):
    case_id: int = Field(..., gt=0)
    case_status: Literal['planned', 'on_hold', 'cancelled']
    documentation_status: Literal['not_started', 'draft', 'surgeon_signed', 'dual_signed']
    pacu_preparation_status: Literal['not_started', 'draft', 'prepared', 'reviewed']

    model_config = ConfigDict(extra='forbid')


class SurgeryDashboardWidgetSchema(BaseModel):
    view: Literal['super_admin', 'management', 'doctor', 'nurse', 'operations', 'finance', 'patient', 'restricted']
    scope: Literal['system_aggregate', 'clinic', 'assigned_cases', 'operational_aggregate', 'billing_aggregate', 'own_patient', 'restricted']
    visibility: Literal['aggregate_only', 'assigned_only', 'clinic_team', 'own_summary', 'restricted', 'release_required']
    total_cases: int = Field(default=0, ge=0)
    planned: int = Field(default=0, ge=0)
    on_hold: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)
    scheduled_bookings: int = Field(default=0, ge=0)
    sealed_documentation: int = Field(default=0, ge=0)
    reviewed_pacu_preparations: int = Field(default=0, ge=0)
    dual_signed_reports: int = Field(default=0, ge=0)
    pending_signatures: int = Field(default=0, ge=0)
    linked_billing_invoices: int = Field(default=0, ge=0)
    cases: list[SurgeryDashboardCasePreviewSchema] = Field(default_factory=list, max_length=8)
    release_required: bool = False
    period_applies: bool = False
    documentation_only: Literal[True] = True
    operative_completion_authorized: Literal[False] = False
    nursing_transfer_authorized: Literal[False] = False
    medication_administration_authorized: Literal[False] = False
    billing_clearance_authorized: Literal[False] = False

    model_config = ConfigDict(extra='forbid')
