#!/usr/bin/env bash
# End-to-end telemetry test: trace context must cross api-service -> inventory-service and
# reach Tempo as one parent/child chain. Needs the compose stack running. Stdlib python3 only.
# Override endpoints with API_URL / TEMPO_URL.
set -euo pipefail

exec python3 "$(dirname "$0")/test_telemetry.py" "$@"
