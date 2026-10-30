# Observability take-home

The starter is two small FastAPI services, one calling the other:

```text
api-service → inventory-service
```

`api-service` serves product lookups, and every lookup calls `inventory-service` for stock. Neither has any instrumentation. When something goes wrong you can tell the API got slow or started failing, and that's about it. You can't tell which service is at fault, or which part of the request went bad.

Your job is to make that answerable. Someone should be able to get from "something looks wrong" to "it's this service, in this part of the call, and here are the log lines for it".

There's also a Docker Compose file, a traffic generator, and a fault you can switch on at runtime. `README.md` covers how to run it all.

Nothing here has to be finished. If you run out of road on something, write down what you would have done and why you stopped. That won't count against you.

---

## What to build

### 1. One command to start

`docker compose up --build` should bring up everything, including whatever you add. No Kubernetes, no cloud.

### 2. Metrics and a dashboard

Request rate, error rate, p95 and p99 latency, at minimum.

One Grafana dashboard. It has to be possible to tell from it which of the two services is the problem, whether you do that with a service label or with separate panels. One dashboard is enough; more isn't better.

Dashboards and datasources belong in the repo as provisioning config. If the only copy lives in the Grafana UI and vanishes when the environment is rebuilt, that doesn't count.

Tell me in the README how you picked the bucket boundaries on your latency histogram.

### 3. Tracing

A request crossing from api-service to inventory-service should come out as one trace, with spans from both.

Pick whatever trace backend you like. Jaeger, Tempo, anything else reasonable.

Traces have to go through an OpenTelemetry Collector. Metrics can either be scraped by Prometheus or routed through the Collector as well. Say which you picked and why.

### 4. Logs that line up with traces

Structured logs. JSON on stdout is fine. When a span is active the log line needs to carry `trace_id` and `span_id`, so you can get from a trace to its logs, or from a log line back to the trace it came from.

Don't bother deploying Loki or Elasticsearch for this. Showing it with stdout is enough.

Write a few lines on what would have to change if those logs later went to a central backend.

### 5. An alert

One alert, tied to user impact, committed as config.

In the README: what signal you picked and why, how you landed on the threshold, and what stops a brief spike from firing it.

### 6. A test that proves the telemetry works

Instrumentation breaks quietly. A refactor drops a header, a library upgrade changes a default, and traces stop joining up. Nothing fails, nothing goes red, and you find out weeks later in the middle of an incident.

Write an automated test that would catch that. At minimum it has to prove that a request crossing both services produces one trace containing spans from both. Not that the services are up, not that a trace exists somewhere. That the context actually survived the hop.

How you do it is up to you. Driving a request and then asserting against the trace backend's query API is one way. Pick whatever fits the stack you built.

Make it runnable from the repo with one documented command, and say in the README what it does and does not cover.

If you want to extend it to the alert rule as well, `promtool test rules` or equivalent is a reasonable addition, but the trace assertion is the part I care about.

### 7. Write up the incident

There are two fault modes, and they don't look the same from the outside.

```bash
./scripts/trigger_incident.sh slow      # upstream gets slow, still inside api-service's timeout
./scripts/trigger_incident.sh timeout   # upstream gets slow enough to blow through it
./scripts/resolve_incident.sh           # off
```

Trigger both, watch what happens, then write `INCIDENT.md`. Two pages at most:

- what a user would notice in each mode
- what the metrics said, with the numbers you actually saw
- what the traces said
- root cause
- what you'd do immediately to stop the bleeding
- what you'd fix properly
- does your alert fire in both modes? If not, what would you change?

Back the conclusions with metrics, traces or logs. Reading the source doesn't count as evidence.

One more thing on this. The p99 figures you report should line up with the durations you see in the traces. If they don't, say why, and what you'd do about it.

### 8. When the observability stack itself breaks

Once everything is running, kill your Collector while traffic is still flowing. Leave it down for a few minutes, then bring it back.

Write up what you saw, in the README or a short separate file:

- what happened to the application. Did it slow down, error, block, or carry on?
- what happened to the data. Did you lose any, and how did you check?
- what, if anything, told you something was wrong
- would any of your dashboards or alerts have caught it? Be honest if the answer is no
- what you'd put in place so that someone would find out without having to go looking

Half a page is enough. I'm interested in what you actually observed, not what the documentation says should happen.

### 9. Credentials

Nothing secret hard-coded in the repo. That includes the Grafana admin password and any backend credentials. Environment variables, a `.env.example`, whatever you prefer. Document how to supply them.

### 10. README

Cover at least:

- starting the environment
- generating traffic
- triggering both fault modes
- where Grafana and the trace UI are
- running the telemetry test
- how to verify the whole thing. Someone handed your repo should be able to walk the investigation path start to finish
- the trade-offs you made
- what you know is missing

Plus the written answers the sections above ask for.

### 11. If this had to scale (written, no code)

A page at most. How would you adapt this to run across several environments, long term?

Assume:

- services run in several isolated cloud accounts, one per environment, with no shared network between them by default
- they run on a managed container platform where you don't get at the underlying hosts. No per-node collector, no host paths
- the observability backends live on their own cluster
- there's also a set of long-lived Linux boxes behind a corporate firewall with nothing able to reach in. Their logs need collecting too

Touch on:

- collector topology, how you'd split agent and gateway tiers, and why
- with no per-node collector available, how you'd collect container logs and telemetry. What the options are, which you'd take, and what you're not sure about
- the boxes with no inbound route. How data gets out, what happens while one is offline, how you'd manage and upgrade the agents on them
- where you'd land on availability versus losing data
- cost. Sampling, retention, cardinality
- how you'd manage and ship all of this config to several environments

Bullets are fine.

---

## Using AI

Use it. Then write a short `AI_USAGE.md`:

- what you used
- what it wrote
- how you checked it
- at least one thing it suggested that you changed or threw out, and why

No need for prompts or transcripts.

## Optional

Not required and not expected. Pick one or two if you feel like it, but don't stretch the submission for them.

1. Ship the structured logs to a real backend, Loki for instance, and make trace and logs navigable both ways in Grafana.
2. Tail sampling at the Collector, keeping errors and slow requests.
3. A better alert. SLO or burn rate, with a runbook attached.
4. Load-test the thing and show what the telemetry looked like under it.

## Submitting

```text
README.md
INCIDENT.md
AI_USAGE.md
docker-compose.yml
application and observability config
Grafana datasource and dashboard definitions
alert rule
the telemetry test
```

A repo link is easiest. An archive works too.

## Before you send it

Check that `docker compose down -v` followed by `up --build` still works, and that someone who doesn't know what the fault is could follow your investigation path.

None of this needs to be highly available or production grade. Extra components thrown in to show range will count against you, not for you.
