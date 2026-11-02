# Starter project

Two Python FastAPI services that run locally:

```text
api-service  →  inventory-service
```

`api-service` serves product lookups. Every lookup calls `inventory-service` for stock.

There's no metrics, tracing or structured logging in here. That's on purpose, and adding it is the assignment. You'll need to extend this project, `docker-compose.yml` included.

The assignment is in `ASSIGNMENT.md`. Read that first.

## Requirements

Docker and Docker Compose.

## Running it

```bash
docker compose up --build
```

Then:

- api-service on <http://localhost:8000> (`GET /products/{sku}`, `GET /health`)
- inventory-service on <http://localhost:8001> (`GET /stock/{sku}`, `GET /health`)

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

## Known limitations

- Local evaluation only. This isn't a production setup.
- No persistence. Stock values are made up in memory.
- Nothing comes out except stdout, which is the point.
## Decisions

### Latency buckets

Buckets (s): `0.06, 0.1, 0.25, 0.5, 1.0, 2.5, 3.5, 6.0` (`common/metrics.py`).
Each edge is tied to a threshold or to a latency I measured in each mode:

| Edge | Why |
|---|---|
| 0.06 | Just above normal p99 (~45–50 ms), so healthy traffic sits in the first bucket. |
| 0.1 | The SLO: ~2x normal p99, and about where users start to notice delay. The alert counts requests above it, so it must be an exact edge. |
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
