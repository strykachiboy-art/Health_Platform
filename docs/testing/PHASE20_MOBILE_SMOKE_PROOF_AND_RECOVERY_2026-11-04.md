# Clinic System Pro — Phase 20 real-device mobile smoke: evidence, fixes & restart runbook

**Date:** 2026-11-04

**Status:** **CORE MOBILE SMOKE GREEN** (Google authentication and principal Emergency Access state transitions); **not** a claim of complete security/E2E coverage.

**Device:** physical TECNO CH6, Android debug build, `com.example.clinic_system_pro`

**Repository:** `strykachiboy-art/Health_Platform` (historical alias `strykachiboy-art/clinic-system-pro`)

**Working branch observed:** `feat/flutter-phase20-emergency-access`; **last known local HEAD prior to smoke:** `245682f4af942a9ea235c9e38cf3a7b9bd776880`. *Git HEAD, upstream parity and worktree status were not reverified when this report was authored.*

**Execution:** mobile user interactions plus read-only checks and a disposable backend test-fixture bootstrap. Tests here are **manual smoke**, not `pytest` or `flutter test` regression.

> **Why this file exists:** prevent recurrence of the TLS/ADB/Docker/PowerShell failures that delayed a successful real-device test. Keep it with the screenshots in `docs/testing/evidence/phase20-mobile-2026-11-04/`. Never use a previous screenshot or test credential as proof for a new build or environment.

## 0. A 30-second restart checklist

1. **Identify the machine.** **New PC:** `C:\Users\USER\Documents\clinic-system`, Android/Flutter/ADB. **Old PC:** `C:\Users\HP\Documents\clinic-system`, candidate Docker backend. They are different environments.
2. Confirm TECNO CH6 is connected via `adb devices`; `adb reverse tcp:55126 tcp:55126` is present **after every ADB reconnect**.
3. Confirm HTTPS bridge and certificate **without TLS bypass**: New PC `https://localhost:55126` → Old PC `10.52.99.29:55124` → candidate proxy HTTPS `127.0.0.1:53780` → candidate backends. Check live portproxies, since addresses/ports are environment-specific.
4. **Critical Flutter debug flag:** `--dart-define=CLINIC_LOCAL_DEV_TLS=true` *and* `--dart-define=CLINIC_LOCAL_DEV_CA_PEM_B64=<base64 of public CA PEM>`; without the first define the custom HTTP client returns `null` and **does not load the CA**. Keep `--dart-define=API_BASE_URL=https://localhost:55126`.
5. Use **one intended staff session at a time**. `doctor` creates request; a **different** authorized reviewer (`admin`) approves/denies/revokes. Do not promote the existing Google PATIENT account.
6. **Don't rerun bootstrap:** disposable clinic `1`, synthetic patient `1`, doctor user `2`, reviewer user `3` were already created in the candidate database. First inspect existence/state if reusing that database. Request IDs `1` and `2` are **not patient IDs**; both refer to patient `1`.
7. **Do not ship the staff smoke harness.** It was a `kDebugMode` + compile-time-flag-controlled temporary patch. Source cleanup, review, commit/push, and a clean-build check are **not yet evidenced** in this report.

## 1. Evidence ledger — precisely what passed

| ID | Test / observation | Result | Basis / qualification |
|---|---|---|---|
| G01 | Native Google account selection and ID-token acquisition | **PASS** | Google account chooser + subsequent valid backend-authenticated session on TECNO. |
| G02 | Google backend token exchange and authenticated screen | **PASS** | Real device screenshot displays signed-in state and `Role: patient`; screenshot `01-...`. |
| G03 | Session persists after app closes and reopens | **PASS (manual)** | User confirmed close/reopen stayed signed in; subsequent ADB force-stop/relaunch smoke confirmation `Y`. No separate screenshot. |
| G04 | Server-confirmed logout | **PASS (app-reported)** | Screenshot `02-...` states “Signed out. The server confirmed logout.” |
| G05 | Post-logout authenticated screen inaccessible | **PASS (manual UI)** | User answered `Y` to actual-device post-logout guard check. |
| G06 | PATIENT role and staff-directory UI restriction | **PASS (UI)** | Signed-in screenshot + manual smoke confirmations. **Not** equivalent to proving backend 403 on every restricted route. |
| E01 | Disposable doctor and reviewer created in backend | **PASS** | Bootstrap output `DISPOSABLE_STAFF_BOOTSTRAP=PASS`, role/clinic IDs below. |
| E02 | Independent doctor/reviewer credential authentication | **PASS** | Strict HTTPS curl New PC: both HTTP `200`, TLS verify `0`, `doctor` user `2`, `admin` user `3`. |
| E03 | Debug APK containing temporary staff harness built and installed | **PASS** | `STAFF_DEBUG_APK_BUILD=PASS`; Flutter `assembleDebug` succeeded; `adb install -r` returned `Success`. |
| E04 | Doctor creates Emergency Access request | **PASS** | TECNO screenshot `03-...`: request `1`, patient `1`, status `requested`, scope `patient:read`. |
| E05 | Separate-reviewer approval / activation of request `1` | **PASS (user-reported)** | User stated approval occurred before revocation; subsequent revoked screenshot shows an expiry. **No captured intermediate ACTIVE screenshot**. |
| E06 | Revocation of request `1` | **PASS (UI/backend status)** | Screenshot `04-...`: `Revoked`, terminal UI, review controls unavailable. |
| E07 | Second distinct request for same patient | **PASS** | Request `2` appears in screenshot `05-...`; same patient `1`, distinct request identifier. |
| E08 | Denial of request `2` with required review notes | **PASS (UI/backend status)** | Screenshot `05-...`: `Denied`, terminal UI, review controls unavailable. |

**Final observed Emergency Access state:** `#1 = revoked`, `#2 = denied`, both for patient ID `1`. Revocation and denial are separate terminal decision paths.

**Out of scope / not independently shown:** actual clinical-resource data read under a live grant; authorization denial for that resource *after* revocation/expiry; persisted audit-log inspection; cross-tenant attempt; forced expiration; server-side 403 assertions for PATIENT; final post-cleanup APK retest; automated regression; Git commit/push. Do not upgrade these to PASS based only on a terminal-state UI.

## 2. Screenshot index

All five JPEGs are copies of the original screenshots supplied during the smoke; names were normalized for discoverability. Original screenshot evidence is visual observation, **not a substitute for network traces or database audit queries**.

| Screenshot | Grounded evidence |
|---|---|
| [01-google-login-signed-in-patient.jpeg](evidence/phase20-mobile-2026-11-04/01-google-login-signed-in-patient.jpeg) | Signed-in patient role and staff access explanation. |
| [02-google-logout-server-confirmed.jpeg](evidence/phase20-mobile-2026-11-04/02-google-logout-server-confirmed.jpeg) | Server-confirmed logout message. |
| [03-emergency-request-1-requested.jpeg](evidence/phase20-mobile-2026-11-04/03-emergency-request-1-requested.jpeg) | Created Emergency Access request `1`, status `requested`. |
| [04-emergency-request-1-revoked.jpeg](evidence/phase20-mobile-2026-11-04/04-emergency-request-1-revoked.jpeg) | Request `1` terminal `Revoked` with expiry and disabled actions. |
| [05-emergency-request-2-denied.jpeg](evidence/phase20-mobile-2026-11-04/05-emergency-request-2-denied.jpeg) | Request `2` terminal `Denied` with disabled actions. |

### File integrity (SHA-256)

| Screenshot | SHA-256 |
|---|---|
| `01-google-login-signed-in-patient.jpeg` | `25d72902be96f15d8e2ec2d576194e6ff9491b8a3fdb0f669c07a5b3a0dabc50` |
| `02-google-logout-server-confirmed.jpeg` | `434f5f31c4f6c82d5b231d1ce0123667ff5cbd8d0af4194302b54a5c5a612086` |
| `03-emergency-request-1-requested.jpeg` | `b62012a09a35fb60ebf5a851f37ae15845a4c1bd2ac5c56e6dd5e08f5d2a5127` |
| `04-emergency-request-1-revoked.jpeg` | `bd15106eaac779af3b47eabbad3fe8d14422b40d88a6c5b468f44b180b1aee0e` |
| `05-emergency-request-2-denied.jpeg` | `ee293442d315328ad587ae02ea265777c81d7a88567ca87b1e5959dbf336105b` |

## 3. Architecture and environment identities — do not mix machines

```text
TECNO CH6 app
  API_BASE_URL=https://localhost:55126
  ADB reverse: device TCP 55126 -> New PC TCP 55126
      |
      v
New PC (C:\Users\USER), Wi-Fi IP 10.52.99.174
  netsh portproxy 127.0.0.1:55126 -> 10.52.99.29:55124
      |
      v
Old PC (C:\Users\HP), Wi-Fi IP 10.52.99.29
  netsh portproxy 10.52.99.29:55124 -> 127.0.0.1:53780
      |
      v
Candidate Docker Nginx HTTPS proxy -> backend replicas
  compose project: clinic-autoscale-live-c1cbbf23
  proxy:     clinic-autoscale-live-c1cbbf23-proxy-1
  backend-1: clinic-autoscale-live-c1cbbf23-backend-1
  backend-2: clinic-autoscale-live-c1cbbf23-backend-2
```

**Caveat:** IPs, portproxies, container names and published ports are a snapshot of this run, not universal configuration. Verify them before the next smoke. Both PCs previously returned `HTTP 200` for `GET /health/live` using the configured custom CA with strict TLS checking.

**CA trust source of truth:** public-only file `C:\Users\USER\Downloads\PHASE20_PUBLIC_CA_ONLY.crt` with SHA-256:

`C1936958C63D5CE7D99195FF9C87D65B1EF9A456BC8CF06C52DFFB26709A5656`

Old PC had a matching public CA under `C:\Users\HP\Downloads\ClinicPhase20TLSBackups\<dated>\PHASE20_PUBLIC_CA_ONLY.crt`. **Do not commit a private key, use `-k`/`--insecure`, override bad-certificate callbacks or disable TLS validation.** The CA hash is an observation from this particular test; reassess validity after certificate rotation.

**Google OAuth:** frontend `GOOGLE_SERVER_CLIENT_ID` matched backend `GOOGLE_CLIENT_ID` for the run: `761971100065-hbn30ntv5e73ni9fdaqlrgps08pthppp.apps.googleusercontent.com`. This is the server client ID, *not* an embedded password.

**Android package/signature (previous debug build evidence):** `com.example.clinic_system_pro`, debug signing SHA-1 `79B678831E5A538BD8E7D1CF07E9A9E609492D3A`. The later V2 APK was successfully built/installed, but its final SHA-256 was **not recorded**. No APK hash should be inferred from the earlier build.

## 4. The real Google Login blocker — resolved

### Symptom

- Native Google account selection succeeded and a Google ID token was acquired.
- On TECNO, `DEBUG AUTH: stage=BACKEND_TOKEN_EXCHANGE type=ApiException kind=timeout http=none cause=TimeoutException`.
- Mobile `ApiClient` default timeout was **10 seconds**. Backend provider transport had separate longer time bounds (up to **30 seconds**) but a provider delay **was not proven** as the root cause.
- Windows curl proved strict-CA `GET /health/live` returned 200, and deliberately invalid `POST /api/v1/auth/google/mobile` returned the *expected* 422 in ~1.4–1.8 s via the same bridge.
- New PC TCP sampling detected a connection during an Android attempt, but this did **not** identify the HTTP request.
- Backend request log counts were misleading: two earlier matches likely represented the invalid-token probes, while a fresh attempt produced zero **completed** route logs. Absence of logs did **not** establish that the request never arrived.

### Root cause and fix

The local debug TLS factory in `mobile/lib/core/networking/local_debug_tls_client.dart` has an **explicit opt-in**:

```dart
const enabled = bool.fromEnvironment(
  'CLINIC_LOCAL_DEV_TLS',
  defaultValue: false,
);
if (!enabled) return null;
```

`main.dart` injected `createLocalDebugTlsClient(backendConfig)` into `ApiClient`; however, the original APK build supplied the PEM Base64 define **but omitted `CLINIC_LOCAL_DEV_TLS=true`**. Thus the custom `SecurityContext` that trusted the local public CA never got used by the request client. The corrected debug build supplied the **missing flag** and real-device Google Login succeeded. This was the decisive build-configuration bug observed in the run.

The client is restricted to **debug mode** and origin `https://localhost:55126`; it creates `SecurityContext(withTrustedRoots: true)..setTrustedCertificatesBytes(caBytes)` and wraps `HttpClient(context: context)` in `IOClient`. It does **not** turn off certificate checking.

### Correct future debug build flags (not a release recipe)

```powershell
$CA = Join-Path $env:USERPROFILE 'Downloads\PHASE20_PUBLIC_CA_ONLY.crt'
$Expected = 'C1936958C63D5CE7D99195FF9C87D65B1EF9A456BC8CF06C52DFFB26709A5656'
if ((Get-FileHash -LiteralPath $CA -Algorithm SHA256).Hash -cne $Expected) { throw 'WRONG_CA' }
$CA64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($CA))
Push-Location (Join-Path $env:USERPROFILE 'Documents\clinic-system\mobile')
try {
    & flutter build apk --debug --no-pub `
        '--dart-define=API_BASE_URL=https://localhost:55126' `
        '--dart-define=CLINIC_LOCAL_DEV_TLS=true' `
        '--dart-define=GOOGLE_SERVER_CLIENT_ID=761971100065-hbn30ntv5e73ni9fdaqlrgps08pthppp.apps.googleusercontent.com' `
        "--dart-define=CLINIC_LOCAL_DEV_CA_PEM_B64=$CA64"
    if ($LASTEXITCODE -ne 0) { throw 'APK_BUILD_FAILED' }
} finally { Pop-Location }
```

For the *temporary staff harness only*, also supply `--dart-define=CLINIC_PHASE20_STAFF_SMOKE=true`; this was used by the successful V2 build. **Do not include that flag in a release build.** The credentials were **not** embedded in the APK.

## 5. Emergency Access smoke, fixture and role model

**Actual disposable backend bootstrap** (`docker exec -i ... python -`, using app factory, `db.session` and real password hashing) succeeded once. Recorded output:

```text
DISPOSABLE_STAFF_BOOTSTRAP=PASS
RUN_TAG=d5d078e1b809
CLINIC_ID=1
PATIENT_ID=1
DOCTOR_USER_ID=2
REVIEWER_USER_ID=3
DOCTOR_EMAIL=phase20.doctor.d5d078e1b809@example.com
REVIEWER_EMAIL=phase20.reviewer.d5d078e1b809@example.com
```

Passwords were generated at bootstrap and used manually; **not stored in this repository evidence pack**, since these are still functioning candidate-backend credentials until explicitly retired. The bootstrap *wrote* disposable clinic/user/staff/patient records; later smoke actions *wrote* emergency requests, reviews, revocation, and denial. The report does **not** claim database mutations were zero overall.

**Backend contracts confirmed by inspection:** `POST /api/v1/emergency-access/requests` (doctor and other eligible active clinical staff), `GET /api/v1/emergency-access/requests/<id>`, and `POST .../<id>/grant`, `/deny`, `/revoke` (eligible reviewer roles). A clinical requester must have an **active linked Staff record** in the right clinic; a reviewer **cannot approve/deny their own request**. An `admin` reviewer and a separate `doctor` requester satisfy the standard workflow without super-admin escalation.

**Exact phone observations:**

- Doctor test login: after a mistyped password was corrected, legitimate login succeeded via the temporary debug form.
- Doctor submitted patient ID `1` with scope `patient:read`. Request screen showed `Request ID: 1`, `Status: requested` — **not active by default**.
- Separate reviewer approved request `1` (reported by user), then revoked it; screenshot showed `Revoked` and a non-reusable terminal state. The exact intermediate active-state screen was not captured.
- Doctor created a *different* request for **the same patient**, yielding request ID `2`. Reviewer entered a required denial note and denied request `2`. Screenshot showed `Denied` and disabled review actions.
- A request ID is not a patient ID. The request review screen opens **the explicitly entered ID**, not the latest request.

**Form values during smoke:** request `1` reason `emergency treatment required`, purpose `emergency clinical care`, scope `patient:read`; request `2` reason `retry access`, purpose `retry purpose`, scope `patient:read` (as displayed on screenshots). Denial requires review notes, and the UI correctly blocked an empty reason.

## 6. The pitfalls that cost us time (and how not to repeat them)

| Failure / distraction | Actual lesson / action |
|---|---|
| **Android timed out while Windows HTTPS worked** | Host curl with correct CA does **not** prove Flutter's `IOClient` uses that CA. First inspect compile-time opt-in and injection path. |
| **Missing `CLINIC_LOCAL_DEV_TLS=true`** | `CLINIC_LOCAL_DEV_CA_PEM_B64` alone was insufficient; factory returned `null`. Use both defines, strict CA hash check. |
| **Backend logs showed zero new Google route matches** | Backend/proxy logs were sparse; completed-log absence isn't a packet capture. Don't infer that nothing reached the backend. |
| **Wrong hypothesis: Google service slowness** | Real ID-token verification has longer provider time bounds than Flutter's 10s timeout, but it wasn't established as the root cause. Don't widen timeouts blindly. |
| **ADB `monkey` PowerShell terminating error** | Some Android utilities write normal diagnostics on stderr. In Windows PowerShell 5.1 capture stderr with `$ErrorActionPreference = 'Continue'` locally and check `$LASTEXITCODE`; or launch explicitly with `adb shell am start -n com.example.clinic_system_pro/.MainActivity`. |
| **`flutter attach` Dart VM service not discovered** | A manually installed debug APK did not expose a discoverable VM service during the attempt. Don't make attach a prerequisite for smoke. |
| **Docker `cp` mount error** | `docker cp` triggered Docker Desktop mount-source error at `/run/desktop/mnt/host/c/Users/HP/AppData/Local/Temp/clinic-autoscale-live-c1cbbf23/runtime/phase9/backups` (`file exists`). Avoid deleting backups or restarting the fleet without evidence; use `docker exec -i` with stdin if container exec works. |
| **`docker exec ... python -c` printed `NameError: PASS`** | Windows PowerShell 5.1 stripped/altered quoted `python -c` text. Use Python code on stdin: `$Python | docker exec -i <container> python -`. |
| **V1 smoke patch failed `PATCH_ANCHOR_NOT_UNIQUE=DISPOSE`** | Automated Dart string surgery is brittle. V2 anchored to unique `super.dispose();` and preserved a pre-patch backup. Verify exact local source before rerunning any patch. |
| **Staff login “connection/configuration” error** | The actual cause was a wrongly entered password. Direct HTTPS login checks for doctor and reviewer were HTTP `200`, TLS `0`; validate credentials before debugging network or rebuilding. |
| **Gradle “restricted method in java.lang.System” warning** | Warning did not fail build; the V2 `assembleDebug` succeeded. Trust exit code and resulting APK rather than warning alone. |
| **Wrong request ID after revocation** | `Patient ID=1` may have many requests. Request `1` was revoked; denial belonged to **new request `2`**. |
| **Assuming temporary test harness is production UI** | V2 modified login screen only for smoke; restore it before final commit, removing **both** staff form and prior `DEBUG AUTH` diagnostics. |

## 7. Exact V2 harness / backups and safe cleanup plan

**Successful local build:** `Phase20_TECNO_Staff_Smoke_Debug_Build_V2.ps1`; `BRANCH_LOCK=PASS`, `CA_HASH_LOCK=PASS`, `DISPOSE_PATCH=PASS`, `DEBUG_ONLY_STAFF_HARNESS_PATCH=PASS`, `STAFF_DEBUG_APK_BUILD=PASS`, `adb install -r = Success`; Flutter Gradle `assembleDebug` elapsed **552.2 s** in that particular run. **This is evidence, not a future duration estimate.**

**Pre-staff-smoke Dart backup** (New PC):

`C:\Users\USER\Downloads\Phase20_PreStaffSmoke_20261104_054346.dart`

Earlier failed V1 also created a backup `Phase20_PreStaffSmoke_20261104_053957.dart` before stopping. The earlier Google diagnostic backup was `C:\Users\USER\AppData\Local\Temp\phase20-google-login-pre-diagnostic-20261104-040947.dart`. Full worktree safety checkpoint: `C:\Users\USER\Downloads\Phase20_Safety_20261104_050352` (**6 files backed up**; precise file identities not recorded in this report).

**Cleanup MUST be reviewed, not assumed complete:**

1. Confirm correct branch, inspect current diff/status (`git status --short`, `git diff -- mobile/lib/features/auth/presentation/google_login_screen.dart`). **No** `git add .`, broad reset/clean/stash, or edits to protected `backend/app/core/web_routes.py`.
2. Restore only the temporary **staff harness** using the exact pre-V2 backup, **after** verifying it matches the desired version and reviewing its diff. The pre-staff backup may still contain the earlier `DEBUG AUTH` code; restoring it alone is **not proof** that all instrumentation is gone.
3. Remove the earlier temporary diagnostic instrumentation via its own known-good pre-diagnostic backup **only if** it preserves all intended role-aware UI changes; compare diffs before copying. Preserve `local_debug_tls_client.dart` plus the `main.dart` HTTP client injection, since missing it caused the original failure. Keep the debug TLS opt-in appropriately restricted.
4. Verify no `CLINIC_PHASE20_STAFF_SMOKE`, `_phase20Staff...`, or `DEBUG AUTH` UI remains in the intended final source. Review `git diff --check` and all exact files before staging.
5. The *installed* test APK currently contains the temporary harness. A final clean rebuild and APK reinstallation are **not evidenced** yet. Do not label production APK ready simply because test APK passed.
6. Review candidate fixture cleanup: test clinic/user/staff/patient plus emergency grants must be removed **only through a deliberate FK-aware, candidate-only cleanup** after recording evidence. Never blindly delete database IDs `1`, `2`, `3` without proving object ownership, because IDs can differ across DBs. No cleanup has been run.
7. Only after review, stage **exact intended files**, commit/push explicitly, and verify remote HEAD. This document does not claim Git commit/push already occurred.

## 8. Handy PowerShell 5.1 diagnostics (read-only unless stated)

**New PC — ADB connection and reverse** (recreates ADB reverse forwarding):

```powershell
$ADB = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
& $ADB devices -l
& $ADB reverse --list
& $ADB reverse tcp:55126 tcp:55126
if ($LASTEXITCODE -ne 0) { throw 'ADB_REVERSE_FAILED' }
```

**New PC — launch app, avoiding `monkey` stderr**:

```powershell
$ADB = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
$prior = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    $result = @(& $ADB shell am start -n 'com.example.clinic_system_pro/.MainActivity' 2>&1 | ForEach-Object { [string]$_ })
    $code = $LASTEXITCODE
} finally { $ErrorActionPreference = $prior }
if ($code -ne 0) { $result | Write-Host; throw "APP_LAUNCH_FAILED=$code" }
Write-Host 'APP_LAUNCH=PASS'
```

**New PC — verify live HTTPS without disabling TLS**:

```powershell
$CA = Join-Path $env:USERPROFILE 'Downloads\PHASE20_PUBLIC_CA_ONLY.crt'
& curl.exe --silent --show-error --max-time 20 --noproxy '*' `
    --cacert $CA --ssl-revoke-best-effort `
    --output NUL --write-out 'HTTP=%{http_code} VERIFY=%{ssl_verify_result}' `
    'https://localhost:55126/health/live'
```

**Old PC — precise container identity, read-only**:

```powershell
$Container = 'clinic-autoscale-live-c1cbbf23-backend-1'
& docker inspect --format '{{.State.Running}}' $Container
$Labels = (& docker inspect --format '{{json .Config.Labels}}' $Container) -join ''
if ($LASTEXITCODE -ne 0) { throw 'DOCKER_INSPECT_FAILED' }
($Labels | ConvertFrom-Json).'com.docker.compose.project'
```

**Avoid relying on the last line's `PASS` if an earlier command failed:** explicitly validate each `$LASTEXITCODE` and all preconditions.

## 9. Final status and handoff

```text
PHASE20_GOOGLE_SIGNIN_REAL_DEVICE=PASS
PHASE20_GOOGLE_SESSION_RESTORE_REAL_DEVICE=PASS
PHASE20_GOOGLE_LOGOUT_SERVER_CONFIRMED=PASS
PHASE20_PATIENT_UI_ROLE_RESTRICTION=PASS
PHASE20_TEST_STAFF_AUTH_BACKEND=PASS
PHASE20_EMERGENCY_REQUEST_1_CREATED=PASS
PHASE20_EMERGENCY_APPROVAL_1=USER_REPORTED_PASS
PHASE20_EMERGENCY_REVOKE_1=PASS
PHASE20_EMERGENCY_REQUEST_2_DENIAL=PASS
PHASE20_EMERGENCY_TERMINAL_UI=PASS
PHASE20_CLINICAL_RESOURCE_ACCESS_AFTER_REVOKE=NOT_TESTED
PHASE20_AUDIT_DB_VERIFICATION=NOT_TESTED
PHASE20_CROSS_TENANT_RUNTIME=NOT_TESTED
PHASE20_REGRESSION_SUITE=NOT_RUN_REQUESTED_SCOPE_SMOKE_ONLY
PHASE20_DEBUG_HARNESS_SOURCE_CLEANUP=PENDING
PHASE20_CANDIDATE_TEST_DATA_CLEANUP=PENDING
PHASE20_FINAL_APK_AFTER_CLEANUP=NOT_VERIFIED
PHASE20_GIT_COMMIT_PUSH=NOT_VERIFIED
PHASE20_CORE_MOBILE_SMOKE=GREEN
PHASE20_PRODUCTION_RELEASE_GATE=NOT_ASSERTED
```

**Bottom line:** The painful Android auth blocker was a missing *debug CA trust opt-in*; Emergency Access request/approval/revocation and denial were exercised using real backend-linked test identities on a real phone. Preserve both the successful evidence **and** the remaining scope gaps. No stale credentials, screenshots, scripts, or guesses should substitute for a new validation run.
