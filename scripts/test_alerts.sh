#!/usr/bin/env bash
# Run promtool unit tests for the Prometheus alert rules (no local install needed).
# Tests live outside prometheus/rules/ because Prometheus loads every rules/*.yml as a rule file.
set -euo pipefail

cd "$(dirname "$0")/.."
docker run --rm -v "$PWD/prometheus:/prometheus-cfg:ro" --entrypoint promtool \
  prom/prometheus:v3.15.1 test rules /prometheus-cfg/tests/alerts.test.yaml
