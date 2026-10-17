# HIE Provider Readiness

## Current status

**External HIE connectivity is an extension point, not a production-ready
integration.**

The repository currently provides the `HIEProvider` protocol, provider
registry, HIE service workflows, tenant and role checks, submission records,
and provider failure classification. It does not contain a concrete
production HIE adapter or register one during application startup.

Tests register deterministic providers directly. Those test registrations
are evidence for service and failure behavior, not evidence of connectivity
to a real health-information-exchange endpoint.

## Runtime safeguard

HIE integration configuration may be created while pending so a future
provider can be configured without claiming it is operational. The service
rejects an activation request unless the named provider has been registered
in the running process. Changing the provider of an active integration is
subject to the same check.

This guard does not implement an adapter, prove that a remote endpoint is
reachable, or establish that an HIE partner has approved the integration.
An existing active record whose provider is unavailable will still fail when
the service tries to resolve that provider; operators must not treat the
stored status as proof of connectivity.

## Required before production activation

1. Identify the actual HIE partner, applicable exchange standard, API
   contract, authentication method, and approved sandbox/production endpoints.
2. Implement a concrete adapter for that contract and register it explicitly
   during application startup.
3. Validate outbound endpoint policy before any HTTP adapter is introduced.
   The configured URL must not become an unrestricted path to internal,
   loopback, link-local, metadata, or otherwise disallowed network targets.
4. Define connection/read timeouts, TLS verification, credential handling,
   response schema validation, and retry versus reconciliation behavior for
   uncertain submission outcomes.
5. Test the actual adapter against an approved sandbox, including successful
   exchanges, malformed responses, authentication failures, timeouts,
   provider errors, recovery, duplicate attempts, audit records, and
   cross-clinic boundaries.
6. Verify deployment configuration and operational monitoring before claiming
   the HIE integration is production-ready.

Until these prerequisites are met and verified, describe the module as the HIE service/registry foundation with test-provider coverage. Do not describe it as a working production HIE connection.


## FHIR interoperability contract gate

Source review date: 2026-10-17. Reviewed repository baseline: `main` at
`f0df46747203cb0e7d42eff506fcb8138249969c`.

### Verified baseline

- The HIE module already supplies integration lifecycle records, a provider
  protocol and process-local registry, service workflows, submission records,
  clinic and role checks, purpose-of-use enumeration, and provider failure
  classification. Extend this module rather than introducing a second,
  disconnected integration subsystem.
- `HIEProvider` currently accepts and returns generic Python dictionaries. This
  establishes a provider extension point; it does not establish FHIR resource
  or profile conformance.
- No concrete production FHIR adapter or startup registration was identified
  in the reviewed source. Existing HIE end-to-end tests register a deterministic
  test provider; that is not external FHIR exchange evidence.
- HIE query authorization verifies an active user, same-clinic context, active
  staff membership and an allowed HIE role. The purpose-of-use field is
  validated and recorded, but this finding does not establish consent
  enforcement or a partner-specific purpose-of-use policy.
- The shared `IntegrationConfig` model stores encrypted credentials and
  non-secret configuration separately. Reuse the established settings and
  credential-encryption boundary; do not store secrets in HIE request data or
  add a competing plaintext credential store.
- `HIESubmission.request_data` and `response_data` are JSON columns and the
  response schema exposes them. HIE service paths persist submitted payloads
  and provider responses. Before recording full FHIR resources, define approved
  data minimization, access, retention, redaction and recovery rules. Do not
  assume a submission row is a safe place for unrestricted clinical resources.
- `HIEIntegration.endpoint_url` is configurable. The readiness requirements
  already prohibit turning it into unrestricted outbound network access. Before
  adding an HTTP client, enforce an explicit endpoint policy that addresses
  approved hosts, HTTPS/TLS, redirects, DNS/IP resolution, loopback/private/
  link-local/metadata targets, timeouts and credential scoping. URL syntax
  validation alone is not a network-target security policy.

### Partner contract decisions - OPEN

| Decision | Required evidence before a partner adapter is implemented |
| --- | --- |
| External system and owning organization | Named hospital, laboratory, insurer or exchange; technical contact and approval to connect |
| FHIR release | Explicit version agreed with the partner; do not infer it from the newest published core specification |
| Implementation guide and profiles | Canonical package/URLs and exact versions, including required must-support fields, terminology bindings and validation rules |
| Workflows and resources | Explicit read/write use cases and resource types; candidate types such as Patient, Encounter, Observation, Condition, MedicationRequest and DiagnosticReport are not automatically in scope |
| Patient identity | Identifier systems, assigning authorities, matching rules, duplicate handling and human-review path; never assume a local database ID is a cross-system identifier |
| Authentication and authorization | Partner-approved OAuth2/SMART, mutual TLS, client credentials, or other documented mechanism; exact scopes/roles and token lifecycle |
| Endpoints and network policy | Approved sandbox and production base URLs, allowed hosts, supported HTTP interactions and outbound-network controls |
| Purpose of use, consent and audit | Applicable policy, required authorization evidence, minimum audit fields, retention and disclosure rules |
| Idempotency and unknown outcomes | Partner contract for create/update identifiers, conditional requests, duplicate submissions, timeouts and reconciliation |
| Sandbox acceptance | Test accounts, sample/test data, expected profile validation output, negative cases and partner sign-off criteria |

No partner, release, implementation guide/profile, authentication contract, endpoint or sandbox has been selected in this source review. Therefore the interoperability contract is **not yet defined**, FHIR compatibility is **not demonstrated**, and production FHIR exchange is **not verified**.

### Standards selection rule

FHIR release and implementation guide must follow the target partner's published
contract. HL7 publishes FHIR R5 as the current core specification, while the
published SMART App Launch 2.2.0 implementation guide is based on FHIR R4. This
is a concrete reason not to select a version solely because it is newest.
Review the target system's `CapabilityStatement`, supported resource interactions,
profiles, search parameters and security requirements before finalizing the
contract.

References:

- HL7 FHIR CapabilityStatement: <https://hl7.org/fhir/capabilitystatement.html>
- HL7 FHIR R4 downloads/specification: <https://hl7.org/fhir/R4/downloads.html>
- HL7 SMART App Launch 2.2.0 (FHIR R4-based): <https://hl7.org/fhir/smart-app-launch/STU2.2/>

### Implementation gates

1. **Contract accepted:** record the named partner, FHIR release, exact profiles,
   resource/workflow scope, identifiers, authentication, endpoint policy,
   purpose-of-use/consent rules and approved sandbox.
2. **HIE security preconditions:** agree how sensitive request/response content is
   minimized or protected in submission history; reuse encrypted integration
   credentials; add outbound endpoint restrictions before any live HTTP calls.
3. **Dedicated adapter inside the existing registry:** map existing domain
   models to the agreed FHIR profiles, validate outgoing and incoming resources,
   negotiate content types and handle pagination/search only as required by the
   contract. Keep the core HIE service responsible for clinic authorization,
   submission lifecycle and audit metadata.
4. **Failure and duplicate handling:** test authentication/authorization failure,
   malformed JSON and FHIR resources, profile violations, rate limits, server
   errors, timeout after server-side success, duplicate requests and recovery.
   Reconcile unknown outcomes; never retry non-idempotent writes blindly.
5. **Approved sandbox verification:** record reproducible successful reads/writes,
   negative cases, audit records, tenant isolation and profile-validation results.
   Test-provider coverage alone cannot close this gate.
6. **Activation and operations:** register the concrete provider explicitly,
   keep unconfigured providers fail-closed, and verify monitored configuration,
   credential rotation, timeout behavior and an operational disable/recovery path.

Until these gates are accepted and evidenced, keep HIE provider readiness as an
extension point. Do not advertise a supported FHIR partner or production-ready
FHIR connectivity.

## US-first FHIR baseline and first workflow (proposed)

Source review date: 2026-10-17. Reviewed repository baseline: `main` at
`962edd21cc76de4a25fdbfab0c7c99c03eeb5b04` (PR #32 merge commit).

This is a proposed US-first evaluation baseline, not a signed partner contract,
implemented adapter, or claim of production compatibility. Keep the existing
partner-contract gate open until the selected vendor's actual capabilities,
security model and test requirements have been recorded.

### Proposed pinned standards baseline

- **FHIR R4 4.0.1** as the initial evaluation version. The official US Core 9.0.0
  publication is based on FHIR R4 4.0.1 and was generated on 2026-05-31.
- **HL7 US Core 9.0.0 (STU 9)** as the initial US profile/terminology evaluation
  package. Pin the exact package and validation output in implementation tests;
  do not silently float to a newer version.
- **SMART App Launch 2.2.0** as the initial OAuth-based app-launch reference
  where applicable. Select the exact authorization pattern, client type, launch
  context and scopes according to the actual user workflow and vendor contract.
- Before each implementation gate, recheck the official published versions and
  the target vendor's declared `CapabilityStatement`, implementation guides,
  API documentation and supported interactions. If the vendor contract requires
  different versions or profiles, record the exception and revise this baseline
  through review; do not force a mismatched profile onto the vendor.

Official references:

- US Core 9.0.0: <https://hl7.org/fhir/us/core/>
- US Core 9.0.0 downloads and package: <https://hl7.org/fhir/us/core/STU9/downloads.html>
- SMART App Launch 2.2.0: <https://hl7.org/fhir/smart-app-launch/STU2.2/>
- Epic on FHIR developer portal: <https://fhir.epic.com/Developer/>
- Epic FHIR documentation: <https://fhir.epic.com/Documentation?docId=fhir>

### First technical test target: Epic on FHIR sandbox (eligibility pending)

Use Epic's public developer sandbox as the first candidate for a bounded
technical evaluation only after access eligibility and app registration are
confirmed using accurate company and contact information. The Epic developer
portal documents a sandbox, client registration and test API documentation; it
also presents a possible region-not-supported registration outcome. Do not
misrepresent company details or bypass a geographic eligibility restriction.
If registration is not available, record the blocker and review another approved
US EHR/vendor sandbox before changing the target.

Sandbox success is not a production customer integration. Epic's documentation
states that customer-specific non-production testing is required for relevant
behaviors not covered by the public sandbox. Do not infer production approval,
customer access, or a commercial relationship from developer-account access.

### First workflow: staff-authorized, read-only pre-visit chart import

The first end-to-end workflow should retrieve and display selected external
clinical information to an authorized clinic user for review before a visit.
Do not write to the external EHR in this first slice. Do not automatically merge
an external patient with an internal patient or silently overwrite local clinical
records. Treat uncertain identity matches as unresolved and route them to an
explicit review path.

Candidate resource scope, subject to the vendor's declared capabilities and the
agreed use case:

- `Patient`: search/read demographics and external identifiers.
- `Encounter`: relevant visit history.
- `Condition`: recorded problems/diagnoses.
- `AllergyIntolerance`: recorded allergies.
- `MedicationRequest`: medication orders where supported and authorized.
- `Observation` and `DiagnosticReport`: selected vital signs and results where
  supported and authorized.

This list is a candidate scope, not a claim that every resource, search parameter,
profile, or interaction is available in the target sandbox or any customer tenant.
Confirm each required read/search operation against the official vendor API
specification and `CapabilityStatement`. Keep any unsupported resource out of the
first release rather than quietly substituting a different representation.

### Security and data-handling constraints

- Use synthetic sandbox patients only. Never place real patient information or
  production credentials in sandbox tests, fixtures, logs, screenshots or PRs.
- Use the authorization flow approved for the specific deployment and actor
  (for example, an appropriate clinician-facing SMART launch or standalone flow).
  Request least-privilege scopes. Use PKCE where the chosen client flow supports
  and requires it. Never embed a confidential client secret in Flutter/mobile code.
- Preserve clinic isolation, staff authorization, purpose-of-use validation and
  auditable access. External IDs must be namespaced by issuer/assigning authority;
  a vendor resource ID is not a global patient identity.
- Reuse the existing encrypted integration-credential mechanism. Do not place
  access tokens or secrets in `HIESubmission.request_data` or `response_data`.
- Before persisting imported FHIR resources, approve data minimization, provenance,
  access, retention/deletion, redaction and recovery behavior. Do not persist full
  raw responses by default. Logs should exclude access tokens and unnecessary PHI.
- Enforce the outbound endpoint policy and TLS/redirect/DNS/IP protections before
  introducing any live HTTP client. Only approved HTTPS endpoints and required
  destinations may receive requests or credentials.

### Acceptance gate for the first adapter slice

1. Confirm eligible developer/sandbox registration and the official test process;
   record the outcome. If unavailable, stop and choose another approved target.
2. Record the actual sandbox base URL, FHIR version, `CapabilityStatement`, exact
   resource interactions, profile package versions, supported search parameters,
   OAuth discovery metadata, scopes, and redirect/client requirements. Keep secrets
   out of documentation.
3. Implement the provider inside the existing HIE registry and services; do not
   create a disconnected interoperability subsystem or bypass current clinic/role
   authorization.
4. Verify the minimal read-only workflow with synthetic patients: successful read
   and search; no-result and ambiguous identity; forbidden scope; expired/invalid
   token; malformed FHIR response; rate limit/server error; timeout and retry;
   pagination where required; audit trail; and cross-clinic isolation.
5. Validate applicable US Core profiles with a pinned validator/package. Preserve
   reproducible test evidence and distinguish tests against local fixtures from
   actual sandbox requests.
6. Keep production activation blocked until a real US healthcare organization
   provides the required approval/access, customer-specific non-production testing
   passes, and privacy, security, contractual and operational gates are accepted.

Until these gates are evidenced, status remains **PROPOSED BASELINE / NOT
IMPLEMENTED / SANDBOX ACCESS NOT VERIFIED / PRODUCTION NOT VERIFIED**. This
section does not claim Epic partnership, EHR vendor certification, US regulatory
compliance, or production FHIR connectivity.

## Outbound HIE endpoint policy primitive (implemented, not wired to HTTP)

Source review date: 2026-10-17. Reviewed repository baseline: `main` at
`fddad418a311dbfc102ee49087264675c42e388d` (PR #34 merge commit).

The new `backend/app/modules/hie/providers/endpoint_policy.py` helper provides
pure, offline validation for a proposed HIE endpoint URL when supplied an
explicit collection of approved DNS hostnames. It requires HTTPS, accepts only
the default HTTPS port, rejects user information, query strings, fragments,
control/whitespace and backslashes, validates DNS hostname syntax, rejects IP
literals and special-use hostnames, requires an exact allowlist match, and
rejects URL path dot-segments. Focused tests cover these rules.

**Scope boundary:** this helper performs no DNS resolution and no HTTP request.
It is not a complete SSRF defense and it is not yet wired into the existing HIE
integration create/update schemas or services. Existing URL parsing is not
runtime enforcement. No endpoint should be contacted using this helper until a
future adapter explicitly calls it with an operator-controlled allowlist.

Before any HTTP client is introduced, the adapter still must constrain DNS
resolution and the actual connected IP (including rebinding risks), enforce
network egress restrictions, disable redirects or revalidate every redirect
target, verify TLS, set connection/read timeouts, scope credentials to the
approved host, and keep secrets and unnecessary PHI out of logs and persisted
submission payloads. The allowlist must come from trusted operator/deployment
configuration, never from untrusted tenant-supplied values.

Status after this slice: **URL POLICY PRIMITIVE IMPLEMENTED / UNIT-VERIFIED /
NOT WIRED TO CONFIGURATION OR HTTP / COMPLETE SSRF PROTECTION NOT IMPLEMENTED**.
The production HIE/FHIR activation gate remains open.


## Outbound destination preflight and redirect target policy

Source baseline: `main` at `dbecc4c1cb1f0a3cc977ed451f74aac89b47dc8a`, after PR #35 was merged.

The new `backend/app/modules/hie/providers/destination_policy.py` adds two pure policy interfaces around the existing URL validator:

- `resolve_hie_endpoint_ips()` validates the configured URL against the explicit hostname allowlist before resolving it. It rejects DNS failures, empty or malformed results, scoped or IPv4-mapped IPv6 addresses, and any answer that is not globally routable or is multicast. All returned addresses must pass; the helper does not silently discard unsafe answers. Tests inject deterministic address results and a fake `getaddrinfo` implementation.
- `validate_hie_redirect_target()` validates a single redirect target against the same explicit allowlist and requires it to remain on the original HTTPS origin. It rejects scheme-relative redirects, cross-origin targets, unsafe URL forms and dot-segment paths.

**Critical limitation: DNS preflight is not connection-time destination enforcement.** The DNS answers may change after validation. An HTTP client that resolves the hostname again can connect to a different IP. This slice does not open sockets, send HTTP requests, wire the policy into HIE integration configuration, or prove protection against DNS rebinding.

Before live HTTP is enabled, the transport must enforce the actual connected destination (or securely pin an address while preserving TLS hostname/SNI and certificate verification), disable automatic redirects or validate every hop under a strict redirect limit, verify TLS, use bounded timeouts, scope credentials, and enforce deployment egress controls. Merely calling the DNS helper before an independently resolving HTTP request is insufficient.

Status after this slice: **DESTINATION DNS PREFLIGHT IMPLEMENTED / REDIRECT TARGET POLICY IMPLEMENTED / OFFLINE TESTS PASSED (421 HIE module tests) / NOT WIRED TO HTTP / CONNECTION-TIME SSRF DEFENSE NOT YET VERIFIED**. Production HIE connectivity remains unimplemented.

## Pinned HTTPS transport primitive

Source baseline: `main` at `1a4b426c735b0b45d516ac23d6e0f708d070c9b2`, after PR #36 was merged.

The new `backend/app/modules/hie/providers/transport.py` defines a strict, read-only HTTPS transport primitive. It accepts an explicit hostname allowlist, validates the endpoint, validates all DNS answers before attempting a connection, and connects directly to a selected numeric IP. The transport checks the connected peer IP and retains the configured hostname for TLS SNI and certificate hostname verification using Python's default verified TLS context.

The transport only supports `GET` and `HEAD`. It enforces bounded connect/read timeouts and a response-body size limit, does not use environment-configured HTTP proxies, blocks caller-controlled transport headers, and rejects HTTP redirects rather than following them. Connection attempts may try another already-validated IP only before the HTTP request is transmitted; the request is not retried after transmission begins.

**Integration boundary:** this primitive is not registered as an `HIEProvider` and is not wired into HIE integration creation/update, credentials, or service workflows. Its tests use deterministic resolver and connection fakes; they do not constitute a live partner or deployment-level TLS/network verification.

The allowlist must be supplied from trusted operator/deployment configuration, never tenant-controlled input. Network egress controls remain necessary. Before enabling a concrete provider, verify the actual runtime environment, TLS trust configuration, connected-peer behavior, redirect requirements, credential scoping, response handling, logging/redaction and partner sandbox behavior. Writes remain deliberately unsupported by this initial transport primitive.

Status: **PINNED HTTPS TRANSPORT IMPLEMENTED / OFFLINE UNIT TESTS PASSED (449 HIE module tests) / READ-ONLY ONLY / NOT WIRED TO HIE PROVIDER REGISTRY / PARTNER OR PRODUCTION CONNECTIVITY NOT VERIFIED**.

## Bounded DNS resolution

The bounded DNS resolution slice uses `dnspython` directly. The resolver sends absolute A and AAAA queries without search-domain expansion and uses a single monotonic deadline across both queries. The default DNS budget is 2 seconds; configured values must be greater than zero and cannot exceed 5 seconds. Each query receives only the remaining budget through dnspython's `lifetime` option.

The timeout budget is passed through `HIEHTTPTransport` to destination resolution. Injected resolver callbacks receive the requested budget and are responsible for honoring it. DNS errors, timeouts and missing usable addresses fail closed. The direct dependency is declared in `backend/requirements.txt`; regenerate `backend/requirements.lock` with the repository's pinned `pip-compile` command before merging.

This bounds the resolver's DNS query lifetime; it does not replace deployment-level DNS availability monitoring, egress restrictions or connection-time destination enforcement. The transport must continue connecting to a validated numeric IP, preserve TLS hostname verification, verify the connected peer, and reject redirects.

Status while this slice is under review: **BOUNDED DNS LOOKUP IMPLEMENTED / REGRESSION PENDING / NOT MERGED**.