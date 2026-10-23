# Prometheus Metrics and Upstream Log Correlation

## Purpose and scope

The backend exposes a small Prometheus text-format endpoint using the existing
Redis-backed HTTP telemetry. No additional metrics library or collector is
required by the application. This endpoint is an internal operations surface,
not a public API.

## Configure access

Set `OBSERVABILITY_METRICS_TOKEN` through the deployment secret manager before
starting the backend. Use a randomly generated secret of at least 32 characters.
The endpoint returns `503` and exports no data if the setting is missing or too
short; an absent or incorrect Bearer token returns `401`. Never put the token in
a URL, dashboard label, source control, or log line.

The included Compose configuration passes this environment variable only to
the backend. Scrapers must reach the service over an approved internal network
or the trusted TLS endpoint, use a secret-backed `Authorization: Bearer ...`
header, and be restricted at the network/firewall layer. Do not expose backend
port 5000 publicly. The token is an application control, not a replacement for
network isolation and TLS.

## Scrape endpoint

- Path: `GET /metrics`
- Format: Prometheus text exposition format `0.0.4`
- Authentication: `Authorization: Bearer $OBSERVABILITY_METRICS_TOKEN`
- Cache: disabled (`Cache-Control: no-store`)
- Metric labels: intentionally low-cardinality; no request paths, patient data,
  request IDs, trace IDs, credentials, or arbitrary provider values are labels.

The export contains:

- `clinic_observability_exporter_up`: endpoint rendered the exposition.
- `clinic_http_telemetry_collection_success`: the collector call completed
  without raising; monitor this separately from exporter availability.
- `clinic_http_telemetry_available` and
  `clinic_http_telemetry_healthy`: Redis availability and freshness/integrity
  of the HTTP telemetry view.
- `clinic_http_requests_in_window`,
  `clinic_http_requests_per_second`, `clinic_http_5xx_errors_in_window`, and
  `clinic_http_5xx_error_ratio` for the existing 30-second rolling window.
- `clinic_http_request_duration_ms` histogram and
  `clinic_http_telemetry_last_sample_timestamp_seconds`.

A successful HTTP scrape does not mean its telemetry is healthy. Alert when
`clinic_http_telemetry_collection_success`, `clinic_http_telemetry_available`,
or `clinic_http_telemetry_healthy` becomes `0`. Telemetry is retained in short
Redis buckets, so this is a near-term operational view, not a historical data
warehouse. Request and health/metrics-scrape samples are counted for
instrumentation coverage; health and scrape endpoints are excluded from
application-demand request counts.

## Verify locally

Supply the token from the deployment secret manager. For a disposable local
run only, a strong token can be generated in the current PowerShell session
with Python and then passed into Compose through the environment:

```powershell
$env:OBSERVABILITY_METRICS_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(48))"
docker compose up -d backend
```

With the token present in the current shell, the following sends the secret in
a header rather than the URL:

```powershell
$headers = @{ Authorization = "Bearer $env:OBSERVABILITY_METRICS_TOKEN" }
$response = Invoke-WebRequest -Uri "https://<trusted-host>/metrics" -Headers $headers
$response.StatusCode
$response.Content
```

Use a trusted certificate chain for the target; do not disable TLS verification
to make an invalid certificate pass. Verify that the exposition includes
`clinic_observability_exporter_up 1` and that the telemetry availability and
health metrics are `1` before treating request-rate or latency values as
reliable. No data points, or a stale/invalid telemetry payload, must be treated
as observability failure rather than as zero application demand.

## Upstream request correlation

The NGINX access log is key-value text written to stdout. It records the
NGINX-generated `request_id`, response `trace_id`, HTTP method, chosen upstream
address, status, total request time and upstream response time. NGINX forwards
its generated `X-Request-ID` to the backend. Application performance/error
records include request and trace identifiers so the edge log and backend log
can be searched together.

The access log deliberately omits raw request paths, query strings, headers,
request bodies and user identifiers. Keep this restriction when forwarding
logs to the log aggregation system. Store logs in an access-controlled sink,
apply retention limits, and do not use request IDs, trace IDs or load-test IDs
as metric labels.

## Routing and capacity validation

During a controlled load test, compare NGINX `upstream` and
`upstream_response_time` records with `clinic_http_requests_per_second`, the
5xx ratio, the latency histogram and backend performance logs. A request ID
must correlate the NGINX row with the backend event. Use a stable deployment
inventory for expected replica coverage where supported by the monitoring
controller; do not infer healthy capacity solely from a fleet aggregate when an
expected backend is silent.

This endpoint and log format establish telemetry export and correlation only.
They do not prove that a production scraper, alert receiver, dashboard, log
sink, or multi-replica routing test has been deployed or operated. Record those
checks separately as executed operational evidence.
