from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.core.enums.clinical_order_enums import (
    ClinicalOrderLifecycleStatus,
    ClinicalOrderPriority,
)


_DIET_TYPES = {
    "regular",
    "clear_liquid",
    "full_liquid",
    "soft",
    "diabetic",
    "renal",
    "cardiac",
    "low_sodium",
    "high_protein",
    "npo",
    "enteral",
    "custom",
}

_FEEDING_ROUTES = {
    "oral",
    "enteral",
    "parenteral",
}

_TEXTURES = {
    "regular",
    "soft",
    "minced",
    "pureed",
    "liquid",
}


class _RequestModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class _ResponseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
    )


class DietOrderCreateSchema(_RequestModel):
    admission_id: int = Field(..., gt=0)
    consultation_id: int | None = Field(default=None, gt=0)
    diet_type: str = Field(..., min_length=1, max_length=32)
    custom_description: str | None = Field(
        default=None,
        max_length=1000,
    )
    feeding_route: str | None = Field(
        default="oral",
        max_length=24,
    )
    texture: str | None = Field(
        default=None,
        max_length=24,
    )
    fluid_restriction_ml_per_day: int | None = Field(
        default=None,
        gt=0,
    )
    sodium_restriction_mg_per_day: int | None = Field(
        default=None,
        gt=0,
    )
    calorie_target_kcal_per_day: int | None = Field(
        default=None,
        gt=0,
    )
    instructions: str | None = Field(
        default=None,
        max_length=10000,
    )
    priority: ClinicalOrderPriority = ClinicalOrderPriority.ROUTINE
    start_at: datetime | None = None

    @model_validator(mode="after")
    def validate_diet(self):
        self.diet_type = self.diet_type.strip().lower()
        if self.diet_type not in _DIET_TYPES:
            raise ValueError("Invalid diet_type")

        if self.diet_type == "custom":
            if not self.custom_description:
                raise ValueError(
                    "custom_description is required for custom diet"
                )

        if self.diet_type == "npo":
            if self.feeding_route is not None:
                raise ValueError(
                    "NPO diet cannot have a feeding_route"
                )
            if self.texture is not None:
                raise ValueError(
                    "NPO diet cannot have a texture"
                )
        else:
            if self.feeding_route is None:
                raise ValueError(
                    "feeding_route is required for non-NPO diet"
                )
            self.feeding_route = self.feeding_route.strip().lower()
            if self.feeding_route not in _FEEDING_ROUTES:
                raise ValueError("Invalid feeding_route")

            if self.texture is not None:
                self.texture = self.texture.strip().lower()
                if self.texture not in _TEXTURES:
                    raise ValueError("Invalid texture")

        return self


class DietOrderCompleteSchema(_RequestModel):
    completion_note: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )


class DietOrderCancelSchema(_RequestModel):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )


class DietOrderResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int = Field(..., gt=0)
    admission_id: int = Field(..., gt=0)
    consultation_id: int | None = Field(default=None, gt=0)

    ordered_by_staff_id: int = Field(..., gt=0)
    completed_by_staff_id: int | None = Field(default=None, gt=0)
    cancelled_by_staff_id: int | None = Field(default=None, gt=0)

    diet_type: str
    custom_description: str | None = None
    feeding_route: str | None = None
    texture: str | None = None

    fluid_restriction_ml_per_day: int | None = None
    sodium_restriction_mg_per_day: int | None = None
    calorie_target_kcal_per_day: int | None = None

    instructions: str | None = None

    priority: ClinicalOrderPriority
    status: ClinicalOrderLifecycleStatus

    start_at: datetime | None = None
    ordered_at: datetime

    completion_note: str | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = None

    version: int = Field(..., ge=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DietOrderListResponseSchema(_ResponseModel):
    items: list[DietOrderResponseSchema] = Field(default_factory=list)
    total: int = Field(..., ge=0)
