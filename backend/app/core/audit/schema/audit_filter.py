from __future__ import annotations

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
)

from app.core.enums.audit_enums import AuditAction


class AuditLogFilterSchema(BaseModel):
    user_id: StrictInt | None = Field(
        default=None,
        gt=0,
    )

    patient_id: StrictInt | None = Field(
        default=None,
        gt=0,
    )

    action: AuditAction | None = None

    entity_type: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=80,
    )

    entity_id: StrictInt | None = Field(
        default=None,
        gt=0,
    )

    page: StrictInt = Field(
        default=1,
        gt=0,
    )

    per_page: StrictInt = Field(
        default=20,
        gt=0,
        le=100,
    )

    model_config = ConfigDict(
        extra="forbid",
    )

    @field_validator("entity_type")
    @classmethod
    def normalize_entity_type(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()

        if not value:
            raise ValueError(
                "Entity type cannot be empty"
            )

        return value
