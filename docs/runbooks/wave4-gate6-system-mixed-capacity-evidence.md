# Wave 4 Gate 6 — System-Mixed Capacity Execution Evidence

| Evidence field | Value |
|---|---|
| Evidence date | 2026-10-26 (UTC) |
| Repository | `strykachiboy-art/Health_Platform` |
| Runtime branch | `feat/hie-endpoint-allowlist-enforcement` |
| Workload-runner source lock | `e24bbf303eee64ccc7ac9a7c207f7fae23b5278b` |
| Candidate application image | `clinic-system-candidate:3f72a63` |
| Expected candidate image ID | `sha256:c0e35846273422571d82d6e3b3bdfb5a0a5217d0af7aaebe33b9a8050e81da03` |
| Rung 1 | `PASS — baseline smoke workload` |
| Rung 2 | `PASS — execution verified` |
| Gate 6 capacity claim | **NOT CLAIMED** |

## 1. Purpose and claim boundary

This record captures two accepted, successful runs of the existing `testing/scenarios/system_mixed.py` scenario against the local Wave 4 candidate stack: a low-volume Rung 1 baseline and a five-user, 90-second Rung 2 execution. It records the tested host, source and image identity, workload controls, observed response-time statistics, and pre/postflight checks.

Keep these status labels distinct:

- **Rung 1 baseline verified:** the low-volume workload completed with zero Locust failures and the candidate remained healthy.
- **Rung 2 execution verified:** the bounded five-user workload completed with exit code 0 and zero aggregate Locust failures; its runtime and postflight assertions passed.
- **Gate 6 capacity established:** **not established by these runs.** Rung 2 is a short, low-concurrency execution, not a maximum-capacity, endurance, saturation, or production benchmark.
- **Production operated:** **not claimed.** The environment is a local candidate deployment.

Only the accepted Rung 1 and Rung 2 results are recorded here. This report does not combine historical runs or unrelated artifacts.

## 2. Source and candidate environment

### Source identity

| Item | Verified value |
|---|---|
| Repository | `strykachiboy-art/Health_Platform` |
| Branch | `feat/hie-endpoint-allowlist-enforcement` |
| Runner checkout `HEAD` at execution | `e24bbf303eee64ccc7ac9a7c207f7fae23b5278b` |
| Candidate application image baseline | `3f72a63ac05d709ae16b0c03900e30e1671196fc` |
| Local repository path | `C:\Users\HP\Documents\clinic-system` |
| Worktree precondition | Scope/source lock passed; pre-existing edit preserved |

The runner/scenario source lock and the deployed image baseline are different identities and must not be conflated. The run used the candidate image already deployed as `clinic-system-candidate:3f72a63`; it did not rebuild or mutate the application image.

### Host and deployment

| Item | Value |
|---|---|
| Host OS | Windows 10 Pro |
| CPU | Intel Core i3-4005U, 2 cores / 4 logical processors |
| Physical memory | approximately 7.92 GiB |
| Storage | 128 GB SSD |
| Candidate Compose project prefix | `w4head-3f72a63` |
| Backends | `w4head-3f72a63-backend-1`, `-2`, `-3` |
| Reverse proxy | `w4head-3f72a63-proxy-1` |
| Local HTTPS endpoint | `https://localhost:55466` |
| TLS trust file | `deployment/nginx/certs/localhost.crt` |
| Locust version | `2.46.5` |
| Scenario | `testing/scenarios/system_mixed.py` |
| Workload wait interval | 2–4 seconds |

TLS verification remained enabled. The runner used the repository's local certificate as its CA bundle; it did not disable certificate verification. Preflight and postflight checks called the candidate through the HTTPS proxy.

Three backend containers were expected and all three used the expected immutable image ID. The proxy was running with the expected image identity; a Docker health check was not configured for the proxy, so its status is reported as running rather than healthy. Separate TLS health requests returned HTTP 200.

The isolation stacks `r5lc15430d0c`, `w4pub-0becd73`, and `w4pub-2b-46ec84e` remained stopped. The final check found **zero non-stopped isolation containers** and **zero residual Locust processes**.

## 3. Workload and test controls

The existing system-mixed scenario was run without modifying application code, scenario defaults, or fixture defaults.

| Control | Value |
|---|---|
| Driver | Locust headless |
| Host | `https://localhost:55466` |
| Rung 1 users / spawn rate / duration | `1 / 1 per second / 30 seconds` |
| Rung 2 users / spawn rate / duration | `5 / 1 per second / 90 seconds` |
| Authentication | pre-authenticated admin and clinical bearer tokens for Rung 2 |
| AI workload | disabled (`LOCUST_INCLUDE_AI=false`) |
| Additional access-control workload | disabled (`LOCUST_SYSTEM_MIXED_INCLUDE_ACCESS_CONTROL=false`) |
| TLS verification | enabled; local CA bundle supplied |
| Wait interval | 2–4 seconds |
| Fixture IDs | resolved dynamically from the candidate test fixtures; values not published |
| Clinic settings prerequisite | created using the supported settings-create endpoint and verified by `GET /api/v1/settings/clinic` returning HTTP 200 |
| Token handling | token file remained outside the repository; token values and file contents were not printed or committed |
| Report artifacts | Locust CSV, full-history CSV, HTML report, run logs, resource samples, and container snapshots stored under the local temporary artifact directory |

The scenario's task weights at this checkout are clinical workflow `4`, medication inventory `2`, front desk `2`, admin operations `1`, and communications `1`. AI and additional access-control tasks were intentionally out of scope for these two runs.

The Rung 2 process used the token-file interface supported by the scenario so simulated users did not each perform an interactive login during the measured workload. The environment used a temporary support/import shim outside application source; no shim or other test support code was committed as part of the workload execution.

No credentials, bearer tokens, response bodies, fixture values, or secret-bearing environment exports are included in this evidence.

## 4. Rung 1 — baseline smoke workload

**Result: PASS — baseline only.**

| Measurement | Result |
|---|---:|
| Profile | 1 user, spawn rate 1, 30 seconds |
| Locust requests | 25 |
| Aggregate Locust failures | 0 |
| Requests per second (Locust aggregate) | 1.1186 |
| Mean response time | 600.05 ms |
| Median response time | 170 ms |
| p95 response time | 990 ms |
| p99 response time | 8,500 ms (Locust percentile estimate) |
| Maximum response time | 8,450.73 ms |
| TLS preflight | HTTP 200; 0.566468 s total |
| TLS postflight | HTTP 200; 0.331834 s total |
| Backend health after run | 3/3 healthy |
| Host free memory before | 1,624 MB |
| Host free memory after | 1,429 MB |
| Worktree status | unchanged by execution |

Rung 1's saved `rung1_stats.csv` was subsequently reconciled by a read-only verifier: the aggregate contained exactly 25 requests and zero failures. Its percentile values are reported as Locust produced them; with only 25 requests, tail-percentile estimates are coarse and must not be treated as stable latency guarantees. The accepted run's saved-statistics SHA-256 and complete non-sensitive artifact hashes are recorded in Section 7.

Rung 1 verifies that a single-user baseline could traverse the authenticated/local-TLS candidate path with no Locust failures and that the backend fleet remained healthy. Its sample size and duration are too small to support a capacity or production-performance conclusion.

## 5. Rung 2 — corrected system-mixed execution

**Run ID:** `system-mixed-capacity-final-20261026T170102Z-a295e10a`

**Artifact directory on the test host:** `C:\Users\HP\AppData\Local\Temp\gate6-system-mixed-rung1-20261026T084716Z-2d2fa5dd\gate6-final-rerun-20261026T170054Z`

**Result:** `FINAL_WORKLOAD_RESULT=PASS`

**Claim scope:** `RUNG_2_EXECUTION_VERIFIED_ONLY`

### Aggregate results

| Measurement | Result |
|---|---:|
| Configured profile | 5 users; spawn rate 1; runtime 90 seconds |
| Locust exit code | `0` |
| Low-memory abort | `False` |
| Deadline abort | `False` |
| Aggregate statistics valid | `True` |
| Total requests | **771** |
| Aggregate failures | **0** |
| Requests per second (Locust aggregate) | **9.0011** |
| Failures per second | `0.0` |
| Average response time | **104.92 ms** |
| Median response time | **60 ms** |
| p95 response time | **260 ms** |
| p99 response time | **850 ms** |
| Maximum response time | **2,462.87 ms** |

These values describe this finite, five-user scenario execution only. They are not a universal throughput target, SLO, saturation point, or estimate of production capacity.

### Host resource observations

- Free memory immediately before launch: **1,822 MB**.
- First periodic sample: **1,826 MB**.
- Minimum periodic free-memory sample shown by the run: **1,655 MB**.
- Free memory after the run: **1,734 MB**.
- The configured low-memory abort threshold was **1,100 MB**; the workload did not trigger it.
- No deadline abort was triggered.

This is a host-specific observation from the constrained local machine. It must not be generalized to other host sizes, concurrent workloads, or deployment configurations.

### Postflight candidate checks

| Check | Result |
|---|---|
| Backend 1 | running, healthy, expected tag and immutable image ID, restart delta 0 |
| Backend 2 | running, healthy, expected tag and immutable image ID, restart delta 0 |
| Backend 3 | running, healthy, expected tag and immutable image ID, restart delta 0 |
| Proxy | running, expected tag and immutable image ID, restart delta 0; Docker health check not configured |
| TLS request after workload | HTTP 200; 2.118528 s total |
| Non-stopped isolation containers | `0` |
| Residual Locust processes | `0` |
| Pre-existing repository edit | preserved |
| Token/password/response-body output | none |

The final Docker resource snapshot reported low observed CPU use at the sampling instant and per-backend memory between approximately 178 MiB and 215 MiB. These are point-in-time snapshots after the load, not time-series resource-utilization results.

### Captured proxy logs

The workload runner captured the candidate proxy container's logs for the run window from `2026-10-26T17:01:04Z` to `2026-10-26T17:02:44Z`:

- Log lines captured: **781**.
- Severity-marked log lines in that capture: **0**.
- Resolver/upstream error-pattern matches in that capture: **0**.
- Log-capture command exit: `0`.

This finding is limited to the recorded Docker log query and matching rules used by the runner. It should not be represented as an exhaustive inspection of every possible in-container Nginx log file.

## 6. Execution integrity and artifact handling

The following assertions passed for Rung 2:

```text
SOURCE_LOCK=PASS
WORKTREE_SCOPE=PASS
TOKEN_FILE_SCHEMA=PASS
TOKEN_EXPIRY=SUFFICIENT
THIRD_PARTY_LOCUST_IMPORT=PASS
SCENARIO_IMPORT=PASS
CLINIC_SETTINGS_PREFLIGHT=PASS
LOCUST_EXIT_CODE=0
AGGREGATE_STATS_VALID=True
AGGREGATE_FAILURES=0
LOW_MEMORY_ABORT=False
DEADLINE_ABORT=False
TLS_POSTFLIGHT_EXIT=0
ISOLATION_NONSTOPPED_COUNT=0
RESIDUAL_LOCUST_PROCESS_COUNT=0
PREEXISTING_MODIFICATION_PRESERVED=True
FINAL_WORKLOAD_RESULT=PASS
GATE6_CAPACITY_CLAIM=RUNG_2_EXECUTION_VERIFIED_ONLY
```

Raw artifacts were kept outside the repository under the local temporary directory. The token-bearing file and all credentials remain excluded. Before publication or archival of raw artifacts, inspect them for accidental secrets and confirm the intended access controls. This Markdown document contains summarized, non-secret results only.

The workload execution itself did not change application code, scenario defaults, or the existing repository modification. The evidence-document edit is a separate documentation-only change.

## 7. Accepted artifact reconciliation and SHA-256 inventory

A separate read-only verifier reconciled both accepted runs' saved aggregate CSVs against the recorded request/failure counts and produced a SHA-256 inventory. It printed no CSV bodies, token values, passwords, response bodies, or other secret contents; it modified no files and took no application/container actions.

**Reconciliation summary:** `GATE6_ARTIFACT_RECONCILIATION=PASS`; `CSV_RECONCILIATION=PASS` for both runs; **23 non-sensitive-named files hashed** across the two run directories. Sensitive-named files were omitted from the printed inventory. The list below is therefore the verifier's non-sensitive inventory, not a claim that every local artifact was included.

### Rung 1 accepted artifacts

Run directory: `C:\Users\HP\AppData\Local\Temp\gate6-system-mixed-rung1-20261026T084716Z-2d2fa5dd\gate6-rung1-20261026T150655Z-1bd19039`

Reconciled CSV: `rung1_stats.csv` — 25 requests, 0 failures. **SHA-256:** `513F6BBF92A3D00AB33D95327C05F8AB80C8BC0EAB7748A7973C84434B755D2E`.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `locust.stderr.log` | 6,826 | `020FE54DA7EF82BB8CD851F246FC9DA1C12323E3DD22587AE6AEA2FE3B83B520` |
| `locust.stdout.log` | 0 | `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855` |
| `rung1_exceptions.csv` | 32 | `50E001907CCFF3338F25A2C46417065ABF1A828FEB0BCF1A8E01D039EB762BF2` |
| `rung1_failures.csv` | 53 | `A0F766D126247D27179B2AB30FD2156F5F16130FF67B761A23011A8A32B3934C` |
| `rung1_stats.csv` | 3,562 | `513F6BBF92A3D00AB33D95327C05F8AB80C8BC0EAB7748A7973C84434B755D2E` |
| `rung1_stats_history.csv` | 44,570 | `5D25C1820ACC161691A3D25ECB4B90541D4448EB0FEF17145454BCFC57908412` |

### Rung 2 accepted artifacts

Run directory: `C:\Users\HP\AppData\Local\Temp\gate6-system-mixed-rung1-20261026T084716Z-2d2fa5dd\gate6-final-rerun-20261026T170054Z`

Reconciled CSV: `locust_stats.csv` — 771 requests, 0 failures. **SHA-256:** `7A8B7101E8F00A5922FD78B71606605DBE79102E389B7ECE71C3E132F43F9855`.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `containers_after.json` | 1,553 | `3229BA9ED8966088A881E7FE516EA5F05385B4ED2A0ABD89A51D1FF5B138C7A6` |
| `containers_before.json` | 1,433 | `FA5FB78899AF7A1439E86CC6977E6ED13624D7A63DE309C1A7492E7CB7C6B205` |
| `docker_stats_after.txt` | 297 | `6733DF1E431E40438DC21FDA440345CB7D07582FA40B80D94C61036E3B7AC551` |
| `docker_stats_before.txt` | 297 | `9508369378873A68ABA6D4F97326E0638676891967D523C24946CF19E06E38E7` |
| `docker_stats_samples.txt` | 4,713 | `7CD527C4BC1234DC8C0762803C25FB004C5D8814A89A86FEDC586A4AD65D3147` |
| `execution_result.txt` | 639 | `1B48A44A3046A1CCD5F44F6DCCE688CE190C7592FC3F6BF247C5AA28D6C63DE6` |
| `locust_exceptions.csv` | 32 | `50E001907CCFF3338F25A2C46417065ABF1A828FEB0BCF1A8E01D039EB762BF2` |
| `locust_failures.csv` | 53 | `A0F766D126247D27179B2AB30FD2156F5F16130FF67B761A23011A8A32B3934C` |
| `locust_report.html` | 970,172 | `8184D4E13CBBD5D6B5C2049341129F2D3D5E4AC6A6D8808DF818F8906BE24CB6` |
| `locust_stats.csv` | 4,507 | `7A8B7101E8F00A5922FD78B71606605DBE79102E389B7ECE71C3E132F43F9855` |
| `locust_stats_history.csv` | 356,279 | `A5956F62892E094D790F442B0EAFBDE924F2996FA324DE07E1FCD06E907A95AC` |
| `locust_stderr.log` | 181,430 | `BD979B0F17522DE7AEE51A75C2C3B31D1DBEB3D95E3DE53371FC7BDADE79DCB2` |
| `locust_stdout.log` | 0 | `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855` |
| `metadata.txt` | 956 | `EF92D889E51B95D2CB7D6F9A8A4E50220A7AB262E295F110D6153C0904ACCC5C` |
| `nginx_docker_logs_run_window.log` | 110,905 | `8AD1742935EDEEEBDB1E486FA49CFF23F557BFDD3889BF4798323C81FE120759` |
| `readiness_probe.py` | 2,273 | `511AE8EEAB0ABA164A7CD58C585F2E1D61061022FB1E47A44F35C835A5FDECA8` |
| `resource_samples.csv` | 525 | `3CBC8FA44E1D93FF5BF43E67CC04FAF3939B1F0286F894724334073FD29F041A` |

The inventory records hashes and sizes, not copies of the raw files. The raw directory remains in the local Windows temporary location noted above; it was not uploaded to GitHub. Hashes support integrity checking of the accepted evidence files but do not alone prove that a file is safe to publish.

## 8. Conclusion and limits

**Accepted findings:**

1. Rung 1 baseline passed with 25 requests and zero aggregate failures.
2. Rung 2 completed the configured five-user, 90-second system-mixed run with 771 requests, zero aggregate failures, and exit code 0.
3. Three expected candidate backends remained healthy and had zero restart delta; postflight TLS returned HTTP 200.
4. The host stayed above the workload's low-memory abort threshold; isolation stacks remained stopped; no Locust process remained after cleanup.
5. The captured proxy log query produced no severity-marked lines or resolver/upstream error-pattern matches under the runner's matching rules.

**Not established:**

- Maximum sustainable throughput, saturation behavior, or a capacity ceiling.
- Long-duration soak, spike, ramp-to-saturation, or repeated-run stability.
- AI or additional access-control workload behavior; both were disabled.
- Production deployment, production operation, or production SLO compliance.
- Worker/Beat durability, database failover, autoscaler behavior, multi-host scaling, or disaster recovery from this workload.
- Exhaustive Nginx error-file inspection beyond the stated container-log capture.

**Final status: RUNG 1 BASELINE PASS; RUNG 2 EXECUTION PASS; GATE 6 CAPACITY CLAIM NOT YET ESTABLISHED; PRODUCTION OPERATION NOT CLAIMED.**
