# Pre-Gate 9 Backend Failover Fix Evidence

## Status

```text
PRE_GATE9_BACKEND_FAILOVER_FIX=GREEN
FAILOVER_CORRECTNESS=GREEN
USER_VISIBLE_5XX=0
RUNTIME_FIX_PROVEN=YES
PERSISTENT_SOURCE_FIX=PENDING
FAILOVER_LATENCY_OPTIMIZATION=OPTIONAL_NEXT
```

## Scope

This evidence records the controlled pre-Gate 9 backend failover drill performed against the isolated Gate 8 candidate only.

```text
TARGET=w8head-7e0e6cc
PRIMARY_FLEET_TOUCHED=NO
GATE7_FLEET_TOUCHED=NO
APPLICATION_SOURCE_CHANGED=NO
PRIMARY_ROUTING_CHANGED=NO
```

The objective was to prove that when either backend becomes unavailable, users continue to receive valid responses from the surviving backend instead of receiving a user-visible 5xx error.

This is internal load-balancer failover. Users continue to use the same proxy endpoint; they are not redirected to a different public URL.

## Source Lock

```text
BRANCH=feat/smtp-email-circuit-breaker-integration
HEAD=07a85e83b774f2697d142c782605d05cd3a7517c
PARENT=7e0e6ccd2744c750c21c7da7cc2b980c9da4128c
PROTECTED_WEB_ROUTES_SHA256=C483E7D6FF6B0216224E1B6713A54E727211D86B5B7D9093B9E4F27CE5BA7352
SOURCE_LOCK=PASS
PROTECTED_WEB_ROUTES=PASS
```

The HEAD commit was the Gate 8 documentation-only commit. The running application source therefore remained aligned with the Gate 8 application source.

## Candidate Precheck

All required candidate services were running before the fix proof:

```text
proxy=running
backend-1=running, healthy
backend-2=running, healthy
postgres=running, healthy
redis=running, healthy
```

Baseline proxy behavior:

```text
BASELINE_HEALTH=200
BASELINE_READY=200
BASELINE_APP=401
BASELINE=PASS
```

The `401` response from `/api/v1/profile/me` is expected for an unauthenticated request and was used as the stable application-route response contract.

## Pre-Fix Failure Discovery

Before the fix, the candidate upstream configuration contained:

```nginx
server w8head-7e0e6cc-backend-1:5000 resolve max_fails=2 fail_timeout=10s;
server w8head-7e0e6cc-backend-2:5000 resolve max_fails=2 fail_timeout=10s;
```

The proxy retry policy was already present:

```nginx
proxy_next_upstream error timeout http_502 http_503 http_504;
proxy_next_upstream_tries 2;

proxy_connect_timeout 5s;
proxy_read_timeout 75s;
proxy_send_timeout 75s;
```

During the initial backend-1 failure drill, one application request returned:

```text
APP=504
```

The proxy log showed the failed request spent approximately five seconds attempting the unavailable backend and then returned a user-visible `504`.

A separate earlier request demonstrated that same-request retry itself was functional:

```text
dead backend -> timeout -> healthy backend -> 401
```

Therefore the issue was not absence of `proxy_next_upstream`. The defect was the passive failure window: with `max_fails=2`, the unavailable backend could remain eligible long enough to receive another request before being removed from rotation.

Pre-fix classification:

```text
BACKEND_FAILOVER_EXISTS=YES
FAILOVER_EVENTUALLY_RECOVERS=YES
SEAMLESS_FAILOVER=NO
USER_VISIBLE_5XX_OBSERVED=YES
USER_VISIBLE_STATUS=504
PRE_GATE9_BACKEND_FAILOVER_PROOF=FAIL
```

## Root Cause

The failure was narrowed to the passive upstream failure threshold combined with dynamic Docker DNS resolution.

Observed runtime characteristics included:

- a stopped backend becoming unavailable through Docker DNS;
- Nginx connection attempts waiting for `proxy_connect_timeout=5s`;
- valid same-request retry behavior when another healthy peer was selected;
- one user-visible `504` while the dead peer remained within the passive failure window.

The minimal corrective action was therefore to lower the passive failure threshold only.

## Single-Variable Fix

The exact runtime change was:

```text
max_fails=2
        ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬Å“
max_fails=1
```

Updated upstream configuration:

```nginx
server w8head-7e0e6cc-backend-1:5000 resolve max_fails=1 fail_timeout=10s;
server w8head-7e0e6cc-backend-2:5000 resolve max_fails=1 fail_timeout=10s;
```

The following settings were intentionally left unchanged:

```nginx
proxy_next_upstream error timeout http_502 http_503 http_504;
proxy_next_upstream_tries 2;

proxy_connect_timeout 5s;
proxy_read_timeout 75s;
proxy_send_timeout 75s;

fail_timeout=10s;
```

This preserved a one-variable experiment.

## Runtime Fix Application

The Gate 8 proxy's mounted upstream file was discovered dynamically:

```text
UPSTREAM_HOST_ROOT=C:\Users\HP\AppData\Local\Temp\clinic-gate8-head-7e0e6cc-r1\runtime\phase9\nginx-upstream
UPSTREAM_HOST_FILE=C:\Users\HP\AppData\Local\Temp\clinic-gate8-head-7e0e6cc-r1\runtime\phase9\nginx-upstream\backend.conf
UPSTREAM_MOUNT_RW=False
```

The host-side file remained writable and was changed from exactly two `max_fails=2` entries to exactly two `max_fails=1` entries.

Validation:

```text
MAX_FAILS_2_COUNT=2
MAX_FAILS_1_COUNT=0

POST_EDIT_MAX_FAILS_1_COUNT=2
POST_EDIT_MAX_FAILS_2_COUNT=0

RUNTIME_UPSTREAM_FIX_APPLIED=PASS
```

Nginx configuration validation and reload both passed:

```text
NGINX_TEST_EXIT=0
NGINX_RELOAD_EXIT=0
NGINX_EFFECTIVE_FIX=PASS
RETRY_POLICY_UNCHANGED=PASS
```

Effective upstream configuration after reload:

```nginx
server w8head-7e0e6cc-backend-1:5000 resolve max_fails=1 fail_timeout=10s;
server w8head-7e0e6cc-backend-2:5000 resolve max_fails=1 fail_timeout=10s;
```

## Proof Leg A ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Backend 1 Down

Backend-1 was deliberately stopped.

```text
FAILED_BACKEND=w8head-7e0e6cc-backend-1
RUNNING=False
STATUS=exited

SURVIVOR=w8head-7e0e6cc-backend-2
RUNNING=True
HEALTH=healthy
```

One hundred application-path requests were sent through the proxy.

Results:

```text
BACKEND1_DOWN_FIRST_APP_CODE=401
BACKEND1_DOWN_HEALTH_MISMATCHES=0
BACKEND1_DOWN_APP_MISMATCHES=0
BACKEND1_DOWN_5XX_COUNT=0
BACKEND1_DOWN_TRANSPORT_FAILURES=0
BACKEND1_DOWN_SLOWEST_PROBE_SECONDS=5.885
BACKEND1_DOWN=PASS
```

The first request after failure still completed successfully through failover, but required approximately 5.9 seconds because the existing `proxy_connect_timeout=5s` remained unchanged.

Backend-1 was restored and returned to healthy state.

```text
BACKEND1_RESTORED=PASS
```

A 12-second settling interval was used before the reverse-direction test so passive failure state and resolver state could expire cleanly.

## Proof Leg B ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Backend 2 Down

Backend-2 was deliberately stopped.

```text
FAILED_BACKEND=w8head-7e0e6cc-backend-2
RUNNING=False
STATUS=exited

SURVIVOR=w8head-7e0e6cc-backend-1
RUNNING=True
HEALTH=healthy
```

One hundred application-path requests were sent through the proxy.

Results:

```text
BACKEND2_DOWN_FIRST_APP_CODE=401
BACKEND2_DOWN_HEALTH_MISMATCHES=0
BACKEND2_DOWN_APP_MISMATCHES=0
BACKEND2_DOWN_5XX_COUNT=0
BACKEND2_DOWN_TRANSPORT_FAILURES=0
BACKEND2_DOWN_SLOWEST_PROBE_SECONDS=5.69
BACKEND2_DOWN=PASS
```

Backend-2 was restored and returned to healthy state.

```text
BACKEND2_RESTORED=PASS
```

## Final Runtime State

After both failure legs:

```text
FINAL_HEALTH=200
FINAL_READY=200
FINAL_APP=401

PROXY_RESTART_DELTA=0
BACKEND1_RESTART_DELTA=0
BACKEND2_RESTART_DELTA=0
```

Both backends were restored and the proxy remained continuously running.

## Final Source Integrity

```text
FINAL_BRANCH=feat/smtp-email-circuit-breaker-integration
FINAL_HEAD=07a85e83b774f2697d142c782605d05cd3a7517c
FINAL_PROTECTED_WEB_ROUTES_SHA256=C483E7D6FF6B0216224E1B6713A54E727211D86B5B7D9093B9E4F27CE5BA7352
FINAL_SOURCE_LOCK=PASS
APPLICATION_SOURCE_CHANGED=NO
```

No application source was modified during the runtime failover experiment.

## Accepted Closure

```text
MAX_FAILS_BEFORE=2
MAX_FAILS_AFTER=1
SINGLE_VARIABLE_FIX=PASS

BACKEND1_DOWN_REQUESTS=100
BACKEND1_DOWN_USER_VISIBLE_5XX=0
BACKEND1_DOWN_FAILOVER=PASS

BACKEND2_DOWN_REQUESTS=100
BACKEND2_DOWN_USER_VISIBLE_5XX=0
BACKEND2_DOWN_FAILOVER=PASS

BOTH_BACKENDS_RESTORED=YES
PROXY_RESTART_DELTA=0
APPLICATION_SOURCE_CHANGED=NO
PRIMARY_FLEET_TOUCHED=NO
GATE7_FLEET_TOUCHED=NO
RUNTIME_FIX_PROVEN=YES
PRE_GATE9_BACKEND_FAILOVER_FIX=GREEN
```

## Claim Boundary

This evidence proves that, for the isolated two-backend Gate 8 candidate:

- either individual backend can be stopped;
- the surviving backend continues serving application traffic;
- 100 repeated requests in each failure direction produced zero user-visible 5xx responses;
- the first application request after each backend failure still completed successfully;
- both backends were restored healthy afterward;
- no proxy restart was required;
- application source remained unchanged.

This evidence does **not** yet prove that the fix survives a fresh environment bootstrap. The successful change currently exists in the runtime-generated upstream file.

## Remaining Work Before Gate 9

### 1. Persist the fix in source-of-truth generation/configuration

The upstream-generation path that currently produces:

```text
max_fails=2
```

must be updated to produce:

```text
max_fails=1
```

The persistent change should then be reviewed, committed, pushed, and revalidated in a fresh or regenerated candidate environment.

### 2. Optional failover-latency optimization

Failover correctness is GREEN, but the first request after backend loss still took approximately:

```text
backend-1 failure: 5.885s
backend-2 failure: 5.690s
```

This is consistent with the unchanged:

```nginx
proxy_connect_timeout 5s;
```

Reducing the connection timeout may improve first-request failover latency, but it should be tested separately as a second variable. It is not required to preserve the proven zero-5xx behavior.

## Final Decision

```text
FAILOVER_CORRECTNESS=GREEN
USER_VISIBLE_5XX=0
RUNTIME_FIX=PROVEN
PERSISTENT_SOURCE_FIX=PENDING
FAILOVER_LATENCY_OPTIMIZATION=OPTIONAL
GATE9=HOLD_UNTIL_FIX_IS_PERSISTED
```
## Persistence closure Ã¢â‚¬â€ tracked source and fresh bootstrap

The runtime failover fix was subsequently persisted into tracked source and verified without mutating the running candidate fleet.

Persistence proof:

```text
TRACKED_RECONCILIATION_SOURCE=max_fails=1
TRACKED_RECONCILIATION_REGENERATION=PASS
FOCUSED_AUTOSCALING_REGRESSION=PASS
```

A tracked initial-upstream creator was added at:

```text
backend/app/core/autoscaling/bootstrap_nginx_upstream.py
```

Starting from a temporary project root where `runtime\phase9\nginx-upstream` did not exist, the tracked bootstrap CLI created `backend.conf` with exactly two backend entries and:

```text
GENERATED_MAX_FAILS1_COUNT=2
GENERATED_MAX_FAILS2_COUNT=0
EMPTY_DIRECTORY_BOOTSTRAP=PASS
FRESH_BOOTSTRAP_PERSISTENCE=PASS
```

The bootstrap is fail-closed against accidental overwrite. A second bootstrap attempt returned a non-zero exit code with `FileExistsError`; the SHA-256 of the existing `backend.conf` was unchanged before and after the rejected attempt:

```text
SECOND_BOOTSTRAP_EXIT=1
FILE_UNCHANGED=True
EXPECTED_FILE_EXISTS_ERROR=True
BOOTSTRAP_OVERWRITE_REFUSAL=PASS
```

Focused regression covering bootstrap plus autoscaling routing completed:

```text
41 passed
BOOTSTRAP_AND_AUTOSCALING_REGRESSION=PASS
```

Final persistence classification:

```text
FAILOVER_RUNTIME=GREEN
TRACKED_RECONCILIATION_PERSISTENCE=GREEN
TRACKED_EMPTY_DIRECTORY_BOOTSTRAP=GREEN
BOOTSTRAP_OVERWRITE_PROTECTION=GREEN
PERSISTENT_SOURCE_FIX=GREEN
```

This persistence closure does not rewrite the earlier historical `max_fails=2` evidence; those records remain as the before-state that motivated the fix.
## Persistence closure â€” tracked source and fresh bootstrap

The runtime failover fix was subsequently persisted into tracked source and verified without mutating the running candidate fleet.

Persistence proof:

```text
TRACKED_RECONCILIATION_SOURCE=max_fails=1
TRACKED_RECONCILIATION_REGENERATION=PASS
FOCUSED_AUTOSCALING_REGRESSION=PASS
```

A tracked initial-upstream creator was added at:

```text
backend/app/core/autoscaling/bootstrap_nginx_upstream.py
```

Starting from a temporary project root where `runtime\phase9\nginx-upstream` did not exist, the tracked bootstrap CLI created `backend.conf` with exactly two backend entries and:

```text
GENERATED_MAX_FAILS1_COUNT=2
GENERATED_MAX_FAILS2_COUNT=0
EMPTY_DIRECTORY_BOOTSTRAP=PASS
FRESH_BOOTSTRAP_PERSISTENCE=PASS
```

The bootstrap is fail-closed against accidental overwrite. A second bootstrap attempt returned a non-zero exit code with `FileExistsError`; the SHA-256 of the existing `backend.conf` was unchanged before and after the rejected attempt:

```text
SECOND_BOOTSTRAP_EXIT=1
FILE_UNCHANGED=True
EXPECTED_FILE_EXISTS_ERROR=True
BOOTSTRAP_OVERWRITE_REFUSAL=PASS
```

Focused regression covering bootstrap plus autoscaling routing completed:

```text
41 passed
BOOTSTRAP_AND_AUTOSCALING_REGRESSION=PASS
```

Final persistence classification:

```text
FAILOVER_RUNTIME=GREEN
TRACKED_RECONCILIATION_PERSISTENCE=GREEN
TRACKED_EMPTY_DIRECTORY_BOOTSTRAP=GREEN
BOOTSTRAP_OVERWRITE_PROTECTION=GREEN
PERSISTENT_SOURCE_FIX=GREEN
```

This persistence closure does not rewrite the earlier historical `max_fails=2` evidence; those records remain as the before-state that motivated the fix.