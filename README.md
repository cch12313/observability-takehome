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
# observability-takehome
