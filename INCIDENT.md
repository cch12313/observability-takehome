# Incident: inventory-service lookups go slow

I triggered each mode for about 4 minutes with six loadgen containers on 2026-11-03: slow at
13:11:54–13:16:10 UTC, timeout at 13:17:51–13:22:06 UTC.

## What a user would notice

- **Slow mode:** nothing fails. About 29% of requests take ~2 s instead of ~30 ms, and the
  rest feel normal.
- **Timeout mode:** about 29% of requests wait ~3 s and then get a 504. The rest work normally.

## What the metrics said

Prometheus, `[1m]` window, near the end of each run:

| | Normal | Slow | Timeout |
|---|---|---|---|
| api 5xx ratio | 0% | **0%** | **30.2%** |
| inventory 5xx ratio (request logs: all 200) | 0% | 0% | 0% |
| api bad ratio (5xx or > 0.1 s) | 0% | 29.8% | 30.2% |
| api p95 / p99 | 57 / 59 ms | 2.25 / 2.45 s | 3.33 / 3.47 s |
| inventory p95 / p99 | 57 / 59 ms | 2.25 / 2.45 s | **5.59 / 5.92 s** |

- Error rate alone misses slow mode. In timeout mode it blames the wrong service, as errors
  appear only on api-service. Inventory's higher latency is what points to it.
- The rate drop on the dashboard comes from loadgen waiting for each response, not from users.

## What the traces said

| Span | Slow `c083eb8c…` | Timeout `c2042660…` |
|---|---|---|
| api SERVER `GET /products/{sku}` | 2036.9 ms, ok | 3004.4 ms, error (504) |
| api CLIENT `GET` | 2032.9 ms, ok | 3002.9 ms, error |
| inventory SERVER `GET /stock/{sku}` | 2029.3 ms, ok | 4033.6 ms, **ok** |

In slow mode almost all the time is inside inventory's span. In timeout mode the api CLIENT
span errors at 3 s, while inventory finishes with a 200 about a second after anyone stopped waiting.

The logs agree: all 620 slow api lines in slow mode and 440 of the 441 504s had an inventory
line with the same `trace_id` (the last landed just after my log window).

## Root cause

inventory-service is slow on a fraction of its lookups. api-service is only passing that on.

- Inventory's durations split in two, with nothing between: 1,527 lookups under 38 ms and 620
  over 2.01 s in slow mode, 1,083 at 48 ms or less and 440 over 4.01 s in timeout mode. Some requests
  get a fixed extra delay and the rest aren't affected, which doesn't look like general overload.
- The fault settings on `/admin/chaos` confirm it: `latency_ms` 2000 or 4000, `probability` 0.3.

Both modes are the same fault: 2 s stays under api-service's 3 s timeout, and 4 s goes over it.

## What I'd do immediately

1. Turn the degraded path off: `./scripts/resolve_incident.sh`. The alert cleared 30–35 s later.
2. If it couldn't be turned off (untested): check the latency distribution before lowering
   `UPSTREAM_TIMEOUT_S`. Healthy lookups stayed under about 60 ms in every run, so 1 s cuts off none of them and
   timeout-mode users get the 504 sooner. In slow mode I'd leave it: 2 s successes would become errors.
3. Otherwise a hotfix, depending on how many requests are slow (untested). **Most slow, and users
   keep refreshing** (ticket sales, say): after 1 s, return a default that reads as "unknown".
   **Only some slow, as here**: wrap the stock lookup in a decorator with a short-lived cache,
   to reduce calls to inventory.

## What I'd fix properly

- **Fail fast when inventory lookups are slow.** Where it's acceptable, tell the user straight
  away to try again, with a `Retry-After`, since retries add load. Where it isn't, answer from
  a faster cache, marked as possibly out of date.
- **Stop inventory working for nobody.** Cancel on client disconnect, or pass the deadline along.
- **Add child spans inside inventory.** Today the telemetry can't say which step is slow.

## Does the alert fire in both modes?

Yes. Slow: pending +35 s, firing +215 s, cleared 35 s after the fault ended. Timeout: pending
+45 s, firing +225 s, cleared after 30 s. It fires in slow mode because it counts slow requests as
well as 5xx, where an error-rate alert would stay silent. It takes 3.6–3.75 minutes to fire, the
price of ignoring spikes. Burn-rate windows should page sooner.

## p99 versus the traces

| | Prometheus p99 | Request logs p99 | Trace example | Over by |
|---|---|---|---|---|
| Slow, both | 2.45 s | 2.03–2.04 s | 2.03–2.04 s | ~20% |
| Timeout, api | 3.47 s | 3.02 s | 3.00 s | ~15% |
| Timeout, inventory | 5.92 s | 4.03 s | 4.03 s | ~47% |

**Why.** Prometheus only knows each request's bucket, and `histogram_quantile` interpolates inside it. 2.03 s is in `(1, 2.5]`, 3.0 s in `(2.5, 3.5]`, 4.03 s in `(3.5, 6]`.
With about 30% of requests in that one bucket, p99 lands near its top edge. Logs and spans are
measured per request, so those are the real values.

**What I'd do about it.** Use p99 for spotting a shift and comparing services, and follow an
exemplar to a trace for the exact figure.

In timeout mode the trace lasts ~4.03 s but the user had the 504 at 3.00 s: read the api SERVER span.
