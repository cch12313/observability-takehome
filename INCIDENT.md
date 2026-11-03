# Incident: inventory-service lookups go slow

I triggered each fault mode for about 4 minutes 15 seconds with six loadgen containers.
Slow mode ran at 13:11:54–13:16:10 UTC and timeout mode at 13:17:51–13:22:06 UTC on
2026-11-03.

## What a user would notice

- **Slow mode:** nothing fails. About 29% of requests take ~2 s instead of ~30 ms, and the
  rest feel normal. It looks random from the outside.
- **Timeout mode:** about 29% of requests wait ~3 s and then get a 504. The rest work normally.

## What the metrics said

Prometheus, `[1m]` window, near the end of each run:

| | Normal | Slow | Timeout |
|---|---|---|---|
| api 5xx ratio | 0% | **0%** | **30.2%** |
| inventory 5xx ratio | 0% | 0% | 0% |
| api bad ratio (5xx or > 0.1 s) | 0% | 29.8% | 30.2% |
| inventory > 0.1 s | 0% | 29.5% | 30.7% |
| api p95 / p99 | 57 / 59 ms | 2.25 / 2.45 s | 3.33 / 3.47 s |
| inventory p95 / p99 | 57 / 59 ms | 2.25 / 2.45 s | **5.59 / 5.92 s** |
| api req/s | 44.9 | 8.2 | 5.8 |

- Error rate alone misses slow mode entirely.
- In timeout mode errors appear only on api-service, so error rate blames the wrong service.
  Inventory's latency, higher than api's, is what points to it.
- The rate drop comes from loadgen waiting for each response, not from users leaving.

## What the traces said

Example traces; each has the same three spans:

| Span | Slow `c083eb8c…` | Timeout `c2042660…` |
|---|---|---|
| api SERVER `GET /products/{sku}` | 2036.9 ms, ok | 3004.4 ms, error (504) |
| api CLIENT `GET` | 2032.9 ms, ok | 3002.9 ms, error |
| inventory SERVER `GET /stock/{sku}` | 2029.3 ms, ok | 4033.6 ms, **ok** |

In slow mode almost all the time is inside inventory's span, and api-service adds about
8 ms. In timeout mode the api CLIENT span errors at the 3 s timeout, while inventory carries
on and finishes with a 200 about a second after anyone stopped waiting.

The logs agree. All 620 slow api lines in slow mode and 440 of the 441 504s had an inventory
line with the same `trace_id` (the last one landed just after my log window). To find them:
`docker compose logs api-service inventory-service | grep <trace_id>`.

## Root cause

inventory-service is slow on a fraction of its lookups. api-service is only passing that on.

- Inventory's durations split in two, with nothing between: 1,527 lookups under 38 ms and 620
  over 2.01 s in slow mode, 1,083 under 48 ms and 440 over 4.01 s in timeout mode. So some
  requests get a fixed extra delay and the rest aren't affected at all. That doesn't look like
  general overload, where I'd expect delays of varying lengths.
- `GET /admin/chaos` confirms it: enabled, `latency_ms` 2000 or 4000, `probability` 0.3.

Inventory's span has no children, so the telemetry can't say which step inside it is slow.

The two modes are the same fault. Only api-service's 3 s timeout makes them look different:
2 s stays under it and 4 s goes over.

## What I'd do immediately

1. Turn the degraded path off: `./scripts/resolve_incident.sh`. The alert cleared 30–35 s later.
2. If it couldn't be turned off, check the latency distribution before lowering
   `UPSTREAM_TIMEOUT_S`. Healthy lookups finished under 48 ms, so 1 s cuts off none of them
   and timeout-mode users get the 504 sooner. In slow mode I'd leave it, because requests that
   succeed at 2 s would become errors.
3. Otherwise, a hotfix depending on how many requests are slow. **Most slow, and users keep
   refreshing** (ticket sales, say): after 1 s, return a default that reads as "unknown".
   **Only some slow, as here**: wrap the stock lookup in a decorator with a short-lived cache,
   to reduce calls to inventory.

I didn't test 2 or 3.

## What I'd fix properly

- **Fail fast when inventory lookups are slow.** Where it's acceptable, tell the user straight
  away to try again, with a `Retry-After`, since retries add load. Where it isn't, answer from
  a faster cache, marked as possibly out of date.
- **Stop inventory working for nobody.** It keeps running for about a second after api-service
  gives up. Cancel on client disconnect, or pass the deadline along.
- **Add child spans inside inventory** so the slow step has a name.

## Does the alert fire in both modes?

Yes, `ApiServiceBadRequestRatioHigh`:

| | Pending | Firing | Cleared after fault off |
|---|---|---|---|
| Slow | +35 s | +215 s | 35 s |
| Timeout | +45 s | +225 s | 30 s |

It fires in slow mode because it counts slow requests as well as 5xx, where an error-rate
alert would stay silent. Two limits: it takes about 3.6 minutes to fire, the price of ignoring
spikes (burn-rate windows would page sooner), and it says users are affected, not which
service is responsible. The dashboard's per-service panels answer that.

## p99 versus the traces

They don't match, and they aren't expected to:

| | Prometheus p99 | Request logs p99 | Trace example | Over by |
|---|---|---|---|---|
| Slow, api | 2.45 s | 2.04 s | 2.04 s | ~20% |
| Slow, inventory | 2.45 s | 2.03 s | 2.03 s | ~20% |
| Timeout, api | 3.47 s | 3.02 s | 3.00 s | ~15% |
| Timeout, inventory | 5.92 s | 4.03 s | 4.03 s | ~47% |

**Why.** Prometheus only knows which bucket a request fell in, and `histogram_quantile`
interpolates inside it. 2.03 s is in `(1, 2.5]`, 3.0 s in `(2.5, 3.5]`, 4.03 s in `(3.5, 6]`.
With about 30% of requests in that one bucket, p99 lands near its top edge. The logs and
spans are measured per request, so those are the real values. Normal traffic reads high in
the same way: 59 ms from the edge at 60 ms, when api's real p99 is ~44 ms.

**What I'd do about it.** Use p99 for spotting a shift and comparing services, and follow an
exemplar to a trace for the exact figure. The alert is unaffected, because it counts `le="0.1"`
directly. Native histograms would shrink the error; I kept classic buckets because each edge
has a stated reason.

One more trap: in timeout mode the whole trace lasts about 4.03 s, but the user had their
504 at 3.00 s. For what the user experienced, read the api SERVER span, not the trace length.
