# Phase 1 — Application and Network Boundary Inventory

**Review date:** 2026-10-21
**Branch checkpoint:** `feat/hie-endpoint-allowlist-enforcement`
**Commit checkpoint:** `1e15f2154126933c7dd9e71224ad4761a8b89a12`
**Scope:** application outbound transports, integration endpoints, ingress/proxy boundaries, runtime secrets, connection budgets, storage and backup integration boundaries.

## Purpose and interpretation

This inventory records the implementation and evidence visible for Phase 1. It is not a production certification, a claim that every integration is operational, or an authorization to expose the service publicly.

Statuses are deliberately separate:

- **IMPLEMENTED** — relevant source code or configuration exists.
- **VERIFIED** — a focused test or configuration assertion demonstrates the stated behavior.
- **PRODUCTION-LIKE EXECUTED** — exercised in a representative environment with recorded evidence.
- **PRODUCTION OPERATED** — evidence demonstrates operation in the real production environment.
- **MISSING** — a required capability or proof has not been established.
- **NOT APPLICABLE** — a requirement genuinely does not apply, with rationale.

A source review alone does not establish runtime or production operation.

## Verification evidence recorded for this checkpoint

1. The user-reported focused transport/security regression completed with **274 passed, 0 failed**, in 197.80 seconds. It included Google OAuth/mobile authentication, email provider, AI transport tests, Compose security/encryption contracts and Nginx proxy security tests.
2. `git diff --check` returned exit code `0`.
3. The effective `compose.yaml` rendered to JSON with synthetic values for required environment variables. The rendered configuration asserted:
   - the backend service publishes no host ports;
   - the proxy publishes host port 8080 to container port 80 and 8443 to container port 8443;
   - backup and integration encryption key settings are non-empty for backend, worker and beat.
4. The Compose validation did **not** start containers, connect to PostgreSQL or Redis, perform migrations, contact providers, verify actual deployed secrets or test a production TLS certificate.
5. No live Google, AI, payment, SMTP or HIE/FHIR provider was contacted by this verification. No `PRODUCTION OPERATED` claim is made here.

## Boundary inventory

| Boundary / integration | Current status | Evidence and limitation | Remediation gate |
|---|---|---|---|
| Google OAuth token exchange and user-info requests | VERIFIED by focused tests | HTTPS provider URLs; explicit 10-second request timeout; automatic redirects disabled and redirect responses rejected. | Retain regression coverage; production credentials/network path require separate operational evidence. |
| Google ID-token certificate retrieval | VERIFIED by focused test | A bounded Google Auth request adapter passes a 10-second timeout to the underlying session request; token-verification test expects the bounded adapter. This proves the tested code path, not Google network availability. | Preserve mobile token verification and timeout tests in subsequent regression. |
| OpenAI client transport | VERIFIED by focused tests | Explicit `https://api.openai.com/v1` base URL, configured request timeout and `max_retries=0`. Client policy does not establish host-level egress enforcement or decide whether every clinical-data transfer is permissible. | Review sensitive-data egress, provider terms and deployment-level egress policy before live use. |
| SMTP transport | VERIFIED by focused tests for this slice | Timeout is set; SSL/STARTTLS is required; default certificate-verifying TLS context is supplied. | Restrict/validate configured SMTP destinations and ensure credentials cannot be used to reach prohibited internal services. |
| SMS and push notifications | MISSING / NOT OPERATIONAL | The inspected providers explicitly raise `NotImplementedError` for delivery. Their presence in a factory does not mean they can deliver messages. | Implement a selected provider adapter, credential policy and delivery/failure/idempotency tests before claiming availability. |
| Paystack and Flutterwave | IMPLEMENTED in source; broader operational proof MISSING | Inspected source uses HTTPS, explicit 30-second requests and redirect rejection. Response-size limits were not evident in the inspected call sites. This checkpoint did not execute a provider sandbox. | Add/verify response bounds and transport/failure tests; verify ambiguous payment outcomes and webhook reconciliation before production use. |
| Stripe | MISSING explicit application transport budget | The inspected SDK call sites did not make their timeout/retry budget explicit. SDK configuration/version behavior requires a targeted implementation and tests. | Dedicated payment-transport slice: bounded SDK requests, explicit retry policy, safe unknown-outcome recovery and regression. |
| HIE/FHIR provider integration | NOT OPERATIONAL | Endpoint policy and bounded transport components exist, but the inspected provider registry has no concrete production adapter registered at startup. No provider/API contract selection or live connection verification is established. | Dedicated HIE integration gate: choose contract/vendor, implement and register an adapter, map/authenticate data, test against a sandbox and record end-to-end evidence. Do not describe HIE as live. |
| HIE DNS and connection-time destination assurance | IMPLEMENTED in components; end-to-end assurance MISSING | Policy enforces HTTPS and approved-host restrictions; DNS preflight and transport-level destination/TLS checks exist. DNS preflight alone is not a connection-time security guarantee, and no registered production adapter currently exercises the full path. | Verify the complete enforcement chain through the concrete adapter, including redirects, peer identity, response limits and failure handling. |
| PostgreSQL connection budget | MISSING explicit budget in inspected initialization | The inspected application initialization did not establish a documented, explicit connection-pool budget for the intended deployment. | Set pool sizing, overflow and acquisition timeout to fit worker/web concurrency and database connection capacity; test exhaustion and recovery. |
| Redis connection budget | MISSING explicit network budgets in inspected initialization | Redis, Celery and related facilities use Redis URLs, but explicit connect/read timeout and an end-to-end budget were not established in the inspected initialization. | Configure bounded connection/read behavior where supported; test Redis unavailability, recovery and queue consequences. |
| Ingress and backend exposure | VERIFIED configuration assertion | Effective Compose rendering showed no published host port on the backend and confirmed proxy publication 8080 → 80 and 8443 → 8443. These are configuration assertions, not a firewall scan or proof of runtime network isolation. | Verify host firewall, deployment network rules, TLS listener and actual running service bindings in the target environment. |
| Reverse proxy and load balancing | IMPLEMENTED in part; active balancing MISSING | Nginx has an upstream and passive failure/retry configuration, but the inspected base topology has a single backend. Passive retry is not real multi-backend traffic balancing. The proxy configuration also hides the HSTS response header; the complete HTTPS/HSTS contract needs deployment-level review. | Phase 2: implement and verify multiple-backend balancing, health behavior and graceful draining. Confirm HSTS reaches clients safely without creating harmful localhost development behavior. |
| Database and runtime secrets in Compose | VERIFIED configuration contract | Static tests passed; effective base Compose rendered with synthetic values, without starting containers. The synthetic values were only for interpolation/render checks and are not credentials to use for deployment. | Verify deployment secret provisioning and startup fail-closed behavior with actual non-production test secrets. Validate the phase-specific Compose overlay separately if it is part of the target deployment. |
| Local file storage | IMPLEMENTED in source | Local storage and file-type/path protections exist. No verified remote object-storage adapter was found in the inspected source. | Decide whether local-only storage is an explicit deployment requirement; otherwise implement the chosen remote adapter and test authorization, size limits, outages and recovery. |
| Backups | IMPLEMENTED in part; operational recovery evidence MISSING | Backup encryption configuration and local backup/encryption code exist. This Compose configuration check does not prove a real backup can be created, decrypted or restored. | Run a non-production backup/restore exercise and record key handling, retention, access control and recovery results. Add remote backup storage if required by the deployment policy. |
| Payment webhooks | IMPLEMENTED in source; live-provider verification MISSING | Inbound payment webhook handling includes rate limiting, provider/reference/amount/currency checks and event reservation/deduplication. No generic outbound webhook sender was identified in the reviewed source. | Verify signature/secret policy, duplicate/replay cases and sandbox delivery; keep inbound and outbound webhooks distinct in the inventory. |
| Celery and asynchronous work | IMPLEMENTED in part | Celery broker/queue configuration and durable task settings exist in the inspected source. This verification did not exercise worker loss, queue saturation or production recovery. | Carry queue-age, drain, broker-outage and task idempotency checks into the appropriate resilience/runtime gate. |

## Production-readiness blockers and assigned gates

1. **Phase 2 — load balancing:** actual multiple-backend routing, graceful draining, health handling and retry/idempotency evidence.
2. **Payment transport hardening:** Stripe timeouts/retry behavior and response bounds for providers where required; preserve correct unknown-outcome and reconciliation semantics.
3. **Database/Redis budgets:** explicit pool/concurrency, connection and request-timeout configuration with exhaustion/outage tests.
4. **SMTP egress policy:** TLS is not a destination allowlist. Constrain the configured destination and test denial of internal/prohibited addresses.
5. **HIE/FHIR integration:** no production adapter is registered, so this capability remains not operational until its contract, implementation and environment evidence exist.
6. **Storage/backups:** decide and document local-only versus remote-storage requirements; exercise an actual encrypted backup and restore.
7. **Proxy/TLS contract:** verify HSTS/header handling, real host/network exposure, and actual listener/certificate behavior in the intended deployment.
8. **Operational evidence:** no production operation should be inferred from tests or effective configuration rendering alone.

## Phase transition decision

This inventory provides the Phase 1 boundary record and identifies the next remediation gates. It does **not** declare the application production-ready. Phase 2 may begin with the recorded limitations carried forward explicitly; no unresolved item may be silently relabeled VERIFIED or PRODUCTION OPERATED.

Before this document is committed, inspect it alongside the exact local diff and verify that its statements still match the checked-in source and the intended deployment.
