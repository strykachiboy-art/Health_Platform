from datetime import time
from typing import Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)

from app.core.enums.clinic_enums import ClinicStatus, ClinicType
from app.modules.clinic.services.clinic_operating_modes import normalize_explicit_operating_mode


class ClinicCreateSchema(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=150,
        description="Name of the clinic",
    )

    clinic_type: ClinicType = Field(
        default=ClinicType.GENERAL,
        description="Type of clinic",
    )

    parent_clinic_id: Optional[StrictInt] = Field(
        None,
        gt=0,
        description="ID of the parent clinic when creating a branch",
    )

    is_headquarters: StrictBool = Field(
        default=False,
        description="Whether this clinic is a headquarters",
    )

    address: Optional[str] = Field(
        None,
        max_length=255,
        description="Clinic address",
    )

    city: Optional[str] = Field(
        None,
        max_length=100,
        description="Clinic city",
    )

    country: Optional[str] = Field(
        None,
        max_length=100,
        description="Clinic country",
    )

    phone: Optional[str] = Field(
        None,
        max_length=30,
        description="Clinic phone number",
    )

    email: Optional[str] = Field(
        None,
        max_length=120,
        description="Clinic email address",
    )

    timezone: str = Field(
        default="UTC",
        min_length=1,
        max_length=50,
        description="Clinic timezone",
    )

    opening_time: Optional[time] = Field(
        None,
        description="Clinic opening time",
    )

    closing_time: Optional[time] = Field(
        None,
        description="Clinic closing time",
    )

    # Explicit mode is optional so existing clients retain their old contract.
    operating_mode: Optional[Literal["unspecified", "always_open", "scheduled"]] = None

    @model_validator(mode="after")
    def validate_opening_hours(self):
        if self.operating_mode is not None:
            normalize_explicit_operating_mode(
                self.opening_time, self.closing_time, self.operating_mode,
            )
        elif (
            self.opening_time is not None
            and self.closing_time is not None
            and self.opening_time >= self.closing_time
        ):
            raise ValueError(
                "Opening time must be earlier than closing time"
            )
        return self

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )


class ClinicBranchCreateSchema(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=150,
        description="Name of the branch",
    )

    clinic_type: ClinicType = Field(
        default=ClinicType.GENERAL,
        description="Type of clinic branch",
    )

    address: Optional[str] = Field(
        None,
        max_length=255,
        description="Branch address",
    )

    city: Optional[str] = Field(
        None,
        max_length=100,
        description="Branch city",
    )

    country: Optional[str] = Field(
        None,
        max_length=100,
        description="Branch country",
    )

    phone: Optional[str] = Field(
        None,
        max_length=30,
        description="Branch phone number",
    )

    email: Optional[str] = Field(
        None,
        max_length=120,
        description="Branch email address",
    )

    timezone: str = Field(
        default="UTC",
        min_length=1,
        max_length=50,
        description="Branch timezone",
    )

    opening_time: Optional[time] = Field(
        None,
        description="Branch opening time",
    )

    closing_time: Optional[time] = Field(
        None,
        description="Branch closing time",
    )

    # Explicit mode is optional so existing clients retain their old contract.
    operating_mode: Optional[Literal["unspecified", "always_open", "scheduled"]] = None

    @model_validator(mode="after")
    def validate_opening_hours(self):
        if self.operating_mode is not None:
            normalize_explicit_operating_mode(
                self.opening_time, self.closing_time, self.operating_mode,
            )
        elif (
            self.opening_time is not None
            and self.closing_time is not None
            and self.opening_time >= self.closing_time
        ):
            raise ValueError(
                "Opening time must be earlier than closing time"
            )
        return self

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )


class ClinicUpdateSchema(BaseModel):
    name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=150,
        description="Updated clinic name",
    )

    clinic_type: Optional[ClinicType] = Field(
        None,
        description="Updated clinic type",
    )

    address: Optional[str] = Field(
        None,
        max_length=255,
        description="Updated clinic address",
    )

    city: Optional[str] = Field(
        None,
        max_length=100,
        description="Updated clinic city",
    )

    country: Optional[str] = Field(
        None,
        max_length=100,
        description="Updated clinic country",
    )

    phone: Optional[str] = Field(
        None,
        max_length=30,
        description="Updated clinic phone number",
    )

    email: Optional[str] = Field(
        None,
        max_length=120,
        description="Updated clinic email address",
    )

    timezone: Optional[str] = Field(
        None,
        min_length=1,
        max_length=50,
        description="Updated clinic timezone",
    )

    opening_time: Optional[time] = Field(
        None,
        description="Updated clinic opening time",
    )

    closing_time: Optional[time] = Field(
        None,
        description="Updated clinic closing time",
    )

    # Explicit mode is optional so existing clients retain their old contract.
    operating_mode: Optional[Literal["unspecified", "always_open", "scheduled"]] = None

    @model_validator(mode="after")
    def validate_opening_hours(self):
        if self.operating_mode is not None:
            normalize_explicit_operating_mode(
                self.opening_time, self.closing_time, self.operating_mode,
            )
        elif (
            self.opening_time is not None
            and self.closing_time is not None
            and self.opening_time >= self.closing_time
        ):
            raise ValueError(
                "Opening time must be earlier than closing time"
            )
        return self

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )


class ClinicBranchConfigurationSchema(BaseModel):
    parent_clinic_id: Optional[StrictInt] = Field(
        None,
        gt=0,
        description=(
            "Parent clinic ID. Set to null to detach "
            "the clinic from its current parent."
        ),
    )

    is_headquarters: Optional[StrictBool] = Field(
        None,
        description="Whether this clinic is a headquarters",
    )

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )


class ClinicStatusUpdateSchema(BaseModel):
    status: ClinicStatus = Field(
        ...,
        description="New clinic status",
    )

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )


class ClinicAICreditsUpdateSchema(BaseModel):
    amount: StrictInt = Field(
        ...,
        gt=0,
        description="Number of AI credits to add",
    )

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )