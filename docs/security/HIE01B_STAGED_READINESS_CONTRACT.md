# HIE-01-B — staged readiness and external access hold

Source: Clinic Hours integrated candidate `11d7f21ba779ce709f5d6435fbb50994b466bb01`.

This checkpoint is not a real adapter, partner onboarding, tenant consolidation, or release approval. Existing registry, protocol, endpoint/DNS/transport, records, encryption/rotation and route permission definitions remain canonical. No new endpoints, migrations, queues or credential store.

## Existing API behavior during hold

- The existing Admin create/PATCH operations may configure pending integrations and suspend/disable existing records. They cannot make an integration active or edit it while leaving it active. A registered implementation alone is not approval.
- All five existing service-level provider operations are blocked before a provider is instantiated or a submission is created, including existing ACTIVE rows, in a non-testing Flask runtime. The stable domain error is HTTP 422 / `HIE_PARTNER_APPROVAL_REQUIRED` through the existing error handler. A production configuration value cannot lift the hold. Existing synthetic testing profile retains its legacy contract; dedicated staged readiness tests explicitly set the hold. This is not a supported live-provider switch.
- Existing create/get/update integration receipts add `readiness: {provider_registered: bool, external_access_allowed: false, reason_code: "HIE_PARTNER_APPROVAL_REQUIRED"}` during hold. No proof of credential, partner, or live connectivity is implied. Flutter must deny external query and activation if readiness is missing, malformed, or indicates hold. A stale ACTIVE record does not imply operational readiness. Unknown provider may still be configured pending; no dynamic adapter download.
- Existing submission list retains pagination, role and clinic filters, IDs, operation, status/failure class, timestamps and status code. During hold `request_data`, `response_data`, `external_reference`, `error_message` are null (withheld); no historical deletion/rewrite. Historical stored payload retention/redaction and audit policy remain open. Do not show raw payloads in Flutter. No claims about full storage minimization after release.
- Existing JWT role decorators are preserved, and the held runtime rechecks live role against the same allowlist and assigned clinic. Super Admin is not granted HIE access. Existing query staff/purpose checks remain. No clinical privilege expansion.

## Explicit open gates

The existing IntegrationConfig settings provider schema admits payment, email and SMS providers, not a concrete HIE provider. Existing `get_integration_credentials(clinic_id, provider)` is the approved extension point. It is not wired to HIE because no partner contract exists. Its unique clinic/provider key and HIE's potentially multiple per-provider records require a verified endpoint/integration binding before credentials can be provisioned or decrypted. Do not widen allowed names generically or invent credential keys, token exchange, consent mappings or transport authentication. Existing consent guard is local patient/self-access or emergency-specific; it does not authorize external chart lookup. No patient mapping, partner consent or new emergency bypass is authorized. Keep external writes disabled.

Named partner, contract-specific provider and credential binding, role/purpose/consent/identity policy, deployment egress and end-to-end deadline, approved sandbox evidence, SaaS tenant consolidation, privacy/retention and audit review, disable/race handling under live traffic, full regression and final live E2E all remain OPEN. Only staged HIE UI parity may proceed. Existing Clinic Hours feature flags and all unrelated release gates remain unchanged.
