from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
)


# ============================================================================
# USER
# ============================================================================


class UserRegisterSchema(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=128,
    )
    clinic_id: int | None = Field(
        default=None,
        gt=0,
    )


class UserLoginSchema(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=1,
        max_length=128,
    )


class UserResponseSchema(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    email: EmailStr
    role: str
    clinic_id: int | None
    is_active: bool
    created_at: datetime | None
    last_login_at: datetime | None = None


class AuthTokenResponseSchema(BaseModel):
    access_token: str
    refresh_token: str
    user_id: int
    role: str
    clinic_context_id: int | None = None



class AuthResponseSchema(BaseModel):
    success: bool = True
    data: AuthTokenResponseSchema


class RegisterResponseSchema(BaseModel):
    success: bool = True
    data: UserResponseSchema


# ============================================================================
# GOOGLE OAUTH
# ============================================================================


class GoogleAuthCallbackSchema(BaseModel):
    code: str = Field(
        min_length=1,
        max_length=4096,
    )
    state: str = Field(
        min_length=1,
        max_length=512,
    )


class GoogleMobileAuthSchema(BaseModel):
    id_token: str = Field(
        min_length=1,
        max_length=8192,
    )


class GoogleUserInfoSchema(BaseModel):
    provider_user_id: str = Field(
        min_length=1,
        max_length=255,
    )
    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    picture: str | None = None
    email_verified: bool = False


class GoogleAuthResponseSchema(BaseModel):
    success: bool = True
    data: AuthTokenResponseSchema