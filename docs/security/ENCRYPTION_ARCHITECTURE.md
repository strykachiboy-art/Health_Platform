# Clinic System Pro v5 — Encryption Architecture

## Purpose

Clinic System Pro uses separate application-level encryption domains for
integration credentials and encrypted database backups.

Encryption status is evidence-based:

- IMPLEMENTED — code exists in the repository.
- VERIFIED — automated or focused verification has passed.
- PRODUCTION-LIKE EXECUTED — behavior has been exercised in a controlled
  production-like environment.
- PRODUCTION OPERATED — behavior has been exercised in actual production.
- INFRASTRUCTURE DEPENDENT — requires deployment secret-management or external
  infrastructure not established by repository tests.
- PLANNED — intentionally not implemented.

No status may be promoted merely because an implementation exists.

## Domain 1 — Integration Credentials

Integration credentials are encrypted at rest in:

`integration_configs.encrypted_credentials`

The non-secret provider configuration remains in:

`integration_configs.configuration`

The integration encryption domain uses:

- `INTEGRATION_ENCRYPTION_KEY`
- `INTEGRATION_ENCRYPTION_KEY_VERSION`
- `INTEGRATION_ENCRYPTION_LEGACY_KEYS`

The implementation is Fernet-based.

`credentials_version` identifies provider credential material/version. It is
not the cryptographic key version.

`encryption_key_version` records the encryption key version associated with
the protected integration credential material.

### Integration key rotation

The verified rotation model is:

1. retain the current active key;
2. retain explicitly configured legacy keys while migration/recovery requires them;
3. read existing ciphertext using its recorded key version;
4. write new ciphertext with the active key/version;
5. verify the resulting record;
6. remove a legacy key only after migration evidence establishes that no protected
   records still require it.

Replacing the active key without a migration/recovery plan is prohibited.

## Domain 2 — Encrypted Database Backups

Database backups use a separate encryption domain:

- `BACKUP_ENCRYPTION_KEY`
- `BACKUP_ENCRYPTION_KEY_VERSION`

The backup key must be distinct from `INTEGRATION_ENCRYPTION_KEY`.

The backup artifact uses chunked AES-256-GCM. The encrypted artifact itself is
the database backup delivered to backup storage.

The database backup flow is:

`pg_dump`
→ plaintext temporary `.partial`
→ AES-256-GCM encryption
→ encrypted staging artifact
→ plaintext temporary artifact removed
→ final encrypted `database.dump`
→ SHA-256 checksum of ciphertext

The checksum verifies artifact integrity. It does not replace authenticated
encryption.

### Backup-key rotation and historical recovery

`BACKUP_ENCRYPTION_KEY` and `BACKUP_ENCRYPTION_KEY_VERSION` identify the
single active backup key/version pair configured in a process. The backup
implementation does not automatically try historical keys.

The deployment must retain historical backup keys in an approved external
secret-management boundary, mapped by key version. The artifact header
version can help operators locate a candidate key, but the header is not
authenticated by the inspection helper and must not be treated as proof of
artifact integrity. Successful AES-GCM authentication during normal
decryption remains mandatory.

For recovery, configure the exact historical key and matching version in an
isolated recovery process and restore only to dedicated database and storage
targets. Decryption failure remains fail-closed; no key guessing or plaintext
fallback is permitted. Keep historical keys until all backups that require
them have expired or have been migrated with verified restores.

The external key inventory, custody, rotation schedule, and production
recovery execution are infrastructure responsibilities.

## Encrypted Restore

For an encrypted database artifact the restore flow is:

`database.dump`
→ verify ciphertext size/checksum
→ require `encrypted: true`
→ decrypt into a unique temporary restore directory
→ run `pg_restore` against the temporary plaintext dump
→ remove the temporary restore directory in `finally`

The encrypted artifact remains the reported backup path.

Decrypt failures are fail-closed. They never fall back to plaintext restore.

A legacy/plaintext backup is accepted only when its metadata explicitly identifies
the artifact as unencrypted. Invalid encryption metadata is rejected.

## Key Separation

These are separate trust domains:

`INTEGRATION_ENCRYPTION_KEY`
≠
`BACKUP_ENCRYPTION_KEY`

Production database credentials, backup repository credentials, integration
provider credentials, and encryption keys are separate operational secrets.

The backup encryption key is not stored in backup metadata or inside the
encrypted backup artifact.

## Recovery Evidence

The backup security sequence has verified:

- encrypted artifact primitive behavior
- backup-key separation
- encrypted database backup pipeline
- encrypted database restore
- backup-key recovery
- crypto failure injection and recovery evidence

The combined encryption regression at the documentation freeze is:

`131 passed`
`0 failed`
`0 errors`

This is VERIFIED evidence, not production-operated evidence.

## Failure-Closed Guarantees

Verified failure cases include:

- missing key
- invalid key
- wrong key
- key-version mismatch
- ciphertext mutation
- ciphertext truncation
- trailing-artifact mutation
- interrupted encryption
- failed database restore

Encryption/decryption failure must not leave a usable plaintext backup
artifact behind.

## Production Boundary

The repository verifies application cryptographic behavior and recovery logic.

Actual secret storage, backup repository access control, key custody, rotation
scheduling, recovery-secret custody, and production operation remain
deployment/infrastructure responsibilities.

## Current Verification Record

Documentation freeze HEAD:

`f36db7ac715e8d772eb36aacc1b63e5df9194b94`

Encryption regression:

`131 passed in 139.23s`