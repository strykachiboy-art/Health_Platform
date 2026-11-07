# P23A.S00 - Patient Clinical Workspace Contract Matrix

## Source lock

Canonical repository: strykachiboy-art/Health_Platform

Flutter source branch: feat/flutter-phase22-role-shell

Flutter base commit: a2d007afb15bbea2edf2c013735a2644e61ae309

New Flutter branch: feat/flutter-phase23a-patient-workspace

Backend contract branch: feat/hospital-core-nursing-workflows

Backend contract commit: de4d360e53c85b228bc40c62bc2b53c17cff0df3

Roadmap: CLINIC_SYSTEM_PRO_FLUTTER_MOBILE_MASTER_ROADMAP_V3_2026-11-04

All paths below are relative to `/api/v1`.

## Existing Flutter foundation

Reuse:
- Patient and PatientPage models.
- PatientRemoteDataSource and shared ApiClient.
- Patient directory, detail, registration and edit screens.
- Existing authenticated session loader and navigation guard.
- Existing EmergencyAccessContext.
- Existing patient data and widget tests.

No replacement networking, authentication, routing or storage stack.

## Backend read contract matrix

| Domain | Method and path | Roles and shape |
|---|---|---|
| Patient | GET /patients/{id} | Admin, doctor, nurse, receptionist, pharmacist, lab technician, paramedic, EMT; `success/data` |
| Allergies | GET /patients/{id}/allergies | Same patient-read role family; `success/data` with items/total/page/per_page |
| Allergy detail | GET /patients/{id}/allergies/{allergyId} | Patient-read role family; `success/data` |
| Vitals | GET /patients/{id}/vitals | Admin, doctor, nurse; paginated `success/data` |
| Latest vitals | GET /patients/{id}/vitals/latest | Admin, doctor, nurse; `success/data`, including null when absent |
| Admissions | GET /wards/patients/{id}/admissions | Admin, doctor, nurse, receptionist; raw items/total/page/per_page |
| Current admission | GET /wards/patients/{id}/current-admission | Same ward-view roles; admission object or 404 with no-active-admission message |
| Consultations | GET /consultations/patient/{id} | Admin, doctor, nurse; success/data array/pagination |
| Prescriptions | GET /prescriptions/patients/{id} | Doctor, pharmacist; success/data paginated |
| Clinical alerts | GET /clinical-safety/alerts?patient_id={id} | Authorized alert roles; raw items/total/page/per_page |
| Emergency request | GET /emergency-access/requests/{requestId} | Authorized emergency-read roles; success/data |

Role visibility never overrides backend authorization.

All clinical reads must remain bound to the current
authenticated user, clinic and patient.

## Pagination and filtering

Structured allergies:
- `active_only`, `include_refuted`, `page`, `per_page`.
- `per_page` is 1 to 500.
- Do not use legacy patient.allergies text as an authoritative
  substitute for the structured allergy registry.

Admissions:
- Paginated list of patient admissions.
- Current admission is a separate endpoint.
- A specific no-active-admission 404 is a valid empty state;
  other errors must not be silently treated as empty.

Consultations:
- Optional consultation_type and pagination.
- Response uses `data` array plus separate `pagination`.
- No single unified patient timeline endpoint is established
  by this inspected route family.

Prescriptions:
- Query supports active_only and pagination.
- Prescription read roles are doctor and pharmacist.
- Do not expose prescription data to other roles merely
  because the patient detail endpoint is accessible.
- Emergency access, when applicable, requires a verified
  request and the backend's resource-specific authorization.

Clinical alerts:
- Query includes patient_id, status, rule_id, severity,
  action, source_type and source_id at the pinned backend SHA.
- The route forwards these filters to its service.
- The response is not wrapped in `success/data`.
- A super-admin requires valid clinic context.

Emergency access:
- Current request status is retrieved by request ID.
- No patient-to-emergency-request discovery endpoint is
  established in this inspected route family.
- A local grant candidate is not proof of active server access.
- Expiry, scope, patient, requester and clinic must be checked.

## P23A read safety

- GET-only expansion first.
- Validate patient identity and clinic ownership.
- Revalidate session context after asynchronous responses.
- Discard stale responses after logout or clinic switch.
- Distinguish loading, empty, denied, unavailable and stale.
- Do not persist new PHI snapshots by default.
- Do not automatically replay clinical mutations.
- Never invent patient admission, allergy, prescription,
  alert or emergency-access data.
- Medication and clinical decisions remain backend-owned.

## Implementation order after S00

S01: Typed read-only models and data adapters.

S02: Patient identity and structured allergy surfaces.

S03: Vitals, admissions and consultation history.

S04: Authorized prescriptions and clinical alerts.

S05: Emergency access context and stale-state hardening.

S06: Focused integration, negative tests, evidence and
controlled closure.

These are proposed Phase 23A execution slices, not a
replacement for the master Phase 23A-I workstream roadmap.

## Status and limits

P23A.S00 source audit: PASS.

Backend route inventory: inspected at pinned backend SHA.

Flutter patient foundation: seven source files and six
existing test files verified at the Phase 22 closing HEAD.

New Flutter feature tests: NOT RUN.

Backend tests: NOT RUN.

Full Flutter regression: DEFERRED P26.

Real-device smoke: DEFERRED final roadmap.

No P23A feature implementation or release readiness claimed.

Contract tests and error semantics must be rechecked for
each adapter before implementation.