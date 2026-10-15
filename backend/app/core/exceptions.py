from __future__ import annotations


class DomainError(Exception):
    status_code = 400

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code


class NotFoundError(DomainError):
    status_code = 404


class ConflictError(DomainError):
    status_code = 409


class ValidationError(DomainError):
    status_code = 422


class InsufficientCreditsError(ConflictError):
    status_code = 402