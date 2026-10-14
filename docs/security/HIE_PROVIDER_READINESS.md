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
