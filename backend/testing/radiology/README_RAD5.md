# RAD5 — synthetic-only OHIF / study-scoped DICOMweb test

Pinned source: RAD4 `74006f0d883d2a6d85cc6f28aeb2d95a6a76b497`. Five previous RAD phases remain protected.

**Scope:** isolated loopback-only Docker Orthanc and OHIF containers; an ephemeral local Python study-scoped reverse proxy; two freshly-generated fabricated DICOM studies; no real clinical user, PACS, browser token, or database. `ohif/app:v3.13.14` and `orthancteam/orthanc:26.11.0` are tag pins, not immutable digests. Record their observed image IDs.

**Only test authorization:** HTTP Basic accounts representing synthetic fixture subjects A/B, with run-generated random passwords known only to the harness. For actual clinical use, bind the viewer to verified JWT clinic/staff and **verified, credentialed study associations** via the real Flask backend. RAD1 `RadiologyStudyAssociation` currently permits only `unverified`/`quarantined`; this gate deliberately cannot authorize it. There is no production DICOMweb route or automatic PACS-to-chart release in this phase.

**Authorization invariants:** every DICOMweb GET has the uniquely scoped study UID; QIDO cannot list unrelated studies; changing series/instance paths cannot cross a study; rejects traversal, encoded paths, query replay, POST/STOW and upstream administration. Same-origin authenticated viewer HTML never receives PACS credentials. Both PACS and OHIF host ports are bound to `127.0.0.1`, not the network. Test credentials must never be printed or committed.

**Testing note:** Browser UI rendering is not automated in RAD5. The HTTP smoke gate validates the genuine pinned OHIF container serves the viewer and local DICOMweb config via the scoped gateway; a real browser/device smoke and regulated diagnostic suitability are later gates. This should not be called a certified diagnostic workstation.

**Deployment blockers:** real radiologist privileges, clinical study verification + DICOM issuer/16-character accession mapping, browser-session CSRF/session policy, secure TLS host, proxy content handling and caching, formal privacy/security review, scanner interoperability, comprehensive PACS backup/restore, and licensing review. This gate is evidence for synthetic auth architecture only.

**Execution policy:** single coordinated Old PC bundle; source locks; fresh isolated worktree; exact six new files; 14+ Python tests; disposable Docker containers and volume; no existing services, application DB, migrations, `git fetch`, reset, clean, stash or force push. Green-only commit/push; on failure preserve worktree; do not replay.
