# RAD4 Rust DICOM-rs validation proof (Old PC)

**Safe scope:** source-locked tests and one-shot binary only. No source model or
Flask changes, no Orthanc/Redis/PostgreSQL application access, no image uploads,
no patient images, no report signing, no queue, no automatic retries.

- Base GitHub commit: `fbf273d8e1f885a5d2d16ece0964cbdc24cfeedc`
- Rust builder Docker: `rust:1.99.1-slim-bookworm`; image identity recorded during the run
- DICOM-rs: `dicom-object` exactly `0.10.0`; `sha2` exactly `0.10.9`; `serde_json` exactly `1.0.145`
- First run uses network **only to fetch the Rust compiler image and crates**. It has no patient data. Validation runs with `--network none`.
- Rust inputs are synthetic DICOM Secondary Capture 8-bit MONOCHROME2 explicit VR little endian; all other image formats are denied.
- Image size <= 4 MiB; no symlinks, no duplicate tags, no indefinite lengths.
- Requires trusted expected patient ID, accession, Study/Series/SOP UIDs, and SHA-256 digests; never assigns patient/study IDs.
- One marked non-diagnostic 8-bit PGM synthetic preview is created only after successful validation and compared byte-for-byte. RAD4 never makes diagnostic or archive claims.

**Checks:** Six Python policy tests, six Rust unit tests, two Rust positive
synthetic cases, twelve Rust negative cases (patient/clinic mapping, accession,
UIDs, tampering, digest mismatch, oversize, truncation and duplicate tag),
source and GitHub pins, Docker cleanup, exact seven-file commit and non-force push.

**Failure:** The installer is never replayed over an existing RAD4 worktree.
Preserve the exact logs and repair only the existing worktree.
