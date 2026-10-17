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
