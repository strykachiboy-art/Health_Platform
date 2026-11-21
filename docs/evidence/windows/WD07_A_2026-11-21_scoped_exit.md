# WD07-A â€” Native Windows safety evidence (21 November 2026)

## Scope

- Repository: strykachiboy-art/Health_Platform
- Parent/source: e22c78fcc65d649667fbd59068d68b5255ddb898 (feat/wd06-windows-coordination-resilience)
- Evidence branch: feat/wd07-windows-safety-evidence
- Tests: the original WD07-A **48 pre-existing Flutter test files in five groups**; all ten source, tooling and safety gates reported PASS. These are not real Flask end-to-end tests.
- WD07-A R05 native Windows Release build: exit code **0**; compiled with API_BASE_URL=https://csp-desktop-build.invalid (not for live login).
- WD07-A R06: **22** Release files, **9** Flutter asset files; artifact integrity and final source scope PASS.
- Native executable SHA-256: 4F15533D49E1E795EED5B714DDC464279DFD5D794FCC01C99343F0FF14B28726

## Local evidence fingerprints (retain original files locally)

- Original gate JSON SHA-256: 3822D81BA57B04FF6D9192D9006689214EAA5B95A8E6C1F11629A258D2FDD895
- R05 verbose build log SHA-256: EF94D3C568D1D6C5DE447B438FA76909C75AA840D5879567E2EA4137CFAF67FF
- R06 release-file manifest SHA-256: F3FFA3F2AA94FAFB7164215E1CC2BB5D167A6D5600B5C0BE0A49DA3B4D4A76CA
- R06 reconciled audit SHA-256: 386BAAE96B29E88F4153A9E20AB27B305DD116B06C3BEFE0A77B8AF339315286

## Explicit boundaries

- WD07-A was an **evidence-only** phase: no Flutter application changes. Generated platform registrant line-ending differences are excluded.
- No original test suites were rerun after R05; no source assertions were changed.
- Real New-PC-to-Old-PC Flask authentication, tenant/clinic isolation, Google desktop OAuth, clinical readback, negative security tests, and audit E2E remain **WD07-B / NOT PROVEN**.
- WD05 finance/admin/export writes remain separately scoped; clinical printing remains **DISABLED**.
- SaaS GAP-B security gates 5â€“6 remain **OPEN**; production release **NOT AUTHORIZED**.
- The complete original audit and binaries remain on the New PC and are not uploaded to Git.