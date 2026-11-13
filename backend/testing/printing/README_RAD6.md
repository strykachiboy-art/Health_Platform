# RAD6: Shared hospital clinical printing - source-authorized copies

Pinned backend: RAD5 `13d16c03a360a8dda7426fce09643c0e2b43e505`.
Pinned Flutter: Surgery C `36f91cf03f08936f1e449df24cefa60eafda9f59`.

## Integrated canonical source adapters (no duplicate records)

- `invoice`: billing `Invoice`, a **copy** with current balance; never a payment receipt.
- `appointment`: appointment `Appointment`, informational slip, no attendance claim.
- `lab_order`: lab `LabOrder`, specimen/order slip **without any verified result**.
- `radiology_draft`: `RadiologyExam` -> `ImagingOrder` -> `RadiologyReport` + exact `RadiologyReportVersion`; **unsigned working note, not for diagnosis**, with SHA-256 integrity check of source text.

Existing administrative `reports` PDF export is preserved and remains the original implementation; RAD6 does not install a parallel reports queue or rewrite it.

## Clinical and operational limitations

- Route `GET /api/v1/printing/<kind>/<record_id>.pdf` is **disabled by default** (`RAD6_PRINTING_ENABLED` default false). Its enablement is an independently approved later release gate; RAD6 does not set it true anywhere.
- Every request needs an active scoped clinic user, active staff, role, record scope, and source-specific owner check for doctor appointment or radiology draft.
- No external printer discovery/spooling, cross-clinic exports, arbitrary document selection, user-submitted HTML, printing of lab results, final radiology signatures, prescriptions, eMAR, discharge attestations, consent, or patient chart release.
- No migration, no new queue/storage system, no automatic offline replay, no PDF persistence; PDF is generated in memory, one request at a time.
- A PDF is a copy of a source snapshot, not an automatically legal/certified/signed record. Patient identifiers appear only in authorized copies. Do not share test evidence from real patient data.
- This is the **unified foundation**. Remaining clinical adapters (prescription, pharmacy dispense, discharge, chart, nursing, surgical consent, etc.) need separate documented role/status/approval checks before exposure. Do not imply they are integrated or final in RAD6.

## Testing

- 29 focused synthetic policy and renderer tests, including source-adapter contract checks.
- Generated synthetic PDFs are checked for watermark, source digest, no embedded Javascript or remote links, no signed claims; visual sample must be inspected.
- One coordinated Old PC bundle: hash and GitHub source preflight, protected-worktree snapshot, exact file install, focused tests and synthetic PDF rendering evidence, then green-only exact-scope commit/non-force push.
- The admin report PDF service remains unmodified; full backend integration and role regression must precede production enablement.
