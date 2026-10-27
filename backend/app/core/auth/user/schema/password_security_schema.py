from __future__ import annotations

from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    SecretStr,
)


class ChangePasswordSchema(BaseModel):
    current_password: SecretStr = Field(
        min_length=1,
        max_length=128,
    )
    new_password: SecretStr = Field(
        min_length=8,
        max_length=128,
    )
    confirm_password: SecretStr = Field(
        min_length=8,
        max_length=128,
    )


class PasswordResetRequestSchema(BaseModel):
    email: EmailStr


class PasswordResetConfirmSchema(BaseModel):
    token: SecretStr = Field(
        min_length=32,
        max_length=512,
    )
    new_password: SecretStr = Field(
        min_length=8,
        max_length=128,
    )
    confirm_password: SecretStr = Field(
        min_length=8,
        max_length=128,
    )
    recovery_factor: SecretStr | None = Field(
        default=None,
        min_length=1,
        max_length=512,
    )
