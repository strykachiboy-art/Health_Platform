# CHAT-R: local media engine feasibility checkpoint

Status: GREEN for local engine initialization and same-host synthetic media exchange only.

- Canonical parent: `6a80fbf8d888173849e91171c61c94ec4a03af7e` (CHAT-B); Doctor parent `68e1e0178216de28dbd6910971d85440a5c46a4e`.
- str0m 0.26.0, `default-features = false`, `rust-crypto`; retain the tested Cargo lock including transitive dependencies.
- R01: locked Windows dependency fetch and offline compilation; engine initialized and not connected.
- R02: actual loopback UDP transport, both peers connected after ICE/DTLS (26 ms observed); 40 Opus and 8 VP8 payloads received per peer with byte-level comparison. A TX/RX 80/78; B TX/RX 78/80.
- 26 ms is a same-host result, not an internet or production benchmark. Payloads are synthetic, not device capture or codec quality evidence.
- The probe is standalone, not a registered Flask route, production service, call authority or SFU.

## Reproduce in a disposable environment

With the approved Windows MSVC toolchain and cached locked dependencies, from `backend/rust/chat_media_feasibility`:

```powershell
cargo run --offline --locked --target x86_64-pc-windows-msvc --bin csp_chat_r_engine_probe
cargo run --offline --locked --target x86_64-pc-windows-msvc --bin csp_r02_transport
```

These are reproduction commands, not tests rerun during this evidence-only promotion.

## Evidence fingerprints

The original archives are retained outside Git; no raw machine logs, media or binaries are included.

- `CSP_CHAT_R_PREFLIGHT_EVIDENCE.zip`: `9b247590b42daaaa7a16beb831051eed0a79afe98512db51a1d5488bd56513d0`
- `R01_evidence_20261128_151835_1026f2.zip`: `0839db5e65325feea2ec6d8ac2c8b9d702a7a311505c38fa45fab5badc4a4223`
- `R02_evidence_20261128_154220_b3dbca.zip`: `987b6ec7665b1b11b1d82ffcfa366ccb7cdca16edcb4ee98978623e139a3182a`
- Cargo.lock SHA-256: `8a803150b3f728e3a5af153258a74aef84caf773528de146478c089ae1bd6813`
- Transport source SHA-256: `f57bd01038d453e69c00c8c4630d3d1f2383c791453b7c1a8899efa53e4b86af`

## Open gates

CHAT-C/V: durable call/meeting lifecycle and concurrency, staff/tenant/clinic/case authorization, scoped grants, revocation, SFU forwarding, TURN relay, loss/jitter/resource limits and migration review. CHAT-F/E: Flutter native media, capture, permissions, device and staged integrated verification. No production activation or migration authorized. No claim of end-to-end encryption.
