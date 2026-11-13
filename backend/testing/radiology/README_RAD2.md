# RAD2 — Isolated Orthanc PACS / Synthetic DICOMweb verification

**Scope:** The Orthanc image and DICOMweb are validated using only generated Secondary Capture DICOM instances. This is not a production PACS installation, a radiologist diagnostic workstation, image-to-patient clinical association, or a release of radiology reports.

- Pinned backend: `fadf1fcd9db00bf71e0dd52942510e07719febd9` (`feat/backend-rad1-ris-foundation`).
- Pinned Flutter: `36f91cf03f08936f1e449df24cefa60eafda9f59` (`feat/flutter-surgery-c-online-documentation`).
- Image: `orthancteam/orthanc:26.11.0` (record image ID and digest in evidence). Tag is a version pin, but not an immutable digest; production requires digest pinning.
- HTTP: loopback-only ephemeral port + Basic authentication + generated test-only credentials.
- DICOM listener: disabled; DICOMweb plugin enabled. No patient records or main Flask environment.
- Storage: uniquely labeled disposable Docker volume. A test container is restarted to demonstrate persistence, then only that container and volume are removed.
- Positive gates: STOW-RS synthetic upload; QIDO-RS study and patient lookup; WADO-RS pixel and identity verification; post-restart retrieval; authenticated native REST inventory; synthetic DICOM export digest.
- Negative gates: anonymous reads denied, nonexistent study denied, invalid/foreign patient DICOM rejected by scoped identity verifier, non-DICOM rejected, no cross-resource metadata mixing.
- **Open risks:** Orthanc Basic credentials are archive-wide (not clinic-aware). The future Flask-authorized DICOMweb proxy is **mandatory** before exposing real images to staff. Raw Orthanc must never be exposed to Flutter or any internet user.
- **Backup limitations:** An exported synthetic DICOM hash is *not* a full PACS index-and-storage backup/restore. Full snapshot restore and multi-node durability are RAD8 release gates.
- **Licensing:** Review Orthanc DICOMweb plugin AGPL obligations with counsel before commercial redistribution/service deployment.

Run with the outer `Install_RAD2_One_Coordinated_Orthanc.py` installer on the Old PC. The outer installer does source checks, isolated worktree install, nine offline contracts, the real Orthanc gate, cleanup, evidence checks, then exact scope commit/push **only if all gates are green**.

Do not rerun an installer after an error if its RAD2 worktree exists. Send the exact console output for surgical same-worktree recovery.
