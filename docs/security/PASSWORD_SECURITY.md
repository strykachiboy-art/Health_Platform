# Password Security and Account Recovery

## Scope

Clinic System Pro exposes three password-security endpoints:

- `POST /api/v1/auth/change-password`
- `POST /api/v1/auth/password-reset/request`
- `POST /api/v1/auth/password-reset/confirm`

The password-reset request endpoint deliberately does not disclose whether an
account exists, is inactive, or is OAuth-only.

## Authenticated password change

Password changes require:

- a valid access JWT;
- the current password;
- a matching new-password confirmation;
- the existing password policy;
- the configured route/IP limit; and
- a Redis-backed per-user failed-current-password window.

A successful password change increments `User.token_version`, invalidating all
previous access and refresh tokens. The caller must sign in again.

## Forgotten-password recovery

The HTTP request path performs no account lookup. After validating the email
shape, configuration, and abuse controls, it queues an asynchronous Celery
task. This keeps account-existence work off the public request timing path.

The worker:

1. resolves the normalized account email;
2. silently stops for missing, inactive, or OAuth-only accounts;
3. creates a cryptographically random reset token;
4. stores only a SHA-256 digest plus user/token-version metadata in Redis;
5. supersedes any older outstanding token for the same user;
6. sends the raw token only through the configured platform email channel.

Reset tokens are short-lived, single-current-token credentials. Confirmation
uses a Redis claim lock to prevent concurrent consumption. The token is bound
to the user's `token_version`; any intervening password/session invalidation
makes it unusable even if Redis cleanup is delayed.

Successful reset changes the password, increments `token_version`, writes an
audit event without credentials or token material, consumes the reset token,
and requires normal login. A best-effort security notice is queued after a
successful change or reset.

## SUPER_ADMIN MFA-aware recovery

Privileged recovery is wired for a future MFA subsystem without pretending MFA
already exists. The policy is controlled by
`AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY`:

- `disabled`: current email-only recovery behavior;
- `if_enrolled`: require the registered recovery factor when the SUPER_ADMIN
  has an MFA recovery binding;
- `required`: fail closed unless the SUPER_ADMIN is enrolled and the recovery
  factor provider is available.

The default is `disabled` until an MFA provider is implemented and registered.
A future MFA module integrates through
`register_super_admin_recovery_factor_provider(app, provider)`. The provider
must expose a stable non-secret enrollment binding and a verifier that can
accept a TOTP value, one-time recovery code, or equivalent recovery credential.

When additional verification is required, the reset token stores only the
provider name and an HMAC digest of the enrollment binding. It never stores an
MFA secret, TOTP seed, recovery code, or raw enrollment identifier. A change to
the MFA provider or enrollment binding invalidates the outstanding reset token,
preventing a privileged account from silently falling back to email-only
recovery after MFA changes.

Invalid recovery-factor attempts are bounded per reset token by
`AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_MAX_FAILURES` (default `5`). Reaching the
limit discards the reset token. Factor failures use the same generic invalid
reset-token response so they do not become a privileged-role or valid-token
oracle.

A safe future rollout is:

1. implement and register the MFA provider while policy remains `disabled`;
2. move to `if_enrolled` while SUPER_ADMIN enrollment is rolled out;
3. move to `required` only after every SUPER_ADMIN has a recovery-capable MFA
   enrollment.

## Abuse controls

The platform uses layered controls:

- Flask-Limiter per-IP ceilings on all three endpoints;
- a per-identity reset-request cooldown;
- per-identity hourly and daily reset-request ceilings;
- a per-user failed-current-password window;
- high-entropy reset tokens;
- confirmation rate limiting;
- one active reset token per user;
- a short-lived confirmation claim lock;
- token-bound failure limits for privileged additional recovery verification.

The reset-request per-identity key is HMAC-derived from the normalized email
using the application secret, so the raw email is not embedded in Redis keys.

## Token handling

Raw reset tokens are never returned by the API, persisted in the database,
stored in Redis, or placed in Celery task arguments. They are generated inside
the delivery worker and exist only long enough to build the recovery message.

The public reset URL is configured server-side and must be HTTPS. The token is
placed in the URL fragment (`#token=...`) rather than the query string, reducing
exposure through ordinary HTTP access logs and referrer headers.

## Platform email configuration

Password recovery uses the existing hardened SMTP transport, including the
operator SMTP host/port allowlist and pinned destination validation.

Required configuration:

- `PASSWORD_RESET_PUBLIC_URL`
- `PLATFORM_EMAIL_HOST`
- `PLATFORM_EMAIL_PORT`
- `PLATFORM_EMAIL_FROM_EMAIL`
- `PLATFORM_EMAIL_USE_TLS`
- `PLATFORM_EMAIL_USE_SSL`

`PLATFORM_EMAIL_USERNAME` and `PLATFORM_EMAIL_PASSWORD` are optional when the
approved SMTP provider does not require authentication.

The configured platform SMTP host and port must also be present in
`SMTP_ALLOWED_HOSTS` and `SMTP_ALLOWED_PORTS`.

## Failure behavior

Redis is security-critical for this feature. Password-change brute-force
protection, reset request throttling, token issuance, and token confirmation
fail closed when Redis is unavailable.

If post-commit reset-token cleanup fails, the password reset still succeeds.
The committed `token_version` increment makes the old reset token unusable and
Redis TTL expiry provides eventual physical cleanup.

No database migration is required because recovery credentials are intentionally
ephemeral and Redis-backed.
