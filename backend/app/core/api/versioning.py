from __future__ import annotations

from app.core.exceptions import DomainError


SUPPORTED_API_VERSIONS = frozenset({"v1"})


class APIVersionNotSupportedError(DomainError):
    status_code = 404
    code = "api_version_not_supported"

    def __init__(self, version: str) -> None:
        self.version = version

        super().__init__(
            f"API version '{version}' is not supported.",
            code=self.code,
        )


def validate_api_version(version: str) -> str:
    normalized_version = version.strip().lower()

    if normalized_version not in SUPPORTED_API_VERSIONS:
        raise APIVersionNotSupportedError(
            normalized_version
        )

    return normalized_version