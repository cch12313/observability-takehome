# AI usage

## What I used

Claude Code. One session (Opus) for planning, laying out options and reviewing, and
separate agents (Sonnet, sometimes Opus) for implementation and independent review. The
agent that wrote a piece of code was not the one that reviewed it. For design choices I
asked for at least three options with trade-offs, and decided myself, often taking its
recommendation. The exceptions are below.

## What it wrote

- Collector, Tempo, Prometheus and Grafana config, Dockerfile and Compose changes, and the
  dashboard JSON.
- `common/metrics.py` and `common/logs.py`, apart from the bucket edges below.
- The alert rule and promtool tests, and `scripts/test_telemetry.py`.
- First drafts of most of the README, `INCIDENT.md` and this file. I changed the root cause,
  fixes and hotfix in `INCIDENT.md`, and the scaling notes, which I had it cut down to what I'd
  really do. The Metrics path section is my own, revised after a review.

What I decided or wrote myself:

- The bucket edges in `LATENCY_BUCKETS`, over several rounds of measuring.
- The 0.1 s SLO threshold.
- Zero-code tracing, and the alert's final `[1m]` window with `for: 3m`.

## How I checked it

- **Numbers come from the running stack.** I ran the fault modes myself while tuning the
  buckets, and compared Prometheus p95/p99 with loadgen logs and Tempo spans. The AI ran the
  final fault-mode and Collector runs and the log and Tempo analysis, while I watched the
  dashboard during the outage and read the results.
- **Tests had to be seen failing.** Bad alert settings (`for: 1m`, `[5m]`) had to fail
  promtool. A broken propagator had to fail the trace test while the services still returned 200.
- **Independent review** of code and README claims, by an agent that hadn't written them.
  Reviewers were wrong sometimes too, so I had those findings rechecked against the records.
- **I looked at the Grafana dashboard myself**, including during the Collector outage.

## What I changed or threw out

- **Instrumentation.** It recommended programmatic setup (`FastAPIInstrumentor`). I chose
  zero-code, because tracing is a platform concern and shouldn't live in the services. An
  end-to-end test guards it instead.
- **SLO threshold.** Its example used 1 s. I set 0.1 s: normal traffic sits around 40 ms,
  and 100 ms is about where a user starts to notice.
- **Top bucket edge.** I settled on 6.0, tied to twice the timeout. A reviewer challenged it
  twice and preferred a tighter fit to the 4 s I had measured. But that 4 s is only the
  fault's preset, not a real limit, so I kept 6.0.
- **Duplicated threshold.** It walked me towards writing `0.1` into the log-level code. I
  pointed out that this adds another copy to maintain, so Python now shares one constant.

## Where it was wrong

- **Alert spike protection.** It said a `[5m]` window with `for: 2m` would ignore a 1–2
  minute spike. It wouldn't: 30% over 1 of 5 minutes is still 6%, above the 5% threshold,
  and it stays in the window for 5 minutes. A promtool test exposed this, and I moved to `[1m]`.
- **Access logs.** The plan assumed they might be enough with trace ids added. A probe showed
  that uvicorn writes no line once the client has disconnected, which is exactly the
  inventory requests you need in timeout mode. So the request log moved to a middleware.
- **Inventory's "4 s limit".** It called 4 s a limit. When I asked where that was written, it
  admitted it was only the preset delay, then checked real spans. That led to a useful
  finding: in timeout mode the trace lasts about 4 s, but the user gets the 504 at about 3 s.

The log-level mapping (`_level_for`) was meant for me to write, but I handed it to the AI
in the end, so that code is AI-written too.
