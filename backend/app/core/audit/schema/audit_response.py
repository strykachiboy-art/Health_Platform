from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictInt,
    StrictStr,
)

from app.core.enums.audit_enums import AuditAction


class AuditLogResponseSchema(BaseModel):
    id: StrictInt
    user_id: StrictInt | None
    clinic_id: StrictInt | None
    patient_id: StrictInt | None

    action: AuditAction

    entity_type: StrictStr
    entity_id: StrictInt

    description: StrictStr | None
    reason: StrictStr | None
    operation_id: StrictStr | None

    old_value: dict[str, Any] | None
    new_value: dict[str, Any] | None

    ip_address: StrictStr | None
    created_at: datetime
    evidence_sha256: StrictStr

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )
