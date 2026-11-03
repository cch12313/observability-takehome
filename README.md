# Observability take-home

Two Python FastAPI services, with metrics, traces, trace-correlated logs, a dashboard, an
alert and a telemetry test:

```text
api-service  →  inventory-service
     │ traces (OTLP)  → OTel Collector → Tempo ┐
     │ metrics (scrape) ──── Prometheus ───────┼→ Grafana
     └ JSON logs → stdout
```

The assignment is in `ASSIGNMENT.md`. The write-ups are `INCIDENT.md` and `AI_USAGE.md`, and
the Collector outage, trade-offs, what's missing and scaling are further down this file.

## Requirements

Docker and Docker Compose. `python3` for the telemetry test.

## Running it

Credentials come from `.env`, which is gitignored. Nothing secret is in the repo, and Compose
refuses to start without a Grafana password:

```bash
cp .env.example .env   # then set GF_ADMIN_PASSWORD
docker compose up --build
```

Then:

| What | Where |
|---|---|
| Grafana | <http://localhost:3000>, log in with your `.env` values. Dashboard: "Service overview (api → inventory)" |
| Traces | Grafana → Explore → Tempo. Query API on <http://localhost:3200> |
| Alert | <http://localhost:9090/alerts> |
| api-service | <http://localhost:8000> (`GET /products/{sku}`, `GET /health`) |
| inventory-service | <http://localhost:8001> (`GET /stock/{sku}`, `GET /health`) |

Quick check:

```bash
curl http://localhost:8000/products/sku-1
# {"sku":"sku-1","name":"Product 1","price":5.99,"quantity":...,"in_stock":true}
```

## Traffic

The generator sits in its own Compose profile, so `docker compose up` won't start it:

```bash
docker compose --profile loadgen up loadgen
```

`RPS` (default 10) and `TARGET_URL` are configurable. It picks uniformly at random from `sku-1` through `sku-20` and prints the status code and duration of each request to stdout.

A note on the rate, because it affects how you size your observation windows. The generator is single-threaded and sends one request at a time, and `RPS` sets the sleep between requests rather than enforcing a fixed rate. So actual throughput comes out lower than the nominal number, and it drops off sharply once responses get slow, since a slow request holds up the next one. Measured on a reference machine: about 7.7 req/s with no fault running, somewhere between 1 and 2 req/s with one active.

Which means you get far fewer samples during a fault than you do at rest. Percentiles over a short rate window will be noisy. Widen the window, run several generators in parallel, write a concurrent one of your own, or just explain in your README how you dealt with it.

One gotcha when you're shutting down: loadgen has `restart: unless-stopped` and lives in a separate profile, so a plain `docker compose down` leaves it running. It'll come back when Docker restarts and sit there logging errors against services that no longer exist. Use this instead:

```bash
docker compose --profile loadgen down -v
```

If you'd rather skip the container, a shell loop does the job:

```bash
while true; do curl -s http://localhost:8000/products/sku-$((RANDOM%20+1)) > /dev/null; sleep 0.1; done
```

## Triggering the fault

Do this while traffic is running:

```bash
./scripts/trigger_incident.sh slow      # upstream gets slow, still inside api-service's timeout
./scripts/trigger_incident.sh timeout   # upstream gets slow enough to blow through it
./scripts/trigger_incident.sh           # no argument is the same as: slow
./scripts/resolve_incident.sh           # off
```

The two modes produce different symptoms. The assignment asks you to trigger and observe both.

Both scripts hit the admin endpoint on inventory-service. You can override the presets with `INVENTORY_ADMIN_URL`, `LATENCY_MS` and `PROBABILITY`.

To see the current state:

```bash
curl -s http://localhost:8001/admin/chaos
```

## Endpoints

| Service | Method | Path | Notes |
|---|---|---|---|
| api-service | GET | `/products/{sku}` | Calls inventory-service |
| api-service | GET | `/health` | |
| inventory-service | GET | `/stock/{sku}` | |
| inventory-service | GET | `/health` | |
| inventory-service | GET/POST | `/admin/chaos` | Read or set the fault state. The scripts use this. |

SKUs run from `sku-1` to `sku-20`.

`api-service` gives upstream calls a 3 second timeout (`UPSTREAM_TIMEOUT_S`, set in `docker-compose.yml`). Go past that and it returns HTTP 504.

A normal `inventory-service` lookup takes around 15 ms (`INVENTORY_BASE_LATENCY_MS`).

## Walking the investigation

Someone handed this repo, who doesn't know the fault, can follow this. Six generators give
enough samples during a fault.

```bash
docker compose --profile loadgen up -d --scale loadgen=6 loadgen
./scripts/trigger_incident.sh timeout
```

1. **Something's wrong.** On the dashboard, the bad-request ratio climbs to ~30%. The alert
   goes pending in under a minute and fires after ~3.75 minutes.
2. **Which service.** In the RED row, 5xx appear only on api-service. Inventory reports 0%
   errors, but its p99 (~5.9 s) is higher than api's (~3.5 s), so inventory is slow, and
   api-service is reporting the result.
3. **Which part of the call.** Click an exemplar on the p99 panel to open a trace. The api
   CLIENT span errors at ~3 s, and the inventory SERVER span runs ~4 s and returns 200.
4. **The log lines.** Copy the trace id:
   ```bash
   docker compose logs api-service inventory-service | grep <trace_id>
   ```
   One line from each service: ERROR 504 at ~3 s, WARNING 200 at ~4 s.
5. **Turn it off.** `./scripts/resolve_incident.sh`. The alert clears in about 30 s.

Try `slow` too. There are no 5xx, both services are ~2 s, and the alert still fires.

Checks: `./scripts/test_telemetry.sh` (trace crosses the hop), `./scripts/test_alerts.sh`
(alert rule, promtool in Docker). Shut down with `docker compose --profile loadgen down -v`.

## Known limitations

- Local evaluation only. This isn't a production setup.
- No persistence. Stock values are made up in memory.

## Decisions

### Latency buckets

Buckets (s): `0.06, 0.1, 0.25, 0.5, 1.0, 2.5, 3.5, 6.0` (`common/metrics.py`).
Each edge is tied to a threshold or to a latency I measured in each mode:

| Edge | Why |
|---|---|
| 0.06 | Just above normal p99 (~45–50 ms), so healthy traffic sits in the first bucket. |
| 0.1 | The SLO (`SLO_LATENCY_SECONDS`): ~2x normal p99, and about where users start to notice delay. The alert counts requests above it, so it must be an exact edge. |
| 0.25, 0.5, 1.0 | Resolution for mild slowdowns, up to "clearly slow" at 1 s. |
| 2.5, 3.5 | A ±0.5 s band around the 3 s `UPSTREAM_TIMEOUT_S`. Below 2.5: slow but well clear of the timeout. Inside: near or past it; 504s land at ~3.02 s, just under the 3.5 upper edge. Success vs. timeout inside the band is split by the `status_code` label, so no 3.0 edge. Measured side effect: slow mode (~2.05 s) is also bounded (p99 reads 2.45 s). |
| 6.0 | Headroom, 2x the timeout: inventory keeps running after api-service gives up. See trade-offs. |

Edges sit just *above* known latencies because `histogram_quantile` interpolates
inside a bucket, and a p99 lands near the bucket's upper edge.

**How I checked.** Six loadgen containers (one gives only 1–2 req/s under a fault),
Prometheus p99 over `[1m]` compared with loadgen logs and Tempo span durations.
My first layout stopped at 0.5 s, so every slow request fell into `+Inf`. A single
`(2.5, 5]` bucket made api (3.02 s) and inventory (4.02 s) both read ~4.9 s, so I
couldn't tell which service was slow. Adding 3.5 split them. Final readings:
slow 2.45 s (real 2.05), timeout api 3.47 s (real 3.02), inventory 5.92 s (real 4.02).

**Trade-offs.**
- Top edge 6.0 instead of 5.0. Today's fault puts inventory at ~4.02 s, which 5.0 would
  cover more precisely (~4.95 s vs ~5.92 s). But that delay is just a preset
  (`LATENCY_MS` can raise it) and a real upstream has no ceiling, so I tied the top
  edge to the timeout rather than to one observed value.
- Normal-range p95/p99 read ~57/59 ms whatever the real value, since healthy traffic
  is all in the first bucket. Fine for alerting, blind to a 20→50 ms regression.

### Metrics path

**Choice.** Prometheus scrapes `/metrics` from both services directly, and also
scrapes the Collector's own metrics on `:8888`. Only traces go through the Collector.

**Why.** §8 stops the Collector. If metrics went through it, the dashboard and the
alert would go blind at the same moment. Scraping directly keeps them working, and
`up` and the `otelcol_*` metrics (dashboard, pipeline row) show when the Collector is down.

**Trade-off.** Traces and metrics take two paths (OTLP push vs. scrape), so there are
two configs. Prometheus itself is still a single point of failure; see Missing.

### Alert

`ApiServiceBadRequestRatioHigh` in `prometheus/rules/alerts.yml`.

**Signal.** Share of api-service requests that are 5xx or slower than the 0.1 s SLO
(404 noise on `route="unmatched"` excluded). Error rate alone misses slow mode, which has no 5xx.
The ratio is computed from bucket counts (`le="0.1"`), so there is no interpolation.
p99 is an estimate, and a fast-failing upstream (502 in a few ms) would make it drop.

**Threshold.** Above 5%, with traffic above 0.5 req/s. Normal traffic measured ~0% bad,
both fault modes ~30%. The traffic guard stops one slow request from swinging the
ratio when there is almost no traffic.

**Brief spikes.** `[1m]` window, `for: 3m`. I started with `[5m]` and `for: 2m`, but a
1-minute spike at 30% is still 6% over 5 minutes and stays in the window for 5 minutes,
so it fired. A longer window stretches a spike instead of hiding it. With `[1m]` the
ratio drops within a minute after the spike, so a spike shorter than ~2m20s never fires.
Measured: sustained slow mode fired after ~3m45s, a 2-minute spike reached pending but
never fired, and the alert cleared ~45 s after the fault was turned off.

**Tests.** `./scripts/test_alerts.sh` runs promtool on six cases: healthy, 2-minute
spike, slow, timeout, low traffic, recovery. Setting `for: 1m` or `[5m]` makes the tests fail.

No Alertmanager: where a page goes is out of scope here.

### Telemetry test

```bash
./scripts/test_telemetry.sh   # needs the stack running and python3; about 8 s
```

**What it does.** Sends one `GET /products/sku-1` with a trace id it generated itself, so
it finds its own trace and not one from loadgen. It polls Tempo for up to 30 s, because
each service flushes spans separately, then asserts the chain: api SERVER (parent = the id
it sent) → api CLIENT → inventory SERVER. It also checks that `/metrics` still has the
`le="0.1"` bucket. The alert needs that edge, and promtool can't notice it going away
because its series are hard-coded.

**How I checked it fails.** Each run below still returned HTTP 200, so nothing else would
have gone red:
- `OTEL_PROPAGATORS=baggage` on inventory: fails, inventory spans are missing from the trace.
- The same setting on api: fails, the trace is not found.

I first tried `OTEL_PROPAGATORS=none`, but on this SDK version it makes every request
return 500. That is a visible outage, not the silent break this test is for. In these runs
the test fails at the polling step. The parent/child assertions only fire when spans share
a trace but are linked wrongly, and I did not simulate that.

**Not covered.** Other routes and error paths; fault modes; log-to-trace correlation;
Prometheus and Grafana; whether the alert fires (that is `./scripts/test_alerts.sh`). If
Tempo or the Collector is down the test fails too, with the same message as a broken
propagator on api, so a failure means "look here", not "propagation is the cause".

### Logs

Each stdout line from the two services is one JSON object (`common/logs.py`,
`common/log_config.json`). A crash before logging starts, or a Python warning, still goes
to stderr as plain text. Lines written while a span is active carry `trace_id` and
`span_id`. Lines written outside a span have neither: startup, exporter errors, and
uvicorn's own traceback line.

**One line per request, from a middleware.** `/health` and `/metrics` are skipped, and
malformed HTTP never reaches the middleware. Fields: `method`, `route`, `path`,
`status_code`, `duration_ms`. `route` uses the same value as the metric label. I turned
the uvicorn access log off because it writes nothing when the client has already
disconnected. In timeout mode those are the inventory lines you need: inventory finishes
with a 200 after api-service has given up. The access log also has no duration.

**Levels.** ERROR for 5xx, WARNING for slower than the 0.1 s SLO, otherwise INFO. The
threshold is `SLO_LATENCY_SECONDS` in `common/telemetry_common.py`, shared with the
histogram buckets. `alerts.yml`, its tests and the dashboard JSON can't import it, so they
are kept in sync by hand. A status-only rule would leave inventory's late 200s at INFO.

**How I checked.** Six loadgen containers. Timeout mode: a 60 s log window, 40 s of it
under the fault. All 2,144 lines parsed as JSON, and all 73 api-service 504s had an
inventory line with the same `trace_id` (200, WARNING, 4.02–4.04 s). Slow mode, 45 s: all
112 api-service WARNING lines (2.03–2.04 s) joined to an inventory WARNING line, with no
ERROR lines. In Tempo, 28 of 28 sampled `span_id`s, from normal and faulted requests, were
that service's SERVER span. It is never the CLIENT span, which has already ended when the
line is written.

**If these logs went to a central backend** (not built):
- Collect stdout with a shipper. Stdout keeps the app independent of the backend, as long
  as the shipper buffers and drops instead of applying back-pressure.
- Add `environment`, `version` and instance fields. `service` comes from an env var today.
- Index only low-cardinality fields (`service`, `level`). `trace_id` and `path` stay
  searchable fields, not index labels.
- Link `trace_id` to the trace backend, for example with a Grafana derived field.
- Sampling. Every request is traced today, so a `trace_id` resolves while the pipeline is
  up and within Tempo's 24 h retention. With sampling,
  many lines would point at traces that were never kept, so log and trace sampling need to
  be decided together, and errors and slow requests kept in both.
- Cost and privacy. One line per request adds up, so INFO may need sampling or short
  retention, and `path` needs checking for sensitive values.

### When the Collector goes down

I ran `docker compose stop otel-collector` for 3 minutes with six loadgen containers
running (~44 req/s), then started it again. `stop` is a graceful shutdown, so a hard kill
could lose a little more from the Collector's own buffers.

**The application carried on.** Every request returned 200, before, during and after.
api-service p99 went from 44 ms to 47 ms, and the rate stayed at ~44 req/s. App memory rose
from about 57 MiB to 71 MiB during the outage and had dropped to 65 MiB 90 s after recovery.

**The data was lost, and not recovered.** About 8,100 requests ran during the outage. I took
their `trace_id`s from the request logs and looked up a sample in Tempo, 25 per 15 s:

| Requests sent (relative to the stop) | Found in Tempo |
|---|---|
| Before the stop | 25/25 api. Inventory also 25/25, except 23/25 in the last 15 s |
| 0 to +165 s | 0/25 in every bucket |
| +165 to +180 s | api 7/25, inventory 15/25, so some traces are only half there |
| After +180 s (restart at +184 s) | 25/25 |

The spans that survived are the newest ones, not the oldest: the SDK's queue dropped old
spans and sent what it still held on reconnect. It holds only ~15 s at this traffic, partly
because the ASGI noise spans use the queue too, as they are filtered at the Collector, not
in the app. Metrics had no gap, because Prometheus scrapes the services directly.

**What told me something was wrong.** The Grafana "OTel Collector up" panel became red and
`down`. The RED panels did not change, so they looked healthy while traces were being lost.
The logs had a signal too, which I only found when I went through them afterwards: exporter
ERROR lines and `Queue full, dropping Span` warnings in stdout from ~12 s in. Nothing watches
those.

**Would anything have caught it?** Only someone already looking at that panel. The alert
never fired, not even as pending, and it shouldn't, because users weren't affected. Nothing
would have told anyone.

**What I'd add.**
- An alert on `up{job="otel-collector"} == 0` for a minute or two. That only covers the
  process being down, so also alert on `otelcol_exporter_send_failed_spans` increasing (the
  Collector can't reach Tempo) and on accepted spans at zero while the services have traffic
  (the apps can't reach the Collector).
- Somewhere for alerts to go. There is no Alertmanager here, so a firing rule notifies nobody.
- A dead man's switch: an always-firing alert sent to an outside heartbeat service, which
  pages when it stops. Otherwise a dead Prometheus takes every alert down with it.

I did not build these: the assignment asks for one alert, and extra components count against it.

## Trade-offs

- **Zero-code tracing** keeps the SDK and exporters out of the services, and the telemetry test
  guards it. The cost is less control: the ASGI noise spans can only be dropped at the
  Collector, so they still fill the app's span queue (see the Collector outage above).
- **Metrics skip the Collector**, so they survive its outage, at the cost of two paths and
  two configs.
- **Classic histograms** give edges with a stated reason, and p99 reads 15–47% high during a
  fault (`INCIDENT.md`). Exact durations come from traces and logs.
- **The alert favours ignoring spikes over speed.** It takes about 3.6 minutes to fire.
- **100% trace sampling** makes every log line resolve to a trace, which wouldn't be affordable
  at higher traffic.
- **Logs stay on stdout** as the assignment allows, so finding lines by `trace_id` means `grep`.

## Missing

Known gaps, none hit by the two fault modes:

- **Log gaps.** uvicorn's traceback line has no `trace_id`, while the request line has the id
  and no traceback; I'd attach `exc_info` to the request line. A 502's reason isn't logged; I'd
  add a line in the handler. `path` has no length limit.
- **Stdout can block the service.** Logging writes synchronously. In a probe with nobody reading
  the pipe, the service hung from about the 207th request, `/health` included. A healthy Docker
  log driver doesn't hit this, but a central log pipeline could.
- **The alert goes quiet if api-service dies.** No traffic means no ratio, and the traffic guard
  fails. It needs an `up`/absence alert and an outside probe. The dashboard has no `up` panel for
  the services, and no per-route request rate to spot scanner traffic.
- **Nothing notifies anyone, and Prometheus is a single point of failure.** See "What I'd add" above.
- **No spans inside inventory**, so a trace can't name the slow step.
- **The `0.1` threshold lives in several files.** Python shares a constant, but the alert, its
  tests and the dashboard are synced by hand. Changing bucket edges also creates new series, so
  queries across the change read wrong for a while.
- **The telemetry test isn't in CI**, and it needs the running stack.
- **No burn-rate alert or runbook.**

## If this had to scale

Design only. I haven't run a setup like this, so these are starting points, not a tested plan.

- **Collector topology.** Apps send to one gateway Collector per account, and it forwards to the
  backends over private connectivity, so apps don't need to know where the backends are. I'd skip
  a separate agent tier until something needs it.
- **No per-node collector.** Traces and metrics go from the SDK straight to the gateway. Logs go
  from stdout through the platform's log driver to the cloud log service, then get forwarded. If
  the SDK's buffer proves too small (~15 s in my Collector outage test), I'd try a sidecar. I'm
  not sure about the delay and cost of the cloud log path.
- **Boxes behind the firewall.** An agent on each box pushes outbound over TLS. While offline it
  queues to disk up to a size cap, dropping the oldest. It pulls config and upgrades, starting
  with a few boxes. I'm not sure how to tell an offline box from a dead agent.
- **Availability versus data loss.** Apps never wait on telemetry: requests carried on untouched
  when I stopped the Collector. Metrics and alerts matter most, traces can be lossy, and logs from
  the boxes get the disk queue because they may be the only copy.
- **Cost.** Keep errors and slow requests and sample the rest. Short retention for traces and INFO
  logs. No raw paths or ids as metric labels.
- **Config.** One base Collector config with per-environment overlays in Git, and pinned versions.
  Run promtool in CI and the telemetry test after each deploy.
