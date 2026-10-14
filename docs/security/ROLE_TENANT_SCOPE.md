# Role and Tenant Scope Matrix

## Consultation templates

The consultation-template HTTP API is clinic-scoped. The `ADMIN` role alone
and a caller-supplied `clinic_id` do not grant cross-clinic template access.

| Role or context | Template permission |
| --- | --- |
| `ADMIN` with an assigned clinic | List shared global templates and templates for the assigned clinic; create templates for the assigned clinic |
| `DOCTOR` with an assigned clinic | List shared global templates and templates for the assigned clinic; create only where permitted by the current route allowlist |
| `NURSE` with an assigned clinic | List shared global templates and templates for the assigned clinic; creation remains disallowed by the route allowlist |
| `ADMIN` without a clinic assignment | Template HTTP listing and creation denied |
| `SUPER_ADMIN` | Not included in the current template route role allowlists; a clinic context alone does not grant template access |
| Clinic-management operations | Existing operation-specific cross-clinic permissions remain separate from clinical-template permissions |

## Enforcement

- Routes require an authenticated clinic assignment.
- An optional `clinic_id` query parameter is allowed only when it matches
  the caller's assigned clinic. A mismatch returns `403 Access denied`.
- `get_active_templates()` rejects a missing clinic scope instead of treating
  it as a request for all tenants.
- Scoped listing returns templates with `clinic_id IS NULL` (shared templates)
  and templates owned by the authenticated clinic. It excludes other clinics.
- The current HTTP creation route does not expose global-template creation.

Cross-clinic template administration, if needed, must be introduced as an
explicitly authorized, audited workflow. It must not be inferred from `ADMIN`
or from a request parameter. This decision does not change clinic-management
routes, nor does it claim every other application module follows this matrix.

## Verification boundary

The matrix defines consultation-template policy. Other role/scope inconsistencies
remain separately tracked and require their own source review and tests.
