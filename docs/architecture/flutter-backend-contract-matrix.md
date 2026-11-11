# Clinic System Pro — Flutter ↔ Backend Offline Contract Matrix (P25)

**Source-lock baseline:** Flutter `aef3d314565096b78e0217f8beb2bff798e5e6af` (`feat/flutter-p24h-feedback-complete`); backend `52288f17f2034fe99490cf0378a7ff02c34714c2` (`feat/p24g-backend-final-engagement`). The P25 runner verifies these remote GitHub branch heads before applying any changes. The final P25 commit SHA is recorded in the runner's closing gate output; it cannot be self-referential inside its own Git commit.

**Roadmap:** Master Flutter Roadmap V3 (2026-11-04), P25.S00–S05. **Scope:** policies for existing mobile endpoint families, with executable default-deny decisions. This document does **not** imply all hospital endpoints support offline mutation. Any route not individually verified is `E` (not approved for automatic replay), pending future server-owned contract validation.

## Required classification vocabulary

| Class | Meaning | Execution permission |
|---|---|---|
| A | Read-only request | Online read, no replay queue, no new persistent PHI cache |
| B | Online-only mutation | One explicit attempt, no background retry; uncertain delivery becomes D |
| C | Verified durable idempotent queued mutation | Stable operation ID + same key + same payload fingerprint + owner/clinic scope + encrypted payload; allowlisted worker only |
| D | Reconciliation required after uncertain outcome | Server-authoritative lookup; no duplicate POST; explicit user review if unresolved |
| E | Never automatically replay / fail closed | No automatic queue or replay even if connectivity returns |

**The only currently C-authorized operation is `appointment.create`.** All unknown operation names are `E` at runtime. A per-endpoint `B` operation with lost acknowledgement becomes a D state and remains locked; that does *not* grant a C contract.

## Source-verified endpoint contracts and safety matrix

`clinic` always means backend-resolved effective clinic context; a local UI route, chosen clinic name or user-supplied ID never confers authorization. Response envelopes below are recorded only when explicitly confirmed. Values such as `ID` stand for numeric route parameters, not literal identifiers.

| Capability / operation | Method / API route | Schema, role, patient/clinic scope | Error & uncertain handling | Idempotency / replay | Class / PHI/cache / reconciliation |
|---|---|---|---|---|---|
| Appointment create | `POST /api/v1/appointments/` | Appointment-create payload, `APPOINTMENT_ROLES`, active staff, clinic; service enforces schedule/patient | 409 conflicts and stable-key unresolved states differentiated by existing adapter; unknown must reconcile | `Idempotency-Key` verified against server record; same operation identity and encrypted payload | **C**; P16–18 queue only. No separate clinical data cache |
| Appointment list by patient | `GET /api/v1/appointments/patient/{ID}` | `APPOINTMENT_ROLES`, active staff; patient/clinic authorized server-side | Scope-denied = no data; no local substitute | No mutation | **A**; online, no new PHI cache |
| Appointment list by staff | `GET /api/v1/appointments/staff/{ID}` | `APPOINTMENT_ROLES`, active staff; clinic | Scope-denied = no data | No mutation | **A**; online |
| Appointment reschedule | `POST /api/v1/appointments/{ID}/reschedule` | `APPOINTMENT_ROLES`, active staff; appointment and clinic | Conflict and unknown outcome require fresh appointment read | No verified safe stable-key replay | **E**; no queue |
| Appointment confirm | `POST /api/v1/appointments/{ID}/confirm` | `APPOINTMENT_ROLES`, active staff | Server action; unknown outcome requires status recheck | No verified safe replay | **E** |
| Appointment cancel | `POST /api/v1/appointments/{ID}/cancel` | `APPOINTMENT_ROLES`, active staff | Unknown result, no duplicate | No verified safe replay | **E** |
| Appointment complete | `POST /api/v1/appointments/{ID}/complete` | `APPOINTMENT_ROLES`, active staff | Unknown result, no duplicate | No verified safe replay | **E** |
| Appointment no-show | `POST /api/v1/appointments/{ID}/no-show` | `APPOINTMENT_ROLES`, active staff | Unknown result, no duplicate | No verified safe replay | **E** |
| Disposition of queued appointment create | `POST /api/v1/offline-operations/{operation_id}/disposition` | Server registry policy `appointment.create`, allowed roles admin/doctor/nurse/receptionist; user/clinic/key match | **Only** `confirmed_no_effect` permit local removal; 409 / unknown = retain encrypted queue and reconcile | Not a new mutation replay grant | **D/E**; explicit user action, server receipt first |
| Chat conversations / history | `GET /api/v1/chat/conversations`, `GET /api/v1/chat/conversations/{ID}/messages` | JWT, staff consistency, membership, clinic and patient-case guard | Denial hides PHI; no client trust in socket event alone | No mutation | **A**; in-memory read only |
| Chat unread summary | `GET /api/v1/chat/conversations/{ID}/unread` | JWT, accepted participant, clinic | Server count authoritative; no message-ID arithmetic on client | No mutation | **A**; no cached cross-session summary |
| Chat read receipt | `POST /api/v1/chat/conversations/{ID}/read` | JWT/clinic, accepted participant; monotonic `(created_at, id)` | 409 backwards/undelivered; unknown must re-read own marker | No verified safe offline replay | **B → D** |
| Chat create message | `POST /api/v1/chat/conversations/{ID}/messages` | JWT/clinic/participant, server text/media policy | Lost response not proof of no message; no resend | No verified stable idempotency | **B → D**; no queue |
| Chat edit/delete message | `PATCH/DELETE /api/v1/chat/messages/{ID}` | Sender, accepted participant, clinic | Unknown requires history inspection | No verified replay | **B → D** |
| Chat reactions | `GET/POST /api/v1/chat/messages/{ID}/reactions`, `DELETE /api/v1/chat/messages/{ID}/reactions/{ID}` | JWT, clinic, participant and own reaction enforcement | Unknown mutation must re-read; never duplicate POST | No verified replay | **A** GET, **B → D** writes |
| Chat mentions | `GET/POST /api/v1/chat/messages/{ID}/mentions` | JWT, accepted participant, verified user target | Unknown must re-read; no cross-clinic patient mention by UI | No verified replay | **A** GET, **B → D** writes |
| Feedback create | `POST /api/v1/feedback` | JWT; `FeedbackCreateSchema`, server clinic and identity rules | Failed response is not proof of no submission | No verified stable key | **B → D**; no PHI cache |
| Feedback list/detail | `GET /api/v1/feedback`, `GET /api/v1/feedback/{ID}` | JWT, creator/staff/admin visibility; active clinic | Server pagination and actor access; no optimistic cache | No mutation | **A**; online |
| Feedback management/status | `PATCH /api/v1/feedback/{ID}` | `FEEDBACK_MANAGE_ROLES`, `FeedbackManageSchema`, clinic; status transitions enforced | 409/validation or unknown requires detail reload | No verified stable key | **B → D** |
| Feedback comments | `GET/POST /api/v1/feedback/{ID}/comments` | JWT, feedback visibility and per-actor comment authorization | POST uncertain → re-read comments, no blind repeat | No verified stable key | **A** GET, **B → D** POST |
| Clinical safety read | `GET /api/v1/clinical-safety/alerts`, `GET /api/v1/clinical-safety/alerts/{ID}` | Clinic/staff/admin and alert visibility | Server truth only | No mutation | **A**; in-memory only |
| Clinical safety acknowledge / resolve | `POST /api/v1/clinical-safety/alerts/{ID}/acknowledge`, `/resolve` | Eligible roles and current alert, patient and clinic; justification for override | High-consequence unknown; no blind acknowledgement/resolve retry | No verified stable-key replay | **E → D** |
| eMAR and barcode verification / administration | Module-specific backend routes; **full per-method contract audit required before any replay extension** | Patient, admission, barcode identity, medication rights and order context | Unknown clinical administration requires server-side reconciliation | **Never** queue until a separately approved, tested contract exists | **E**; clinical PHI, no new durable cache |
| Medication reconciliation finalization | Module-specific backend route; phase implementation already online-only | Doctor/staff and clinical context, server final authority | Unknown finalization requires readback, no repeat | No verified replay | **E → D** |
| Pharmacy dispense / lab critical result | Module-specific backend routes; not entered in replay allowlist | Authorized role, patient/clinic and order | Status can only be confirmed by server | No verified replay | **E** |
| Billing payment / ambulance dispatch | Module-specific backend routes, online-only in P23I | Financial/operational role, clinic and patient | Duplicate payment or dispatch unacceptable | No verified stable replay for P25 | **E → D** |
| Emergency Access grant/revoke | Module-specific emergency routes | Separate reviewer/role and patient/clinic/time-bound scope | Unknown permission state: fail closed | No verified replay | **E → D** |
| Notification read actions / device revocation | Server-authenticated module routes; exact method/path remains under P26/P29 audit | User/device owner, clinic and session | Unknown requires authoritative refresh | No queued mutation policy | **B/E → D** |

### Error and status rules

- 401/session invalidation: pause scheduler, clear sensitive screen state; never deliver old-user work.
- 403, tenant or patient mismatch: fail closed. UI hiding is not an authorization claim.
- 409: evaluate *endpoint-specific* server code. A scheduling conflict is not interchangeable with an idempotency-key payload mismatch or unresolved record.
- Timeout, DNS failure, lost response, 5xx: outcome may be unknown; use the existing state machine to classify and reconcile. **Do not** convert uncertain non-idempotent actions into queueable operations.
- Existing queue states: `queued`, `inFlight`, `retryScheduled`, `reconciliationRequired`, `acknowledged`, `rejected`, `userInterventionRequired`, `cancelled`. UI labels must not imply an appointment exists merely from queue acknowledgement.
- Existing queue ownership: `ownerUserId` + `clinicContextId`; encrypted operation body has versioned key material, nonce, tag and authenticated context. There is **no whole-Drift database encryption**.
- Retention and disposal: the store's existing terminal retention applies. `OperationDiscardService` sends a server disposition, validates receipt, then removes a local record only on confirmed no effect. Key cleanup is dependency-aware.

## P25 implementation boundary and evidence

The P25 Pending Work screen wraps the **existing** `AppointmentCreateQueueStateReadModel.loadPage`, `OperationReplayWorker.reconcileAppointmentCreate` and `OperationDiscardService.discard`. The source revalidates role, user, clinic, tokens and session-guard revision, and locks further actions following a possibly unconfirmed result. It displays metadata without operation IDs or sensitive payloads. `manualNoEffectConfirmed` is always `false` in this UI. Reconciliation and discard require separate user confirmation; neither creates an appointment.

The `OfflineContractRegistry` admits `appointment.create` only, independently of the server-bound worker's appointment-only restriction. No additional operation type is written, enqueued, or replay-enabled in P25. Safe broadening requires Old PC backend contract tests and an explicit revised source lock.

**P25.S00–S05 evidence:** Runner enforces exact source SHAs, exact changed files, `flutter pub get --enforce-lockfile`, focused analyzer, queue/scheduler/crypto/reconciliation/widget/route tests, protected source hash checks, exact commit and non-force GitHub HEAD verification. Actual results and final commit are printed by the New PC; no PASS is claimed until that run completes.

**Deferred:** P26 full Flutter suite and analyzer, P27 chaos/restart, P28 Golden Mobile E2E, P29 PHI/device security, P30+ release/pilot checks. Earlier P20 evidence debt remains separate and open until its designated gate.
