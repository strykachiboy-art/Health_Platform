# Backup / Restore Security Drill

## Purpose

This runbook covers the controlled backup and restore workflow for database
recovery, filesystem recovery, authentication invalidation, chat outbox
recovery, and post-restore verification.

The current database backup artifact is encrypted before it is finalized.

## Backup Security Model

Database backup encryption uses:

- `BACKUP_ENCRYPTION_KEY`
- `BACKUP_ENCRYPTION_KEY_VERSION`

The backup encryption key is a separate domain from
`INTEGRATION_ENCRYPTION_KEY`.

The final database backup artifact is ciphertext. Its SHA-256 checksum is a
ciphertext integrity record.

The application does not put the backup encryption key in:

- `metadata.json`
- the database backup artifact
- restore reports

## Backup Key Rotation and Historical Restore

`BACKUP_ENCRYPTION_KEY` and `BACKUP_ENCRYPTION_KEY_VERSION` form one
active key/version pair per application process. The current implementation
does not automatically maintain or try a historical backup-key ring.

Maintain an external, access-controlled inventory mapping each backup key
version to its corresponding secret. The inventory must live outside backup
metadata, database dumps, restore reports, and source control.

The header version can be inspected with the read-only helper:

```powershell
$env:PYTHONPATH="$PWD\testing;$PWD\backend"
python -c "from app.core.backup.backup_encryption import read_backup_artifact_key_version; print(read_backup_artifact_key_version(r'<path-to-database.dump>'))"
```

The header version is an **untrusted lookup hint**, not proof that the
artifact is authentic or that a candidate key is correct. The normal
decryption path must still authenticate the encrypted content successfully.

### Historical restore procedure

1. Verify the backup metadata and the expected ciphertext checksum.
2. Read the artifact's key-version hint and look up that version in the
   approved external recovery-key inventory.
3. Use an isolated recovery process with that historical key and matching
   version configured.
4. Restore to dedicated, empty database and storage targets. Keep the source
   and target databases and storage separate.
5. Require authenticated decryption to complete before `pg_restore` runs.
   Preserve the fail-closed behavior; never fall back to plaintext restore.
6. Verify the restored database and files before considering recovery complete.

Do not change the production application's active key merely to recover an
older backup. Do not run production schedulers or workers in the isolated
recovery environment unless the drill explicitly requires them.

Retire a historical backup key only after every backup that requires it has
expired under the approved retention policy or has been migrated, and the
required restore verification has passed.

Key custody, the external version-to-secret inventory, access controls, and
rotation scheduling remain deployment/infrastructure responsibilities.

## Restore Flow

For an encrypted database artifact:

1. verify the backup artifact exists;
2. verify expected size and SHA-256 checksum;
3. require valid encryption metadata;
4. decrypt into a unique temporary restore directory;
5. require the decrypted database dump to exist and be non-empty;
6. invoke `pg_restore` against the temporary plaintext dump;
7. remove the temporary restore directory regardless of decrypt or restore
   success/failure.

A decrypt failure never falls back to plaintext restore.

Legacy/plaintext backup artifacts are accepted only when their metadata
explicitly identifies them as unencrypted.

## Preconditions

- completed backup with valid metadata
- dedicated PostgreSQL restore target
- dedicated filesystem restore target
- source database and target database must be different
- source storage and target storage must be different
- backup encryption key available through the approved secret-management boundary
- `pg_restore` available
- `psql` available where the drill requires it
- operator authorization
- restore target must not contain existing application data

## Execute

```powershell
$env:RESTORE_DRILL_CONFIRMATION="RESTORE_DRILL_APPROVED"
$env:RESTORE_DRILL_TARGET_DATABASE_URL="<dedicated-postgres-target>"

python -m app.core.backup.restore_drill `
    --backup-path "<backup-directory>" `
    --target-storage-root "<dedicated-storage-target>" `
    --report-path "artifacts/restore-drill-report.json"
```
## Verification Evidence

A successful controlled restore requires:

- `authentication_invalidated: true`
- `outbox_recovery_completed: true`
- `post_restore_verification_completed: true`
- `success: true`

Encrypted-backup verification additionally requires:

- ciphertext checksum verified before decryption
- encrypted metadata accepted
- temporary plaintext restore path used by `pg_restore`
- temporary plaintext restore path absent after restore completion/failure

## Current Verification

Focused verification covers:

- encrypted backup generation
- separate backup key domain
- encrypted database restore
- missing/wrong backup key
- decrypted-artifact cleanup
- backup-key recovery
- corrupted/truncated ciphertext
- interrupted encryption and retry recovery

Combined encryption regression at documentation freeze:

`131 passed`

This is VERIFIED evidence. It is not a claim of production operation.
