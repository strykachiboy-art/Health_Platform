"""Google Windows installed-app OAuth code flow (no direct desktop ID-token route).

Uses the existing pinned Google HTTPS transport, circuit breaker and patient-only
Google identity workflow. Google credentials and JWT signing keys stay on Flask.
"""
from __future__ import annotations

from collections.abc import Mapping
import re

from flask import current_app
from google.auth import exceptions as google_auth_exceptions
from google.oauth2 import id_token as google_id_token

from app.core.auth.user.schema.user_schema import GoogleUserInfoSchema
from app.core.auth.user.services import google_auth_service as base
from app.core.auth.user.services.google_auth_circuit_breaker import (
    GoogleAuthTransientError,
    call_google_auth_with_circuit,
    is_transient_google_http_status,
)
from app.core.exceptions import ValidationError


_CLIENT_ID = re.compile(r"^[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$")
_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_REDIRECT = re.compile(r"^http://127\.0\.0\.1:([0-9]{2,5})$")


def authenticate_desktop_google_code(*, code: str, code_verifier: str,
                                     redirect_uri: str, nonce: str) -> dict:
    """Consume a one-time Google code, verify signed OIDC claims and issue CSP JWTs."""
    client_id = current_app.config.get("GOOGLE_WINDOWS_CLIENT_ID")
    client_secret = current_app.config.get("GOOGLE_WINDOWS_CLIENT_SECRET")
    if not isinstance(client_id, str) or not _CLIENT_ID.fullmatch(client_id):
        raise ValidationError("Google Windows OAuth client is not configured")
    if not isinstance(client_secret, str) or not client_secret.strip():
        raise ValidationError("Google Windows OAuth exchange credential is not configured")
    if not isinstance(code, str) or not (1 <= len(code) <= 4096):
        raise ValidationError("Invalid desktop Google code handoff")
    if not isinstance(code_verifier, str) or not _VERIFIER.fullmatch(code_verifier):
        raise ValidationError("Invalid desktop Google PKCE verifier")
    if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
        raise ValidationError("Invalid desktop Google nonce")
    match = _REDIRECT.fullmatch(redirect_uri) if isinstance(redirect_uri, str) else None
    if match is None or not (1 <= int(match.group(1)) <= 65535):
        raise ValidationError("Invalid desktop Google loopback redirect")

    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    def _exchange():
        try:
            response = base.requests.post(
                base.GOOGLE_TOKEN_URL,
                data=payload,
                timeout=10,
                allow_redirects=False,
            )
        except base.requests.RequestException as exc:
            raise GoogleAuthTransientError("Unable to communicate with Google") from exc
        status = getattr(response, "status_code", None)
        if isinstance(status, int) and 300 <= status < 400:
            raise ValidationError("Google desktop authorization failed")
        if not response.ok:
            if is_transient_google_http_status(status):
                raise GoogleAuthTransientError("Google authorization temporarily unavailable")
            raise ValidationError("Google desktop authorization failed")
        return response

    response = call_google_auth_with_circuit(
        operation="token_exchange", function=_exchange,
    )
    try:
        data = response.json()
    except ValueError as exc:
        raise ValidationError("Invalid Google desktop token response") from exc
    if not isinstance(data, dict):
        raise ValidationError("Invalid Google desktop token response")
    token = data.get("id_token")
    if not isinstance(token, str) or not (1 <= len(token) <= 8192):
        raise ValidationError("Google desktop ID token was not returned")

    try:
        claims = google_id_token.verify_oauth2_token(
            token, base._BoundedGoogleAuthRequest(), audience=client_id,
        )
    except (ValueError, google_auth_exceptions.GoogleAuthError) as exc:
        raise ValidationError("Invalid Google desktop ID token") from exc
    if not isinstance(claims, Mapping):
        raise ValidationError("Invalid Google desktop ID token")
    if claims.get("nonce") != nonce:
        raise ValidationError("Google desktop nonce mismatch")
    if claims.get("azp") not in (None, client_id):
        raise ValidationError("Invalid Google desktop authorized party")
    subject = claims.get("sub")
    email = claims.get("email")
    if not isinstance(subject, str) or not subject.strip():
        raise ValidationError("Google desktop subject missing")
    if not isinstance(email, str) or not email.strip() or claims.get("email_verified") is not True:
        raise ValidationError("Google desktop email is not verified")
    user = GoogleUserInfoSchema(
        provider_user_id=subject,
        email=email.strip().lower(),
        first_name=claims.get("given_name") if isinstance(claims.get("given_name"), str) else None,
        last_name=claims.get("family_name") if isinstance(claims.get("family_name"), str) else None,
        picture=claims.get("picture") if isinstance(claims.get("picture"), str) else None,
        email_verified=True,
    )
    return base.authenticate_google_user(user)
